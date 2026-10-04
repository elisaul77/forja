"""Snapshot-based versioning store (ADR-0003, ADR-0006).

Every accepted mutation on a document (script re-run that creates/updates
it, or notes/strokes CRUD) snapshots the affected files **before** applying
the change. If the change fails, the caller restores the just-taken
snapshot and the document is left exactly as it was — never silently
corrupted by a partially-applied edit.

This module is a plain, file-only store: it knows nothing about
`build123d`/OCP, the in-memory document registry, or the notes schema —
callers (`app/documents.py`, `app/notes.py`) hand it a ``dict[str, bytes]``
of whichever files make up "the document's state" at that moment (e.g. the
STEP/STL file plus its sibling `.notas.json`) and get that same shape back
on read/restore. Keeping it generic like this is what avoids a circular
import between `app/documents.py` and `app/notes.py` (both only need to
import this module, never each other, to snapshot/restore).
"""
from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

# See `app/documents.py`'s `DOCUMENTOS_DIR` for the `FORJA_DATA_DIR`
# override contract; every module deriving a path from the documents
# storage root must agree on the same base directory.
HISTORIAL_DIR = Path(os.environ.get("FORJA_DATA_DIR", "/data/documentos")) / ".historial"

_MANIFEST_NAME = "manifest.json"


def _dir_documento(doc_id: str) -> Path:
    return HISTORIAL_DIR / doc_id


def _ruta_manifest(doc_id: str) -> Path:
    return _dir_documento(doc_id) / _MANIFEST_NAME


def _leer_manifest(doc_id: str) -> list[dict[str, Any]]:
    ruta = _ruta_manifest(doc_id)
    if not ruta.exists():
        return []
    try:
        return json.loads(ruta.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _escribir_manifest(doc_id: str, entradas: list[dict[str, Any]]) -> None:
    _dir_documento(doc_id).mkdir(parents=True, exist_ok=True)
    _ruta_manifest(doc_id).write_text(json.dumps(entradas))


def crear_snapshot(doc_id: str, mensaje: str, archivos: dict[str, bytes]) -> str:
    """Persist ``archivos`` (name -> bytes) as a new snapshot for ``doc_id``
    and append a compact manifest entry. Returns the new snapshot id."""
    snapshot_id = uuid.uuid4().hex[:12]
    snapshot_dir = _dir_documento(doc_id) / snapshot_id
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for nombre, contenido in archivos.items():
        (snapshot_dir / nombre).write_bytes(contenido)

    entradas = _leer_manifest(doc_id)
    entradas.append(
        {
            "id": snapshot_id,
            "fecha": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z",
            "mensaje": mensaje,
            "archivos": sorted(archivos.keys()),
        }
    )
    _escribir_manifest(doc_id, entradas)
    return snapshot_id


def listar_historial(doc_id: str) -> list[dict[str, Any]]:
    """Compact history entries for ``doc_id``: ``[{id, fecha, mensaje}]``,
    most-recent-last (creation order). Never includes file contents/diffs —
    those are only available via :func:`leer_snapshot` on explicit request."""
    return [
        {"id": e["id"], "fecha": e["fecha"], "mensaje": e["mensaje"]}
        for e in _leer_manifest(doc_id)
    ]


def borrar_historial(doc_id: str) -> None:
    """Permanently remove every snapshot for ``doc_id`` (used by `DELETE
    /documentos/{doc_id}` — Phase 4 fix-review test-isolation cleanup — so a
    deleted throwaway document doesn't leave an orphaned `.historial/`
    entry). A no-op if the document never had any history."""
    shutil.rmtree(_dir_documento(doc_id), ignore_errors=True)


def existe_snapshot(doc_id: str, snapshot_id: str) -> bool:
    return any(e["id"] == snapshot_id for e in _leer_manifest(doc_id))


def leer_snapshot(doc_id: str, snapshot_id: str) -> dict[str, bytes]:
    """Read back the files stored under ``snapshot_id`` for ``doc_id``.

    Raises ``FileNotFoundError`` if the snapshot id is unknown.
    """
    if not existe_snapshot(doc_id, snapshot_id):
        raise FileNotFoundError(f"snapshot {snapshot_id!r} no encontrado para {doc_id!r}")
    snapshot_dir = _dir_documento(doc_id) / snapshot_id
    return {ruta.name: ruta.read_bytes() for ruta in snapshot_dir.iterdir() if ruta.is_file()}
