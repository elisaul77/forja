"""Sandbox executor: PID 1 of the `forja-sandbox` container (F0.2, ADR-0013).

Runs as uid 65534 in a container with ``network_mode: none``, a read-only
root, ``tmpfs /tmp``, ``pids_limit``, ``cap_drop: ALL`` and
``no-new-privileges``; the only mount is the shared ``staging`` volume. It
listens on a Unix socket there and, per request, starts one fresh child
(``python -P /app/sandbox/bootstrap.py``, job dir on a stdin pipe) with a
minimal environment, waits for it with a deadline, and answers with the exit
code and the tail of stderr. Protocol: one JSON line each way.

    -> {"op": "ping"}                                   <- {"ok": true}
    -> {"op": "ejecutar", "id": "<32 hex>", "timeout": s}
    <- {"codigo_salida": int, "tiempo_agotado": bool, "stderr": str}

Containment choices:

- **One job at a time** (a lock around the whole job): no sibling job is
  alive to read or write another's files, and the host (no ``mem_limit``
  yet) never holds two 1.5 GB children. A queued request spends its wait out
  of its own ``timeout``, so the server's deadline still holds.
- ``ejecutar`` is only accepted from a peer whose uid (``SO_PEERCRED``) is
  not ours: the server is uid 1000, children are 65534 like us, so a script
  cannot queue jobs. ``ping`` is open (healthcheck via ``docker exec``).
- The job dir travels on the child's stdin, never in argv
  (``/proc/<pid>/cmdline`` is world-readable); the child's ``HOME`` name
  carries no part of the id. The child confines itself with Landlock and
  marks itself non-dumpable (see `bootstrap`).
- The socket lives in ``/staging/ejecutor`` (ours, 0755). Children cannot
  write there (Landlock), and as a second line the accept loop re-checks the
  socket once a second: if it is gone or is not ours (inode changed) it is
  removed and bound again. Landlock does not cover ``chmod`` and both are
  owned by our uid, so the loop also restores 0666/0755; after every job,
  before answering, everything in that directory except the socket is
  removed and both modes are restored.
- PID 1 marks itself non-dumpable, so a script cannot read
  ``/proc/1/environ``. SIGTERM/SIGINT are blocked and collected by one
  thread with ``sigwaitinfo``: the executor only stops when the sender is
  outside its pid namespace (``si_pid == 0``: ``docker stop``); a script's
  ``os.kill(1, SIGTERM)`` is ignored (and, on Landlock ABI >= 6, refused
  by the kernel). Other signals from inside the namespace never reach PID 1
  with the default disposition.
- Each child gets its own session/process group and private ``HOME`` under
  ``/tmp``; after it ends the whole group is killed, then a sweep kills any
  other process that escaped it (orphans reparent to PID 1). Processes with
  ``ppid 0`` were entered from outside (``docker exec``, healthchecks) and
  are left alone.
- A watcher thread kills the job's group if its ``salida/`` grows past
  ``MAX_SALIDA_TRABAJO_BYTES`` (or ``MAX_ENTRADAS_SALIDA`` entries), or if
  the staging volume's free space falls under ``MIN_LIBRE_STAGING_BYTES``.
- PID 1 must reap every orphan, so children are started with
  ``os.posix_spawn`` and all exit statuses come from one reaper thread
  (``subprocess.Popen.wait`` would race with it).
- Only the two expected outputs survive in ``salida/``, and only as single-
  link regular files made readable (0644) for the server; anything else the
  script left there (symlinks, FIFOs, directories — even mode 000 ones —,
  extra files) is removed, by dir fds and without recursion. The server
  still re-checks them with ``O_NOFOLLOW`` + ``lstat`` + size cap.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import signal
import socket
import stat
import struct
import sys
import tempfile
import threading
import time
from pathlib import Path

from sandbox import bootstrap, protocolo

_RE_ID = re.compile(r"^[0-9a-f]{32}$")
MAX_TIMEOUT_S = 600.0
BARRIDO_PERIODO_S = 30.0
VIGILANCIA_SOCKET_S = 1.0
VIGILANCIA_ESCRITURA_S = 0.25
MAX_PROFUNDIDAD_BORRADO = 256  # open dir fds while removing a tree (nofile is 1024)
PR_SET_DUMPABLE = 4
SENALES_PARADA = {signal.SIGTERM, signal.SIGINT}

_estados: dict[int, tuple[int, float]] = {}
_estados_cond = threading.Condition()
_activos: set[int] = set()  # process-group ids of running children
_activos_lock = threading.Lock()
_trabajo_lock = threading.Lock()  # one job at a time
_parar = threading.Event()


def _log(mensaje: str) -> None:
    print(f"[ejecutor] {mensaje}", flush=True)


def _no_volcable() -> None:
    try:
        ctypes.CDLL(None, use_errno=True).prctl(PR_SET_DUMPABLE, 0, 0, 0, 0)
    except (OSError, AttributeError):
        _log("aviso: no se pudo marcar el proceso como no volcable")


def _esperar_senales() -> None:
    """Stop only on SIGTERM/SIGINT sent from outside our pid namespace."""
    ultimo_aviso = 0.0
    while True:
        info = signal.sigwaitinfo(SENALES_PARADA)
        if info.si_pid == 0:
            _log(f"senal {info.si_signo} desde fuera del contenedor; parando")
            _parar.set()
            return
        ahora = time.monotonic()
        if ahora - ultimo_aviso > 10:
            ultimo_aviso = ahora
            _log(f"senal {info.si_signo} del pid {info.si_pid} ignorada")


def _segador() -> None:
    """Reap every child and orphan; keep statuses for the job threads."""
    while not _parar.is_set():
        try:
            pid, estado = os.waitpid(-1, 0)
        except ChildProcessError:
            time.sleep(0.05)
            continue
        except InterruptedError:
            continue
        with _estados_cond:
            ahora = time.monotonic()
            _estados[pid] = (estado, ahora)
            for viejo in [p for p, (_e, t) in _estados.items() if ahora - t > 120]:
                del _estados[viejo]
            _estados_cond.notify_all()


def _esperar_estado(pid: int, limite: float | None) -> int | None:
    with _estados_cond:
        while pid not in _estados:
            restante = None if limite is None else limite - time.monotonic()
            if restante is not None and restante <= 0:
                return None
            _estados_cond.wait(restante if restante is None else min(restante, 1.0))
        return _estados.pop(pid)[0]


def _matar_grupo(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _proc_stat(pid: int) -> tuple[int, int] | None:
    """(ppid, pgrp) from /proc/<pid>/stat, or None if gone/unreadable."""
    try:
        texto = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    campos = texto[texto.rfind(")") + 2 :].split()
    try:
        return int(campos[1]), int(campos[2])
    except (IndexError, ValueError):
        return None


def barrer() -> int:
    """Kill processes that escaped their job's process group. Only as PID 1
    (in the sandbox container every other process descends from us)."""
    if os.getpid() != 1:
        return 0
    muertos = 0
    with _activos_lock:
        for entrada in os.listdir("/proc"):
            if not entrada.isdigit() or int(entrada) == 1:
                continue
            datos = _proc_stat(int(entrada))
            if datos is None:
                continue
            ppid, pgrp = datos
            if ppid == 0 or pgrp in _activos:
                continue  # entered via docker exec / healthcheck, or a live job
            try:
                os.kill(int(entrada), signal.SIGKILL)
                muertos += 1
            except (ProcessLookupError, PermissionError):
                pass
    return muertos


def _leer_cola(fd: int, destino: list[bytes]) -> None:
    """Drain the child's stderr pipe keeping only the last MAX_STDERR_BYTES."""
    cola = b""
    with os.fdopen(fd, "rb", buffering=0) as f:
        while True:
            try:
                bloque = f.read(65536)
            except OSError:
                break
            if not bloque:
                break
            cola = (cola + bloque)[-protocolo.MAX_STDERR_BYTES :]
    destino.append(cola)


# --- removal of untrusted trees ----------------------------------------------

_O_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def vaciar_dir(ruta: Path | str, conservar: frozenset[str] | set[str] = frozenset()) -> None:
    """Remove everything inside ``ruta`` except the top-level names in
    ``conservar``. Works on dir fds (no path length limit, never follows a
    symlink), iteratively (no recursion limit), and gives directories we own
    0700 before listing them, so a mode-000 directory left by a script does
    not survive. Best effort: what cannot be removed is skipped, never
    retried in a loop; nesting beyond MAX_PROFUNDIDAD_BORRADO is left."""
    try:
        raiz = os.open(ruta, _O_DIR)
    except OSError:
        return
    pila: list[tuple[int, int | None, str | None]] = [(raiz, None, None)]
    fallidos: set[tuple[int, int]] = set()
    try:
        while pila:
            fd, padre, nombre = pila[-1]
            try:
                nombres = os.listdir(fd)
            except OSError:
                nombres = []
            bajar = False
            for n in nombres:
                if padre is None and n in conservar:
                    continue
                try:
                    st = os.stat(n, dir_fd=fd, follow_symlinks=False)
                except OSError:
                    continue
                if (st.st_dev, st.st_ino) in fallidos:
                    continue
                if not stat.S_ISDIR(st.st_mode):
                    try:
                        os.unlink(n, dir_fd=fd)
                    except OSError:
                        fallidos.add((st.st_dev, st.st_ino))
                    continue
                if len(pila) >= MAX_PROFUNDIDAD_BORRADO:
                    fallidos.add((st.st_dev, st.st_ino))
                    continue
                try:
                    os.chmod(n, 0o700, dir_fd=fd)  # lstat said directory; its writer is dead
                except OSError:
                    pass
                try:
                    sub = os.open(n, _O_DIR, dir_fd=fd)
                except OSError:
                    fallidos.add((st.st_dev, st.st_ino))
                    continue
                pila.append((sub, fd, n))
                bajar = True
                break
            if bajar:
                continue
            pila.pop()
            if padre is not None:
                st = os.fstat(fd)
                os.close(fd)
                try:
                    os.rmdir(nombre, dir_fd=padre)
                except OSError:
                    fallidos.add((st.st_dev, st.st_ino))
    finally:
        abiertos = {fd for fd, _padre, _nombre in pila}
        for fd in abiertos:
            os.close(fd)
        if raiz not in abiertos:  # popped (never closed there) after a full pass
            os.close(raiz)


def borrar_arbol(ruta: Path | str) -> None:
    """``ruta`` and everything under it (see `vaciar_dir`)."""
    try:
        st = os.lstat(ruta)
    except OSError:
        return
    if not stat.S_ISDIR(st.st_mode):
        try:
            os.unlink(ruta)
        except OSError:
            pass
        return
    try:
        os.chmod(ruta, 0o700)
    except OSError:
        pass
    vaciar_dir(ruta)
    try:
        os.rmdir(ruta)
    except OSError:
        pass


def _limpiar_salida(salida: Path) -> None:
    """Keep only the expected outputs, as single-link regular files, 0644."""
    conservar = set()
    for nombre in protocolo.SALIDAS_ESPERADAS:
        ruta = salida / nombre
        try:
            st = os.lstat(ruta)
        except OSError:
            continue
        if not (stat.S_ISREG(st.st_mode) and st.st_nlink == 1):
            continue
        try:
            fd = os.open(ruta, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
            try:
                if stat.S_ISREG(os.fstat(fd).st_mode):
                    os.fchmod(fd, 0o644)
                    conservar.add(nombre)
            finally:
                os.close(fd)
        except OSError:
            pass
    vaciar_dir(salida, conservar)


# --- per-job write cap --------------------------------------------------------


def _chmod_dir_sin_seguir(ruta: str) -> None:
    """0700 on ``ruta`` only if it is a directory itself (not a symlink a
    live script just swapped in): chmod through an ``O_PATH`` fd."""
    fd = os.open(ruta, os.O_PATH | os.O_NOFOLLOW | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.chmod(f"/proc/self/fd/{fd}", 0o700)
    finally:
        os.close(fd)


def medir_arbol(ruta: Path | str, max_entradas: int) -> tuple[int, int, bool]:
    """(bytes on disk, entries, complete) of the tree under ``ruta`` while
    its writer may still be alive. Never follows symlinks; an unreadable
    directory we own is given 0700 and retried once; ``complete`` is False
    if something could not be measured or the entry cap was hit."""
    total = 0
    entradas = 0
    completo = True
    pendientes = [str(ruta)]
    while pendientes:
        actual = pendientes.pop()
        try:
            iterador = os.scandir(actual)
        except PermissionError:
            try:
                _chmod_dir_sin_seguir(actual)
                iterador = os.scandir(actual)
            except OSError:
                completo = False
                continue
        except OSError:
            continue
        with iterador:
            for entrada in iterador:
                entradas += 1
                if entradas > max_entradas:
                    return total, entradas, False
                try:
                    st = entrada.stat(follow_symlinks=False)
                except OSError:
                    continue
                total += st.st_blocks * 512
                if stat.S_ISDIR(st.st_mode):
                    pendientes.append(entrada.path)
    return total, entradas, completo


def libre_staging() -> int:
    vfs = os.statvfs(protocolo.STAGING_DIR)
    return vfs.f_bavail * vfs.f_frsize


def vigilar_escritura(
    salida: Path | str,
    pgid: int,
    fin: threading.Event,
    motivo: list[str],
    max_bytes: int = protocolo.MAX_SALIDA_TRABAJO_BYTES,
    min_libre: int = protocolo.MIN_LIBRE_STAGING_BYTES,
    max_entradas: int = protocolo.MAX_ENTRADAS_SALIDA,
    periodo: float = VIGILANCIA_ESCRITURA_S,
    libre=libre_staging,
) -> None:
    """Until ``fin`` is set: kill process group ``pgid`` (and record why in
    ``motivo``) if ``salida`` exceeds ``max_bytes`` or ``max_entradas``, if
    it cannot be measured, or if the staging volume has less than
    ``min_libre`` bytes free."""
    while not fin.wait(periodo):
        usado, entradas, completo = medir_arbol(salida, max_entradas)
        razon = None
        if usado > max_bytes:
            razon = f"limite de escritura excedido: la salida supera {max_bytes // (1024 * 1024)} MB"
        elif entradas > max_entradas:
            razon = f"limite de escritura excedido: mas de {max_entradas} entradas en la salida"
        elif not completo:
            razon = "limite de escritura excedido: salida no medible"
        else:
            try:
                if libre() < min_libre:
                    razon = "limite de escritura excedido: poco espacio libre en el disco"
            except OSError:
                pass
        if razon:
            motivo.append(razon)
            _matar_grupo(pgid)
            return


# --- jobs -----------------------------------------------------------------------


def _limpiar_dir_ejecutor() -> None:
    """Everything in the socket's directory except the socket itself, and
    the modes back to 0755/0666 (Landlock does not cover chmod, and both are
    ours, so a script may have changed them); done before answering, so the
    server's next connection finds them right."""
    ruta = protocolo.SOCKET_PATH
    try:
        os.chmod(ruta.parent, 0o755)
    except OSError:
        pass
    vaciar_dir(ruta.parent, {ruta.name})
    try:
        if stat.S_ISSOCK(os.lstat(ruta).st_mode):
            os.chmod(ruta, 0o666)
    except OSError:
        pass


def _limpiar_tmp() -> None:
    """Our leftovers in /tmp (only job homes live there; jobs run one at a time)."""
    vaciar_dir("/tmp")


def ejecutar_trabajo(id_trabajo: str, timeout: float) -> dict:
    llegada = time.monotonic()
    if not _trabajo_lock.acquire(timeout=timeout):
        return {"codigo_salida": 1, "tiempo_agotado": True, "stderr": "sandbox ocupado"}
    try:
        return _ejecutar_trabajo(id_trabajo, llegada + timeout)
    finally:
        _trabajo_lock.release()


def _ejecutar_trabajo(id_trabajo: str, limite: float) -> dict:
    trabajo = protocolo.TRABAJOS_DIR / id_trabajo
    salida = trabajo / protocolo.SALIDA_DIR
    try:
        if not stat.S_ISDIR(os.lstat(trabajo).st_mode) or not stat.S_ISDIR(os.lstat(salida).st_mode):
            raise FileNotFoundError
    except OSError:
        return {"codigo_salida": 1, "tiempo_agotado": False, "stderr": "trabajo inexistente"}
    vaciar_dir(salida)  # clean slate, whatever was planted there before

    home = tempfile.mkdtemp(prefix="forja-trabajo-", dir="/tmp")
    entrada_lectura, entrada_escritura = os.pipe()
    lectura, escritura = os.pipe()
    acciones = [
        (os.POSIX_SPAWN_DUP2, entrada_lectura, 0),
        (os.POSIX_SPAWN_OPEN, 1, "/dev/null", os.O_WRONLY, 0),
        (os.POSIX_SPAWN_DUP2, escritura, 2),
    ]
    argv = [sys.executable, "-P", bootstrap.__file__]
    inicio = time.monotonic()
    with _activos_lock:
        try:
            pid = os.posix_spawn(
                sys.executable,
                argv,
                bootstrap.entorno_minimo(home),
                file_actions=acciones,
                setsid=True,
                setsigmask=(),  # our SIGTERM/SIGINT block must not be inherited
            )
        except OSError as exc:
            for fd in (entrada_lectura, entrada_escritura, lectura, escritura):
                os.close(fd)
            borrar_arbol(home)
            return {"codigo_salida": 1, "tiempo_agotado": False, "stderr": f"no se pudo lanzar el script: {exc}"}
        _activos.add(pid)
    os.close(entrada_lectura)
    os.close(escritura)
    try:
        os.write(entrada_escritura, str(trabajo).encode() + b"\n")
    except OSError:
        pass
    os.close(entrada_escritura)
    cola: list[bytes] = []
    lector = threading.Thread(target=_leer_cola, args=(lectura, cola), daemon=True)
    lector.start()
    fin = threading.Event()
    motivo: list[str] = []
    vigia = threading.Thread(target=vigilar_escritura, args=(salida, pid, fin, motivo), daemon=True)
    vigia.start()

    estado = _esperar_estado(pid, limite)
    agotado = estado is None
    fin.set()
    _matar_grupo(pid)
    if estado is None:
        estado = _esperar_estado(pid, time.monotonic() + 10)
    with _activos_lock:
        _activos.discard(pid)
    for _ in range(5):
        if barrer() == 0:
            break
        time.sleep(0.05)
    lector.join(timeout=2)
    vigia.join(timeout=2)
    _limpiar_salida(salida)
    borrar_arbol(home)
    _limpiar_tmp()
    _limpiar_dir_ejecutor()

    codigo = os.waitstatus_to_exitcode(estado) if estado is not None else -signal.SIGKILL
    stderr = (cola[0] if cola else b"").decode("utf-8", "replace")
    if motivo:
        agotado = False
        stderr = f"{stderr.rstrip()}\n{motivo[0]}".lstrip()
        codigo = codigo if codigo != 0 else 1
    _log(f"{id_trabajo[:8]} codigo={codigo} agotado={agotado} {time.monotonic() - inicio:.2f}s")
    return {"codigo_salida": codigo, "tiempo_agotado": agotado, "stderr": stderr}


def _leer_linea(conn: socket.socket) -> bytes:
    datos = b""
    while b"\n" not in datos:
        bloque = conn.recv(65536)
        if not bloque:
            break
        datos += bloque
        if len(datos) > protocolo.MAX_MENSAJE_BYTES:
            raise ValueError("mensaje demasiado grande")
    return datos.split(b"\n", 1)[0]


def _uid_par(conn: socket.socket) -> int:
    _pid, uid, _gid = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
    return uid


def atender(conn: socket.socket) -> None:
    with conn:
        try:
            conn.settimeout(10)
            pedido = json.loads(_leer_linea(conn))
            conn.settimeout(None)
            if pedido.get("op") == "ping":
                respuesta = {"ok": True}
            elif pedido.get("op") == "ejecutar":
                if _uid_par(conn) == os.getuid():
                    raise ValueError("pedido no autorizado")
                id_trabajo = str(pedido.get("id", ""))
                timeout = float(pedido.get("timeout", 30))
                if not _RE_ID.match(id_trabajo) or not 0 < timeout <= MAX_TIMEOUT_S:
                    raise ValueError("pedido invalido")
                respuesta = ejecutar_trabajo(id_trabajo, timeout)
            else:
                raise ValueError("operacion desconocida")
        except (ValueError, TypeError, OSError) as exc:
            respuesta = {"codigo_salida": 1, "tiempo_agotado": False, "stderr": f"pedido invalido: {exc}"}
        try:
            conn.sendall(json.dumps(respuesta).encode() + b"\n")
        except OSError:
            pass


def _barrido_periodico() -> None:
    while not _parar.wait(BARRIDO_PERIODO_S):
        barrer()


# --- the socket -------------------------------------------------------------------


def _enlazar(ruta: Path) -> tuple[socket.socket, tuple[int, int]]:
    """(Re)create the listening socket at ``ruta``: whatever is there goes."""
    try:
        os.chmod(ruta.parent, 0o755)
    except OSError:
        pass
    borrar_arbol(ruta)
    servidor = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        servidor.bind(str(ruta))
        os.chmod(ruta, 0o666)
        st = os.lstat(ruta)
        servidor.listen(16)
        servidor.settimeout(1.0)
    except OSError:
        servidor.close()
        raise
    return servidor, (st.st_dev, st.st_ino)


def socket_intacto(ruta: Path, identidad: tuple[int, int]) -> bool:
    """True if ``ruta`` is still our socket; restores its and its
    directory's mode if a script changed them."""
    try:
        st_dir = os.lstat(ruta.parent)
        if stat.S_ISDIR(st_dir.st_mode) and stat.S_IMODE(st_dir.st_mode) != 0o755:
            os.chmod(ruta.parent, 0o755)
        st = os.lstat(ruta)
    except OSError:
        return False
    if not stat.S_ISSOCK(st.st_mode) or (st.st_dev, st.st_ino) != identidad:
        return False
    if stat.S_IMODE(st.st_mode) != 0o666:
        try:
            os.chmod(ruta, 0o666)
        except OSError:
            return False
    return True


def servir() -> None:
    ruta = protocolo.SOCKET_PATH
    while not ruta.parent.is_dir():
        _log(f"esperando {ruta.parent}")
        time.sleep(1)
    _limpiar_dir_ejecutor()
    servidor: socket.socket | None = None
    identidad = (0, 0)
    revisado = 0.0
    while not _parar.is_set():
        ahora = time.monotonic()
        if servidor is None or ahora - revisado >= VIGILANCIA_SOCKET_S:
            revisado = ahora
            if servidor is None or not socket_intacto(ruta, identidad):
                if servidor is not None:
                    _log("socket ausente o sustituido; re-enlazando")
                    servidor.close()
                    servidor = None
                try:
                    servidor, identidad = _enlazar(ruta)
                    _log(f"escuchando en {ruta} (uid {os.getuid()}, pid {os.getpid()})")
                except OSError as exc:
                    _log(f"no se pudo enlazar {ruta}: {exc}")
                    time.sleep(0.5)
                    continue
        try:
            conn, _ = servidor.accept()
        except TimeoutError:
            continue
        except OSError:
            if _parar.is_set():
                break
            time.sleep(0.1)
            continue
        try:
            threading.Thread(target=atender, args=(conn,), daemon=True).start()
        except RuntimeError:  # pids exhausted (fork bomb in a job): drop this one
            conn.close()
    if servidor is not None:
        servidor.close()


def main() -> None:
    os.umask(0o022)
    _no_volcable()
    # Block before any thread exists: every thread inherits the mask and
    # only _esperar_senales collects them (children get an empty mask).
    signal.pthread_sigmask(signal.SIG_BLOCK, SENALES_PARADA)
    threading.Thread(target=_esperar_senales, daemon=True).start()
    threading.Thread(target=_segador, daemon=True).start()
    threading.Thread(target=_barrido_periodico, daemon=True).start()
    servir()
    _log("parado")


if __name__ == "__main__":
    main()
