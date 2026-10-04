"""Regressions found by the final Phase 5 review; storage is test-isolated."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import httpx
import pytest
from fastapi.testclient import TestClient

import auth
import documents
import parametros
import solids
from main import app
from mcp_server import client as mcp_client


SCRIPT = """from build123d import Box
PARAMETROS = {'lado': 4}
def construir(p):
    return {'pieza': Box(p['lado'], 5, 6)}
"""


def _post(client, url, body):
    response = client.post(url, json=body, headers={"X-Forja-Token": auth.obtener_token()})
    assert response.status_code == 200, response.text
    return response.json()


def test_note_snapshot_restore_preserves_current_parametric_state_and_names():
    client = TestClient(app)
    doc = _post(client, "/documentos/script", {"codigo": SCRIPT})
    doc_id = doc["id"]
    _post(client, f"/documentos/{doc_id}/notas", {
        "comentario": "revisar", "referencia": {"tipo": "punto", "punto": [0, 0, 0]},
    })
    history = client.get(f"/documentos/{doc_id}/historial").json()
    note_snapshot = next(item["id"] for item in history if item["mensaje"] == "crear nota")
    changed = _post(client, f"/documentos/{doc_id}/parametros", {"valores": {"lado": 8}})
    names_before = solids.ruta_solidos(doc_id).read_bytes()
    state_before = parametros.cargar(doc_id)

    restored = _post(client, f"/documentos/{doc_id}/restaurar", {"snapshot": note_snapshot})
    assert restored["volumen"] == pytest.approx(changed["volumen"])
    assert parametros.cargar(doc_id) == state_before
    assert solids.ruta_solidos(doc_id).read_bytes() == names_before
    assert client.get(f"/documentos/{doc_id}/notas").json()["resumen"] == []
    assert _post(client, f"/documentos/{doc_id}/parametros", {"valores": {"lado": 9}})["volumen"] == pytest.approx(270)


def test_script_replacement_serializes_with_parameter_apply(monkeypatch):
    client = TestClient(app)
    doc_id = _post(client, "/documentos/script", {"codigo": SCRIPT})["id"]
    entered, release, apply_started, state_read = Event(), Event(), Event(), Event()
    original_execute = documents.scripts_runner.ejecutar_script
    original_load = parametros.cargar
    replacement = SCRIPT.replace("'lado': 4", "'lado': 7").replace(
        "Box(p['lado'], 5, 6)", "Box(p['lado'], 10, 6)"
    )

    def execute(code, *args, **kwargs):
        if code == replacement and not release.is_set():
            entered.set()
            assert release.wait(15), "test did not release the script replacement"
        return original_execute(code, *args, **kwargs)

    def load(doc_id):
        state_read.set()
        return original_load(doc_id)

    def apply():
        apply_started.set()
        return _post(client, f"/documentos/{doc_id}/parametros", {"valores": {"lado": 9}})

    monkeypatch.setattr(documents.scripts_runner, "ejecutar_script", execute)
    monkeypatch.setattr(parametros, "cargar", load)
    with ThreadPoolExecutor(max_workers=2) as pool:
        update = pool.submit(_post, client, "/documentos/script", {
            "codigo": replacement, "documento_id": doc_id,
        })
        try:
            assert entered.wait(5)
            applied = pool.submit(apply)
            assert apply_started.wait(5)
            assert not state_read.wait(0.2), "apply read old state during script replacement"
        finally:
            release.set()
        update.result(timeout=20)
        assert applied.result(timeout=20)["volumen"] == pytest.approx(540)
    assert original_load(doc_id)["script"] == {"codigo": replacement}
    assert original_load(doc_id)["valores"] == {"lado": 9}


def test_replacing_script_without_timeout_drops_previous_override():
    client = TestClient(app)
    doc_id = _post(client, "/documentos/script", {"codigo": SCRIPT, "timeout": 60})["id"]
    _post(client, "/documentos/script", {"codigo": SCRIPT, "documento_id": doc_id})
    assert parametros.cargar(doc_id)["timeout"] is None


def test_captura_http_emits_image_and_caption(monkeypatch):
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
    )
    monkeypatch.setattr(mcp_client, "captura", lambda *a, **kw: {"png": png, "caption": "pieza"})
    headers = {
        "X-Forja-Token": auth.obtener_token(),
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-06-18",
    }
    with TestClient(app) as client:
        response = client.post("/mcp", headers=headers, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "captura", "arguments": {"id": "isolated"}},
        })
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert not result.get("isError"), result
    image, caption = result["content"]
    assert image["type"] == "image" and image["mimeType"] == "image/png"
    assert base64.b64decode(image["data"]) == png
    assert caption == {"type": "text", "text": "pieza"}


@pytest.mark.parametrize("name,args", [
    ("exportar", ("isolated", "3mf")),
    ("check_colisiones", ("isolated",)),
    ("percibir", ("isolated",)),
    ("check_fdm", ("isolated",)),
    ("captura", ("isolated",)),
])
@pytest.mark.parametrize("times_out", [False, True])
def test_heavy_mcp_timeout_budget_and_actionable_error(monkeypatch, name, args, times_out):
    requests = []

    def handle(request):
        requests.append(request)
        assert request.extensions["timeout"]["read"] == 95
        assert request.extensions["timeout"]["connect"] == 10
        if times_out:
            raise httpx.ReadTimeout("slow backend", request=request)
        if name == "captura":
            return httpx.Response(200, content=b"png", headers={"x-forja-captura": "pieza"})
        return httpx.Response(200, json={"ok": True})

    original_client = httpx.Client
    monkeypatch.setattr(mcp_client, "_HEAVY_TIMEOUT", 95)
    monkeypatch.setattr(mcp_client.httpx, "Client", lambda **kw: original_client(
        **kw, transport=httpx.MockTransport(handle)
    ))
    result = getattr(mcp_client, name)(*args)
    assert len(requests) == 1  # no retries, including export side effects
    if times_out:
        assert result["error"] is True
        assert name in result["mensaje"] and "95 s" in result["mensaje"]
        assert "FORJA_HEAVY_TIMEOUT" in result["mensaje"]
    else:
        assert "error" not in result


def test_anonymous_notes_requests_neither_grow_the_lock_table_nor_500():
    """Phase 6.0 findings 1 and 3 (notes routes are token-free by design):
    `parametros.bloqueo` *creates and keeps* one lock per id it is asked
    about, so validating the id only after taking the lock let any LAN
    client grow `parametros._bloqueos` forever with ids that never named a
    document; and an over-long id made `Path.exists()` raise `OSError`
    ("File name too long"), a 500 where the contract is 404.

    The over-long shapes cover both units the limit is expressed in: ASCII
    characters, and UTF-8 *bytes* — `"🔧" * 100` is 100 characters but 400
    bytes, so a character-only clamp still handed the filesystem probe an id
    past the ~255-byte limit of a path component and answered 500 (Phase 6.2
    fix round)."""
    client = TestClient(app)
    desconocido, larguisimo, multibyte = "0" * 32, "a" * 4096, "🔧" * 100
    antes = len(parametros._bloqueos)
    for doc_id in (desconocido, larguisimo, multibyte):
        assert client.get(f"/documentos/{doc_id}/notas").status_code == 404
        assert client.delete(f"/documentos/{doc_id}/notas/nada").status_code == 404
        assert client.patch(
            f"/documentos/{doc_id}/notas/nada", json={"visible": False}
        ).status_code == 404
        assert client.delete(f"/documentos/{doc_id}/trazos/nada").status_code == 404
        assert client.patch(
            f"/documentos/{doc_id}/trazos/nada", json={"visible": False}
        ).status_code == 404
    assert larguisimo not in parametros._bloqueos
    assert len(parametros._bloqueos) == antes
