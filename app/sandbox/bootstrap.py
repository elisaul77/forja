"""First code of every sandboxed child (F0.2, ADR-0013).

Started by `sandbox.ejecutor` as ``python -P /app/sandbox/bootstrap.py``
inside `forja-sandbox` (``-P``: neither the cwd nor the script dir is
prepended to ``sys.path``, so nothing planted in the shared ``/tmp`` by an
earlier job can shadow ``kernel``/``solids``). The job directory arrives as
one line on stdin (a pipe from the executor), never in argv: argv is
readable by any process through ``/proc/<pid>/cmdline``. Before the user's
script sees a single byte it:

1. marks itself non-dumpable (``PR_SET_DUMPABLE 0``): its ``/proc`` entries
   (cwd, fds, environ, mem) become root-owned, so another process of uid
   65534 cannot inspect it;
2. sets ``umask 077`` (the executor re-opens the two expected outputs
   afterwards and makes only those readable for the server);
3. lowers its own limits: ``RLIMIT_DATA`` 1.5 GB, ``RLIMIT_CPU`` timeout +
   10 s, ``RLIMIT_FSIZE`` 512 MB, no core files — never ``RLIMIT_NPROC``
   (hangs numpy's thread pool; the container's ``pids_limit`` covers it);
4. writes ``oom_score_adj = 1000`` so the kernel kills this child, never the
   server, under memory pressure;
5. reduces the environment to a fixed allow-list (a per-job ``HOME`` under
   ``/tmp``, also ``TMPDIR``, BLAS/OMP at 1 thread, ``PYTHONPATH=/app``) —
   the executor already spawns it that way;
6. confines itself with Landlock (unprivileged; the container already has
   ``no-new-privileges``): writes, removals, renames and node creation are
   allowed only beneath its own ``salida/`` and its ``HOME`` (+ writing to
   ``/dev/null``), and — kernel ABI >= 6 — no signals or abstract sockets
   outside its own domain (so it cannot unlink/replace the executor's
   socket, touch another job's ``salida/`` or signal PID 1). Reads are not
   restricted (the root is read-only and holds no secret). Best effort: on
   a kernel without Landlock the executor's own checks still apply;
7. reads ``peticion.json`` and runs the script, then exports ``resultado``
   to ``salida/resultado.step`` (+ ``resultado.nombres.json`` for named
   solids), exactly as the pre-F0.2 in-process wrapper did.

Everything this process writes is untrusted data for the server
(`scripts_runner` re-validates it). The ``__FORJA_ERROR__`` stderr marker
is a diagnostic aid, not a security boundary (see `_reportar_error_script`).
"""
from __future__ import annotations

import ctypes
import json
import math
import os
import resource
import sys
import traceback
from pathlib import Path

from sandbox import protocolo

NOMBRE_SCRIPT = "<forja-script>"

ENTORNO_MINIMO = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "PYTHONPATH": "/app",
    "LANG": "C.UTF-8",
    "OPENBLAS_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "PYTHONNOUSERSITE": "1",
}


PR_SET_DUMPABLE = 4
PR_SET_NO_NEW_PRIVS = 38

# Landlock (uapi linux/landlock.h); the syscall numbers are the same on
# every architecture.
_SYS_LANDLOCK_CREATE_RULESET = 444
_SYS_LANDLOCK_ADD_RULE = 445
_SYS_LANDLOCK_RESTRICT_SELF = 446
_LANDLOCK_CREATE_RULESET_VERSION = 1
_LANDLOCK_RULE_PATH_BENEATH = 1
_LL_WRITE_FILE = 1 << 1
_LL_ESCRITURA_DIR = (
    (1 << 4)  # REMOVE_DIR
    | (1 << 5)  # REMOVE_FILE
    | (1 << 6)  # MAKE_CHAR
    | (1 << 7)  # MAKE_DIR
    | (1 << 8)  # MAKE_REG
    | (1 << 9)  # MAKE_SOCK
    | (1 << 10)  # MAKE_FIFO
    | (1 << 11)  # MAKE_BLOCK
    | (1 << 12)  # MAKE_SYM
)
_LL_REFER = 1 << 13  # ABI 2
_LL_TRUNCATE = 1 << 14  # ABI 3
_LL_SCOPE_ABSTRACT_UNIX = 1 << 0  # ABI 6
_LL_SCOPE_SIGNAL = 1 << 1  # ABI 6


class _AtributosRuleset(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64), ("handled_access_net", ctypes.c_uint64), ("scoped", ctypes.c_uint64)]


class _AtributosRuta(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


def entorno_minimo(home: str) -> dict[str, str]:
    """The child's whole environment: the allow-list plus its private HOME
    (also matplotlib's config dir and the temp dir), never anything inherited."""
    return {**ENTORNO_MINIMO, "HOME": home, "MPLCONFIGDIR": home, "TMPDIR": home}


def _libc() -> ctypes.CDLL:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    return libc


def no_volcable() -> None:
    """``PR_SET_DUMPABLE 0``: /proc/<pid>/{cwd,fd,environ,mem} of this
    process stop being readable by other processes of the same uid."""
    try:
        _libc().prctl(PR_SET_DUMPABLE, 0, 0, 0, 0)
    except (OSError, AttributeError):
        pass


def confinar(escribibles: list[str], archivos_escribibles: list[str] = ()) -> int:
    """Landlock: from now on this process (and its descendants) may only
    write/remove/create beneath ``escribibles`` (directories) and write to
    ``archivos_escribibles`` (files); ABI >= 6 also scopes signals and
    abstract Unix sockets to its own domain. Returns the ABI applied, or 0
    if Landlock is unavailable (nothing changed)."""
    libc = _libc()
    abi = libc.syscall(_SYS_LANDLOCK_CREATE_RULESET, None, ctypes.c_size_t(0), ctypes.c_uint32(_LANDLOCK_CREATE_RULESET_VERSION))
    if abi < 1:
        return 0
    acceso_dir = _LL_WRITE_FILE | _LL_ESCRITURA_DIR
    acceso_archivo = _LL_WRITE_FILE
    if abi >= 2:
        acceso_dir |= _LL_REFER
    if abi >= 3:
        acceso_dir |= _LL_TRUNCATE
        acceso_archivo |= _LL_TRUNCATE
    atributos = _AtributosRuleset(handled_access_fs=acceso_dir)
    tamano = 8
    if abi >= 6:
        atributos.scoped = _LL_SCOPE_ABSTRACT_UNIX | _LL_SCOPE_SIGNAL
        tamano = ctypes.sizeof(_AtributosRuleset)
    ruleset = libc.syscall(_SYS_LANDLOCK_CREATE_RULESET, ctypes.byref(atributos), ctypes.c_size_t(tamano), ctypes.c_uint32(0))
    if ruleset < 0:
        return 0
    try:
        for rutas, acceso in ((escribibles, acceso_dir), (archivos_escribibles, acceso_archivo)):
            for ruta in rutas:
                fd = os.open(ruta, os.O_PATH | os.O_CLOEXEC)
                try:
                    regla = _AtributosRuta(allowed_access=acceso, parent_fd=fd)
                    if libc.syscall(_SYS_LANDLOCK_ADD_RULE, ctypes.c_int(ruleset), ctypes.c_int(_LANDLOCK_RULE_PATH_BENEATH), ctypes.byref(regla), ctypes.c_uint32(0)) != 0:
                        raise OSError(ctypes.get_errno(), f"landlock_add_rule {ruta}")
                finally:
                    os.close(fd)
        libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)
        if libc.syscall(_SYS_LANDLOCK_RESTRICT_SELF, ctypes.c_int(ruleset), ctypes.c_uint32(0)) != 0:
            raise OSError(ctypes.get_errno(), "landlock_restrict_self")
    finally:
        os.close(ruleset)
    return abi


def aplicar_limites(timeout: float) -> None:
    os.umask(0o077)
    cpu = int(math.ceil(timeout)) + protocolo.RLIMIT_CPU_MARGEN_S
    for limite, valor in (
        (resource.RLIMIT_DATA, protocolo.RLIMIT_DATA_BYTES),
        (resource.RLIMIT_FSIZE, protocolo.RLIMIT_FSIZE_BYTES),
        (resource.RLIMIT_CORE, 0),
    ):
        resource.setrlimit(limite, (valor, valor))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 5))
    try:
        with open("/proc/self/oom_score_adj", "w") as f:
            f.write("1000")
    except OSError:
        pass  # best effort: raising it never needs privileges, but /proc may be odd
    home = os.environ.get("HOME", "")
    if not home.startswith("/tmp/"):
        home = "/tmp"
    os.environ.clear()
    os.environ.update(entorno_minimo(home))
    os.chdir(home)  # posix_spawn has no cwd: leave the executor's


def _reportar_error_script(exc: BaseException, codigo: str) -> None:
    """Print the usual full traceback (stderr, never parsed by the server
    beyond its last line) PLUS one machine-readable marker line carrying the
    failing line number/source line *within the user's own script* (frame
    filtered by NOMBRE_SCRIPT). Best effort, not a security boundary: a
    script that prints its own fake marker only changes the SHORT error
    shown back to the same caller who ran it."""
    lineas = codigo.splitlines()
    marco = None
    for candidato in traceback.extract_tb(exc.__traceback__):
        if candidato.filename == NOMBRE_SCRIPT:
            marco = candidato
    detalle = {"mensaje": f"{type(exc).__name__}: {exc}"}
    if marco is not None:
        detalle["linea"] = marco.lineno
        if 0 < marco.lineno <= len(lineas):
            detalle["codigo_linea"] = lineas[marco.lineno - 1].strip()
    traceback.print_exc()
    print("__FORJA_ERROR__" + json.dumps(detalle), file=sys.stderr)


def _inyectar_perfil(namespace: dict) -> None:
    """fdm-A: bind ``agujero(d)``, ``eje(d)``, ``ranura(w)`` and
    ``ajuste(nombre, d=None)`` to the injected ``PERFIL`` (seed if absent).
    Names the caller already passed as variables are left alone."""
    import functools

    import perfil_fdm

    perfil = namespace.get("PERFIL")
    if not isinstance(perfil, dict):
        perfil = dict(perfil_fdm.SEMILLA)
        namespace["PERFIL"] = perfil

    def agujero(d, horizontal=False):
        return perfil_fdm.agujero(d, perfil, horizontal=horizontal)

    funciones = {
        "agujero": agujero,
        "eje": functools.partial(perfil_fdm.eje, perfil=perfil),
        "ranura": functools.partial(perfil_fdm.ranura, perfil=perfil),
        "ajuste": lambda nombre, d=None: perfil_fdm.ajuste(nombre, d, perfil),
    }
    for nombre, funcion in funciones.items():
        namespace.setdefault(nombre, funcion)
    _inyectar_fdm_ops(namespace, perfil)


def _inyectar_fdm_ops(namespace: dict, perfil: dict) -> None:
    """fdm-C: bind the design fixes of ``fdm_ops`` (``agujero_gota``,
    ``chaflan_base``, ``puente_sacrificio``, ``partir_para_cama``) to the
    same ``PERFIL``, plus ``AVISOS_FDM`` (their warnings). Never overrides
    names the caller passed. ``fdm_ops`` (build123d) is imported on first
    use only: importing it here would alter the child's environment and
    stderr before the user's script runs."""
    avisos: list = []

    def _ops():
        import fdm_ops

        return fdm_ops

    def agujero_gota(d, largo, eje="X", centro=(0.0, 0.0, 0.0), compensar=True):
        return _ops().agujero_gota(d, largo, eje=eje, centro=centro, perfil=perfil, compensar=compensar)

    def chaflan_base(pieza, alto=0.5):
        return _ops().chaflan_base(pieza, alto, avisos=avisos)

    def puente_sacrificio(d, z, centro=(0.0, 0.0), capas=1, solape=0.4):
        return _ops().puente_sacrificio(d, z, centro=centro, perfil=perfil, capas=capas, solape=solape)

    def partir_para_cama(pieza, cama=(220.0, 220.0, 250.0), union="pasadores", nombre="pieza",
                         d_pasador=None, prof=None, holgura="eje_presion"):
        return _ops().partir_para_cama(pieza, cama=cama, union=union, nombre=nombre, perfil=perfil,
                                       d_pasador=d_pasador, prof=prof, holgura=holgura, avisos=avisos)

    funciones = {
        "agujero_gota": agujero_gota,
        "chaflan_base": chaflan_base,
        "puente_sacrificio": puente_sacrificio,
        "partir_para_cama": partir_para_cama,
        "AVISOS_FDM": avisos,
    }
    for nombre, funcion in funciones.items():
        namespace.setdefault(nombre, funcion)


def ejecutar(trabajo: Path) -> int:
    peticion = json.loads((trabajo / protocolo.PETICION).read_text())
    codigo = peticion["codigo"]
    variables = peticion.get("variables") or {}
    salida = trabajo / protocolo.SALIDA_DIR / protocolo.RESULTADO
    # Same argv layout as the pre-F0.2 wrapper ([wrapper, script, salida]):
    # scripts (and tests of the nombres.json attack) read sys.argv[2].
    sys.argv = [NOMBRE_SCRIPT, str(trabajo / protocolo.PETICION), str(salida)]

    namespace = dict(variables)
    _inyectar_perfil(namespace)
    try:
        exec(compile(codigo, NOMBRE_SCRIPT, "exec"), namespace)
    except Exception as exc:  # noqa: BLE001 - reported compactly to the server
        _reportar_error_script(exc, codigo)
        return 1

    resultado = namespace.get("resultado")
    if resultado is None and callable(namespace.get("construir")):
        # Phase 5C parametric scripts: `construir(params)` receives the
        # current values -- the `PARAMS` global the server injected, or
        # (script run outside the params flow) the defaults of its own
        # PARAMETROS literal.
        params = namespace.get("PARAMS")
        if not isinstance(params, dict):
            declarados = namespace.get("PARAMETROS")
            params = {}
            if isinstance(declarados, dict):
                for nombre, entrada in declarados.items():
                    params[nombre] = entrada.get("valor") if isinstance(entrada, dict) else entrada
        try:
            resultado = namespace["construir"](dict(params))
        except Exception as exc:  # noqa: BLE001
            _reportar_error_script(exc, codigo)
            return 1
    if resultado is None:
        print(
            "el script debe asignar la figura resultante a una variable llamada 'resultado' "
            "(o definir construir(params) que la devuelva)",
            file=sys.stderr,
        )
        return 1

    from kernel import b123d_kernel
    import solids

    try:
        if isinstance(resultado, dict):
            solids.validar_nombres(resultado.keys())
            nombres_ordenados = list(resultado.keys())
            figura = b123d_kernel.combinar_nombrados(resultado)
            # Explicit side channel (never re-derive naming from re-imported
            # STEP labels, see app/solids.py). Not a security boundary: the
            # user's code runs in this very process and could write this file
            # itself; `scripts_runner._leer_nombres_confiables` re-validates it.
            (salida.parent / protocolo.NOMBRES).write_text(json.dumps(nombres_ordenados))
        else:
            figura = resultado
        b123d_kernel.export_to_step(figura, salida)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return 1
    return 0


def _leer_trabajo() -> Path:
    """The job directory, from the executor's pipe on stdin (one line).
    Then fd 0 becomes /dev/null, as before."""
    with os.fdopen(os.dup(0), "rb") as tubo:
        linea = tubo.read(4096).decode("ascii", "strict").strip()
    fd = os.open(os.devnull, os.O_RDONLY)
    os.dup2(fd, 0)
    os.close(fd)
    trabajo = Path(linea)
    if trabajo.parent != protocolo.TRABAJOS_DIR or not trabajo.name.isalnum():
        raise ValueError("ruta de trabajo invalida")
    return trabajo


def main(argv: list[str]) -> int:
    no_volcable()
    try:
        trabajo = _leer_trabajo()
        timeout = float(json.loads((trabajo / protocolo.PETICION).read_text()).get("timeout") or 30)
    except (OSError, ValueError):
        print("peticion ilegible", file=sys.stderr)
        return 1
    aplicar_limites(timeout)
    try:
        confinar([os.environ["HOME"], str(trabajo / protocolo.SALIDA_DIR)], [os.devnull])
    except OSError as exc:
        print(f"no se pudo confinar el script: {exc}", file=sys.stderr)
        return 1
    return ejecutar(trabajo)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
