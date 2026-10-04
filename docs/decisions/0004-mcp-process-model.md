Status: Superseded in part by ADR-0005

# ADR 0004: MCP process model — runs inside the Docker container

Date: 2026-09-27
Status: Accepted

## Context

Forja exposes an MCP server (Phase 3) so Claude can operate the editor
directly (open/inspect/edit documents, run parametric scripts, query
checks). The user's standing rule is that everything for this project runs
in Docker — no host-Python runtime dependency is ever assumed (ADR-0001).
Two process models were considered: (a) run the MCP server as a separate
process on the host, talking to the Dockerized backend over HTTP, or (b)
run the MCP server **inside** the same Docker container as the FastAPI
backend, reusing its Python environment and in-process access to the kernel
modules, communicating with Claude Code over stdio via `docker exec -i`.

Option (a) would require a second, host-side Python environment with its
own pinned dependencies (at least the `mcp` package) purely to proxy calls
— duplicating environment management and reintroducing exactly the
host-Python dependency the project explicitly avoids. Option (b) reuses the
already-pinned, already-tested container environment as-is.

PyPI availability was verified for `mcp` 2.2.0 (`python>=3.10`), compatible
with the project's Python 3.12 pin (`plans/forja-plan-detail.md`, "Verified
environment"), and `docker exec -i` was confirmed as the standard way to
bridge a stdio-based MCP client to a process running inside an already-up
container.

## Decision

The MCP server (`mcp_server/`) runs **inside** the `forja` Docker container,
as a separate entry point from the FastAPI web process
(`python -m mcp_server.server`), using **stdio** transport. It is registered
with Claude Code as:

```
claude mcp add forja -- docker exec -i forja python -m mcp_server.server
```

`mcp` 2.2.0 is a pinned dependency in the project's **main**
`requirements.txt` (not a separate requirements file) — there is one Python
environment for the whole container, shared by the web backend and the MCP
server, both importing the same `app/` modules (documents, kernel, notes,
checks) directly, in-process, with no HTTP hop between them.

## Consequences

- No second Python environment to maintain; the MCP server and the FastAPI
  app share one pinned dependency set and one Docker image.
- Starting the MCP server requires the `forja` container to already be
  running (`docker compose up -d`) — `docker exec -i` attaches to an
  existing container, it does not start one. This must be documented
  wherever the `claude mcp add` command is given to a new environment.
- MCP tool implementations can call kernel/document/notes functions
  in-process (no network serialization overhead), which supports the
  "whole-script calls beat granular tool calls ~13x in tokens" lesson
  documented from the fusion360 skill — MCP tools are script-level, not
  thin per-property RPC wrappers.
- If the MCP server ever needs to run independently of the web backend
  (e.g. different scaling needs), that would require a new ADR superseding
  this one — not an edit to it.
