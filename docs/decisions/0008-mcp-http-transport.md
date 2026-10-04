# ADR 0008: MCP over Streamable HTTP, served in-process at `/mcp`

Date: 2026-09-27
Status: Accepted
Supersedes: ADR-0004 and ADR-0005 in part (MCP **transport** only)

## Context

ADR-0004/0005 run the MCP server over stdio, as a per-session process
started by `docker exec -i forja python -m mcp_server.server`. Real use
(feedback item 7b, reproduced twice in one session) showed the cost: any
`docker compose restart forja` (routine after a backend change, since `app/`
is bind-mounted) kills that process, and the Claude Code session loses every
Forja tool until the user reconnects the server by hand. The only
workaround was plain REST with `curl` (SKILL.md, "Respaldo por HTTP").

The pinned `mcp` 2.2.0 SDK (verified inside the container) ships
`MCPServer.streamable_http_app(...)`, which builds a
`StreamableHTTPSessionManager` (exposed as `MCPServer.session_manager`)
whose `run()` must be active -- as an async context manager, once per
instance -- before any request is served. It supports a **stateless** mode
(no `mcp-session-id`) and plain JSON responses. Sync tool functions are run
on a worker thread (`anyio.to_thread.run_sync`). No new dependency is
needed (`starlette`, `anyio`, `sse-starlette` are already present as `mcp`/
`fastapi` dependencies).

## Decision

1. **Transport**: MCP Streamable HTTP, **stateless** with JSON responses.
   Stateless means the server never issues a session id, so a container
   restart cannot invalidate anything the client holds: the request in
   flight during the restart fails (connection refused), the next one
   succeeds -- with or without a new `initialize`. The tools need no
   server-to-client requests (sampling/elicitation), which is the only
   thing stateless mode gives up.
2. **Serving: in-process, same port.** The endpoint is a plain Starlette
   `Route("/mcp")` appended to the existing FastAPI app (`app/main.py`), on
   the existing port 8000 (host 8710) -- one process, no new port, no
   second uvicorn. A `Route`, not a `Mount`, so `POST /mcp` is not
   redirected to `/mcp/`. FastAPI's startup hook became a `lifespan` that
   reloads documents (as before) and then runs the session manager for the
   app's lifetime. Because `run()` is once-per-instance,
   `mcp_server/http_app.py` builds a fresh server per lifespan (uvicorn
   runs it once; tests may open several `TestClient` contexts). Rejected:
   a second uvicorn process/port -- more moving parts (a second entrypoint,
   port and healthcheck) for no gain once the lifespan requirement is met.
3. **Single source of truth**: `mcp_server/server.py` exposes
   `crear_servidor()`, which builds the `MCPServer` and registers the 14
   tools; stdio (`mcp = crear_servidor()`, `python -m mcp_server.server`)
   and HTTP both call it. Stdio stays as a fallback.
4. **Backend path unchanged (ADR-0005)**: tools still go through
   `mcp_server/client.py`'s loopback HTTP, also when served in-process. No
   deadlock: the tools are sync, so the SDK runs each on a worker thread and
   the event loop stays free to answer the loopback request. Verified with
   real requests (`estado`, `ejecutar_script` + `percibir`) against the
   live container.
5. **Auth**: `/mcp` is LAN-reachable, so every request must carry the same
   shared token as the write routes (`app/auth.py`, now with a
   `token_coincide()` helper shared by both), as `X-Forja-Token: <token>` or
   `Authorization: Bearer <token>`; otherwise 401 before the MCP layer. The
   SDK's Host/Origin DNS-rebinding check is explicitly disabled: its
   auto-enabled allowlist is loopback-only and would reject the LAN
   address; the token is the guard. Registration:

   ```
   claude mcp add --transport http forja-http http://localhost:8710/mcp --header "X-Forja-Token: <token>"
   ```

## Consequences

- A container restart no longer drops the Forja tools; at most one call
  fails while uvicorn restarts (~7 s measured).
- Every write/read of `/mcp` costs a token check; the token lives in the
  client's MCP config (`~/.claude.json`), so rotating it (deleting
  `.forja_token` or changing `FORJA_TOKEN`) means re-registering.
- No server-initiated requests or resumable streams (stateless). If a
  future tool needs elicitation/sampling, revisit with a stateful manager
  plus an event store.
- `GET /mcp` (standalone SSE stream) is not offered in stateless mode.
