"""Real MCP -> loopback HTTP integration on an isolated temporary server.

No production tokens, documents or live mutation routes are used.
"""

import hashlib
import json
import os
import socket
import subprocess
import time

import httpx
import trimesh


def test_shared_workspace_over_real_mcp_http(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "FORJA_DATA_DIR": str(tmp_path / "data"),
        "FORJA_SECRET_FILE": str(tmp_path / "missing" / "token"),
        "FORJA_TOKEN": "isolated-design-test-only",
        "FORJA_HTTP_BASE_URL": base,
        "FORJA_PUBLIC_URL": base,
    }
    (tmp_path / "data").mkdir()
    with (tmp_path / "server.log").open("w") as log:
        proc = subprocess.Popen(
            [
                "python",
                "-m",
                "uvicorn",
                "main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            env=env,
            stdout=log,
            stderr=log,
        )
        try:
            with httpx.Client(base_url=base, timeout=30) as c:
                for _ in range(100):
                    try:
                        if c.get("/salud").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    if proc.poll() is not None:
                        raise AssertionError((tmp_path / "server.log").read_text())
                    time.sleep(0.1)
                headers = {
                    "X-Forja-Token": "isolated-design-test-only",
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": "2025-06-18",
                }

                def rpc(method, params):
                    r = c.post(
                        "/mcp",
                        headers=headers,
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": method,
                            "params": params,
                        },
                    )
                    assert r.status_code == 200, r.text
                    body = r.json()
                    assert "error" not in body, body
                    return body["result"]

                def call(name, args):
                    result = rpc("tools/call", {"name": name, "arguments": args})
                    assert not result.get("isError"), result
                    data = result.get("structuredContent") or json.loads(
                        result["content"][0]["text"]
                    )
                    assert not data.get("error"), data
                    return data

                rpc(
                    "initialize",
                    {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "workspace-test", "version": "1"},
                    },
                )
                assert len(rpc("tools/list", {})["tools"]) == 34
                uploaded = c.post(
                    "/documentos",
                    files={
                        "file": (
                            "mcp-part.stl",
                            trimesh.creation.box(extents=[10, 10, 10]).export(
                                file_type="stl"
                            ),
                        )
                    },
                )
                assert uploaded.status_code == 200, uploaded.text
                doc = uploaded.json()["id"]
                intent = c.post(f"/documentos/{doc}/design/intents", json={"text": "Mover en X"})
                assert intent.status_code == 200, intent.text
                intent_id = intent.json()["created_intent"]
                context = call("contexto_diseno", {"id": doc})
                piece = context["pieces"][0]["name"]
                assert context["intents_view"][0]["attention"] == "waiting_agent"
                context["design"]["objective"] = "Flujo real MCP"
                context = call(
                    "guardar_diseno",
                    {
                        "id": doc,
                        **{
                            k: context[k]
                            for k in ["revision", "state_revision", "design"]
                        },
                    },
                )
                report = call(
                    "consultar_diseno",
                    {
                        "id": doc,
                        "revision": context["revision"],
                        "queries": [{"kind": "inspect", "piece": piece}],
                    },
                )
                assert report["results"][0]["result"]["closed"]
                answered = call(
                    "responder_indicacion",
                    {
                        "id": doc,
                        "indicacion": intent_id,
                        "interpretacion": "Desplazar 1 mm en X",
                    },
                )
                assert answered["intents_view"][0]["attention"] == "understood"
                context = answered  # state_revision moved with the reply
                candidate = call(
                    "preparar_candidato",
                    {
                        "id": doc,
                        "revision": context["revision"],
                        "state_revision": context["state_revision"],
                        "request_id": "wire",
                        "indicacion": intent_id,
                        "resumen": "Desplaza 1 mm en X",
                        "operations": [
                            {
                                "kind": "transform",
                                "piece": piece,
                                "translation": [1, 0, 0],
                            }
                        ],
                    },
                )
                applied = call(
                    "candidato_diseno",
                    {"id": doc, "accion": "aplicar", "candidato": candidate["id"]},
                )
                assert applied["state"] == "applied"
                exported = call("exportar_diseno", {"id": doc, "formato": "3mf"})
                downloaded = c.get(exported["download_url"])
                assert (
                    hashlib.sha256(downloaded.content).hexdigest() == exported["sha256"]
                )
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
