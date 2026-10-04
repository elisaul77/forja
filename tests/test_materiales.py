"""fdm-D: material/filament per named piece — `MATERIALES` declaration,
sidecar persistence (update/restore), REST read/edit, and the 3MF carrying
the assignments (core `basematerials` + Orca/Bambu `model_settings.config`)."""
from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile

import pytest
import trimesh
from fastapi.testclient import TestClient

import auth
import export
import materiales
from main import app

client = TestClient(app, raise_server_exceptions=False)
_NS = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}

_SCRIPT = (
    "from build123d import Box, Pos\n"
    "MATERIALES = {\n"
    "    'tapa': 'PETG negro',\n"
    "    'junta': {'material': 'TPU', 'color': '#202020', 'extrusor': 2},\n"
    "    'fantasma': 'PLA',\n"
    "}\n"
    "resultado = {\n"
    "    'tapa': Box(30, 20, 5),\n"
    "    'junta': Pos(40, 0, 0) * Box(10, 10, 10),\n"
    "    'base': Pos(60, 0, 0) * Box(10, 10, 10),\n"
    "}\n"
)


def _h() -> dict:
    return {"X-Forja-Token": auth.obtener_token()}


def _crear(script: str = _SCRIPT) -> dict:
    resp = client.post("/documentos/script", json={"codigo": script}, headers=_h())
    assert resp.status_code == 200, resp.text
    return resp.json()


def _zip(doc_id: str, ruta: str) -> zipfile.ZipFile:
    resp = client.get(f"/documentos/{doc_id}/descarga/{ruta}")
    assert resp.status_code == 200, resp.text
    return zipfile.ZipFile(io.BytesIO(resp.content))


def _config(zf: zipfile.ZipFile) -> dict[str, dict[str, str]]:
    raiz = ET.fromstring(zf.read("Metadata/model_settings.config"))
    salida = {}
    for obj in raiz.findall("object"):
        meta = {m.get("key"): m.get("value") for m in obj.findall("metadata")}
        salida[meta["name"]] = {**meta, "id": obj.get("id")}
    return salida


# --- declaration / validation ------------------------------------------------

def test_desde_codigo_normaliza_forma_corta_y_larga():
    assert materiales.desde_codigo(_SCRIPT) == {
        "tapa": {"material": "PETG negro"},
        "junta": {"material": "TPU", "color": "#202020", "extrusor": 2},
        "fantasma": {"material": "PLA"},
    }
    assert materiales.desde_codigo("resultado = 1\n") is None


@pytest.mark.parametrize("decl", [
    "MATERIALES = {'a': {'color': 'rojo'}}",
    "MATERIALES = {'a': {'extrusor': 0}}",
    "MATERIALES = {'a': {'extrusor': 17}}",
    "MATERIALES = {'a': {'extrusor': True}}",
    "MATERIALES = {'a': 'x' * 65}",
    "MATERIALES = {'a': {'otra': 1}}",
    "MATERIALES = {'a': {}}",
    "MATERIALES = {'a': 'PLA\\x00rojo'}",
    "MATERIALES = dict(a='PLA')",
    "MATERIALES = ['PLA']",
])
def test_desde_codigo_rechaza_invalidos(decl):
    with pytest.raises(materiales.MaterialesInvalidos):
        materiales.desde_codigo(decl)


def test_script_con_materiales_invalidos_422_sin_crear():
    n = len(client.get("/documentos").json())
    resp = client.post(
        "/documentos/script",
        json={"codigo": "from build123d import Box\nMATERIALES = {'a': {'color': '#12'}}\nresultado = {'a': Box(1, 1, 1)}\n"},
        headers=_h(),
    )
    assert resp.status_code == 422
    assert "color" in resp.json()["detail"]
    assert len(client.get("/documentos").json()) == n


# --- persistence / REST ------------------------------------------------------

def test_crear_persiste_y_reporta_ignorados_y_resumen():
    doc = _crear()
    assert doc["materiales_ignorados"] == ["fantasma"]
    assert materiales.ruta(doc["id"]).exists()
    leido = client.get(f"/documentos/{doc['id']}/materiales").json()
    assert leido == {
        "materiales": {"tapa": {"material": "PETG negro"},
                       "junta": {"material": "TPU", "color": "#202020", "extrusor": 2}},
        "piezas": ["tapa", "junta", "base"],
    }
    resumen = client.get(f"/documentos/{doc['id']}").json()
    assert resumen["materiales"] == leido["materiales"]


def test_resumen_sin_materiales_no_agrega_campo():
    doc = _crear("from build123d import Box, Pos\nresultado = {'a': Box(5, 5, 5), 'b': Pos(9, 0, 0) * Box(5, 5, 5)}\n")
    assert "materiales" not in client.get(f"/documentos/{doc['id']}").json()
    assert "materiales_ignorados" not in doc


def test_post_materiales_edita_valida_y_requiere_token():
    doc_id = _crear()["id"]
    assert client.post(f"/documentos/{doc_id}/materiales", json={"materiales": {}}).status_code == 401
    n_hist = len(client.get(f"/documentos/{doc_id}/historial").json())
    for malo in ({"nada": "PLA"}, {"base": {"color": "azul"}}, {"base": {"extrusor": 99}}):
        resp = client.post(f"/documentos/{doc_id}/materiales", json={"materiales": malo}, headers=_h())
        assert resp.status_code == 422, malo
    assert len(client.get(f"/documentos/{doc_id}/historial").json()) == n_hist

    resp = client.post(
        f"/documentos/{doc_id}/materiales",
        json={"materiales": {"base": {"material": "PLA blanco", "color": "#ffffff", "extrusor": 3}, "tapa": None}},
        headers=_h(),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["materiales"] == {
        "junta": {"material": "TPU", "color": "#202020", "extrusor": 2},
        "base": {"material": "PLA blanco", "color": "#FFFFFF", "extrusor": 3},
    }
    assert len(client.get(f"/documentos/{doc_id}/historial").json()) == n_hist + 1


def test_actualizar_con_misma_declaracion_conserva_edicion_y_con_otra_reemplaza():
    doc_id = _crear()["id"]
    client.post(f"/documentos/{doc_id}/materiales", json={"materiales": {"base": "ASA"}}, headers=_h())
    resp = client.post("/documentos/script", json={"codigo": _SCRIPT, "documento_id": doc_id}, headers=_h())
    assert resp.status_code == 200, resp.text
    assert client.get(f"/documentos/{doc_id}/materiales").json()["materiales"]["base"] == {"material": "ASA"}
    # script without MATERIALES: stored materials survive untouched
    sin_decl = "from build123d import Box, Pos\nresultado" + _SCRIPT.split("resultado", 1)[1]
    client.post("/documentos/script", json={"codigo": sin_decl, "documento_id": doc_id}, headers=_h())
    assert "base" in client.get(f"/documentos/{doc_id}/materiales").json()["materiales"]
    # changed declaration replaces
    nuevo = _SCRIPT.replace("'PETG negro'", "'PETG rojo'")
    client.post("/documentos/script", json={"codigo": nuevo, "documento_id": doc_id}, headers=_h())
    mats = client.get(f"/documentos/{doc_id}/materiales").json()["materiales"]
    assert mats["tapa"] == {"material": "PETG rojo"} and "base" not in mats


def test_restaurar_devuelve_los_materiales_de_esa_instantanea():
    doc_id = _crear()["id"]
    creacion = client.get(f"/documentos/{doc_id}/historial").json()[0]["id"]
    client.post(f"/documentos/{doc_id}/materiales",
                json={"materiales": {"tapa": "PLA verde"}}, headers=_h())
    assert client.get(f"/documentos/{doc_id}/materiales").json()["materiales"]["tapa"] == {"material": "PLA verde"}
    resp = client.post(f"/documentos/{doc_id}/restaurar", json={"snapshot": creacion}, headers=_h())
    assert resp.status_code == 200, resp.text
    assert client.get(f"/documentos/{doc_id}/materiales").json()["materiales"]["tapa"] == {"material": "PETG negro"}


def test_eliminar_borra_sidecar():
    doc_id = _crear()["id"]
    assert client.delete(f"/documentos/{doc_id}", headers=_h()).status_code == 200
    assert not materiales.ruta(doc_id).exists()


# --- 3MF ---------------------------------------------------------------------

def test_3mf_documento_lleva_extrusor_nombre_y_color():
    doc_id = _crear()["id"]
    zf = _zip(doc_id, "modelo.3mf")
    cfg = _config(zf)
    assert cfg["junta"]["extruder"] == "2"
    assert cfg["tapa"].get("forja_material") == "PETG negro" and "extruder" not in cfg["tapa"]
    assert "extruder" not in cfg["base"]
    modelo = ET.fromstring(zf.read("3D/3dmodel.model"))
    objetos = {o.get("name"): o for o in modelo.findall("m:resources/m:object", _NS)}
    assert {o.get("id") for o in objetos.values()} == {cfg[n]["id"] for n in cfg}
    bases = modelo.find("m:resources/m:basematerials", _NS)
    lista = bases.findall("m:base", _NS)
    junta = objetos["junta"]
    assert junta.get("pid") == bases.get("id")
    assert lista[int(junta.get("pindex"))].get("displaycolor") == "#202020FF"
    assert lista[int(junta.get("pindex"))].get("name") == "TPU"
    assert objetos["base"].get("pid") is None
    assert "config" in zf.read("[Content_Types].xml").decode()


def test_3mf_una_pieza_conserva_su_material():
    doc_id = _crear()["id"]
    zf = _zip(doc_id, "junta/modelo_junta.3mf")
    cfg = _config(zf)
    assert list(cfg) == ["junta"] and cfg["junta"]["extruder"] == "2" and cfg["junta"]["id"] == "1"
    modelo = ET.fromstring(zf.read("3D/3dmodel.model"))
    (obj,) = modelo.findall("m:resources/m:object", _NS)
    assert obj.get("pid") == "2" and obj.get("pindex") == "0"


def test_3mf_sin_materiales_identico_al_formato_previo():
    malla = trimesh.creation.box(extents=(1, 1, 1))
    datos = export.bytes_3mf([("a", malla)])
    with zipfile.ZipFile(io.BytesIO(datos)) as zf:
        assert set(zf.namelist()) == {"[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model"}
        assert b"basematerials" not in zf.read("3D/3dmodel.model")
    assert export.bytes_3mf([("a", malla)], {"otra": {"material": "PLA"}}) == datos


def test_exportar_3mf_tambien_lleva_materiales():
    doc_id = _crear()["id"]
    resp = client.get(f"/documentos/{doc_id}/exportar", params={"formato": "3mf"})
    assert resp.status_code == 200, resp.text
    with zipfile.ZipFile(resp.json()["ruta"]) as zf:
        assert _config(zf)["junta"]["extruder"] == "2"
