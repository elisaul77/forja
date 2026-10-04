"""Notes ("notas"/pins) and whiteboard strokes ("trazos") — Phase 4.

Schema ported field-for-field from the FreeCAD `ClaudeNotas` add-on:

- Pin ("Nota", `ClaudeNota.FCMacro`): ``comentario``, ``referencia``
  (``{tipo: cara|arista|punto, id, punto}``), plus ``visible``.
- Stroke ("Trazo", `pizarra.py`): ``tipo`` (``quitar``/``anadir``/``medida``/
  ``comentario``), ``comentario``, ``puntos`` (3D polyline, mm),
  ``plano_origen``/``plano_normal``, ``referencia`` (same shape as a pin's,
  optional — a stroke need not reference a specific face/edge), ``visible``.

Persistence: one JSON file per document, ``{doc_id}.notas.json`` inside
`DOCUMENTOS_DIR`, so notes survive a container restart and are reloaded
alongside the document registry (`app/documents.py`'s startup reload).

This module never imports `app/documents.py` (it only needs a filesystem
existence check on the document's own files, done via a plain glob against
the shared `DOCUMENTOS_DIR`) — that keeps the notes/naming/documents import
graph acyclic (`app/documents.py` *does* import a couple of pure helpers
from here for its `/historial`/`/restaurar` hooks, so the dependency must
stay one-directional).
"""
from __future__ import annotations

import json
import os
import uuid
from functools import wraps
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

import versioning
import parametros
import eventos

router = APIRouter()

# See `app/documents.py`'s `DOCUMENTOS_DIR` for the `FORJA_DATA_DIR`
# override contract; both modules must always agree on the same directory.
DOCUMENTOS_DIR = Path(os.environ.get("FORJA_DATA_DIR", "/data/documentos"))

TIPOS_TRAZO = ("quitar", "anadir", "medida", "comentario")
TIPOS_REFERENCIA = ("cara", "arista", "punto")

MAX_COMENTARIO = 2000
MAX_PUNTOS_TRAZO = 5000
MIN_PUNTOS_TRAZO = 1

# Never surfaced from the nested `referencia` sub-dict in `resumen()`: these
# belong on the note/stroke itself (`item["referencia_perdida"]`/
# `item["ambigua"]`, the single top-level source of truth) or are the
# internal geometric fingerprint (`huella`) — Phase 5A re-review follow-up
# from Phase 4: filter the whole set defensively, not just `huella`, in case
# any stored/legacy data still carries them nested.
_CLAVES_INTERNAS_REFERENCIA = {"huella", "referencia_perdida", "ambigua"}


_EXTS_DOCUMENTO = (".step", ".stp", ".stl")

# Ceiling for a document id used as a path component, same as
# `app/assemblies.py`'s `_directory` regex. An unbounded id is not merely
# wasteful: `Path.exists()` raises `OSError` ("File name too long") past the
# filesystem limit, turning a route whose contract is 404 into a 500
# (Phase 6.0 finding 3).
#
# The limit is counted in UTF-8 BYTES, not characters: a path component is
# capped near 255 bytes, so `"🔧" * 63` is 63 characters but 252 bytes and
# still reached the probe, whose `OSError` escaped as a 500 (an anonymous
# log-flooding vector, since the four notes/trazos routes are token-free).
MAX_DOC_ID = 128


def _doc_id_valido(doc_id: str) -> bool:
    """Bound the id in both units: characters (what the regex in
    `app/assemblies.py` allows) and UTF-8 bytes (what the filesystem allows)."""
    if not isinstance(doc_id, str) or not 0 < len(doc_id) <= MAX_DOC_ID:
        return False
    return len(doc_id.encode("utf-8", "replace")) <= MAX_DOC_ID


def _existe_documento(doc_id: str) -> bool:
    if not _doc_id_valido(doc_id):
        return False
    for ext in _EXTS_DOCUMENTO:
        try:
            if (DOCUMENTOS_DIR / f"{doc_id}{ext}").exists():
                return True
        except (OSError, ValueError, UnicodeError):
            # Defense in depth: whatever the clamp above misses (an unusual
            # fs limit, an unencodable name), an id the filesystem refuses
            # names no document — 404, never a 500 from this probe.
            return False
    return False


def ruta_notas(doc_id: str) -> Path:
    return DOCUMENTOS_DIR / f"{doc_id}.notas.json"


def cargar(doc_id: str) -> dict[str, list[dict[str, Any]]]:
    ruta = ruta_notas(doc_id)
    if not ruta.exists():
        return {"notas": [], "trazos": []}
    try:
        datos = json.loads(ruta.read_text())
    except (json.JSONDecodeError, OSError):
        return {"notas": [], "trazos": []}
    datos.setdefault("notas", [])
    datos.setdefault("trazos", [])
    return datos


def guardar(doc_id: str, datos: dict[str, list[dict[str, Any]]]) -> None:
    ruta_notas(doc_id).write_text(json.dumps(datos, ensure_ascii=False))


def _snapshot_antes(doc_id: str, mensaje: str) -> None:
    """Snapshot the notes file as it is *right now*, before applying a
    mutation (ADR-0003: snapshot BEFORE the change, one entry per applied
    edit)."""
    ruta = ruta_notas(doc_id)
    actual = ruta.read_bytes() if ruta.exists() else b'{"notas": [], "trazos": []}'
    versioning.crear_snapshot(doc_id, mensaje, {"notas.json": actual})


class ReferenciaBody(BaseModel):
    tipo: str
    id: str | None = None
    punto: list[float] = Field(min_length=3, max_length=3)
    huella: dict[str, Any] | None = None

    @field_validator("tipo")
    @classmethod
    def _tipo_valido(cls, v: str) -> str:
        if v not in TIPOS_REFERENCIA:
            raise ValueError(f"tipo de referencia invalido (usar {TIPOS_REFERENCIA})")
        return v


class NotaBody(BaseModel):
    comentario: str = Field(max_length=MAX_COMENTARIO)
    referencia: ReferenciaBody


class TrazoBody(BaseModel):
    tipo: str
    comentario: str = Field(default="", max_length=MAX_COMENTARIO)
    puntos: list[list[float]] = Field(min_length=MIN_PUNTOS_TRAZO, max_length=MAX_PUNTOS_TRAZO)
    plano_origen: list[float] = Field(min_length=3, max_length=3)
    plano_normal: list[float] = Field(min_length=3, max_length=3)
    referencia: ReferenciaBody | None = None

    @field_validator("tipo")
    @classmethod
    def _tipo_valido(cls, v: str) -> str:
        if v not in TIPOS_TRAZO:
            raise ValueError(f"tipo de trazo invalido (usar {TIPOS_TRAZO})")
        return v

    @field_validator("puntos")
    @classmethod
    def _puntos_validos(cls, v: list[list[float]]) -> list[list[float]]:
        for p in v:
            if len(p) != 3:
                raise ValueError("cada punto debe tener 3 coordenadas [x, y, z]")
        return v


class VisibilidadBody(BaseModel):
    visible: bool


def _serialized(fn):
    """Keep annotation read/snapshot/write atomic with rebuild and restore.

    The document is validated *before* `parametros.bloqueo` is called:
    `bloqueo` creates and keeps one `threading.Lock` per id it is asked
    about, so validating afterwards (Phase 6.0 finding 1) let any anonymous
    LAN client grow `parametros._bloqueos` forever with ids that never named
    a document — every notes/trazos route is token-free by design. The lock
    table is never evicted on delete: handing a fresh lock to a document id
    another live request still holds would silently destroy the mutual
    exclusion these routes rely on.
    """
    @wraps(fn)
    def wrapped(doc_id, *args, **kwargs):
        if not _existe_documento(doc_id):
            raise HTTPException(status_code=404, detail="documento no encontrado")
        with parametros.bloqueo(doc_id):
            result = fn(doc_id, *args, **kwargs)
            if result is not None and result is not False:
                eventos.publicar("anotaciones_actualizadas", doc_id,
                                cambios=["notas", "historial"])
            return result
    return wrapped


@_serialized
def crear_nota(doc_id: str, body: NotaBody) -> dict[str, Any]:
    datos = cargar(doc_id)
    _snapshot_antes(doc_id, "crear nota")
    nota = {
        "n": uuid.uuid4().hex[:10],
        "comentario": body.comentario,
        "referencia": body.referencia.model_dump(),
        "referencia_perdida": False,
        "visible": True,
    }
    datos["notas"].append(nota)
    guardar(doc_id, datos)
    return nota


@_serialized
def crear_trazo(doc_id: str, body: TrazoBody) -> dict[str, Any]:
    datos = cargar(doc_id)
    _snapshot_antes(doc_id, "crear trazo")
    trazo = {
        "n": uuid.uuid4().hex[:10],
        "tipo": body.tipo,
        "comentario": body.comentario,
        "puntos": body.puntos,
        "plano_origen": body.plano_origen,
        "plano_normal": body.plano_normal,
        "referencia": body.referencia.model_dump() if body.referencia else None,
        "referencia_perdida": False,
        "visible": True,
    }
    datos["trazos"].append(trazo)
    guardar(doc_id, datos)
    return trazo


def _buscar(items: list[dict[str, Any]], item_id: str) -> dict[str, Any] | None:
    return next((it for it in items if it["n"] == item_id), None)


@_serialized
def borrar_nota(doc_id: str, nota_id: str) -> bool:
    datos = cargar(doc_id)
    if _buscar(datos["notas"], nota_id) is None:
        return False
    _snapshot_antes(doc_id, f"borrar nota {nota_id}")
    datos["notas"] = [n for n in datos["notas"] if n["n"] != nota_id]
    guardar(doc_id, datos)
    return True


@_serialized
def borrar_trazo(doc_id: str, trazo_id: str) -> bool:
    datos = cargar(doc_id)
    if _buscar(datos["trazos"], trazo_id) is None:
        return False
    _snapshot_antes(doc_id, f"borrar trazo {trazo_id}")
    datos["trazos"] = [t for t in datos["trazos"] if t["n"] != trazo_id]
    guardar(doc_id, datos)
    return True


@_serialized
def actualizar_visibilidad_nota(doc_id: str, nota_id: str, visible: bool) -> dict[str, Any] | None:
    datos = cargar(doc_id)
    nota = _buscar(datos["notas"], nota_id)
    if nota is None:
        return None
    _snapshot_antes(doc_id, f"visibilidad nota {nota_id}")
    nota["visible"] = visible
    guardar(doc_id, datos)
    return nota


@_serialized
def actualizar_visibilidad_trazo(doc_id: str, trazo_id: str, visible: bool) -> dict[str, Any] | None:
    datos = cargar(doc_id)
    trazo = _buscar(datos["trazos"], trazo_id)
    if trazo is None:
        return None
    _snapshot_antes(doc_id, f"visibilidad trazo {trazo_id}")
    trazo["visible"] = visible
    guardar(doc_id, datos)
    return trazo


def _bbox(puntos: list[list[float]]) -> list[list[float]]:
    xs = [p[0] for p in puntos]
    ys = [p[1] for p in puntos]
    zs = [p[2] for p in puntos]
    return [[min(xs), min(ys), min(zs)], [max(xs), max(ys), max(zs)]]


def resumen(doc_id: str, detalle: bool = False) -> list[dict[str, Any]]:
    """Compact MCP-facing shape for both notas and trazos (mirrors
    `pizarra.py`'s `resumen()`): ``{n, tipo, comentario, referencia,
    referencia_perdida, ambigua, plano, puntos_resumen}``. Full ``puntos``
    only when ``detalle=True``; ``huella`` (internal fingerprint) is never
    included. ``ambigua`` is ADR-0007's extra flag: ``true`` only when a
    rebuild's re-resolution specifically failed because two or more
    candidates tied within tolerance (as opposed to finding nothing at
    all); always present (default ``false``) for a predictable schema."""
    datos = cargar(doc_id)
    out: list[dict[str, Any]] = []
    for nota in datos["notas"]:
        referencia = {k: v for k, v in nota["referencia"].items() if k not in _CLAVES_INTERNAS_REFERENCIA}
        entrada = {
            "n": nota["n"],
            "tipo": "nota",
            "comentario": nota["comentario"],
            "referencia": referencia,
            "referencia_perdida": nota["referencia_perdida"],
            "ambigua": nota.get("ambigua", False),
            "visible": nota["visible"],
            "plano": None,
            "puntos_resumen": {"n_puntos": 1, "bbox": [referencia["punto"], referencia["punto"]]},
        }
        if detalle:
            entrada["puntos"] = [referencia["punto"]]
        out.append(entrada)
    for trazo in datos["trazos"]:
        referencia = None
        if trazo["referencia"] is not None:
            referencia = {
                k: v for k, v in trazo["referencia"].items() if k not in _CLAVES_INTERNAS_REFERENCIA
            }
        entrada = {
            "n": trazo["n"],
            "tipo": trazo["tipo"],
            "comentario": trazo["comentario"],
            "referencia": referencia,
            "referencia_perdida": trazo["referencia_perdida"],
            "ambigua": trazo.get("ambigua", False),
            "visible": trazo["visible"],
            "plano": {"origen": trazo["plano_origen"], "normal": trazo["plano_normal"]},
            "puntos_resumen": {"n_puntos": len(trazo["puntos"]), "bbox": _bbox(trazo["puntos"])},
        }
        if detalle:
            entrada["puntos"] = trazo["puntos"]
        out.append(entrada)
    return out


# ---------------------------------------------------------------- rutas HTTP
# Open (no X-Forja-Token), like the web UI's upload route — notes/strokes are
# regular editor content, not code execution/arbitrary path reads, but they
# stay bounded by the Pydantic limits above (ADR-0005 boundary contract).


@router.get("/documentos/{doc_id}/notas")
def listar_notas_y_trazos(doc_id: str, detalle: bool = False) -> dict[str, Any]:
    """Unified compact shape for both the web UI and the MCP `leer_notas`
    tool: ``{"resumen": [{n, tipo, comentario, referencia,
    referencia_perdida, visible, plano, puntos_resumen[, puntos]}]}``.
    Full ``puntos`` (the whiteboard polyline) only when ``detalle=true``;
    the internal fingerprint (``huella``) is never included here."""
    if not _existe_documento(doc_id):
        raise HTTPException(status_code=404, detail="documento no encontrado")
    return {"resumen": resumen(doc_id, detalle=detalle)}


@router.post("/documentos/{doc_id}/notas")
def crear_nota_ruta(doc_id: str, body: NotaBody) -> dict[str, Any]:
    if not _existe_documento(doc_id):
        raise HTTPException(status_code=404, detail="documento no encontrado")
    return crear_nota(doc_id, body)


@router.delete("/documentos/{doc_id}/notas/{nota_id}")
def borrar_nota_ruta(doc_id: str, nota_id: str) -> dict[str, Any]:
    if not borrar_nota(doc_id, nota_id):
        raise HTTPException(status_code=404, detail="nota no encontrada")
    return {"ok": True}


@router.patch("/documentos/{doc_id}/notas/{nota_id}")
def actualizar_nota_ruta(doc_id: str, nota_id: str, body: VisibilidadBody) -> dict[str, Any]:
    nota = actualizar_visibilidad_nota(doc_id, nota_id, body.visible)
    if nota is None:
        raise HTTPException(status_code=404, detail="nota no encontrada")
    return nota


@router.post("/documentos/{doc_id}/trazos")
def crear_trazo_ruta(doc_id: str, body: TrazoBody) -> dict[str, Any]:
    if not _existe_documento(doc_id):
        raise HTTPException(status_code=404, detail="documento no encontrado")
    return crear_trazo(doc_id, body)


@router.delete("/documentos/{doc_id}/trazos/{trazo_id}")
def borrar_trazo_ruta(doc_id: str, trazo_id: str) -> dict[str, Any]:
    if not borrar_trazo(doc_id, trazo_id):
        raise HTTPException(status_code=404, detail="trazo no encontrado")
    return {"ok": True}


@router.patch("/documentos/{doc_id}/trazos/{trazo_id}")
def actualizar_trazo_ruta(doc_id: str, trazo_id: str, body: VisibilidadBody) -> dict[str, Any]:
    trazo = actualizar_visibilidad_trazo(doc_id, trazo_id, body.visible)
    if trazo is None:
        raise HTTPException(status_code=404, detail="trazo no encontrado")
    return trazo
