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
from starlette.concurrency import run_in_threadpool
from starlette.routing import Route

import assembly_routes
import bridges
import cupon
import documents
import eventos
import notes
import perfiles
import ramas
import fusion
import historial_grafo
import versioning
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
app.include_router(cupon.router)
app.include_router(fusion.router)
app.include_router(historial_grafo.router)
app.include_router(ramas.router)


class _OrigenDelCambio:
    """Pure ASGI middleware (G1): tags the request with who caused it, so
    the history commit gets a neutral author. ``X-Forja-Origen: agente``
    (sent by the MCP client) or ``humano`` wins; otherwise a browser
    (``Sec-Fetch-Site`` present) is ``humano``; anything else ``forja``.

    G2: for mutating requests it also collects the snapshots taken while
    handling it and, when the response starts with a status < 400, records
    the complete post-change state on each document's active branch
    BEFORE the response leaves (so the client's next request already sees
    the step)."""

    def __init__(self, app_asgi) -> None:
        self.app = app_asgi

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        cabeceras = dict(scope.get("headers") or [])
        origen = cabeceras.get(b"x-forja-origen", b"").decode("latin-1").strip().lower()
        if origen not in ("agente", "humano"):
            origen = "humano" if b"sec-fetch-site" in cabeceras else "forja"
        marca = versioning.autor_actual.set(origen)
        if scope.get("method") in ("GET", "HEAD", "OPTIONS"):
            try:
                await self.app(scope, receive, send)
            finally:
                versioning.autor_actual.reset(marca)
            return
        pendientes: list = []
        marca_p = versioning.cambios_pendientes.set(pendientes)

        async def enviar(mensaje) -> None:
            if mensaje["type"] == "http.response.start" and pendientes:
                lote = pendientes[:]
                pendientes.clear()
                if mensaje.get("status", 500) < 400:
                    await run_in_threadpool(ramas.registrar_pendientes, lote)
            await send(mensaje)

        try:
            await self.app(scope, receive, enviar)
        finally:
            versioning.cambios_pendientes.reset(marca_p)
            versioning.autor_actual.reset(marca)


app.add_middleware(_OrigenDelCambio)


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
