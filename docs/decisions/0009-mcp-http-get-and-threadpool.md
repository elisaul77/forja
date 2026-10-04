# ADR 0009: `/mcp` is POST-only; MCP tools share the anyio threadpool

Date: 2026-09-27
Status: Accepted
Amends: ADR-0008 (does not edit it -- ADRs are immutable)

## Context

The Phase 5D review found two loose ends in ADR-0008's in-process MCP
endpoint:

1. `/mcp` was routed for `GET`, `POST` and `DELETE`. In stateless mode the
   SDK offers no standalone `GET` SSE stream and there is no session to
   `DELETE`, yet an authenticated `GET` reached the SDK and got whatever
   it chose to answer, instead of a plain "method not allowed".
2. The MCP tools are sync functions that the SDK runs on a worker thread
   (`anyio.to_thread.run_sync`), and each one does loopback HTTP to a sync
   FastAPI route that ALSO runs on a worker thread. Both draw from the
   same default anyio limiter: **40 tokens** per process.

## Decision

1. `app/main.py` routes `/mcp` for `POST` only. Starlette answers any
   other method with **405** before the token check or the SDK run
   (tested: `GET /mcp` with a valid token -> 405). The live-backend probe
   in `tests/test_mcp_http.py` switched from "GET -> 401" to "POST without
   token -> 401".
2. The threadpool is left at anyio's default (40). Each in-flight MCP tool
   call holds 2 tokens (the tool thread + the loopback route thread), so
   ~20 concurrent tool calls saturate it and further calls queue. Worst
   case, 40+ simultaneous tool calls could each hold a token while their
   loopback route waits for one: a starvation stall that only clears when
   the client-side httpx timeouts (30 s) fire. Single-user Forja (one
   agent, sequential tool calls, plus the web viewer) is nowhere near it. If that ever becomes real
   (several agents on one Forja), raise it at startup with
   `anyio.to_thread.current_default_thread_limiter().total_tokens = N`
   in the lifespan -- not done now (YAGNI).

## Consequences

- MCP clients that probe with `GET` get a clean 405 and fall back to
  plain POST (the Streamable HTTP spec allows 405 for servers without a
  GET stream).
- Long-running tools (a 30 s `ejecutar_script`, a `parametros` re-run)
  hold their 2 tokens for their whole duration; the web viewer's own sync
  routes share the same pool.
