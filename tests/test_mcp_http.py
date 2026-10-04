"""Phase 5D: MCP over Streamable HTTP at `/mcp` (ADR-0008).

Two layers:

- In-process (`TestClient` inside its context manager, so the FastAPI
  lifespan -- and with it the MCP session manager -- actually runs): auth
  (401 without/with a wrong token, both header styles accepted),
  `initialize` + `tools/list` returning exactly the 21 tools (fdm-B adds `cupon`, G2 adds `rama`), GET -> 405.
- Live (`http://localhost:8000/mcp`, the container's own uvicorn): real tool
  roundtrips (`estado`, `percibir` on a small script document, and the whole
  Phase-6 assembly sequence plus `puentes`). This is the path Claude Code
  uses, and the one that proves the in-process mounting doesn't deadlock:
  the sync tools do loopback HTTP back to the very uvicorn process serving
  the MCP request.

The container-restart check can't run from inside the container; it is
done from the host with curl (see the Phase 5D report / ADR-0008).
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

import auth
from main import app
from mcp_server import client, tools

_ACCEPT = "application/json, text/event-stream"
_VERSION = "2025-06-18"
_TOOLS = {
    "estado",
    "listar_documentos",
    "abrir_archivo",
    "resumen_documento",
    "ejecutar_script",
    "exportar",
    "captura",
    "check_colisiones",
    "cupon",
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
    "rama",
}


def _init_body() -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": _VERSION,
            "capabilities": {},
            "clientInfo": {"name": "forja-tests", "version": "0"},
        },
    }


def _rpc(post, metodo: str, params: dict[str, Any], id_: int, headers: dict[str, str]) -> dict:
    resp = post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": id_, "method": metodo, "params": params},
        headers={**headers, "MCP-Protocol-Version": _VERSION},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "error" not in data, data
    return data["result"]


def _sesion(post, headers: dict[str, str]) -> None:
    resp = post("/mcp", json=_init_body(), headers=headers)
    assert resp.status_code == 200, resp.text
    resultado = resp.json()["result"]
    assert resultado["serverInfo"]["name"] == "forja"
    notif = post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={**headers, "MCP-Protocol-Version": _VERSION},
    )
    assert notif.status_code in (200, 202), notif.text


def _texto_herramienta(resultado: dict) -> Any:
    assert not resultado.get("isError"), resultado
    return json.loads(resultado["content"][0]["text"])


# ---------------------------------------------------------------- in-process


@pytest.fixture
def cliente():
    with TestClient(app) as c:
        yield c


def test_mcp_http_sin_token_401(cliente):
    resp = cliente.post("/mcp", json=_init_body(), headers={"Accept": _ACCEPT})
    assert resp.status_code == 401


def test_mcp_http_token_incorrecto_401(cliente):
    for cabecera in ({"X-Forja-Token": "no-es"}, {"Authorization": "Bearer no-es"}):
        resp = cliente.post("/mcp", json=_init_body(), headers={"Accept": _ACCEPT, **cabecera})
        assert resp.status_code == 401, cabecera


@pytest.mark.parametrize("estilo", ["x-forja-token", "bearer"])
def test_mcp_http_initialize_y_lista_20_herramientas(cliente, estilo):
    token = auth.obtener_token()
    cabecera = {"X-Forja-Token": token} if estilo == "x-forja-token" else {"Authorization": f"Bearer {token}"}
    headers = {"Accept": _ACCEPT, **cabecera}
    _sesion(cliente.post, headers)
    resultado = _rpc(cliente.post, "tools/list", {}, 2, headers)
    assert {t["name"] for t in resultado["tools"]} == _TOOLS


def test_mcp_http_get_con_token_405(cliente):
    """Phase 5C (ADR-0009): `/mcp` is POST-only -- stateless mode has no GET
    SSE stream and no session to DELETE."""
    headers = {"Accept": _ACCEPT, "X-Forja-Token": auth.obtener_token()}
    assert cliente.get("/mcp", headers=headers).status_code == 405
    assert cliente.delete("/mcp", headers=headers).status_code == 405


def test_mcp_http_sin_session_id_stateless(cliente):
    """Stateless by design (ADR-0008): no session id to lose on restart."""
    headers = {"Accept": _ACCEPT, "X-Forja-Token": auth.obtener_token()}
    resp = cliente.post("/mcp", json=_init_body(), headers=headers)
    assert resp.status_code == 200
    assert "mcp-session-id" not in resp.headers


# ---------------------------------------------------------------- live backend


def _backend_disponible() -> bool:
    try:
        return httpx.post(f"{client.BASE_URL}/mcp", json={}, timeout=2.0).status_code == 401
    except httpx.HTTPError:
        return False


vivo = pytest.mark.skipif(
    not _backend_disponible(),
    reason="requiere el uvicorn en vivo del contenedor con /mcp (localhost:8000)",
)


@pytest.fixture
def vivo_post(token_vivo):
    # Live backend = separate process with the real token (see conftest.py);
    # `token_vivo` also pins it for the loopback `client` calls of this test.
    headers = {"Accept": _ACCEPT, "X-Forja-Token": token_vivo}
    with httpx.Client(base_url=client.BASE_URL, timeout=60.0) as c:
        _sesion(c.post, headers)
        yield c.post, headers


@vivo
def test_mcp_http_vivo_estado(vivo_post):
    post, headers = vivo_post
    data = _texto_herramienta(_rpc(post, "tools/call", {"name": "estado", "arguments": {}}, 3, headers))
    assert data["ok"] is True
    assert isinstance(data["num_documentos"], int)


@vivo
def test_mcp_http_vivo_percibir_documento_pequeno(vivo_post):
    post, headers = vivo_post
    script = (
        "from build123d import Box, Pos\n"
        "resultado = {'mesa': Pos(0, 0, 5) * Box(20, 20, 10), 'vaso': Pos(0, 0, 13) * Box(4, 4, 6)}\n"
    )
    creado = _texto_herramienta(
        _rpc(
            post,
            "tools/call",
            {"name": "ejecutar_script", "arguments": {"codigo": script, "nombre": "mcp_http_test"}},
            4,
            headers,
        )
    )
    try:
        assert "error" not in creado, creado
        data = _texto_herramienta(
            _rpc(post, "tools/call", {"name": "percibir", "arguments": {"id": creado["id"]}}, 5, headers)
        )
        assert "vaso" in data["texto"] and "mesa" in data["texto"]
    finally:
        if "id" in creado:
            client.eliminar_documento(creado["id"])
    assert creado["id"] not in {d["id"] for d in tools.listar_documentos()}


@vivo
def test_mcp_http_vivo_ensamble_y_puentes(vivo_post):
    """Phase 6.4 acceptance sequence over the real transport: create ->
    define -> pose -> history -> clear, plus the bridge readiness tool. This
    is the same sequence an agent runs through `mcp__forja__*`, so it proves
    the tools survive registration, JSON-schema generation and the loopback
    roundtrip, not just the Python call."""
    post, headers = vivo_post
    script = (
        "from build123d import Box, Pos\n"
        "resultado = {'base': Box(20, 20, 4), 'brazo': Pos(14, 0, 4) * Box(6, 6, 16)}\n"
    )
    creado = _texto_herramienta(
        _rpc(
            post,
            "tools/call",
            {"name": "ejecutar_script", "arguments": {"codigo": script, "nombre": "mcp_http_ensamble"}},
            6,
            headers,
        )
    )
    try:
        assert "error" not in creado, creado
        doc_id = creado["id"]
        resumen = _texto_herramienta(
            _rpc(post, "tools/call", {"name": "resumen_documento", "arguments": {"id": doc_id}}, 7, headers)
        )
        assert set(resumen) >= {"id", "nombre", "bbox", "volumen", "solidos", "valido"}

        definido = _texto_herramienta(
            _rpc(
                post,
                "tools/call",
                {
                    "name": "ensamble",
                    "arguments": {
                        "id": doc_id,
                        "articulaciones": [
                            {
                                "id": "bisagra", "tipo": "giro", "padre": "base", "hijo": "brazo",
                                "origen": [14, 0, 4], "eje": [0, 1, 0], "limites": [-90, 90], "valor": 0,
                            }
                        ],
                    },
                },
                8,
                headers,
            )
        )
        assert definido["piezas"] == ["base", "brazo"], definido

        posado = _texto_herramienta(
            _rpc(
                post,
                "tools/call",
                {"name": "ensamble", "arguments": {"id": doc_id, "valores": {"bisagra": 60}}},
                9,
                headers,
            )
        )
        assert posado["valores"] == {"bisagra": 60.0}, posado
        assert posado["bbox"] != resumen["bbox"]

        historial = _rpc(post, "tools/call", {"name": "leer_historial", "arguments": {"id": doc_id}}, 10, headers)
        # A list comes back as one content block per entry (the wire shape of
        # every list-returning tool), so the messages are read block by block.
        mensajes = [json.loads(bloque["text"])["mensaje"] for bloque in historial["content"]]
        assert "antes de definir articulaciones" in mensajes, mensajes
        assert "antes de mover articulaciones" in mensajes, mensajes

        quitado = _texto_herramienta(
            _rpc(post, "tools/call", {"name": "ensamble", "arguments": {"id": doc_id, "quitar": True}}, 11, headers)
        )
        assert quitado["articulaciones"] == [] and quitado["piezas"] == ["base", "brazo"]

        puentes = _texto_herramienta(
            _rpc(post, "tools/call", {"name": "puentes", "arguments": {}}, 12, headers)
        )
        assert set(puentes) == {"suspension", "blender", "kybercore"}, puentes
        assert puentes["suspension"]["habilitado"] is True
    finally:
        if "id" in creado:
            client.eliminar_documento(creado["id"])
    assert creado["id"] not in {d["id"] for d in tools.listar_documentos()}
