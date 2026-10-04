"""`POST /documentos/{id}/incorporar`: one document's geometry into another,
in place (same id), restorable, origin untouched, and the MCP `rama` action."""
from __future__ import annotations

import hashlib
import io
import json

import pytest
import trimesh
from fastapi.testclient import TestClient

import auth
import documents
import eventos
import solids
from main import app
from mcp_server import client as mcp_client

client = TestClient(app)

SCRIPT_SOPORTE = (
    "from build123d import Box, Pos\n"
    "resultado = {'soporte': Pos(100, 0, 0) * Box(10, 10, 10),\n"
    "             'brazo': Pos(0, 100, 0) * Box(4, 4, 4)}\n"
)
SCRIPT_BASE = (
    "from build123d import Box, Pos\n"
    "resultado = {'soporte': Box(20, 20, 2), 'pata': Pos(0, 0, -20) * Box(2, 2, 2)}\n"
)


def _h() -> dict[str, str]:
    return {"X-Forja-Token": auth.obtener_token()}


def _script(codigo: str, nombre: str) -> str:
    r = client.post("/documentos/script", json={"codigo": codigo, "nombre": nombre}, headers=_h())
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _stl() -> str:
    datos = trimesh.creation.box(extents=(30, 30, 30)).export(file_type="stl")
    r = client.post("/documentos", files={"file": ("carro.stl", io.BytesIO(datos), "application/sla")})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _huella(doc_id: str) -> tuple[str, str]:
    ruta = documents._files[doc_id]
    lado = solids.ruta_solidos(doc_id)
    return (hashlib.sha256(ruta.read_bytes()).hexdigest(),
            hashlib.sha256(lado.read_bytes()).hexdigest() if lado.exists() else "")


def _triangulos(doc_id: str) -> int:
    r = client.get(f"/documentos/{doc_id}/malla")
    assert r.status_code == 200
    return len(trimesh.load(io.BytesIO(r.content), file_type="stl", force="mesh", process=False).faces)


@pytest.fixture()
def docs():
    creados: list[str] = []
    yield creados
    for d in creados:
        client.delete(f"/documentos/{d}", headers=_h())


def test_step_en_stl_suma_triangulos_bbox_evento_y_restaurable(docs, monkeypatch):
    carro, soporte = _stl(), _script(SCRIPT_SOPORTE, "soporte_doc")
    docs += [carro, soporte]
    huella_origen = _huella(soporte)
    tri_carro = _triangulos(carro)
    tri_pieza = len(documents.mesh.tessellate_to_trimesh(
        documents.b123d_kernel.import_from_step(documents._files[soporte])).faces)
    publicados: list[tuple] = []
    original = eventos.publicar
    monkeypatch.setattr(eventos, "publicar", lambda *a, **k: (publicados.append(a), original(*a, **k)))
    antes = client.get(f"/documentos/{carro}").json()

    r = client.post(f"/documentos/{carro}/incorporar", json={"desde": soporte, "piezas": ["soporte"]}, headers=_h())
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["id"] == carro and cuerpo["incorporadas"] == ["soporte"]
    assert cuerpo["mensaje"] == "incorporar soporte desde soporte_doc.step"
    # Only "soporte" (12 tri as a box), not "brazo".
    assert cuerpo["triangulos_despues"] == cuerpo["triangulos_antes"] + 12
    assert _triangulos(carro) == tri_carro + 12 < tri_carro + tri_pieza
    bb = cuerpo["bbox"]
    assert bb[0] == pytest.approx(-15) and bb[1] == pytest.approx(105)
    assert cuerpo["revision"] != antes["revision"]
    assert any(p[0] == "documento_actualizado" and p[1] == carro for p in publicados)
    assert _huella(soporte) == huella_origen

    pasos = client.get(f"/documentos/{carro}/ramas/main/pasos").json()["pasos"]
    assert pasos[0]["mensaje"] == "incorporar soporte desde soporte_doc.step"

    r = client.post(f"/documentos/{carro}/restaurar", json={"snapshot": cuerpo["snapshot_previo"]}, headers=_h())
    assert r.status_code == 200, r.text
    assert _triangulos(carro) == tri_carro
    assert r.json()["bbox"] == pytest.approx(antes["bbox"])


def test_todo_el_origen_en_stl(docs):
    carro, soporte = _stl(), _script(SCRIPT_SOPORTE, "soporte_doc")
    docs += [carro, soporte]
    r = client.post(f"/documentos/{carro}/incorporar", json={"desde": soporte}, headers=_h())
    assert r.status_code == 200, r.text
    assert sorted(r.json()["incorporadas"]) == ["brazo", "soporte"]
    assert r.json()["triangulos_despues"] == 12 + 24


def test_stl_en_stl(docs):
    a, b = _stl(), _stl()
    docs += [a, b]
    r = client.post(f"/documentos/{a}/incorporar", json={"desde": b}, headers=_h())
    assert r.status_code == 200, r.text
    assert r.json()["triangulos_despues"] == 24
    assert client.post(f"/documentos/{a}/incorporar", json={"desde": b, "piezas": ["x"]},
                       headers=_h()).status_code == 400


def test_step_en_step_con_nombres(docs):
    base, soporte = _script(SCRIPT_BASE, "base"), _script(SCRIPT_SOPORTE, "soporte_doc")
    docs += [base, soporte]
    huella_origen = _huella(soporte)
    r = client.post(f"/documentos/{base}/incorporar", json={"desde": soporte}, headers=_h())
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["solidos"] == 4
    assert cuerpo["renombradas"] == {"soporte": "soporte_2"}
    nombres = [e["nombre"] for e in json.loads(solids.ruta_solidos(base).read_text())]
    assert sorted(nombres) == ["brazo", "pata", "soporte", "soporte_2"]
    assert _huella(soporte) == huella_origen
    r = client.post(f"/documentos/{base}/restaurar", json={"snapshot": cuerpo["snapshot_previo"]}, headers=_h())
    assert r.status_code == 200 and r.json()["solidos"] == 2


def test_stl_en_step_es_400(docs):
    base, carro = _script(SCRIPT_BASE, "base"), _stl()
    docs += [base, carro]
    huella = _huella(base)
    r = client.post(f"/documentos/{base}/incorporar", json={"desde": carro}, headers=_h())
    assert r.status_code == 400 and "STL" in r.json()["detail"]
    assert _huella(base) == huella


def test_token_requerido_e_ids_invalidos(docs):
    carro, soporte = _stl(), _script(SCRIPT_SOPORTE, "soporte_doc")
    docs += [carro, soporte]
    assert client.post(f"/documentos/{carro}/incorporar", json={"desde": soporte}).status_code == 401
    assert client.post(f"/documentos/{'0' * 32}/incorporar", json={"desde": soporte}, headers=_h()).status_code == 404
    assert client.post(f"/documentos/{carro}/incorporar", json={"desde": "no-existe"}, headers=_h()).status_code == 404
    assert client.post(f"/documentos/{carro}/incorporar", json={"desde": carro}, headers=_h()).status_code == 400
    assert client.post(f"/documentos/{carro}/incorporar", json={"desde": soporte, "piezas": ["nada"]},
                       headers=_h()).status_code == 400


def test_mcp_rama_incorporar(docs, monkeypatch):
    carro, soporte = _stl(), _script(SCRIPT_SOPORTE, "soporte_doc")
    docs += [carro, soporte]
    monkeypatch.setattr(mcp_client, "_headers_con_token", _h)

    class _Envoltura:
        def __init__(self, cab):
            self.cab = cab

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, json=None, headers=None):
            return client.post(url, json=json, headers={**self.cab, **(headers or {})})

    monkeypatch.setattr(mcp_client.httpx, "Client", lambda *a, headers=None, **k: _Envoltura(headers or {}))
    from mcp_server import tools
    assert tools.rama(carro, "incorporar")["error"] is True
    r = tools.rama(carro, "incorporar", desde=soporte, piezas=["soporte"])
    assert r["incorporadas"] == ["soporte"] and r["id"] == carro
