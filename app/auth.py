"""Shared-secret token guard for Forja's code-execution routes.

`POST /documentos/script` runs arbitrary Python (via `scripts_runner`) and
`POST /documentos/desde_ruta` reads an arbitrary path off disk into the
registry — both are dangerous by design and must not be reachable by
anyone who can merely reach the LAN-exposed web viewer
(`0.0.0.0:8710`, opened from the user's phone at `192.168.1.50:8710`).
Rather than binding loopback (which would also cut off the phone), only
these routes require a shared token, via the `requiere_token` FastAPI
dependency below.

Token source (checked in this order, so a container restart never
invalidates a token already handed to the MCP client):

1. The server secret `SECRET_FILE` (F0.2, ADR-0013; default
   `/run/secrets/forja_token`, a named volume private to the `forja`
   container, 0400 and owned by uvicorn's uid 1000). `app/entrypoint.sh`
   moved the legacy token there once, without printing it.
2. Env var `FORJA_TOKEN`, if set (tests pin a throwaway token this way and
   point `FORJA_SECRET_FILE` at a path that does not exist).
3. The legacy `.forja_token` inside `documentos_data` (`TOKEN_FILE`), if it
   still exists (an install the entrypoint has not migrated yet).
4. Otherwise, a fresh `secrets.token_urlsafe(32)`, created 0400 in
   `SECRET_FILE` when its directory exists, else 0600 in `TOKEN_FILE`.

The token's value is never logged or printed by this module.

`mcp_server/client.py` imports this same module (both live under `/app` on
`PYTHONPATH`, see `Dockerfile`) so the MCP client and the web backend always
agree on the token without duplicating the logic.
"""
from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path

from fastapi import Header, HTTPException

# See `app/documents.py`'s `DOCUMENTOS_DIR` for the `FORJA_DATA_DIR`
# override contract. In practice tests set `FORJA_TOKEN` directly (see
# `tests/conftest.py`), which short-circuits `obtener_token()` below before
# this path is ever touched — this override exists so a stray direct call
# to `_cargar_o_crear_token()` during tests never writes into the real
# bind-mounted `documentos_data/.forja_token`.
TOKEN_FILE = Path(os.environ.get("FORJA_DATA_DIR", "/data/documentos")) / ".forja_token"
SECRET_FILE = Path(os.environ.get("FORJA_SECRET_FILE", "/run/secrets/forja_token"))


def _leer_si_existe(ruta: Path) -> str | None:
    try:
        return ruta.read_text().strip() or None
    except FileNotFoundError:
        return None


def _cargar_o_crear_token() -> str:
    """Atomically claim creating the token file (the secret when its
    directory exists, else the legacy `TOKEN_FILE`); if another
    process/request already created it, just read it back (avoids two
    different generated tokens racing to overwrite each other)."""
    if SECRET_FILE.parent.is_dir():
        destino, modo = SECRET_FILE, 0o400
    else:
        destino, modo = TOKEN_FILE, 0o600
        destino.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(destino, os.O_CREAT | os.O_EXCL | os.O_WRONLY, modo)
    except FileExistsError:
        return destino.read_text().strip()
    token = secrets.token_urlsafe(32)
    with os.fdopen(fd, "w") as f:
        f.write(token)
    return token


def obtener_token() -> str:
    """Return the shared token (see the module docstring for the order):
    the server secret, then `FORJA_TOKEN`, then the legacy file, else a
    freshly generated one."""
    secreto = _leer_si_existe(SECRET_FILE)
    if secreto:
        return secreto
    env_token = os.environ.get("FORJA_TOKEN")
    if env_token:
        return env_token
    legado = _leer_si_existe(TOKEN_FILE)
    if legado:
        return legado
    return _cargar_o_crear_token()


def token_coincide(candidato: str | None) -> bool:
    """Constant-time comparison of a presented token against
    `obtener_token()`. Shared by `requiere_token` and the MCP HTTP endpoint
    (`mcp_server/http_app.py`, ADR-0008)."""
    if not candidato:
        return False
    return hmac.compare_digest(candidato.encode(), obtener_token().encode())


def requiere_token(
    x_forja_token: str | None = Header(default=None, alias="X-Forja-Token"),
) -> None:
    """FastAPI dependency guarding write/exec routes.

    Compares the `X-Forja-Token` header against `obtener_token()` with
    `hmac.compare_digest` (constant-time). Missing or wrong token -> 401.
    """
    if not token_coincide(x_forja_token):
        raise HTTPException(status_code=401, detail="token requerido")
