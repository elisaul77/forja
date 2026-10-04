"""Forja MCP server package.

Runs as `python -m mcp_server.server` **inside** the `forja` Docker
container (ADR-0004), registered with Claude Code over stdio:

    claude mcp add forja -- docker exec -i forja python -m mcp_server.server

Process-model note (documented here rather than editing the immutable
ADR-0004): `docker exec -i` starts a *new* OS process for the MCP server,
separate from the `uvicorn` process already running as the container's
entrypoint. The document registry in `app/documents.py` is an in-memory
dict local to that uvicorn process, so the two processes do **not** share
Python state even though they share a container. To keep the MCP-created
documents visible in the web viewer (and vice versa), `mcp_server.client`
talks to the FastAPI backend over `http://localhost:8000` (the loopback
interface inside the container) instead of importing `app/documents.py`
in-process. See `mcp_server/client.py` and DEVIATIONS in the Phase 3 report.
"""
