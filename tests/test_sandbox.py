"""F0.2 (ADR-0013): user scripts run in `forja-sandbox`, not in `forja`.

Against the real containers: these tests run inside `forja` (as the suite
always does, `docker exec forja python -m pytest /tests`) and talk to the
live `forja-sandbox` executor through the shared `staging` volume. Skipped
when the executor socket is absent (outside the compose stack).

The 11 penetration cases of the F0a card must all FAIL inside the sandbox
and come back as a compact error (one short line, no traceback). Then the
happy paths: Box - Cylinder + export, named solids, `import build123d`
under the production limits in < 5 s. Unit tests cover the server-side
output reader (symlink / FIFO / hard link / oversize rejected) without the
sandbox.
"""
from __future__ import annotations

import os
import re
import shutil
import socket
import statistics
import subprocess
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import scripts_runner
from conftest import TOKEN_PRUEBA
from sandbox import protocolo

hay_sandbox = pytest.mark.skipif(
    not protocolo.SOCKET_PATH.exists(), reason="sin forja-sandbox (socket del ejecutor ausente)"
)

MARCA_EXITO = "PENETRACION_OK"


def _fallo_compacto(codigo: str, timeout: float = 20) -> str:
    """Run an attack; return the compact error message it produced."""
    with pytest.raises((scripts_runner.ScriptError, scripts_runner.ScriptTimeoutError)) as info:
        salida, _ = scripts_runner.ejecutar_script(codigo + f"\nraise RuntimeError({MARCA_EXITO!r})\n", timeout=timeout)
        shutil.rmtree(salida.parent, ignore_errors=True)
    mensaje = getattr(info.value, "mensaje", None) or str(info.value)
    assert MARCA_EXITO not in mensaje, f"el ataque tuvo exito: {mensaje}"
    assert "Traceback" not in mensaje and "\n" not in mensaje and len(mensaje) < 300, mensaje
    return mensaje


PENETRACION = {
    "secreto_run_secrets": ("open('/run/secrets/forja_token').read()", r"FileNotFoundError|PermissionError"),
    "token_legado_documentos": ("open('/data/documentos/.forja_token').read()", r"FileNotFoundError|PermissionError"),
    "listar_fuentes": ("import os\nos.listdir('/data/fuentes')", r"FileNotFoundError|PermissionError"),
    "entorno_forja_token": ("import os\nos.environ['FORJA_TOKEN']", r"KeyError"),
    "escribir_app": ("open('/app/x', 'w').write('x')", r"Read-only file system|PermissionError"),
    "proc_1_environ": ("open('/proc/1/environ', 'rb').read()", r"PermissionError"),
    "api_local_sin_token": (
        "import urllib.request\nurllib.request.urlopen('http://127.0.0.1:8000/documentos', data=b'{}', timeout=3)",
        r"URLError|ConnectionRefused|OSError",
    ),
    "blender_9877": (
        "import socket\nsocket.create_connection(('host.docker.internal', 9877), timeout=3)",
        r"gaierror|OSError",
    ),
    "moonraker_7125": (
        "import socket\nsocket.create_connection(('host.docker.internal', 7125), timeout=3)",
        r"gaierror|OSError",
    ),
}


@hay_sandbox
@pytest.mark.parametrize("caso", sorted(PENETRACION))
def test_penetracion_falla_con_error_compacto(caso):
    codigo, esperado = PENETRACION[caso]
    mensaje = _fallo_compacto(codigo)
    assert re.search(esperado, mensaje), f"{caso}: {mensaje}"


@hay_sandbox
def test_penetracion_symlink_resultado_al_secreto():
    """The child plants `resultado.step -> /run/secrets/forja_token` and exits
    0: the token must never come back as the STEP (the executor drops the
    link; the server's reader would reject it anyway, see the unit tests)."""
    codigo = (
        "import os, sys\n"
        "from pathlib import Path\n"
        "salida = Path(sys.argv[2]).parent\n"
        "os.symlink('/run/secrets/forja_token', salida / 'resultado.step')\n"
        "os.symlink('/run/secrets/forja_token', salida / 'resultado.nombres.json')\n"
        "os._exit(0)\n"
    )
    mensaje = _fallo_compacto(codigo)
    assert "resultado.step" in mensaje, mensaje


@hay_sandbox
def test_penetracion_fork_bomb_muere_y_el_sandbox_sigue_vivo():
    codigo = "import os\nwhile True:\n    try:\n        os.fork()\n    except OSError:\n        pass\n"
    mensaje = _fallo_compacto(codigo, timeout=4)
    assert "tiempo agotado" in mensaje or "terminado" in mensaje, mensaje
    assert scripts_runner.pedir_al_ejecutor({"op": "ping"}, 10) == {"ok": True}
    salida, _ = scripts_runner.ejecutar_script("from build123d import *\nresultado = Box(1, 1, 1)\n", timeout=60)
    shutil.rmtree(salida.parent, ignore_errors=True)


@hay_sandbox
def test_penetracion_red_sin_interfaces_hacia_el_gateway():
    """Beyond the name: the docker bridge gateway IP is unreachable too."""
    mensaje = _fallo_compacto("import socket\nsocket.create_connection(('172.17.0.1', 9877), timeout=3)")
    assert re.search(r"unreachable|OSError|timed out", mensaje, re.I), mensaje


@hay_sandbox
def test_hijo_uid_65534_y_entorno_minimo():
    codigo = (
        "import os\n"
        "raise RuntimeError('INFO uid=%d env=%s' % (os.getuid(), ','.join(sorted(os.environ))))\n"
    )
    with pytest.raises(scripts_runner.ScriptError) as info:
        scripts_runner.ejecutar_script(codigo, timeout=20)
    texto = info.value.mensaje
    assert "uid=65534" in texto, texto
    claves = set(texto.split("env=", 1)[1].split(","))
    permitidas = {
        "HOME", "PATH", "PYTHONPATH", "LANG", "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS",
        "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS", "PYTHONNOUSERSITE", "MPLCONFIGDIR", "TMPDIR",
    }
    assert claves <= permitidas, claves - permitidas
    assert info.value.linea == 2


@hay_sandbox
def test_reserva_de_3_gb_falla_por_rlimit_data():
    """RLIMIT_DATA 1.5 GB makes a big allocation fail at once inside the
    child (MemoryError, compact) instead of growing toward a host OOM."""
    mensaje = _fallo_compacto("x = bytearray(3 * 1024 ** 3)")
    assert "MemoryError" in mensaje, mensaje


@hay_sandbox
def test_ejecutor_rechaza_pedidos_invalidos():
    for pedido in ({"op": "ejecutar", "id": "../../etc", "timeout": 5}, {"op": "otra"}):
        respuesta = scripts_runner.pedir_al_ejecutor(pedido, 10)
        assert respuesta["codigo_salida"] == 1 and "invalido" in respuesta["stderr"]


CAJA_MENOS_CILINDRO = "from build123d import *\nresultado = Box(20, 20, 10) - Cylinder(4, 10)\n"


@hay_sandbox
def test_box_menos_cylinder_y_export():
    from kernel import b123d_kernel

    salida, nombres = scripts_runner.ejecutar_script(CAJA_MENOS_CILINDRO, timeout=60)
    try:
        assert nombres is None
        analisis = b123d_kernel.analyze(b123d_kernel.import_from_step(salida))
        assert analisis.solidos == 1
        assert analisis.volumen == pytest.approx(20 * 20 * 10 - 3.141592653589793 * 16 * 10, rel=1e-3)
    finally:
        shutil.rmtree(salida.parent, ignore_errors=True)

    from main import app

    cliente = TestClient(app)
    cabeceras = {"X-Forja-Token": TOKEN_PRUEBA}
    resp = cliente.post("/documentos/script", json={"codigo": CAJA_MENOS_CILINDRO, "nombre": "caja"}, headers=cabeceras)
    assert resp.status_code == 200, resp.text
    doc_id = resp.json()["id"]
    try:
        exportado = cliente.get(f"/documentos/{doc_id}/exportar", params={"formato": "stl"})
        assert exportado.status_code == 200, exportado.text
        descarga = cliente.get(f"/documentos/{doc_id}/descarga/caja.stl")
        assert descarga.status_code == 200 and len(descarga.content) > 1000
    finally:
        cliente.delete(f"/documentos/{doc_id}", headers=cabeceras)


@hay_sandbox
def test_solidos_con_nombre_por_el_sandbox():
    codigo = "from build123d import *\nresultado = {'base': Box(10, 10, 2), 'poste': Pos(0, 0, 6) * Cylinder(1, 8)}\n"
    salida, nombres = scripts_runner.ejecutar_script(codigo, timeout=60)
    shutil.rmtree(salida.parent, ignore_errors=True)
    assert nombres == ["base", "poste"]


@hay_sandbox
def test_import_build123d_bajo_limites_menos_de_5s():
    codigo = (
        "import time\n"
        "t = time.perf_counter()\n"
        "import build123d\n"
        "raise RuntimeError('IMPORT_S=%.3f' % (time.perf_counter() - t))\n"
    )
    with pytest.raises(scripts_runner.ScriptError) as info:
        scripts_runner.ejecutar_script(codigo, timeout=30)
    segundos = float(re.search(r"IMPORT_S=([0-9.]+)", info.value.mensaje).group(1))
    assert segundos < 5, segundos


@hay_sandbox
def test_latencia_del_salto_se_mide(capsys):
    """Median wall time of 3 Box-Cylinder runs through the sandbox; printed
    for the phase report (pre-F0.2 baseline in-process: median 2.80 s)."""
    tiempos = []
    for _ in range(3):
        inicio = time.perf_counter()
        salida, _ = scripts_runner.ejecutar_script(CAJA_MENOS_CILINDRO, timeout=60)
        tiempos.append(time.perf_counter() - inicio)
        shutil.rmtree(salida.parent, ignore_errors=True)
    with capsys.disabled():
        print(f"\n[latencia sandbox] {[round(t, 3) for t in tiempos]} mediana {statistics.median(tiempos):.3f}s")
    assert statistics.median(tiempos) < 15


# --- F0.2 security review fixes: socket, isolation, signals, write cap -------

como_root = pytest.mark.skipif(os.geteuid() != 0, reason="necesita root en forja (docker exec por defecto)")


def _esperar_ping(segundos: float = 6) -> bool:
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        try:
            if scripts_runner.pedir_al_ejecutor({"op": "ping"}, 2) == {"ok": True}:
                return True
        except (scripts_runner.ScriptError, OSError):
            pass
        time.sleep(0.2)
    return False


def _trabajo_crudo(codigo: str, timeout: float = 30) -> tuple[str, Path]:
    return scripts_runner._preparar_trabajo(codigo, timeout, None)


def _pedir(id_trabajo: str, timeout: float = 30) -> dict:
    return scripts_runner.pedir_al_ejecutor({"op": "ejecutar", "id": id_trabajo, "timeout": timeout}, timeout + 20)


def _caja_funciona() -> None:
    salida, _ = scripts_runner.ejecutar_script("from build123d import *\nresultado = Box(1, 1, 1)\n", timeout=60)
    shutil.rmtree(salida.parent, ignore_errors=True)


@hay_sandbox
def test_script_no_puede_borrar_ni_ensuciar_el_socket():
    """Landlock: the child cannot unlink the executor's socket nor create
    files next to it; the next job works either way."""
    codigo = (
        "import os\n"
        "r = []\n"
        f"for f in (lambda: os.unlink({str(protocolo.SOCKET_PATH)!r}),\n"
        f"          lambda: open({str(protocolo.SOCKET_PATH.parent / 'basura')!r}, 'w')):\n"
        "    try:\n"
        "        f()\n"
        "        r.append('hecho')\n"
        "    except OSError as e:\n"
        "        r.append(type(e).__name__)\n"
        "raise RuntimeError('INTENTOS=' + ','.join(r))\n"
    )
    with pytest.raises(scripts_runner.ScriptError) as info:
        scripts_runner.ejecutar_script(codigo, timeout=20)
    assert _esperar_ping(), "el ejecutor no volvio a escuchar"
    assert "INTENTOS=PermissionError,PermissionError" in info.value.mensaje, info.value.mensaje
    assert os.listdir(protocolo.SOCKET_PATH.parent) == [protocolo.SOCKET_PATH.name]
    _caja_funciona()


@hay_sandbox
def test_chmod_del_socket_se_restaura_antes_de_responder():
    """Landlock does not cover chmod: a script may set 000 on the socket and
    its directory (same uid); the executor restores both before answering,
    so the very next job connects."""
    codigo = (
        "import os\n"
        f"os.chmod({str(protocolo.SOCKET_PATH)!r}, 0)\n"
        f"os.chmod({str(protocolo.SOCKET_PATH.parent)!r}, 0)\n"
        "raise RuntimeError('CAMBIADO')\n"
    )
    with pytest.raises(scripts_runner.ScriptError, match="CAMBIADO"):
        scripts_runner.ejecutar_script(codigo, timeout=20)
    _caja_funciona()


@hay_sandbox
@como_root
def test_socket_borrado_o_sustituido_se_reenlaza():
    """Second line (no Landlock): the accept loop notices a missing or
    foreign socket within ~1 s and binds its own again; junk next to the
    socket is removed after the next job."""
    ruta = protocolo.SOCKET_PATH
    os.unlink(ruta)
    assert _esperar_ping(), "no re-enlazo tras borrar el socket"

    os.unlink(ruta)
    impostor = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        impostor.bind(str(ruta))
        impostor.listen(1)
        ino_impostor = os.lstat(ruta).st_ino
        limite = time.monotonic() + 6
        while time.monotonic() < limite and os.lstat(ruta).st_ino == ino_impostor:
            time.sleep(0.2)
        assert os.lstat(ruta).st_ino != ino_impostor, "el socket impostor sigue en su lugar"
    finally:
        impostor.close()
    assert _esperar_ping()

    (ruta.parent / "basura").write_text("x")
    _caja_funciona()
    assert os.listdir(ruta.parent) == [ruta.name]


@hay_sandbox
@como_root
def test_trabajos_concurrentes_no_se_ven_ni_se_tocan():
    """Two jobs sent at once: B builds a box after 2 s; A, queued behind it,
    looks for B's id in every /proc/*/cmdline|environ, counts other live
    bootstrap processes and tries to overwrite B's output. Jobs run one at a
    time, the id is not in argv and A may only write its own salida/."""
    id_b, trabajo_b = _trabajo_crudo("import time\ntime.sleep(2)\nfrom build123d import *\nresultado = Box(1, 1, 1)\n", 60)
    codigo_a = (
        "import os\n"
        f"ID_B = {id_b!r}\n"
        "vistos = hermanos = 0\n"
        "for pid in os.listdir('/proc'):\n"
        "    if not pid.isdigit() or int(pid) == os.getpid():\n"
        "        continue\n"
        "    for nombre in ('cmdline', 'environ'):\n"
        "        try:\n"
        "            datos = open(f'/proc/{pid}/{nombre}', 'rb').read()\n"
        "        except OSError:\n"
        "            continue\n"
        "        vistos += ID_B.encode() in datos\n"
        "        hermanos += nombre == 'cmdline' and b'bootstrap.py' in datos\n"
        "try:\n"
        "    with open('/staging/trabajos/' + ID_B + '/salida/resultado.step', 'wb') as f:\n"
        "        f.write(b'INTRUSO')\n"
        "    escrito = 'si'\n"
        "except OSError as e:\n"
        "    escrito = type(e).__name__\n"
        "raise RuntimeError(f'VISTOS={vistos} HERMANOS={hermanos} ESCRITO={escrito}')\n"
    )
    id_a, trabajo_a = _trabajo_crudo(codigo_a, 60)
    respuestas: dict[str, dict] = {}
    try:
        hilo_b = threading.Thread(target=lambda: respuestas.__setitem__("b", _pedir(id_b, 60)))
        hilo_a = threading.Thread(target=lambda: respuestas.__setitem__("a", _pedir(id_a, 60)))
        hilo_b.start()
        time.sleep(0.5)
        hilo_a.start()
        hilo_b.join(90)
        hilo_a.join(90)
        assert respuestas["b"]["codigo_salida"] == 0, respuestas["b"]
        assert "VISTOS=0 HERMANOS=0 ESCRITO=PermissionError" in respuestas["a"]["stderr"], respuestas["a"]
        contenido = (trabajo_b / protocolo.SALIDA_DIR / protocolo.RESULTADO).read_bytes()
        assert contenido.startswith(b"ISO-10303-21") and b"INTRUSO" not in contenido
    finally:
        shutil.rmtree(trabajo_a, ignore_errors=True)
        shutil.rmtree(trabajo_b, ignore_errors=True)


@hay_sandbox
def test_script_no_mata_al_ejecutor_con_sigterm():
    codigo = (
        "import os, signal, time\n"
        "r = []\n"
        "for s in (signal.SIGTERM, signal.SIGINT):\n"
        "    try:\n"
        "        os.kill(1, s)\n"
        "        r.append('enviada')\n"
        "    except OSError as e:\n"
        "        r.append(type(e).__name__)\n"
        "time.sleep(1.5)\n"
        "raise RuntimeError('SENALES=' + ','.join(r))\n"
    )
    with pytest.raises(scripts_runner.ScriptError) as info:
        scripts_runner.ejecutar_script(codigo, timeout=20)
    assert "SENALES=" in info.value.mensaje, info.value.mensaje
    assert scripts_runner.pedir_al_ejecutor({"op": "ping"}, 10) == {"ok": True}
    _caja_funciona()


@hay_sandbox
@como_root
def test_directorio_000_en_salida_no_impide_la_limpieza():
    codigo = (
        "import os, sys\n"
        "from pathlib import Path\n"
        "s = Path(sys.argv[2]).parent\n"
        "(s / 'd' / 'e').mkdir(parents=True)\n"
        "(s / 'd' / 'e' / 'f.txt').write_text('x')\n"
        "os.symlink('/etc/passwd', s / 'd' / 'enlace')\n"
        "os.chmod(s / 'd' / 'e', 0)\n"
        "os.chmod(s / 'd', 0)\n"
        "raise RuntimeError('PLANTADO')\n"
    )
    id_trabajo, trabajo = _trabajo_crudo(codigo)
    try:
        respuesta = _pedir(id_trabajo)
        assert "PLANTADO" in respuesta["stderr"], respuesta
        assert os.listdir(trabajo / protocolo.SALIDA_DIR) == []
    finally:
        shutil.rmtree(trabajo, ignore_errors=True)


@hay_sandbox
def test_ejecutor_no_acepta_trabajos_desde_un_script():
    """A child (same uid as the executor) cannot queue jobs (SO_PEERCRED)."""
    codigo = (
        "import json, socket\n"
        "c = socket.socket(socket.AF_UNIX)\n"
        f"c.connect({str(protocolo.SOCKET_PATH)!r})\n"
        "c.sendall(json.dumps({'op': 'ejecutar', 'id': '0' * 32, 'timeout': 5}).encode() + b'\\n')\n"
        "raise RuntimeError('RESPUESTA=' + c.recv(4096).decode().strip())\n"
    )
    with pytest.raises(scripts_runner.ScriptError) as info:
        scripts_runner.ejecutar_script(codigo, timeout=20)
    assert "no autorizado" in info.value.mensaje, info.value.mensaje


def _grupo_dormido() -> subprocess.Popen:
    return subprocess.Popen(["sleep", "30"], start_new_session=True)


@pytest.mark.parametrize(
    "caso, esperado",
    [("bytes", "supera 1 MB"), ("libre", "poco espacio"), ("entradas", "entradas")],
)
def test_vigia_de_escritura_mata_el_grupo(tmp_path, caso, esperado):
    """The executor's per-job watcher, with small limits (no disk filled)."""
    from sandbox import ejecutor

    salida = tmp_path / "salida"
    salida.mkdir()
    if caso == "bytes":
        (salida / "grande").write_bytes(os.urandom(2 * 1024 * 1024))
    if caso == "entradas":
        for i in range(30):
            (salida / f"f{i}").write_text("x")
    proceso = _grupo_dormido()
    fin = threading.Event()
    motivo: list[str] = []
    try:
        ejecutor.vigilar_escritura(
            salida, proceso.pid, fin, motivo,
            max_bytes=1024 * 1024, min_libre=1, max_entradas=20, periodo=0.05,
            libre=(lambda: 0) if caso == "libre" else (lambda: 10**12),
        )
        assert proceso.wait(timeout=5) == -9
        assert motivo and esperado in motivo[0], motivo
    finally:
        proceso.kill()
        proceso.wait()


def test_vigia_no_mata_por_debajo_de_los_limites(tmp_path):
    from sandbox import ejecutor

    (tmp_path / "pequeno").write_bytes(b"x" * 1000)
    proceso = _grupo_dormido()
    fin = threading.Event()
    motivo: list[str] = []
    hilo = threading.Thread(
        target=ejecutor.vigilar_escritura,
        args=(tmp_path, proceso.pid, fin, motivo),
        kwargs={"max_bytes": 1024 * 1024, "min_libre": 1, "periodo": 0.05, "libre": lambda: 10**12},
    )
    hilo.start()
    time.sleep(0.4)
    fin.set()
    hilo.join(2)
    try:
        assert proceso.poll() is None and motivo == []
    finally:
        proceso.kill()
        proceso.wait()


def test_vaciar_dir_profundo_y_conservando(tmp_path):
    from sandbox import ejecutor

    (tmp_path / "ejecutor.sock").write_text("")
    profundo = tmp_path / "a"
    actual = profundo
    for _ in range(50):
        actual = actual / "b"
    actual.mkdir(parents=True)
    (actual / "f").write_text("x")
    (tmp_path / "enlace").symlink_to("/etc")
    ejecutor.vaciar_dir(tmp_path, {"ejecutor.sock"})
    assert os.listdir(tmp_path) == ["ejecutor.sock"]
    assert os.path.isdir("/etc")


# --- server-side output reader (no sandbox needed) ---------------------------


def _dir_fd(ruta: Path) -> int:
    return os.open(ruta, os.O_RDONLY | os.O_DIRECTORY)


def test_lector_acepta_archivo_regular(tmp_path):
    (tmp_path / "resultado.step").write_bytes(b"ISO-10303-21;")
    fd = _dir_fd(tmp_path)
    try:
        destino = tmp_path / "copia.step"
        assert scripts_runner.leer_salida(fd, "resultado.step", 100, destino) is True
        assert destino.read_bytes() == b"ISO-10303-21;"
        assert scripts_runner.leer_salida(fd, "no-existe", 100) is None
    finally:
        os.close(fd)


def test_lector_rechaza_symlink_al_secreto(tmp_path):
    secreto = tmp_path / "secreto"
    secreto.write_text("forja-falso-no-es-el-token")
    (tmp_path / "salida").mkdir()
    (tmp_path / "salida" / "resultado.step").symlink_to(secreto)
    fd = _dir_fd(tmp_path / "salida")
    try:
        with pytest.raises(scripts_runner.SalidaRechazada, match="no es un archivo regular"):
            scripts_runner.leer_salida(fd, "resultado.step", 100, tmp_path / "copia")
        assert not (tmp_path / "copia").exists()
    finally:
        os.close(fd)


def test_lector_rechaza_fifo_enlace_duro_y_tamano(tmp_path):
    os.mkfifo(tmp_path / "fifo")
    (tmp_path / "grande").write_bytes(b"x" * 101)
    (tmp_path / "original").write_bytes(b"x")
    os.link(tmp_path / "original", tmp_path / "enlazado")
    fd = _dir_fd(tmp_path)
    try:
        for nombre, motivo in (("fifo", "regular"), ("grande", "supera"), ("enlazado", "enlaces duros")):
            with pytest.raises(scripts_runner.SalidaRechazada, match=motivo):
                scripts_runner.leer_salida(fd, nombre, 100)
    finally:
        os.close(fd)
