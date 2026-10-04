"""Document history store (ADR-0003; backend: git, ADR-0014 — supersedes
the plain-directory store of ADR-0006).

Every accepted mutation on a document snapshots the affected files
**before** applying the change. If the change fails, the caller restores
the just-taken snapshot and the document is left exactly as it was.

The public contract is unchanged since ADR-0006: callers
(`app/documents.py`, `app/notes.py`, `app/assembly_routes.py`) hand in a
``dict[str, bytes]`` and get that same shape back; ``listar_historial``
returns ``[{id, fecha, mensaje}]``, oldest first. Since G1 each snapshot is
one git commit in ``.repos/{doc_id}.git`` (`app/git_store.py`), keyed by the
same 12-hex ``id`` as before (kept in a commit trailer). Snapshots of a
not-yet-migrated ``.historial/{doc_id}/`` (ADR-0006 layout) are still read
and listed first, so nothing disappears between deploying this code and
running `migrar_historial`.

This module knows nothing about `build123d`, the registry or the notes
schema — that is what keeps `documents` and `notes` free of a circular
import.
"""
from __future__ import annotations

import contextvars
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

import git_store

# See `app/documents.py`'s `DOCUMENTOS_DIR` for the `FORJA_DATA_DIR`
# override contract.
HISTORIAL_DIR = Path(os.environ.get("FORJA_DATA_DIR", "/data/documentos")) / ".historial"

_MANIFEST_NAME = "manifest.json"

# Who caused the current request's change: "agente" (MCP), "humano"
# (viewer), "forja" (unknown/internal). Set by the middleware in `main.py`.
autor_actual: contextvars.ContextVar[str] = contextvars.ContextVar("forja_autor", default="forja")


# ------------------------------------------------------- legacy (ADR-0006)

def _dir_documento(doc_id: str) -> Path:
    return HISTORIAL_DIR / doc_id


def _leer_manifest(doc_id: str) -> list[dict[str, Any]]:
    ruta = _dir_documento(doc_id) / _MANIFEST_NAME
    if not ruta.exists():
        return []
    try:
        return json.loads(ruta.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def leer_snapshot_legado(doc_id: str, snapshot_id: str) -> dict[str, bytes]:
    snapshot_dir = _dir_documento(doc_id) / snapshot_id
    return {ruta.name: ruta.read_bytes() for ruta in snapshot_dir.iterdir() if ruta.is_file()}


# ------------------------------------------------------------------ public

def _ahora() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


def crear_snapshot(doc_id: str, mensaje: str, archivos: dict[str, bytes],
                   autor: str | None = None, fuente: dict[str, bytes] | None = None) -> str:
    """Commit ``archivos`` (name -> bytes) as a new snapshot for ``doc_id``.
    Returns the new snapshot id. ``fuente`` (optional) adds source blobs
    under ``_fuente/`` that are never returned by :func:`leer_snapshot`."""
    snapshot_id = uuid.uuid4().hex[:12]
    git_store.commit(doc_id, archivos, mensaje, snapshot_id, _ahora(),
                     autor or autor_actual.get(), fuente)
    return snapshot_id


def _entradas(doc_id: str) -> list[dict[str, Any]]:
    legado = [{**e, "origen": "legado"} for e in _leer_manifest(doc_id)]
    vistos = {e["id"] for e in legado}
    return legado + [e for e in git_store.log(doc_id) if e["id"] not in vistos]


def listar_historial(doc_id: str) -> list[dict[str, Any]]:
    """Compact history entries for ``doc_id``: ``[{id, fecha, mensaje}]``,
    most-recent-last. Never includes file contents/diffs."""
    return [{"id": e["id"], "fecha": e["fecha"], "mensaje": e["mensaje"]} for e in _entradas(doc_id)]


def listar_commits(doc_id: str) -> list[dict[str, Any]]:
    """Git-only view (G1+): ``[{id, sha, fecha, mensaje, autor, archivos}]``."""
    return git_store.log(doc_id)


def borrar_historial(doc_id: str) -> None:
    """Permanently remove every snapshot for ``doc_id`` (git repo and any
    legacy directory). A no-op if the document never had any history."""
    shutil.rmtree(_dir_documento(doc_id), ignore_errors=True)
    try:
        git_store.borrar(doc_id)
    except ValueError:
        pass


def existe_snapshot(doc_id: str, snapshot_id: str) -> bool:
    try:
        return any(e["id"] == snapshot_id for e in _entradas(doc_id))
    except ValueError:
        return False


def leer_snapshot(doc_id: str, snapshot_id: str) -> dict[str, bytes]:
    """Read back the files stored under ``snapshot_id`` for ``doc_id``.

    Raises ``FileNotFoundError`` if the snapshot id is unknown.
    """
    try:
        entradas = _entradas(doc_id)
    except ValueError:
        entradas = []
    for entrada in entradas:
        if entrada["id"] == snapshot_id:
            if entrada.get("origen") == "legado":
                return leer_snapshot_legado(doc_id, snapshot_id)
            return git_store.leer(doc_id, entrada["sha"])
    raise FileNotFoundError(f"snapshot {snapshot_id!r} no encontrado para {doc_id!r}")
