# ADR 0005: MCP-backend HTTP boundary, real process model, and shared-token auth

Date: 2026-09-27
Status: Accepted
Supersedes: ADR-0004 in part

## Context

ADR-0004 decided that the MCP server runs *inside* the `forja` container and
described it as sharing "one Python environment... with no HTTP hop"
between the MCP tools and the FastAPI app. Building Phase 3
(`mcp_server/client.py`, `mcp_server/__init__.py`) surfaced the actual
constraint ADR-0004 did not name: `docker exec -i forja python -m
mcp_server.server` starts a **second OS process**, distinct from the
`uvicorn` process that is the container's PID 1 entrypoint. The in-memory
document registry (`app/documents.py`'s `_registry`/`_files` dicts) lives
in the uvicorn process's memory only. Two processes inside one container do
**not** share that memory just because they share a filesystem and Python
environment — so the MCP tools *do* cross an HTTP hop to
`http://localhost:8000`, exactly the network hop ADR-0004's Option (a)
(rejected) was trying to avoid, just confined to loopback instead of the
host network.

Separately, the Phase 3 review flagged that `POST /documentos/script`
(runs arbitrary user-supplied Python via `scripts_runner`) and `POST
/documentos/desde_ruta` (reads an arbitrary path off disk into the
registry) were reachable by anyone on the LAN, because the `forja` service
binds `0.0.0.0:8710` with no authentication. Binding loopback only was
considered and rejected: the user opens the web viewer from his phone at
`192.168.1.50:8710`, so the HTTP port must stay LAN-reachable; only the two
dangerous routes need to be closed off, not the whole API.

## Decision

1. **Process model** (clarifying ADR-0004, not reversing it): one Docker
   container, **two OS processes**:
   - The `uvicorn` process (container PID 1, `main:app`) owns the
     in-memory document registry and the `app/` modules — it is the single
     source of truth for what documents exist.
   - The MCP stdio server (`python -m mcp_server.server`) is started
     per-session by `docker exec -i forja ...`, as a short-lived process
     distinct from PID 1. `mcp_server/tools.py` never imports
     `app/documents.py` in-process; every tool call goes through
     `mcp_server/client.py` over loopback HTTP to `http://localhost:8000`
     (`FORJA_HTTP_BASE_URL`, overridable for tests). This is what keeps
     MCP-created documents visible in `GET /documentos` and the web viewer,
     and vice versa — ADR-0004's "in-process, no HTTP hop" language
     described the shared Python *environment* (one `requirements.txt`,
     one image), not a shared memory space; that part of ADR-0004 is
     superseded by this ADR's explicit two-process model.
   - `mcp_server/client.py` and `app/documents.py` both live under `/app`
     on `PYTHONPATH` (`Dockerfile`), so they can share a plain top-level
     module (`app/auth.py`) even though they run as separate processes;
     that module reads the same env var/file, it is not shared memory.

2. **Auth boundary**: rather than binding loopback (which would also cut
   off the phone-based web UI), only the code-execution/arbitrary-read
   routes require a shared secret:
   - `app/auth.py` exposes a `requiere_token` FastAPI dependency, applied
     to `POST /documentos/script` and `POST /documentos/desde_ruta` only.
     Every GET route, `/salud`, static files, and the web UI's `POST
     /documentos` upload stay open (LAN-reachable, no token) — the upload
     route keeps its existing size cap as its only guard.
   - Token source: env `FORJA_TOKEN` if set; otherwise a
     `secrets.token_urlsafe(32)` generated on first use and persisted to
     `/data/documentos/.forja_token` (mode 0600, inside the existing
     bind-mounted `documentos_data` volume, already gitignored) so a
     container restart does not invalidate a token already handed to the
     MCP client.
   - `mcp_server/client.py` imports the same `app/auth.py` module (both
     under `/app` on `PYTHONPATH`) to read the token and send it as
     `X-Forja-Token` on the two calls that hit protected routes
     (`ejecutar_script`, `abrir_archivo`). Comparison uses
     `hmac.compare_digest` (constant-time). Missing/wrong token -> `401
     {"detail": "token requerido"}`.

## Consequences

- The registered `claude mcp add` command is unchanged (`docker exec -i
  forja python -m mcp_server.server`, stdio) — this ADR documents the
  process model more precisely, it does not change how Claude Code
  launches the server.
- **Phase 4 impact (snapshot/rollback, history)**: because the registry
  only exists inside the uvicorn process, snapshot/rollback logic
  (versioning, topological naming) must live server-side, inside the
  FastAPI backend's own request handlers — never inside an MCP tool
  function, which runs in a separate, memory-isolated process. MCP tools
  for history/rollback must be thin wrappers that call new compact backend
  endpoints (e.g. `GET /documentos/{id}/historial`, `POST
  /documentos/{id}/restaurar`), the same pattern `ejecutar_script`/
  `abrir_archivo` already follow — not new in-process calls into
  `app/versioning.py`.
- Any future write/exec route must be added to `app/auth.py`'s
  `requiere_token` dependency list at the point it is defined (reusable
  dependency, not copy-pasted checks per route), and `mcp_server/client.py`
  must send `X-Forja-Token` on any call that reaches it.
- If the MCP server and the FastAPI backend are ever split across
  different containers/hosts (as ADR-0004 flagged as a possible future
  need), `app/auth.py` can no longer be a shared top-level module — the
  token would need to travel as an explicit env var/secret to both sides,
  and that split would warrant its own superseding ADR.
