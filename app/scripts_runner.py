"""Sandboxed execution of user-provided build123d scripts.

Backs the MCP ``ejecutar_script`` tool (Phase 3). Since F0.2 (ADR-0013) the
script never runs in this container: the request goes to the separate
``forja-sandbox`` container (uid 65534, no network, read-only root, no
data/fonts/secret mounts) through the shared ``staging`` volume:

1. write ``staging/trabajos/<id>/peticion.json`` (job dir 0711, so the
   sandbox can reach it by its random id but cannot list its siblings) and
   an empty ``salida/`` the child may write into;
2. ask the executor over its Unix socket to run job ``<id>``;
3. read back ``salida/resultado.step`` and ``resultado.nombres.json`` as
   untrusted data: ``lstat`` must say single-link regular file, the open
   uses ``O_NOFOLLOW | O_NONBLOCK`` and must be the same inode, and sizes
   are capped (a symlink to a secret, a FIFO or a huge file is rejected);
4. copy the STEP into a private temp dir of this process and remove the
   job dir.

The executor runs one job at a time; this process queues its own calls on
a lock (up to ``protocolo.MAX_ESPERA_COLA_S``) before talking to it, so the
socket deadline (``timeout`` + margin) starts when the job can really run.

A broken or infinite-looping script can never hang or crash the web
backend — it is killed after ``timeout`` seconds. Only a path to the
resulting STEP file or a short error message ever crosses back to the
caller; a full traceback never leaks into an MCP tool response
(token-economy contract) — Phase 5A adds the failing line number/source
line *from the user's own script* (``linea``/``codigo_linea``).
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import signal
import socket
import stat
import tempfile
import threading
from pathlib import Path
from typing import Any

import solids
from sandbox import protocolo

DEFAULT_TIMEOUT = 30.0

# Fixed filename the child compiles the user's script under (never a real
# path) — used to filter traceback frames down to the user's own code
# (Phase 5A error reporting). Mirrors `sandbox.bootstrap.NOMBRE_SCRIPT`.
NOMBRE_SCRIPT = "<forja-script>"

# Extra wait on the socket beyond the script's own timeout: the executor
# kills the child at `timeout` and still has to sweep and answer.
MARGEN_SOCKET_S = 20.0

_PREFIJO_ERROR = "__FORJA_ERROR__"

# One job at a time reaches the executor from this process (it serializes
# anyway; waiting here keeps the socket deadline honest).
_cola = threading.Lock()


class ScriptError(Exception):
    """The script raised an exception or produced no valid shape.

    ``linea``/``codigo_linea`` (Phase 5A) are only populated when the
    failure could be attributed to a specific line inside the user's own
    script (an exception raised out of the ``exec(...)`` block); other
    failures (kernel export rejecting the result, missing ``resultado``,
    invalid solid names) leave them ``None`` — ``mensaje`` alone still
    carries a short, compact description either way.
    """

    def __init__(self, mensaje: str, linea: int | None = None, codigo_linea: str | None = None):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.linea = linea
        self.codigo_linea = codigo_linea


class ScriptTimeoutError(Exception):
    """The script did not finish within the configured timeout."""


def _extraer_error(stderr: str) -> ScriptError:
    """Build a ``ScriptError`` from the sandboxed child's stderr: if the last
    line is the ``__FORJA_ERROR__`` marker ``_reportar_error_script`` prints
    (a script-attributable exception), parse it for ``linea``/
    ``codigo_linea``; otherwise fall back to the plain last traceback line
    (kernel/export failures, the "sin resultado" message, timeouts never
    reach here)."""
    lineas = [linea for linea in stderr.strip().splitlines() if linea.strip()]
    if not lineas:
        return ScriptError("error desconocido al ejecutar el script")

    ultima = lineas[-1]
    if ultima.startswith(_PREFIJO_ERROR):
        try:
            detalle = json.loads(ultima[len(_PREFIJO_ERROR) :])
            return ScriptError(
                detalle.get("mensaje", "error desconocido al ejecutar el script"),
                linea=detalle.get("linea"),
                codigo_linea=detalle.get("codigo_linea"),
            )
        except json.JSONDecodeError:
            pass
        if len(lineas) >= 2:
            return ScriptError(lineas[-2])
        return ScriptError("error desconocido al ejecutar el script")
    return ScriptError(ultima)


def _validar_nombres(texto: str) -> list[str] | None:
    try:
        candidato = json.loads(texto)
    except json.JSONDecodeError:
        return None
    if not isinstance(candidato, list) or not all(isinstance(n, str) for n in candidato):
        return None
    try:
        solids.validar_nombres(candidato)
    except ValueError:
        return None
    return candidato


def _leer_nombres_confiables(nombres_path: Path) -> list[str] | None:
    """Re-validate ``.nombres.json`` before trusting ANY of it (Phase 5A
    fix-review): the sandboxed child runs arbitrary user code in the very
    process that writes this file, so nothing stops a malicious script from
    writing it itself with crafted content (e.g. a path-traversal name like
    ``"../evil"``, a real risk once names become per-solid export
    filenames). Writing this file is a best-effort convenience, never a
    security boundary on its own.

    Only a ``list[str]`` where every entry passes ``solids.validar_nombres``
    (safe chars, non-empty, length cap, unique) is trusted; anything else —
    wrong type, non-string entries, an unsafe/invalid name — falls back to
    ``None`` (flat ``solido_N`` naming downstream), never partially
    trusted. Whether the count matches the actual number of solids is
    checked downstream by ``solids.construir_entradas``."""
    try:
        texto = nombres_path.read_text()
    except (OSError, UnicodeDecodeError):
        return None
    return _validar_nombres(texto)


class SalidaRechazada(Exception):
    """An output in staging is not a plain, single-link, bounded file."""


def abrir_salida(dir_fd: int, nombre: str, max_bytes: int) -> int | None:
    """Open ``nombre`` inside the directory ``dir_fd`` as untrusted data.

    ``None`` if it does not exist. Raises ``SalidaRechazada`` unless
    ``lstat`` says regular file with one link and at most ``max_bytes``,
    and the ``O_NOFOLLOW | O_NONBLOCK`` open (never blocks on a FIFO, never
    follows a symlink) lands on that same inode. Returns the open fd."""
    try:
        previo = os.stat(nombre, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(previo.st_mode):
        raise SalidaRechazada(f"{nombre} no es un archivo regular")
    if previo.st_nlink != 1:
        raise SalidaRechazada(f"{nombre} tiene enlaces duros")
    if previo.st_size > max_bytes:
        raise SalidaRechazada(f"{nombre} supera {max_bytes // (1024 * 1024)} MB")
    try:
        fd = os.open(nombre, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dir_fd)
    except OSError as exc:
        raise SalidaRechazada(f"{nombre} no se pudo abrir de forma segura") from exc
    actual = os.fstat(fd)
    if (
        not stat.S_ISREG(actual.st_mode)
        or (actual.st_dev, actual.st_ino) != (previo.st_dev, previo.st_ino)
        or actual.st_size > max_bytes
    ):
        os.close(fd)
        raise SalidaRechazada(f"{nombre} cambio durante la lectura")
    return fd


def leer_salida(dir_fd: int, nombre: str, max_bytes: int, destino: Path | None = None) -> bytes | bool | None:
    """Read an output through `abrir_salida`, never past ``max_bytes``.
    With ``destino`` the bytes are streamed into that new file (0600) and
    ``True`` is returned; otherwise the bytes themselves. ``None`` if absent."""
    fd = abrir_salida(dir_fd, nombre, max_bytes)
    if fd is None:
        return None
    total = 0
    trozos: list[bytes] = []
    try:
        with os.fdopen(fd, "rb") as origen:
            salida = open(destino, "xb") if destino is not None else None
            try:
                while bloque := origen.read(1 << 20):
                    total += len(bloque)
                    if total > max_bytes:
                        raise SalidaRechazada(f"{nombre} supera el tamano maximo")
                    if salida is not None:
                        salida.write(bloque)
                    else:
                        trozos.append(bloque)
            finally:
                if salida is not None:
                    salida.close()
    except SalidaRechazada:
        if destino is not None:
            destino.unlink(missing_ok=True)
        raise
    return True if destino is not None else b"".join(trozos)


def _preparar_trabajo(codigo: str, timeout: float, variables: dict[str, Any] | None) -> tuple[str, Path]:
    id_trabajo = secrets.token_hex(16)
    trabajo = protocolo.TRABAJOS_DIR / id_trabajo
    os.mkdir(trabajo, 0o700)
    os.chmod(trabajo, 0o711)
    peticion = json.dumps({"codigo": codigo, "variables": variables or {}, "timeout": timeout}).encode()
    fd = os.open(trabajo / protocolo.PETICION, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(peticion)
        os.fchmod(f.fileno(), 0o644)
    salida = trabajo / protocolo.SALIDA_DIR
    os.mkdir(salida, 0o700)
    os.chmod(salida, 0o777)
    return id_trabajo, trabajo


def pedir_al_ejecutor(pedido: dict[str, Any], espera_s: float) -> dict[str, Any]:
    """One JSON line to the executor's Unix socket, one JSON line back."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(espera_s)
        try:
            conn.connect(str(protocolo.SOCKET_PATH))
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            raise ScriptError("sandbox no disponible; reintentar en unos segundos") from exc
        conn.sendall(json.dumps(pedido).encode() + b"\n")
        datos = b""
        while b"\n" not in datos:
            bloque = conn.recv(65536)
            if not bloque:
                break
            datos += bloque
            if len(datos) > protocolo.MAX_MENSAJE_BYTES:
                raise ScriptError("respuesta del sandbox demasiado grande")
    try:
        respuesta = json.loads(datos.split(b"\n", 1)[0])
    except json.JSONDecodeError as exc:
        raise ScriptError("respuesta del sandbox ilegible") from exc
    if not isinstance(respuesta, dict):
        raise ScriptError("respuesta del sandbox ilegible")
    return respuesta


def _error_de_salida(codigo_salida: int, stderr: str) -> ScriptError:
    if codigo_salida == -signal.SIGXCPU:
        return ScriptError("limite de cpu excedido")
    if codigo_salida == -signal.SIGXFSZ:
        return ScriptError("limite de tamano de archivo excedido (512 MB)")
    if codigo_salida == -signal.SIGKILL and not stderr.strip():
        return ScriptError("el script fue terminado (memoria o procesos)")
    return _extraer_error(stderr)


def _con_perfil(variables: dict[str, Any] | None) -> dict[str, Any]:
    """fdm-A: every job gets the printer profile as the ``PERFIL`` global
    (the active one, a named one if ``PERFIL`` is a string, or the caller's
    own dict). Best effort: a broken profile store never fails a script."""
    variables = dict(variables or {})
    try:
        import perfiles

        variables["PERFIL"] = perfiles.resolver_para_script(variables.get("PERFIL"))
    except Exception:  # noqa: BLE001
        import perfil_fdm

        if not isinstance(variables.get("PERFIL"), dict):
            variables["PERFIL"] = dict(perfil_fdm.SEMILLA)
    return variables


def ejecutar_script(
    codigo: str, timeout: float = DEFAULT_TIMEOUT, variables: dict[str, Any] | None = None
) -> tuple[Path, list[str] | None]:
    """Run ``codigo`` in the sandbox; return ``(salida, nombres)``: the path
    to the resulting STEP file (inside a temp directory owned by the
    caller), and — only when ``resultado`` was a ``{nombre: Shape}`` dict
    (Phase 5A named solids) — the ordered list of dict keys, read back from
    the child's side channel and re-validated (never re-derived from the
    STEP file's own unreliable labels, see ``app/solids.py``). ``None``
    otherwise, or if that re-validation fails.

    The caller is responsible for moving the STEP file out of that temp
    directory and then removing the directory (``shutil.rmtree``) once done
    with it. ``variables`` (Phase 5A), if given, is injected as module
    globals in the script's namespace *before* it runs (already JSON-safe).

    Raises ``ScriptError`` on any script failure (see its docstring for the
    ``linea``/``codigo_linea`` contract), a rejected output or an
    unreachable sandbox, and ``ScriptTimeoutError`` if it exceeds
    ``timeout`` seconds.
    """
    variables = _con_perfil(variables)
    id_trabajo, trabajo = _preparar_trabajo(codigo, timeout, variables)
    tmp_dir: Path | None = None
    try:
        if not _cola.acquire(timeout=protocolo.MAX_ESPERA_COLA_S):
            raise ScriptError("sandbox ocupado; reintentar en unos segundos")
        try:
            respuesta = pedir_al_ejecutor(
                {"op": "ejecutar", "id": id_trabajo, "timeout": timeout}, timeout + MARGEN_SOCKET_S
            )
        except TimeoutError as exc:
            raise ScriptTimeoutError(f"tiempo agotado tras {timeout:.0f}s") from exc
        finally:
            _cola.release()
        if respuesta.get("tiempo_agotado"):
            raise ScriptTimeoutError(f"tiempo agotado tras {timeout:.0f}s")
        codigo_salida = respuesta.get("codigo_salida")
        stderr = str(respuesta.get("stderr") or "")
        if codigo_salida != 0:
            raise _error_de_salida(codigo_salida if isinstance(codigo_salida, int) else 1, stderr)

        dir_fd = os.open(trabajo / protocolo.SALIDA_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            tmp_dir = Path(tempfile.mkdtemp(prefix="forja_script_"))
            salida_path = tmp_dir / protocolo.RESULTADO
            try:
                copiado = leer_salida(dir_fd, protocolo.RESULTADO, protocolo.MAX_RESULTADO_BYTES, salida_path)
                if not copiado:
                    raise _extraer_error(stderr) if stderr.strip() else ScriptError(
                        "el script no produjo un resultado.step"
                    )
                nombres_ordenados: list[str] | None = None
                try:
                    datos = leer_salida(dir_fd, protocolo.NOMBRES, protocolo.MAX_NOMBRES_BYTES)
                except SalidaRechazada:
                    datos = None  # untrusted side channel: flat naming, as for bad content
                if isinstance(datos, bytes):
                    nombres_ordenados = _validar_nombres(datos.decode("utf-8", "replace"))
            except SalidaRechazada as exc:
                raise ScriptError(f"salida rechazada: {exc}") from exc
        finally:
            os.close(dir_fd)
    except BaseException:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(trabajo, ignore_errors=True)
    return salida_path, nombres_ordenados
