"""Live-view contracts: stable revision, bounded SSE and mesh identity."""
from __future__ import annotations

import asyncio
import io

from fastapi.testclient import TestClient

import eventos
import revision
from kernel import b123d_kernel
from main import app


def test_step_header_timestamp_does_not_change_revision():
    one = b"ISO-10303-21;\nHEADER;\nFILE_NAME('a','2026-01-01');\nFILE_DESCRIPTION(('x'),'1');\nENDSEC;\nDATA;\n#1=CARTESIAN_POINT('',(1.,2.,3.));\nENDSEC;"
    two = one.replace(b"2026-01-01", b"2026-10-03").replace(b"('x')", b"('y')")
    assert revision.calcular(one, True) == revision.calcular(two, True)
    assert revision.calcular(one, True) != revision.calcular(one.replace(b"3.", b"4."), True)


def test_sse_hello_then_document_event_and_disconnect():
    async def run():
        hub = eventos.Hub(cola_maxima=2)
        stream = hub.flujo(latido_s=1, estado_inicial=lambda: [{"id": "a", "revision": "r1"}])
        assert "retry:" in await anext(stream)
        assert '"revision": "r1"' in await anext(stream)
        hub.publicar("documento_actualizado", "a", "r2", ["geometria"])
        message = await asyncio.wait_for(anext(stream), timeout=1)
        assert "event: documento_actualizado" in message
        assert '"cambios": ["geometria"]' in message
        await stream.aclose()
        assert hub.cantidad() == 0
    asyncio.run(run())


def test_sse_queue_overflow_asks_browser_to_resynchronise():
    async def run():
        hub = eventos.Hub(cola_maxima=1)
        stream = hub.flujo(latido_s=1)
        await anext(stream)
        hub.publicar("documento_actualizado", "a", "r1")
        hub.publicar("documento_actualizado", "a", "r2")
        await asyncio.sleep(0)
        assert "event: resincronizar" in await asyncio.wait_for(anext(stream), timeout=1)
        await stream.aclose()
    asyncio.run(run())


def test_mesh_header_matches_document_revision(tmp_path):
    source = tmp_path / "cubo.step"
    b123d_kernel.export_to_step(b123d_kernel.make_box(6, 6, 6), source)
    with TestClient(app) as client:
        created = client.post("/documentos", files={"file": ("cubo.step", io.BytesIO(source.read_bytes()), "application/step")})
        assert created.status_code == 200
        doc_id = created.json()["id"]
        mesh = client.get(f"/documentos/{doc_id}/malla?componentes=true")
        assert mesh.status_code == 200
        assert mesh.headers["x-forja-revision"] == created.json()["revision"]
        assert client.get(f"/documentos/{doc_id}").json()["revision"] == created.json()["revision"]
