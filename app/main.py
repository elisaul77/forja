"""Forja FastAPI entrypoint (health check, document API, web viewer, and
the MCP Streamable HTTP endpoint at `/mcp`, ADR-0008)."""
from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from importlib import metadata
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.routing import Route

import assembly_routes
import bridges
import documents
import eventos
import notes
import perfiles
from mcp_server.http_app import app_mcp_http

WEB_DIR = Path(__file__).resolve().parent / "web"



@contextlib.asynccontextmanager
async def _ciclo_de_vida(_app: FastAPI) -> AsyncIterator[None]:
    """Rebuild the document registry from disk (Phase 4: a restart must not
    orphan notes attached to a document that already exists under
    `DOCUMENTOS_DIR`), then run the MCP HTTP session manager for the whole
    lifetime of the app (ADR-0008: the SDK requires its task group to be
    running before `/mcp` can serve a request)."""
    documents.recargar_documentos()
    async with app_mcp_http.ciclo_de_vida():
        yield


app = FastAPI(title="Forja", lifespan=_ciclo_de_vida)
app.include_router(documents.router)
app.include_router(notes.router)
app.include_router(assembly_routes.router)
app.include_router(bridges.router)
app.include_router(perfiles.router)


@app.get("/eventos")
async def eventos_en_vivo() -> StreamingResponse:
    try:
        suscriptor = eventos.hub.suscribir()
    except RuntimeError:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="demasiadas conexiones en vivo")
    return StreamingResponse(
        eventos.hub.flujo(estado_inicial=lambda: [
            {"id": doc_id, "revision": rev}
            for doc_id, rev in documents._revisiones.copy().items()
            if doc_id in documents._registry
        ], suscriptor=suscriptor),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
# A plain Route (not a Mount) so `POST /mcp` is served as-is, without
# Starlette's `/mcp` -> `/mcp/` redirect. POST only (Phase 5C, ADR-0009):
# stateless mode offers no standalone GET SSE stream nor a session to
# DELETE, so Starlette answers 405 for any other method before the token
# check or the SDK ever run.
app.router.routes.append(Route("/mcp", endpoint=app_mcp_http, methods=["POST"]))


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(WEB_DIR / "index.html"))


def _version(package: str) -> str | None:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return None


@app.get("/salud")
def salud() -> dict:
    versions = {
        "build123d": _version("build123d"),
        "ocp": _version("cadquery-ocp"),
        "manifold3d": _version("manifold3d"),
        "trimesh": _version("trimesh"),
        "fcl": _version("python-fcl"),
    }
    return {"ok": all(v is not None for v in versions.values()), **versions}
