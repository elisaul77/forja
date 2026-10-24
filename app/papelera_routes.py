"""Rutas de la papelera de la galeria (galeria ordenada, fase 1).

- `POST /documentos/{id}/papelera`: manda un documento vivo a la papelera. Sale
  de la galeria y de todas las rutas `/documentos/{id}/*`; ningun archivo se
  borra.
- `GET /papelera`: lista los documentos en papelera.
- `POST /papelera/{id}/restaurar`: lo vuelve a cargar desde disco.
- `DELETE /papelera/{id}`: borrado definitivo de un documento que YA esta en la
  papelera (el unico camino de borrado definitivo para documentos reversibles).

Todas exigen el token (ADR-0005). La persistencia del estado vive en
`app/papelera.py`; aqui solo se tocan los registros en memoria de
`app/documents.py`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

import auth
import documents
import eventos
import medidas
import papelera
import parametros

router = APIRouter()
logger = logging.getLogger(__name__)

_DIR = documents.DOCUMENTOS_DIR


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def _formato(ruta: Path | None) -> str | None:
    if ruta is None:
        return None
    return "step" if ruta.suffix.lower() in documents._STEP_EXTS else "stl"


def _exigir_en_papelera(doc_id: str) -> None:
    """404 unless ``doc_id`` is in the papelera. A corrupt state sidecar is a
    422 with its own message, never a silent 404 or a silent restore."""
    try:
        en_papelera = papelera.esta_en_papelera(_DIR, doc_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="estado de la papelera ilegible") from exc
    if not en_papelera:
        raise HTTPException(status_code=404, detail="documento no encontrado en la papelera")


def _quitar_de_memoria(doc_id: str) -> None:
    documents._registry.pop(doc_id, None)
    documents._files.pop(doc_id, None)
    documents._revisiones.pop(doc_id, None)
    medidas.olvidar(doc_id)


def _mover_a_papelera_bloqueado(doc_id: str, motivo: str | None = None) -> None:
    """Caller holds `parametros.bloqueo(doc_id)` and checked the registry. The
    sidecar is written first, so a crash in between leaves the document loaded
    (and it is skipped on the next startup)."""
    registro = documents._registry[doc_id]
    estado: dict[str, Any] = {
        "papelera": _ahora(),
        "nombre": registro["nombre"],
        "volumen": registro["volumen"],
        "solidos": registro["solidos"],
        "formato": _formato(documents._files.get(doc_id)),
    }
    if motivo is not None:
        estado["motivo"] = motivo
    papelera.guardar_estado(_DIR, doc_id, estado)
    _quitar_de_memoria(doc_id)
    eventos.publicar("documento_eliminado", doc_id)


@router.post("/documentos/{doc_id}/papelera", dependencies=[Depends(auth.requiere_token)])
def mover_a_papelera(doc_id: str) -> dict[str, Any]:
    """Send a live document to the papelera. Nothing is deleted from disk."""
    # Validated before the lock (mirrors DELETE /documentos/{id}): the lock
    # helper creates one entry per id it is asked about and never evicts it.
    if doc_id not in documents._registry:
        raise HTTPException(status_code=404, detail="documento no encontrado")
    with parametros.bloqueo(doc_id):
        if doc_id not in documents._registry:
            raise HTTPException(status_code=404, detail="documento no encontrado")
        _mover_a_papelera_bloqueado(doc_id)
    return {"ok": True, "id": doc_id}


def barrer_borradores(ahora: datetime | None = None) -> list[str]:
    """Send every draft whose expiry (`papelera.expiracion`) has passed to the
    papelera with `motivo: borrador_expirado`. Idempotent. Files are never
    deleted. A corrupt sidecar is logged and left alone: never trashed on
    unreadable state. Returns the ids sent this time."""
    instante = ahora or datetime.now(timezone.utc)
    enviados: list[str] = []
    for doc_id in papelera.ids_con_estado(_DIR):
        if doc_id not in documents._registry:
            continue  # ya en papelera, o sin cargar: nada que caducar aqui
        with parametros.bloqueo(doc_id):
            if doc_id not in documents._registry:
                continue
            try:
                estado = papelera.leer_estado(_DIR, doc_id)
            except ValueError:
                logger.warning("estado ilegible, el borrador %s no caduca hasta corregirlo", doc_id)
                continue
            ruta = documents._files.get(doc_id)
            if not papelera.es_borrador(estado) or ruta is None or not ruta.exists():
                continue
            if papelera.expiracion(ruta.stat().st_mtime, estado) > instante:
                continue
            _mover_a_papelera_bloqueado(doc_id, motivo="borrador_expirado")
            enviados.append(doc_id)
    return enviados


@router.post("/documentos/{doc_id}/conservar", dependencies=[Depends(auth.requiere_token)])
def conservar_documento(doc_id: str) -> dict[str, Any]:
    """Promote a draft to a permanent document. Idempotent; a document that is
    not a draft is answered with `cambiado: false`."""
    if doc_id not in documents._registry:
        raise HTTPException(status_code=404, detail="documento no encontrado")
    with parametros.bloqueo(doc_id):
        if doc_id not in documents._registry:
            raise HTTPException(status_code=404, detail="documento no encontrado")
        try:
            cambiado = papelera.quitar_borrador(_DIR, doc_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="estado de documento ilegible") from exc
    return {"ok": True, "id": doc_id, "borrador": False, "cambiado": cambiado}


@router.get("/papelera", dependencies=[Depends(auth.requiere_token)])
def listar_papelera() -> list[dict[str, Any]]:
    """Documents in the papelera, most recently trashed first."""
    entradas = []
    for doc_id in papelera.ids_en_papelera(_DIR):
        estado = papelera.leer_estado(_DIR, doc_id)
        entradas.append({
            "id": doc_id,
            "nombre": estado.get("nombre"),
            "volumen": estado.get("volumen"),
            "solidos": estado.get("solidos"),
            "formato": estado.get("formato"),
            "papelera": estado.get("papelera"),
        })
    return sorted(entradas, key=lambda e: (e["papelera"] or "", e["id"]), reverse=True)


@router.post("/papelera/{doc_id}/restaurar", dependencies=[Depends(auth.requiere_token)])
def restaurar_de_papelera(doc_id: str) -> dict[str, Any]:
    """Reload a trashed document from its untouched files. Same revision and
    history as before it was trashed (nothing on disk was rewritten)."""
    _exigir_en_papelera(doc_id)
    with parametros.bloqueo(doc_id):
        _exigir_en_papelera(doc_id)
        ruta = papelera.ruta_archivo(_DIR, doc_id)
        if ruta is None:
            raise HTTPException(status_code=409, detail="el archivo del documento ya no existe")
        try:
            documents._cargar_documento(ruta)
        except Exception as exc:  # noqa: BLE001 - reported to the caller below
            _quitar_de_memoria(doc_id)
            raise HTTPException(status_code=422, detail="archivo invalido: no se puede restaurar") from exc
        try:
            papelera.borrar_estado(_DIR, doc_id)
        except OSError as exc:
            _quitar_de_memoria(doc_id)
            raise HTTPException(status_code=500, detail="no se pudo quitar el estado de papelera") from exc
        documents.confirmar_revision(doc_id, "documento_creado")
        return {**documents._registry[doc_id], "revision": documents._revisiones.get(doc_id)}


@router.delete("/papelera/{doc_id}", dependencies=[Depends(auth.requiere_token)])
def purgar_de_papelera(doc_id: str) -> dict[str, Any]:
    """Permanent delete of a document that is already in the papelera. Same
    cleanup as `DELETE /documentos/{id}` (file, notes, history, assembly,
    exports, state sidecar)."""
    _exigir_en_papelera(doc_id)
    with parametros.bloqueo(doc_id):
        _exigir_en_papelera(doc_id)
        return documents._purgar_archivos(doc_id)
