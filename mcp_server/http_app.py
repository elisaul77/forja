"""MCP over Streamable HTTP, served in-process by the FastAPI app (ADR-0008).

Mounted by `app/main.py` as a plain route at `/mcp` on the existing port
(container 8000 -> host 8710), so the Forja tools survive a container
restart: there is no long-lived per-session process to lose (the stdio
transport, `docker exec -i forja python -m mcp_server.server`, remains as a
fallback).

Design points (see ADR-0008 for the full reasoning):

- **Single source of truth**: the server is built with
  `mcp_server.server.crear_servidor()`, the same function stdio uses.
- **Lifespan**: a `StreamableHTTPSessionManager` can only `run()` once, so
  `ciclo_de_vida()` builds a *fresh* server + manager each time the
  FastAPI lifespan starts (uvicorn start, or each `with TestClient(app)`
  block in tests) and runs the manager for the lifespan's duration.
- **Stateless + JSON responses**: no `mcp-session-id` is ever issued, so a
  restart can't invalidate a session the client is holding; every POST is
  self-contained and answered with a plain `application/json` body.
- **No deadlock**: the tools in `mcp_server/tools.py` are sync functions
  doing loopback HTTP to this same uvicorn process (ADR-0005). The SDK runs
  sync tools on a worker thread (`anyio.to_thread.run_sync`), so the event
  loop stays free to serve that loopback request.
- **Auth**: the endpoint is LAN-reachable, so every request needs the same
  shared token as the write routes (`app/auth.py`), either as
  `X-Forja-Token: <token>` or `Authorization: Bearer <token>`; anything
  else gets 401 before reaching the MCP layer. The SDK's Host/Origin
  DNS-rebinding check is disabled on purpose: its auto-enabled allowlist is
  loopback-only and would reject the LAN address; the token is the guard.
"""
from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

import auth
from mcp_server.server import crear_servidor


def _token_de_cabeceras(headers: Headers) -> str | None:
    """`X-Forja-Token` wins; otherwise `Authorization: Bearer <token>`."""
    directo = headers.get("x-forja-token")
    if directo:
        return directo
    autorizacion = headers.get("authorization", "")
    esquema, _, valor = autorizacion.partition(" ")
    if esquema.lower() == "bearer" and valor.strip():
        return valor.strip()
    return None


class AppMcpHttp:
    """ASGI endpoint for `/mcp`: token check, then the SDK's session manager."""

    def __init__(self) -> None:
        self._servidor: MCPServer | None = None

    @contextlib.asynccontextmanager
    async def ciclo_de_vida(self) -> AsyncIterator[None]:
        servidor = crear_servidor()
        # Called only to create the session manager; the Starlette app it
        # returns (which would route `/mcp` itself) is not used -- this
        # class is mounted directly as the `/mcp` route instead.
        servidor.streamable_http_app(
            stateless_http=True,
            json_response=True,
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )
        async with servidor.session_manager.run():
            self._servidor = servidor
            try:
                yield
            finally:
                self._servidor = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        if not auth.token_coincide(_token_de_cabeceras(Headers(scope=scope))):
            respuesta = JSONResponse(
                {"detail": "token requerido"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await respuesta(scope, receive, send)
            return
        if self._servidor is None:
            await JSONResponse({"detail": "MCP no iniciado"}, status_code=503)(scope, receive, send)
            return
        await self._servidor.session_manager.handle_request(scope, receive, send)


app_mcp_http = AppMcpHttp()
