"""Phase 5C tests: parametric scripts (`app/parametros.py` + the
`/documentos/{id}/parametros` routes), FDM checks (`app/checks/fdm.py` +
`/documentos/{id}/fdm`) and the hand-written multi-object 3MF export
(`app/export.py`).

Same in-process `TestClient` + isolated `FORJA_DATA_DIR` pattern as
`tests/test_captura_colisiones_exportar.py`.
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
import zipfile

import numpy as np
import pytest
import trimesh
from fastapi.testclient import TestClient

import auth
import parametros
from main import app

client = TestClient(app)

_NS = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}


def _headers() -> dict[str, str]:
    return {"X-Forja-Token": auth.obtener_token()}


def _crear(script: str) -> dict:
    resp = client.post("/documentos/script", json={"codigo": script}, headers=_headers())
    assert resp.status_code == 200, resp.text
    return resp.json()


_SCRIPT_PARAMETRICO = '''\
from build123d import Box, Pos

PARAMETROS = {
    "alto": {"valor": 10, "min": 5, "max": 40, "paso": 1, "unidad": "mm", "desc": "alto del poste"},
    "lado": 4,
}


def construir(p):
    return {
        "base": Box(20, 20, 10),
        "poste": Pos(0, 0, 5 + p["alto"] / 2) * Box(p["lado"], p["lado"], p["alto"]),
    }
'''


def _volumen_esperado(alto: float, lado: float = 4.0) -> float:
    return 20 * 20 * 10 + lado * lado * alto


# ---------------------------------------------------------------- parametros


def test_esquema_parametros_se_lee_normalizado():
    creado = _crear(_SCRIPT_PARAMETRICO)
    assert creado["volumen"] == pytest.approx(_volumen_esperado(10), rel=1e-6)
    datos = client.get(f"/documentos/{creado['id']}/parametros").json()
    assert datos["valores"] == {"alto": 10, "lado": 4}
    assert datos["esquema"]["alto"] == {
        "valor": 10, "min": 5, "max": 40, "paso": 1, "unidad": "mm", "desc": "alto del poste"
    }
    # forma corta -> {"valor": ..., "unidad": "mm"}
    assert datos["esquema"]["lado"] == {"valor": 4, "unidad": "mm"}


def test_documento_sin_parametros_devuelve_esquema_vacio_y_no_aplica():
    creado = _crear("from build123d import Box\nresultado = Box(10, 10, 10)\n")
    assert client.get(f"/documentos/{creado['id']}/parametros").json() == {"esquema": {}, "valores": {}}
    resp = client.post(
        f"/documentos/{creado['id']}/parametros", json={"valores": {"x": 1}}, headers=_headers()
    )
    assert resp.status_code == 422


def test_aplicar_parametros_recalcula_en_su_sitio_con_latencia_y_conserva_nombres_y_notas():
    creado = _crear(_SCRIPT_PARAMETRICO)
    doc_id = creado["id"]

    caras = client.get(f"/documentos/{doc_id}/caras").json()
    fondo = next(c for c in caras if c["normal"][2] < -0.99 and abs(c["area"] - 400) < 1e-6)
    referencia = {
        "tipo": "cara",
        "id": fondo["id"],
        "punto": fondo["centroide"],
        "huella": {
            "tipo": "cara",
            "subtipo": fondo["tipo"],
            "centroide": fondo["centroide"],
            "direccion": fondo["normal"],
            "medida": fondo["area"],
        },
    }
    resp = client.post(f"/documentos/{doc_id}/notas", json={"comentario": "fondo", "referencia": referencia})
    assert resp.status_code == 200, resp.text

    resp = client.post(f"/documentos/{doc_id}/parametros", json={"valores": {"alto": 30}}, headers=_headers())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["id"] == doc_id
    assert data["volumen"] == pytest.approx(_volumen_esperado(30), rel=1e-6)
    assert data["valores"] == {"alto": 30, "lado": 4}
    assert isinstance(data["ms"], int) and data["ms"] >= 0  # latencia medida en el servidor
    assert isinstance(data["valores"]["alto"], int)  # declarado int -> sigue int

    # nombres de solido conservados
    ficha = client.get(f"/documentos/{doc_id}").json()
    assert {s["nombre"] for s in ficha["solidos_detalle"]["lista"]} == {"base", "poste"}
    # nota re-resuelta, no perdida
    notas = client.get(f"/documentos/{doc_id}/notas").json()["resumen"]
    assert len(notas) == 1 and notas[0]["referencia_perdida"] is False
    # snapshot previo al cambio en el historial
    historial = client.get(f"/documentos/{doc_id}/historial").json()
    assert any(h["mensaje"] == "antes de parametros: alto=30, lado=4" for h in historial)
    # los valores persisten y un segundo cambio parte de ellos
    resp = client.post(f"/documentos/{doc_id}/parametros", json={"valores": {"lado": 6}}, headers=_headers())
    assert resp.status_code == 200, resp.text
    assert resp.json()["volumen"] == pytest.approx(_volumen_esperado(30, 6), rel=1e-6)
    assert client.get(f"/documentos/{doc_id}/parametros").json()["valores"] == {"alto": 30, "lado": 6}


@pytest.mark.parametrize(
    "valores",
    [{"alto": 100}, {"alto": 1}, {"desconocido": 3}, {"alto": "alto"}, {"alto": True}],
)
def test_aplicar_parametros_fuera_de_rango_o_invalido_422_sin_tocar_el_documento(valores):
    creado = _crear(_SCRIPT_PARAMETRICO)
    doc_id = creado["id"]
    n_historial = len(client.get(f"/documentos/{doc_id}/historial").json())
    resp = client.post(f"/documentos/{doc_id}/parametros", json={"valores": valores}, headers=_headers())
    assert resp.status_code == 422, resp.text
    assert client.get(f"/documentos/{doc_id}").json()["volumen"] == pytest.approx(creado["volumen"])
    assert len(client.get(f"/documentos/{doc_id}/historial").json()) == n_historial


def test_aplicar_parametros_sin_token_401():
    creado = _crear(_SCRIPT_PARAMETRICO)
    resp = client.post(f"/documentos/{creado['id']}/parametros", json={"valores": {"alto": 20}})
    assert resp.status_code == 401
    resp = client.post(
        f"/documentos/{creado['id']}/parametros",
        json={"valores": {"alto": 20}},
        headers={"X-Forja-Token": "no-es"},
    )
    assert resp.status_code == 401


def test_parametros_estilo_params_global_sin_construir():
    script = (
        "from build123d import Box\n"
        "PARAMETROS = {'lado': {'valor': 10, 'min': 1, 'max': 50}}\n"
        "resultado = Box(PARAMS['lado'], PARAMS['lado'], PARAMS['lado'])\n"
    )
    creado = _crear(script)
    assert creado["volumen"] == pytest.approx(1000, rel=1e-6)
    resp = client.post(f"/documentos/{creado['id']}/parametros", json={"valores": {"lado": 20}}, headers=_headers())
    assert resp.status_code == 200, resp.text
    assert resp.json()["volumen"] == pytest.approx(8000, rel=1e-6)


def test_parametros_no_literal_422():
    script = "from build123d import Box\nPARAMETROS = {'a': 2 * 3}\nresultado = Box(1, 1, 1)\n"
    resp = client.post("/documentos/script", json={"codigo": script}, headers=_headers())
    assert resp.status_code == 422
    assert "literal" in resp.json()["detail"]


def test_esquema_valida_valor_por_defecto_fuera_de_rango():
    with pytest.raises(parametros.ParametrosInvalidos):
        parametros.esquema_desde_codigo("PARAMETROS = {'a': {'valor': 100, 'max': 10}}\n")


def test_parametros_numero_enorme_422():
    creado = _crear(_SCRIPT_PARAMETRICO)
    resp = client.post(
        f"/documentos/{creado['id']}/parametros", json={"valores": {"alto": 10**400}}, headers=_headers()
    )
    assert resp.status_code == 422, resp.text
    script = "from build123d import Box\nPARAMETROS = {'a': " + "9" * 400 + "}\nresultado = Box(1, 1, 1)\n"
    resp = client.post("/documentos/script", json={"codigo": script}, headers=_headers())
    assert resp.status_code == 422, resp.text


# ------------------------------------------ fix-review: params in history


def _restaurar(doc_id: str, snapshot: str) -> dict:
    resp = client.post(f"/documentos/{doc_id}/restaurar", json={"snapshot": snapshot}, headers=_headers())
    assert resp.status_code == 200, resp.text
    return resp.json()


def _aplicar(doc_id: str, valores: dict) -> dict:
    resp = client.post(f"/documentos/{doc_id}/parametros", json={"valores": valores}, headers=_headers())
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_restaurar_devuelve_valores_de_parametros_y_se_puede_deshacer():
    creado = _crear(_SCRIPT_PARAMETRICO)
    doc_id = creado["id"]
    creacion = client.get(f"/documentos/{doc_id}/historial").json()[0]["id"]
    _aplicar(doc_id, {"alto": 30})

    restaurado = _restaurar(doc_id, creacion)
    assert restaurado["bbox"] == pytest.approx(creado["bbox"])
    assert client.get(f"/documentos/{doc_id}/parametros").json()["valores"] == {"alto": 10, "lado": 4}
    # el siguiente cambio parte de los valores restaurados, no de los de antes
    assert _aplicar(doc_id, {"lado": 6})["volumen"] == pytest.approx(_volumen_esperado(10, 6), rel=1e-6)

    # "antes de restaurar" deshace el restore: vuelven alto=30, lado=4
    historial = client.get(f"/documentos/{doc_id}/historial").json()
    antes = next(h["id"] for h in historial if h["mensaje"] == f"antes de restaurar {creacion}")
    deshecho = _restaurar(doc_id, antes)
    assert deshecho["bbox"][5] == pytest.approx(5 + 30)
    assert client.get(f"/documentos/{doc_id}/parametros").json()["valores"] == {"alto": 30, "lado": 4}


def test_restaurar_a_traves_de_un_cambio_de_script_revierte_el_script():
    creado = _crear(_SCRIPT_PARAMETRICO)
    doc_id = creado["id"]
    creacion = client.get(f"/documentos/{doc_id}/historial").json()[0]["id"]
    resp = client.post(
        "/documentos/script",
        json={"documento_id": doc_id, "codigo": "from build123d import Box\nresultado = Box(7, 7, 7)\n"},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    assert client.get(f"/documentos/{doc_id}/parametros").json() == {"esquema": {}, "valores": {}}

    _restaurar(doc_id, creacion)
    datos = client.get(f"/documentos/{doc_id}/parametros").json()
    assert set(datos["esquema"]) == {"alto", "lado"}
    assert _aplicar(doc_id, {"alto": 20})["volumen"] == pytest.approx(_volumen_esperado(20), rel=1e-6)


def test_restaurar_snapshot_sin_parametros_deja_documento_no_parametrico(tmp_path):
    from kernel import b123d_kernel

    ruta_step = tmp_path / "caja.step"
    b123d_kernel.export_to_step(b123d_kernel.make_box(10, 10, 10), ruta_step)
    resp = client.post("/documentos", files={"file": ("caja.step", ruta_step.read_bytes(), "model/step")})
    assert resp.status_code == 200, resp.text
    doc_id = resp.json()["id"]
    creacion = client.get(f"/documentos/{doc_id}/historial").json()[0]["id"]

    resp = client.post(
        "/documentos/script", json={"documento_id": doc_id, "codigo": _SCRIPT_PARAMETRICO}, headers=_headers()
    )
    assert resp.status_code == 200, resp.text
    assert set(client.get(f"/documentos/{doc_id}/parametros").json()["esquema"]) == {"alto", "lado"}

    _restaurar(doc_id, creacion)
    assert client.get(f"/documentos/{doc_id}/parametros").json() == {"esquema": {}, "valores": {}}
    resp = client.post(f"/documentos/{doc_id}/parametros", json={"valores": {"alto": 20}}, headers=_headers())
    assert resp.status_code == 400
    assert client.get(f"/documentos/{doc_id}").json()["nombre"] == "caja.step"  # meta 'nombre' intacto


def test_parametros_documento_inexistente_404():
    assert client.get("/documentos/no-existe/parametros").status_code == 404


# ---------------------------------------------------------------- check_fdm


def _repisa(angulo: float) -> str:
    """Prism whose right face overhangs `angulo` degrees from vertical
    (profile in XZ, extruded 20 mm along Y); a short vertical face above it
    so the top edge is not a knife edge."""
    d = 20 * math.tan(math.radians(angulo))
    return (
        "from build123d import *\n"
        "with BuildPart() as bp:\n"
        "    with BuildSketch(Plane.XZ):\n"
        f"        Polygon((0, 0), (10, 0), ({10 + d}, 20), ({10 + d}, 22), (0, 22), align=None)\n"
        "    extrude(amount=20)\n"
        "resultado = {'repisa': bp.part}\n"
    )


def _vaso(pared: float) -> str:
    interior = 20 - 2 * pared
    return (
        "from build123d import Box, Pos\n"
        f"resultado = {{'vaso': Box(20, 20, 10) - Pos(0, 0, {pared}) * Box({interior}, {interior}, 10)}}\n"
    )


def _fdm(doc_id: str, **params) -> dict:
    resp = client.get(f"/documentos/{doc_id}/fdm", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_fdm_pieza_que_no_cabe_en_la_cama():
    creado = _crear("from build123d import Box\nresultado = {'viga': Box(300, 50, 10)}\n")
    data = _fdm(creado["id"])
    assert data["ok"] is False
    assert data["problemas"]["viga"]["cama"]["dims"] == [300.0, 50.0, 10.0]
    assert "no cabe" in data["problemas"]["viga"]["cama"]["sugerencia"]


def test_fdm_sugiere_rotar_90_si_cabe_rotada():
    creado = _crear("from build123d import Box\nresultado = {'placa': Box(200, 230, 10)}\n")
    data = _fdm(creado["id"], cama="250x210x250")
    assert data["problemas"]["placa"]["cama"]["sugerencia"] == "rotar 90 en Z"


def test_fdm_voladizo_60_grados_marcado_y_chaflan_30_no():
    malo = _fdm(_crear(_repisa(60))["id"])
    voladizo = malo["problemas"]["repisa"]["voladizo"]
    assert voladizo["mm2"] == pytest.approx(800.0, rel=0.01)  # 40 mm x 20 mm
    assert voladizo["peor"]["ang"] == pytest.approx(60.0, abs=0.5)
    assert voladizo["peor"]["bbox"][4] == pytest.approx(0.0, abs=0.1)

    bueno = _fdm(_crear(_repisa(30))["id"])
    assert bueno["ok"] is True, bueno
    # con un angulo_max mas estricto, el chaflan de 30 si aparece
    estricto = _fdm(_crear(_repisa(30))["id"], angulo_max=20)
    assert "voladizo" in estricto["problemas"]["repisa"]


def test_fdm_pared_fina_05_marcada_y_2mm_no():
    fino = _fdm(_crear(_vaso(0.5))["id"], boquilla=0.4)
    pared = fino["problemas"]["vaso"]["pared_fina"]
    assert pared["min_mm"] == pytest.approx(0.5, abs=0.05)
    assert pared["muestras"] >= 3

    normal = _fdm(_crear(_vaso(2.0))["id"], boquilla=0.4)
    assert normal["ok"] is True, normal


def test_fdm_base_chica_y_diminuto():
    script = (
        "from build123d import Sphere, Box, Pos\n"
        "resultado = {'bola': Sphere(5), 'pelo': Pos(20, 0, 0) * Box(0.2, 5, 5)}\n"
    )
    data = _fdm(_crear(script)["id"])
    assert data["problemas"]["bola"]["base"]["contacto_mm2"] < 10
    assert data["problemas"]["pelo"]["diminuto"]["dim_min_mm"] == pytest.approx(0.2, abs=0.01)


def test_fdm_respuesta_compacta_y_cama_invalida_422():
    creado = _crear(_repisa(60))
    data = _fdm(creado["id"])
    assert len(str(data)) / 4 < 600
    assert client.get(f"/documentos/{creado['id']}/fdm", params={"cama": "220x220"}).status_code == 422


# ---------------------------------------------------------------- 3MF


_SCRIPT_TRES_PIEZAS = (
    "from build123d import Box, Pos, Cylinder\n"
    "resultado = {\n"
    "    'mesa': Box(30, 20, 5),\n"
    "    'silla_1': Pos(40, 0, 0) * Box(10, 10, 10),\n"
    "    'silla_2': Pos(60, 0, 0) * Box(10, 10, 10),\n"
    "}\n"
)


def _leer_3mf(ruta: str) -> list[tuple[str, trimesh.Trimesh]]:
    """Parse the 3MF XML directly: trimesh 5.1's 3MF *loader* needs
    `networkx` too (not in the image), so the round-trip is checked against
    the raw package instead."""
    with zipfile.ZipFile(ruta) as zf:
        nombres = set(zf.namelist())
        assert {"[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model"} <= nombres
        raiz = ET.fromstring(zf.read("3D/3dmodel.model"))
    assert raiz.get("unit") == "millimeter"
    objetos = []
    for obj in raiz.findall("m:resources/m:object", _NS):
        vertices = np.array(
            [[float(v.get(k)) for k in "xyz"] for v in obj.findall("m:mesh/m:vertices/m:vertex", _NS)]
        )
        caras = np.array(
            [[int(t.get(k)) for k in ("v1", "v2", "v3")] for t in obj.findall("m:mesh/m:triangles/m:triangle", _NS)]
        )
        objetos.append((obj.get("name"), trimesh.Trimesh(vertices, caras, process=False)))
    assert len(raiz.findall("m:build/m:item", _NS)) == len(objetos)
    return objetos


@pytest.mark.parametrize("por_solido", [False, True])
def test_3mf_multiobjeto_con_nombres_y_volumen_round_trip(por_solido):
    creado = _crear(_SCRIPT_TRES_PIEZAS)
    resp = client.get(
        f"/documentos/{creado['id']}/exportar",
        params={"formato": "3mf", "por_solido": str(por_solido).lower()},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["objetos"] == ["mesa", "silla_1", "silla_2"]
    objetos = _leer_3mf(data["ruta"])
    assert [n for n, _ in objetos] == ["mesa", "silla_1", "silla_2"]
    for _nombre, malla in objetos:
        assert malla.is_watertight  # vertices compartidos: manifold para el laminador
    volumen = sum(m.volume for _, m in objetos)
    assert abs(volumen - creado["volumen"]) / creado["volumen"] < 1e-3  # tolerancia Fase 1
    assert objetos[0][1].volume == pytest.approx(30 * 20 * 5, rel=1e-3)


def test_3mf_documento_stl_un_objeto():
    malla = trimesh.creation.box(extents=(10, 10, 10))
    resp = client.post(
        "/documentos", files={"file": ("cubo.stl", malla.export(file_type="stl"), "model/stl")}
    )
    assert resp.status_code == 200, resp.text
    data = client.get(f"/documentos/{resp.json()['id']}/exportar", params={"formato": "3mf"}).json()
    objetos = _leer_3mf(data["ruta"])
    assert [n for n, _ in objetos] == ["cubo"]
    assert objetos[0][1].volume == pytest.approx(1000, rel=1e-3)


def test_3mf_esfera_sin_triangulos_degenerados():
    creado = _crear("from build123d import Sphere\nresultado = {'bola': Sphere(10)}\n")
    data = client.get(f"/documentos/{creado['id']}/exportar", params={"formato": "3mf"}).json()
    (_nombre, malla), = _leer_3mf(data["ruta"])
    f = malla.faces
    assert len(f) > 0
    assert not np.any((f[:, 0] == f[:, 1]) | (f[:, 1] == f[:, 2]) | (f[:, 0] == f[:, 2]))
    assert abs(malla.volume - creado["volumen"]) / creado["volumen"] < 1e-2


def test_3mf_nombre_con_caracteres_xml_se_escapa():
    from export import escribir_3mf

    ruta = parametros.DOCUMENTOS_DIR / "escape.3mf"
    escribir_3mf([('a"<&>b', trimesh.creation.box())], ruta)
    assert _leer_3mf(str(ruta))[0][0] == 'a"<&>b'
