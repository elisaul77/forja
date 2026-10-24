"""Papelera (galeria ordenada, fase 1): borrado reversible de documentos.

- Mover a la papelera saca el documento de la galeria y de todas sus rutas,
  pero no borra ningun archivo; restaurar lo devuelve identico (misma
  `revision`, mismo historial).
- Solo `DELETE /papelera/{id}` borra definitivamente un documento que ya
  esta en la papelera. `DELETE /documentos/{id}` sigue igual (vivos).
- El arranque no carga documentos en papelera.
- Todas las rutas que mutan (y el listado de la papelera) exigen el token.

Las pruebas en proceso usan documentos temporales (`FORJA_DATA_DIR` de
`tests/conftest.py`) y los limpian. Las del MCP en vivo se saltan si el
`uvicorn` del contenedor no responde (igual que `tests/test_mcp_tools.py`).
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import auth
import documents
import papelera
from main import app
from mcp_server import client as mcp_client
from mcp_server import tools

client = TestClient(app)

FIXTURE_STEP = Path(__file__).parent / "fixtures" / "tuerca_p5.step"
DIR = documents.DOCUMENTOS_DIR


def _cabeceras() -> dict[str, str]:
    return {"X-Forja-Token": auth.obtener_token()}


def _subir() -> str:
    resp = client.post(
        "/documentos",
        files={"file": ("pieza.step", FIXTURE_STEP.read_bytes(), "application/step")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _archivos(doc_id: str) -> set[str]:
    """Rutas relativas de todo lo que el documento deja en disco, sin su
    estado de papelera (ese archivo es el unico que la operacion crea)."""
    estado = papelera.ruta_estado(DIR, doc_id).name
    return {
        p.relative_to(DIR).as_posix()
        for p in DIR.rglob("*")
        if p.is_file() and doc_id in p.name and p.name != estado
    }


def _quedan_rastros(doc_id: str) -> list[str]:
    return [p.relative_to(DIR).as_posix() for p in DIR.rglob("*") if doc_id in p.name]


def _en_galeria(doc_id: str) -> bool:
    return doc_id in [d["id"] for d in client.get("/documentos").json()]


def _en_papelera(doc_id: str) -> bool:
    resp = client.get("/papelera", headers=_cabeceras())
    assert resp.status_code == 200, resp.text
    return doc_id in [d["id"] for d in resp.json()]


def _limpiar(doc_id: str) -> None:
    """Borra lo que el test deja: hard delete si esta vivo, purga si esta en papelera."""
    client.delete(f"/documentos/{doc_id}", headers=_cabeceras())
    client.delete(f"/papelera/{doc_id}", headers=_cabeceras())


@pytest.fixture
def doc():
    doc_id = _subir()
    yield doc_id
    _limpiar(doc_id)


def test_documento_sin_estado_es_permanente(doc):
    assert not papelera.ruta_estado(DIR, doc).exists()
    assert _en_galeria(doc)
    assert not _en_papelera(doc)


def test_mover_a_papelera_saca_de_galeria_y_no_borra_archivos(doc):
    antes = _archivos(doc)
    assert f"{doc}.step" in antes

    resp = client.post(f"/documentos/{doc}/papelera", headers=_cabeceras())
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True

    assert not _en_galeria(doc)
    assert client.get(f"/documentos/{doc}").status_code == 404
    assert client.get(f"/documentos/{doc}/historial").status_code == 404
    assert _en_papelera(doc)
    assert _archivos(doc) == antes
    assert papelera.ruta_estado(DIR, doc).exists()


def test_restaurar_devuelve_el_mismo_documento(doc):
    antes = next(d for d in client.get("/documentos").json() if d["id"] == doc)
    historial_antes = client.get(f"/documentos/{doc}/historial").json()

    client.post(f"/documentos/{doc}/papelera", headers=_cabeceras())
    resp = client.post(f"/papelera/{doc}/restaurar", headers=_cabeceras())
    assert resp.status_code == 200, resp.text
    assert resp.json()["revision"] == antes["revision"]

    despues = next(d for d in client.get("/documentos").json() if d["id"] == doc)
    assert despues["revision"] == antes["revision"]
    assert client.get(f"/documentos/{doc}/historial").json() == historial_antes
    assert not _en_papelera(doc)
    assert not papelera.ruta_estado(DIR, doc).exists()


def test_restaurar_un_documento_que_no_esta_en_papelera_da_404(doc):
    resp = client.post(f"/papelera/{doc}/restaurar", headers=_cabeceras())
    assert resp.status_code == 404
    assert _en_galeria(doc)


def test_purga_desde_papelera_borra_todo_lo_del_documento(doc):
    client.post(f"/documentos/{doc}/papelera", headers=_cabeceras())

    resp = client.delete(f"/papelera/{doc}", headers=_cabeceras())
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}
    assert _quedan_rastros(doc) == []
    assert not _en_papelera(doc)


def test_delete_documento_vivo_sigue_borrando_definitivamente(doc):
    resp = client.delete(f"/documentos/{doc}", headers=_cabeceras())
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert _quedan_rastros(doc) == []


def test_purgar_un_documento_vivo_da_404_y_no_lo_toca(doc):
    resp = client.delete(f"/papelera/{doc}", headers=_cabeceras())
    assert resp.status_code == 404
    assert _en_galeria(doc)
    assert _quedan_rastros(doc)


def test_arranque_omite_los_documentos_en_papelera(doc, monkeypatch):
    otro = _subir()
    try:
        client.post(f"/documentos/{doc}/papelera", headers=_cabeceras())
        monkeypatch.setattr(documents, "_registry", {})
        monkeypatch.setattr(documents, "_files", {})
        monkeypatch.setattr(documents, "_revisiones", {})

        documents.recargar_documentos()

        assert doc not in documents._registry
        assert otro in documents._registry
        assert doc in papelera.ids_en_papelera(DIR)
    finally:
        _limpiar(otro)


@pytest.mark.parametrize("metodo,ruta", [
    ("post", "/documentos/{id}/papelera"),
    ("get", "/papelera"),
    ("post", "/papelera/{id}/restaurar"),
    ("delete", "/papelera/{id}"),
])
def test_rutas_de_papelera_exigen_token(doc, metodo, ruta):
    client.post(f"/documentos/{doc}/papelera", headers=_cabeceras())
    url = ruta.format(id=doc)

    sin_token = getattr(client, metodo)(url)
    assert sin_token.status_code == 401
    token_malo = getattr(client, metodo)(url, headers={"X-Forja-Token": "token-incorrecto"})
    assert token_malo.status_code == 401

    assert _en_papelera(doc)


def test_ids_desconocidos_o_con_ruta_dan_404(doc):
    assert client.post("/documentos/no-existe/papelera", headers=_cabeceras()).status_code == 404
    assert client.post("/papelera/no-existe/restaurar", headers=_cabeceras()).status_code == 404
    assert client.post("/papelera/..%2F..%2Fetc/restaurar", headers=_cabeceras()).status_code == 404
    assert client.delete("/papelera/..%2Fpasswd", headers=_cabeceras()).status_code == 404
    assert _en_galeria(doc)


def test_leaf_rechaza_ids_con_ruta_o_mal_formados():
    for malo in ("../algo", "a/b", "", "X" * 32, "0" * 31):
        assert papelera.ruta_archivo(DIR, malo) is None
        assert papelera.esta_en_papelera(DIR, malo) is False
        with pytest.raises(ValueError):
            papelera.ruta_estado(DIR, malo)


def test_leaf_estado_corrupto_es_error_explicito(doc):
    ruta = papelera.ruta_estado(DIR, doc)
    ruta.write_text("{no es json")
    try:
        with pytest.raises(ValueError):
            papelera.esta_en_papelera(DIR, doc)
    finally:
        ruta.unlink(missing_ok=True)


def test_mcp_papelera_valida_accion_y_id_sin_red():
    assert tools.papelera("vaciar")["error"] is True
    assert tools.papelera("restaurar")["error"] is True
    assert tools.papelera("listar", id="x")["error"] is True


# ---------------------------------------------------------------- MCP en vivo

def _backend_disponible() -> bool:
    try:
        httpx.get(f"{mcp_client.BASE_URL}/salud", timeout=2.0)
        return True
    except httpx.HTTPError:
        return False


requiere_vivo = pytest.mark.skipif(
    not _backend_disponible(),
    reason="requiere el proceso uvicorn en vivo del contenedor (localhost:8000)",
)

_CAJA_SCRIPT = "from build123d import Box\nresultado = Box(10, 10, 10)\n"


@requiere_vivo
def test_mcp_borrar_documento_y_papelera_en_vivo(token_vivo):
    creado = tools.ejecutar_script(_CAJA_SCRIPT, nombre="papelera_mcp")
    assert "error" not in creado, creado
    doc = creado["id"]
    try:
        borrado = tools.borrar_documento(doc)
        assert borrado.get("error") is not True, borrado
        assert doc not in [d["id"] for d in tools.listar_documentos()]
        assert doc in [d["id"] for d in tools.papelera("listar")]

        restaurado = tools.papelera("restaurar", id=doc)
        assert restaurado.get("error") is not True, restaurado
        assert doc in [d["id"] for d in tools.listar_documentos()]
    finally:
        mcp_client.eliminar_documento(doc)
        mcp_client.purgar_papelera(doc)
