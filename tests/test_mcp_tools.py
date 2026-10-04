"""Phase 3 MCP tests.

These exercise `mcp_server.tools`/`mcp_server.client` against the **live**
`uvicorn` process already running as the container's entrypoint (PID 1) —
that is the whole point of ADR-0004's process model as clarified for MCP:
`docker exec -i forja python -m mcp_server.server` starts a separate OS
process from the web server, so the only way MCP-created documents show up
in `GET /documentos`/the web viewer is by talking to that same live process
over `http://localhost:8000` (see `mcp_server/__init__.py`). Running
`pytest tests/ -q` via `docker compose exec forja ...` satisfies this: the
container's CMD (uvicorn) is already up.
"""
from __future__ import annotations

import io
import json

import httpx
import pytest

import assemblies
from kernel import b123d_kernel, mesh
from mcp_server import client, tools
from mcp_server.server import mcp as mcp_instance

BASE_URL = client.BASE_URL

_CAJA_SCRIPT = "from build123d import Box\nresultado = Box(10, 10, 10)\n"
_SCRIPT_ROTO = "resultado = 1 / 0\n"


def _backend_disponible() -> bool:
    try:
        httpx.get(f"{BASE_URL}/salud", timeout=2.0)
        return True
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.skipif(
    not _backend_disponible(),
    reason="requiere el proceso uvicorn en vivo del contenedor (localhost:8000)",
)


@pytest.fixture(autouse=True)
def _limpiar_documentos_creados(token_vivo):
    """These tests hit the live `uvicorn` process (a separate OS process, so
    it never sees `tests/conftest.py`'s `FORJA_DATA_DIR` tmp-dir override) —
    any document a test creates would otherwise permanently pile up in the
    real `documentos_data/` (Phase 4 fix-review: test isolation). Diffing
    the document list before/after each test and deleting whatever appeared
    catches every creation path (`ejecutar_script`, `abrir_archivo`, the
    raw `POST /documentos` calls a couple of tests make directly) without
    having to thread cleanup through each test individually."""
    antes = {d["id"] for d in tools.listar_documentos()}
    yield
    despues = {d["id"] for d in tools.listar_documentos()}
    for doc_id in despues - antes:
        client.eliminar_documento(doc_id)


def test_tool_names_match_skill_table():
    """Tool set in server.py == tool table in SKILL.md (Phase 3 contract)."""
    esperadas = {
        "estado",
        "listar_documentos",
        "abrir_archivo",
        "resumen_documento",
        "ejecutar_script",
        "exportar",
        "captura",
        "check_colisiones",
        "percibir",
        "parametros",
        "check_fdm",
        "leer_notas",
        "crear_nota",
        "borrar_nota",
        "leer_historial",
        "restaurar",
        "ensamble",
        "suspension",
        "puentes",
        "cupon",  # fdm-B
    }
    registradas = {tool.name for tool in mcp_instance._tool_manager.list_tools()}  # noqa: SLF001
    assert registradas == esperadas


def test_parametros_y_check_fdm_via_mcp():
    """Phase 5C: one `parametros` tool (None = read, dict = apply) over the
    same endpoint the web UI uses, plus `check_fdm`."""
    script = (
        "from build123d import Box\n"
        "PARAMETROS = {'alto': {'valor': 10, 'min': 5, 'max': 300}}\n"
        "def construir(p):\n"
        "    return {'torre': Box(10, 10, p['alto'])}\n"
    )
    creado = tools.ejecutar_script(script, nombre="param_mcp")
    assert "error" not in creado, creado
    doc_id = creado["id"]
    leido = tools.parametros(doc_id)
    assert leido["valores"] == {"alto": 10}
    assert leido["esquema"]["alto"]["max"] == 300

    aplicado = tools.parametros(doc_id, {"alto": 280})
    assert "error" not in aplicado, aplicado
    assert aplicado["id"] == doc_id and aplicado["ms"] >= 0
    assert abs(aplicado["volumen"] - 28000) < 1e-3

    fuera = tools.parametros(doc_id, {"alto": 999})
    assert fuera["error"] is True and "maximo" in fuera["mensaje"]

    fdm = tools.check_fdm(doc_id, cama="300x300x100")
    assert fdm["problemas"]["torre"]["cama"]["sugerencia"].startswith("tumbar")


def test_estado_es_compacto_y_reporta_versiones():
    data = tools.estado()
    assert data["ok"] is True
    assert isinstance(data["num_documentos"], int)
    for clave in ("build123d", "ocp", "manifold3d", "trimesh", "fcl"):
        assert data["versiones"].get(clave)


def test_listar_documentos_devuelve_lista():
    data = tools.listar_documentos()
    assert isinstance(data, list)


def test_abrir_archivo_rechaza_ruta_fuera_de_raices_permitidas():
    resultado = tools.abrir_archivo("/etc/passwd")
    assert resultado["error"] is True


def test_abrir_archivo_rechaza_traversal():
    resultado = tools.abrir_archivo("/data/documentos/../../etc/passwd")
    assert resultado["error"] is True


def test_ejecutar_script_crea_documento_y_resumen_es_compacto():
    creado = tools.ejecutar_script(_CAJA_SCRIPT, nombre="cubo_mcp")
    assert "error" not in creado, creado
    assert creado["solidos"] == 1
    assert abs(creado["volumen"] - 1000.0) / 1000.0 < 1e-3

    resumen = tools.resumen_documento(creado["id"])
    assert set(resumen.keys()) == {"id", "nombre", "bbox", "volumen", "solidos", "valido"}

    # Acceptance target: a box summary stays well under ~800 bytes (~200 tokens).
    tamano = len(json.dumps(resumen).encode("utf-8"))
    assert tamano < 800, f"resumen_documento demasiado grande: {tamano} bytes"


def test_ejecutar_script_con_error_devuelve_mensaje_corto_no_traceback_completo():
    resultado = tools.ejecutar_script(_SCRIPT_ROTO)
    assert resultado["error"] is True
    assert len(resultado["mensaje"]) < 200
    assert "Traceback" not in resultado["mensaje"]


def test_ejecutar_script_respeta_timeout():
    script_lento = "import time\ntime.sleep(5)\nfrom build123d import Box\nresultado = Box(1, 1, 1)\n"
    resultado = tools.ejecutar_script(script_lento, timeout=1.0)
    assert resultado["error"] is True


def test_exportar_stl_y_step_desde_documento_script():
    creado = tools.ejecutar_script(_CAJA_SCRIPT, nombre="cubo_export")
    assert "error" not in creado, creado

    exportado_stl = tools.exportar(creado["id"], "stl")
    assert exportado_stl["tamano_bytes"] > 0
    assert exportado_stl["ruta"].endswith(".stl")

    exportado_step = tools.exportar(creado["id"], "step")
    assert exportado_step["tamano_bytes"] > 0
    assert exportado_step["ruta"].endswith(".step")


def test_exportar_step_rechazado_para_documento_stl():
    box = b123d_kernel.make_box(6, 6, 6)
    tri_mesh = mesh.tessellate_to_trimesh(box, tolerance=0.05)
    stl_bytes = mesh.to_stl_bytes(tri_mesh)

    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        resp = c.post(
            "/documentos",
            files={"file": ("cubito_mcp.stl", io.BytesIO(stl_bytes), "application/sla")},
        )
    assert resp.status_code == 200
    doc_id = resp.json()["id"]

    resultado = tools.exportar(doc_id, "step")
    assert resultado["error"] is True


def test_crear_nota_leer_notas_y_leer_historial_flujo_completo():
    creado = tools.ejecutar_script(_CAJA_SCRIPT, nombre="cubo_notas_mcp")
    assert "error" not in creado, creado
    doc_id = creado["id"]

    referencia = {"tipo": "punto", "punto": [1.0, 2.0, 3.0]}
    nota = tools.crear_nota(doc_id, "revisar aqui", referencia)
    assert "error" not in nota, nota
    assert nota["comentario"] == "revisar aqui"

    notas = tools.leer_notas(doc_id)
    assert isinstance(notas, list) and len(notas) == 1
    entrada = notas[0]
    assert set(entrada.keys()) >= {
        "n", "tipo", "comentario", "referencia", "referencia_perdida", "plano", "puntos_resumen",
    }
    assert "puntos" not in entrada  # compacto por defecto (sin detalle=true)
    assert "huella" not in entrada["referencia"]

    historial = tools.leer_historial(doc_id)
    assert isinstance(historial, list) and len(historial) >= 2  # creacion + crear nota
    for entrada_historial in historial:
        assert set(entrada_historial.keys()) == {"id", "fecha", "mensaje"}

    borrado = tools.borrar_nota(doc_id, entrada["n"])
    assert borrado == {"ok": True}
    assert tools.leer_notas(doc_id) == []


def test_borrar_nota_inexistente_devuelve_error():
    creado = tools.ejecutar_script(_CAJA_SCRIPT, nombre="cubo_notas_mcp_2")
    resultado = tools.borrar_nota(creado["id"], "no-existe")
    assert resultado["error"] is True


def test_restaurar_snapshot():
    creado = tools.ejecutar_script(_CAJA_SCRIPT, nombre="cubo_restaurar_mcp")
    doc_id = creado["id"]
    historial = tools.leer_historial(doc_id)
    primer_snapshot = historial[0]["id"]

    tools.crear_nota(doc_id, "algo temporal", {"tipo": "punto", "punto": [0.0, 0.0, 0.0]})
    assert len(tools.leer_notas(doc_id)) == 1

    restaurado = tools.restaurar(doc_id, primer_snapshot)
    assert "error" not in restaurado, restaurado
    assert restaurado["id"] == doc_id
    assert tools.leer_notas(doc_id) == []


def test_ejecutar_script_documento_id_actualiza_en_su_sitio_via_mcp():
    """Phase 5A feedback item 5: `documento_id` was already accepted by the
    HTTP route but never exposed through the MCP tool itself."""
    creado = tools.ejecutar_script(_CAJA_SCRIPT, nombre="cubo_mcp_update")
    assert "error" not in creado, creado
    doc_id = creado["id"]

    script_agujero = (
        "from build123d import Box, Cylinder\n"
        "resultado = Box(10, 10, 10) - Cylinder(1.0, 20)\n"
    )
    actualizado = tools.ejecutar_script(script_agujero, documento_id=doc_id)
    assert "error" not in actualizado, actualizado
    assert actualizado["id"] == doc_id
    assert actualizado["volumen"] < creado["volumen"]


def test_ejecutar_script_con_ruta_y_variables_via_mcp():
    creado = tools.ejecutar_script(
        ruta="/data/fuentes/forja/casa-munecas/casa_v1.py", nombre="casa_v1_mcp"
    )
    assert "error" not in creado, creado
    assert creado["solidos"] == 1


def test_ejecutar_script_error_devuelve_linea_y_codigo_linea():
    resultado = tools.ejecutar_script("x = 1\nresultado = 1 / 0\n")
    assert resultado["error"] is True
    assert resultado.get("linea") == 2
    assert resultado.get("codigo_linea") == "resultado = 1 / 0"


def test_captura_devuelve_imagen_y_caption_via_mcp():
    """Phase 5B: `captura` is a real render now, not the Phase 3 stub."""
    creado = tools.ejecutar_script(
        "from build123d import Box\nresultado = {'caja': Box(10, 10, 10)}\n",
        nombre="cubo_captura_mcp",
    )
    assert "error" not in creado, creado

    resultado = tools.captura(creado["id"], ancho=200, alto=200)
    assert isinstance(resultado, list) and len(resultado) == 2
    imagen, caption = resultado
    assert imagen.data and len(imagen.data) > 0
    assert imagen._mime_type == "image/png"  # noqa: SLF001 - Image has no public accessor
    assert "1 solido" in caption


def test_captura_documento_inexistente_devuelve_error_via_mcp():
    resultado = tools.captura("no-existe")
    assert resultado["error"] is True


def test_check_colisiones_via_mcp():
    creado = tools.ejecutar_script(
        "from build123d import Box, Pos\n"
        "resultado = {'a': Box(10, 10, 10), 'b': Pos(5, 0, 0) * Box(10, 10, 10)}\n",
        nombre="choque_mcp",
    )
    assert "error" not in creado, creado

    resultado = tools.check_colisiones(creado["id"])
    assert resultado["ok"] is False
    assert len(resultado["choques"]) == 1
    assert abs(resultado["choques"][0]["vol"] - 500.0) / 500.0 < 0.01


def test_exportar_por_solido_via_mcp():
    creado = tools.ejecutar_script(
        "from build123d import Box, Pos\n"
        "resultado = {'silla_1': Pos(0,0,0)*Box(5,5,5), 'silla_2': Pos(20,0,0)*Box(5,5,5)}\n",
        nombre="sillas_export_mcp",
    )
    assert "error" not in creado, creado

    resultado = tools.exportar(creado["id"], "stl", por_solido=True)
    assert "error" not in resultado, resultado
    assert len(resultado["archivos"]) == 1  # ambas sillas son geometricamente identicas
    (nombres,) = resultado["archivos"].values()
    assert sorted(nombres) == ["silla_1", "silla_2"]


# ---------------------------------------------------------------- Phase 6.4:
# assemblies and the read-only local-service bridges.

_ENSAMBLE_SCRIPT = (
    "from build123d import Box, Pos\n"
    "resultado = {'base': Box(20, 20, 4), 'brazo': Pos(14, 0, 4) * Box(6, 6, 16)}\n"
)

_ENSAMBLE_ARTICULACION = {
    "id": "bisagra", "tipo": "giro", "padre": "base", "hijo": "brazo",
    "origen": [14, 0, 4], "eje": [0, 1, 0], "limites": [-90, 90], "valor": 0,
}


def test_ensamble_via_mcp_define_posa_lee_y_quita():
    """Phase 6.4: one tool, four mutually exclusive modes, over the same
    REST contract the viewer uses — the agent's whole assembly surface."""
    creado = tools.ejecutar_script(_ENSAMBLE_SCRIPT, nombre="ensamble_mcp")
    assert "error" not in creado, creado
    doc_id = creado["id"]
    antes = tools.resumen_documento(doc_id)

    definido = tools.ensamble(doc_id, articulaciones=[dict(_ENSAMBLE_ARTICULACION)])
    assert "error" not in definido, definido
    assert definido["id"] == doc_id
    assert definido["piezas"] == ["base", "brazo"]
    assert definido["valores"] == {"bisagra": 0}
    assert definido["obsoleto"] is False
    assert definido["articulaciones"] == [{
        "id": "bisagra", "tipo": "giro", "padre": "base", "hijo": "brazo",
        "origen": [14.0, 0.0, 4.0], "eje": [0.0, 1.0, 0.0], "limites": [-90.0, 90.0], "valor": 0.0,
    }]  # la huella interna nunca se devuelve
    assert "huella" not in json.dumps(definido)

    assert tools.ensamble(doc_id) == definido  # leer sin argumentos

    posado = tools.ensamble(doc_id, valores={"bisagra": 60})
    assert "error" not in posado, posado
    assert posado["valores"] == {"bisagra": 60.0}
    assert posado["ms"] >= 0
    assert posado["bbox"] != antes["bbox"]  # la pose movio el brazo
    assert posado["bbox"][1] > antes["bbox"][1]  # xmax crece al inclinarlo

    fuera = tools.ensamble(doc_id, valores={"bisagra": 999})
    assert fuera["error"] is True and "fuera de límites" in fuera["mensaje"]

    mensajes = [entrada["mensaje"] for entrada in tools.leer_historial(doc_id)]
    assert "antes de definir articulaciones" in mensajes
    assert "antes de mover articulaciones" in mensajes

    quitado = tools.ensamble(doc_id, quitar=True)
    assert quitado["articulaciones"] == [] and quitado["valores"] == {}
    assert quitado["piezas"] == ["base", "brazo"]
    # Quitar conserva la posicion: la geometria no se mueve ni un milimetro.
    assert tools.resumen_documento(doc_id)["bbox"] == posado["bbox"]
    assert "antes de quitar articulaciones (conservar posición)" in [
        entrada["mensaje"] for entrada in tools.leer_historial(doc_id)
    ]


def test_ensamble_rechaza_modos_mezclados_sin_tocar_el_documento():
    """Two modes at once -> a clear message before any HTTP call (the id is
    bogus on purpose: a rejection that consulted the backend would 404)."""
    mezcla = tools.ensamble("no-existe", articulaciones=[{"id": "a"}], valores={"a": 1})
    assert mezcla["error"] is True and "un solo modo" in mezcla["mensaje"]
    assert tools.ensamble("no-existe", valores={"a": 1}, quitar=True)["error"] is True
    assert tools.ensamble("no-existe", articulaciones=[{"id": "a"}], quitar=True)["error"] is True
    assert tools.ensamble("no-existe", articulaciones=[])["error"] is True
    assert tools.ensamble("no-existe", valores={})["error"] is True
    assert "no encontrado" in tools.ensamble("no-existe")["mensaje"]  # el id si se consulta al leer


def test_ensamble_surface_el_409_del_formato_verbatim(monkeypatch):
    """The one new failure the agent must be able to act on (Phase 6.3): a
    `state.json` from before the rename is refused with its own actionable
    sentence, verbatim — not a 500, not a silent success. Asserted through
    the tool with a stubbed transport, so no real state file is planted."""
    class _FalsoCliente:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def _respuesta(self, *args, **kwargs):
            return httpx.Response(409, json={"detail": assemblies.MENSAJE_FORMATO})

        get = post = delete = _respuesta

    monkeypatch.setattr(client.httpx, "Client", _FalsoCliente)
    resultado = tools.ensamble("un-documento", valores={"bisagra": 10})
    assert resultado == {"error": True, "mensaje": assemblies.MENSAJE_FORMATO}
    assert "quitar las articulaciones y redefinirlas" in resultado["mensaje"]


def test_suspension_proyeccion_compacta_solo_problemas():
    """Phase 6.4: the `/pose` table projected to what an agent acts on —
    `dentro` gone, violations under `fuera_de_carrera`, one `marcha` row."""
    crudo = {
        "subida_rueda": [
            {"mm": 0, "der": {"L": 60.5, "compresion": 0.0, "dentro": True},
             "izq": {"L": 60.4, "compresion": 0.0, "dentro": True}},
            {"mm": 8, "der": {"L": 57.1, "compresion": 3.4, "dentro": True},
             "izq": {"L": 66.2, "compresion": -5.8, "dentro": False}},
        ],
        "cabeceo_sobre_eje_ruedas": [],
        "articulacion": [],
        "signo_correcto_al_subir": True,
    }
    proyectado = client._proyeccion_pose(crudo, [56.0, 65.8])  # noqa: SLF001
    assert proyectado["marcha"] == {"der": {"L": 60.5, "compresion": 0.0},
                                    "izq": {"L": 60.4, "compresion": 0.0}}
    assert proyectado["subida_rueda"] == [
        {"mm": 8, "der": {"L": 57.1, "compresion": 3.4}, "izq": {"L": 66.2, "compresion": -5.8}}
    ]
    assert proyectado["fuera_de_carrera"] == [
        {"caso": "subida_rueda", "valor": 8, "lado": "izq", "L": 66.2}
    ]
    assert "cabeceo_sobre_eje_ruedas" not in proyectado  # no se pidio, no se inventa
    assert "dentro" not in json.dumps(proyectado)


def _stub_puente_suspension(monkeypatch, payload):
    """Point `client.suspension` at a stubbed bridge: `/config` answers with
    the real limits and `/pose` with `payload` — malformed on purpose in the
    tests below — so the projection is exercised without the simulator."""
    class _FalsoCliente:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, *args, **kwargs):
            return httpx.Response(200, json={"geo": {"L_min": 56.0, "L_max": 65.8}})

        def post(self, *args, **kwargs):
            # Bytes, not `json=`: httpx refuses to *encode* a non-finite float
            # (allow_nan=False), and an upstream that sends `Infinity`/`NaN`
            # is exactly the shape under test here.
            return httpx.Response(200, content=json.dumps(payload),
                                  headers={"content-type": "application/json"})

    monkeypatch.setattr(client.httpx, "Client", _FalsoCliente)


_MENSAJE_POSE = "respuesta inesperada del puente de suspension (/pose)"

# Una tabla `/pose` valida (marcha + una subida), reusada por los casos que
# atacan algo distinto de las filas.
_FILAS_VALIDAS = [
    {"mm": 0, "der": {"L": 60.0, "compresion": 0.0, "dentro": True},
     "izq": {"L": 60.0, "compresion": 0.0, "dentro": True}},
    {"mm": 4, "der": {"L": 59.0, "compresion": 1.0, "dentro": True},
     "izq": {"L": 59.0, "compresion": 1.0, "dentro": True}},
]


@pytest.mark.parametrize(
    "payload",
    [
        {"subida_rueda": "x"},                                    # el caso llega como texto
        {"subida_rueda": {"4": {"der": {"L": 60.0, "compresion": 0.0}}}},  # el caso llega como objeto
        {"subida_rueda": [[1, 2]]},                               # la fila llega como lista
        {"subida_rueda": [{"mm": 4, "der": "arriba"}]},           # el lado no es un objeto
        {"subida_rueda": [{"der": {"L": 60.0, "compresion": 0.0}}]},       # fila sin mm ni grados
        {"subida_rueda": [{"mm": 4, "der": {"L": 60.0}}]},        # KeyError: falta compresion
        {"subida_rueda": [{"mm": "4", "der": {"L": 60.0, "compresion": 0.0}}]},  # TypeError: mm no numerico
        {"subida_rueda": [{"mm": 4, "der": {"L": "alto", "compresion": 0.0}}]},  # ValueError: L no numerico
        {"subida_rueda": [{"mm": 4, "der": {"L": float("inf"), "compresion": 0.0}}]},   # L infinito
        {"subida_rueda": [{"mm": 4, "der": {"L": float("nan"), "compresion": 0.0}}]},   # L NaN
        {"subida_rueda": [{"mm": 4, "der": {"L": 60.0, "compresion": float("inf")}}]},  # compresion infinita
        {"subida_rueda": [{"mm": float("inf"), "der": {"L": 60.0, "compresion": 0.0}}]},  # valor infinito
        {"subida_rueda": [{"mm": 4, "der": {"L": 60.0, "compresion": 0.0, "dentro": "si"}}]},  # dentro no booleano
        {"subida_rueda": _FILAS_VALIDAS, "signo_correcto_al_subir": "si"},       # signo no booleano
        [{"mm": 4}],                                              # el payload entero no es un objeto
    ],
    ids=["caso-texto", "caso-objeto", "fila-lista", "lado-texto", "fila-sin-valor",
         "falta-compresion", "mm-no-numerico", "L-no-numerico", "L-infinito", "L-nan",
         "compresion-infinita", "valor-infinito", "dentro-texto", "signo-texto", "payload-lista"],
)
def test_suspension_payload_malformado_no_escapa(monkeypatch, payload):
    """Phase 6.4 fix round: NO shape of upstream payload may escape the tool
    as an exception (each one used to come back as `isError: Error executing
    tool suspension` plus a traceback in the container log), and a number
    nobody measured — `Infinity`, `NaN` — is not a number either: the bridge
    500s on it, so it must be refused here with the same sentence."""
    _stub_puente_suspension(monkeypatch, payload)
    assert tools.suspension() == {"error": True, "mensaje": _MENSAJE_POSE}


def test_suspension_no_inventa_el_signo_que_el_simulador_no_da(monkeypatch):
    """Phase 6.4 closing round: `signo_correcto_al_subir` is the simulator's
    verdict, so a payload WITHOUT it must not come back as `false` — "the sign
    is wrong" and "nobody measured it" are different answers, and an agent
    acts on the difference. The absence is reported by omitting the key."""
    _stub_puente_suspension(monkeypatch, {"subida_rueda": _FILAS_VALIDAS})
    resultado = tools.suspension()
    assert "error" not in resultado, resultado
    assert "signo_correcto_al_subir" not in resultado
    assert resultado["marcha"] == {"der": {"L": 60.0, "compresion": 0.0},
                                   "izq": {"L": 60.0, "compresion": 0.0}}
    assert resultado["fuera_de_carrera"] == []


def test_suspension_fuera_de_carrera_lo_deciden_los_limites():
    """Phase 6.4 fix round: `limites` is used, not decoration. A side whose
    observed `L` violates them is NEVER reported as inside (`dentro` present,
    absent or optimistically true all included); the simulator's own flag
    stays as a secondary signal in the other direction."""
    limites = [56.0, 65.8]
    filas = [
        {"mm": 0, "der": {"L": 60.0, "compresion": 0.0, "dentro": True},
         "izq": {"L": 60.0, "compresion": 0.0, "dentro": True}},
        {"mm": 9,
         "der": {"L": 70.0, "compresion": -9.0, "dentro": True},   # L > L_max, `dentro` miente
         "izq": {"L": 55.0, "compresion": 5.0}},                   # L < L_min y sin `dentro`
    ]
    con_marca = [{"mm": 12, "der": {"L": 60.0, "compresion": 0.0, "dentro": False},
                  "izq": {"L": 65.8, "compresion": 0.0, "dentro": True}}]
    proyectado = client._proyeccion_pose({"subida_rueda": filas}, limites)  # noqa: SLF001
    assert {(e["caso"], e["valor"], e["lado"], e["L"]) for e in proyectado["fuera_de_carrera"]} == {
        ("subida_rueda", 9, "der", 70.0), ("subida_rueda", 9, "izq", 55.0)}

    marcada = client._proyeccion_pose({"subida_rueda": con_marca}, limites)  # noqa: SLF001
    # `L` en el limite (65.8) esta dentro, pero el simulador ya la marca fuera:
    # la union de las dos senales, nunca una fila fuera reportada como dentro.
    assert [(e["lado"], e["L"]) for e in marcada["fuera_de_carrera"]] == [("der", 60.0)]


def test_puentes_reporta_los_tres_servicios_sin_reventar():
    """Phase 6.4: readiness of the three bridges; a disabled service is a
    clean `false`, never an exception (only suspension is on by default)."""
    estado = tools.puentes()
    assert set(estado) == {"suspension", "blender", "kybercore"}
    for datos in estado.values():
        assert isinstance(datos["habilitado"], bool) and isinstance(datos["disponible"], bool)
        if not datos["habilitado"]:
            assert datos["disponible"] is False
    assert estado["suspension"]["habilitado"] is True  # el unico encendido por defecto
    assert len(json.dumps(estado)) < 400  # ~30 tokens, no una tabla del simulador


def test_suspension_compacta_contra_el_simulador_vivo():
    estado = tools.puentes()["suspension"]
    if not estado["disponible"]:
        pytest.skip(f"suspension-sim no disponible: {estado.get('mensaje')}")

    datos = tools.suspension()
    assert datos["signo_correcto_al_subir"] is True
    assert datos["limites"][0] < datos["limites"][1]
    assert set(datos["marcha"]) == {"der", "izq"}
    assert set(datos["subida_rueda"][0]) == {"mm", "der", "izq"}
    assert datos["ms"] >= 0
    for fila in datos["subida_rueda"]:
        for lado, medidas in fila.items():
            if lado != "mm":
                assert set(medidas) == {"L", "compresion"}

    fuera = tools.suspension(subida_mm=30)  # muy por encima del recorrido
    assert fuera["fuera_de_carrera"]
    assert {entrada["caso"] for entrada in fuera["fuera_de_carrera"]} == {"subida_rueda"}
    # Los casos no consultados no aparecen ni en las filas ni en los avisos:
    # un `cabeceo_deg` omitido no puede volver como el barrido por defecto del
    # simulador ([-3,-1.5,0,1.5,3]) ni meter ruido en `fuera_de_carrera`.
    assert set(fuera) == {"signo_correcto_al_subir", "limites", "marcha", "subida_rueda",
                          "fuera_de_carrera", "ms"}

    crudo = tools.suspension(cabeceo_deg=3, crudo=True)
    # crudo=true: la tabla completa del simulador, fila de marcha incluida.
    assert [fila["grados"] for fila in crudo["cabeceo_sobre_eje_ruedas"]] == [0.0, 3.0]
    assert [fila["mm"] for fila in crudo["subida_rueda"]] == [0.0]
    assert "dentro" in json.dumps(crudo)


def test_puentes_y_ensamble_degrada_sin_backend(monkeypatch):
    """A network failure is an answer (`{error, mensaje}`), never an
    exception escaping the tool — the client-side half of the bridge's
    "degrades cleanly" contract."""
    monkeypatch.setattr(client, "BASE_URL", "http://127.0.0.1:9")
    for resultado in (tools.puentes(), tools.ensamble("cualquiera"), tools.suspension()):
        assert resultado["error"] is True
        assert "Traceback" not in resultado["mensaje"] and resultado["mensaje"]
