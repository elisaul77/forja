"""Estado por documento y papelera de la galeria (galeria ordenada, fase 1).

Cada documento puede tener un sidecar ``{id}.estado.json`` con su estado de
galeria. Sin sidecar el documento es normal y permanente: no hay migracion.
Un documento en papelera conserva TODOS sus archivos; solo se quita de los
registros en memoria de `app/documents.py`, asi que ninguna ruta de documento
lo ve, y restaurarlo lo vuelve a cargar desde disco.

Este modulo no importa `app/documents.py`: recibe el directorio de datos como
argumento para no crear un ciclo de importacion. Las rutas y la logica que
tocan los registros viven en `app/papelera_routes.py`.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Ids de documento: `uuid.uuid4().hex` (documents.py crea todos asi).
_ID_RE = re.compile(r"[0-9a-f]{32}")
_EXTS_GEOMETRIA = (".step", ".stp", ".stl")
_SUFIJO_ESTADO = ".estado.json"
_HORAS_BORRADOR_POR_DEFECTO = 24.0
_MINUTOS_BARRIDO_POR_DEFECTO = 15.0


def doc_id_valido(doc_id: Any) -> bool:
    return isinstance(doc_id, str) and _ID_RE.fullmatch(doc_id) is not None


def ruta_estado(directorio: Path, doc_id: str) -> Path:
    """Sidecar de estado de ``doc_id``. Un id mal formado nunca llega a
    formar una ruta: se rechaza con ValueError."""
    if not doc_id_valido(doc_id):
        raise ValueError(f"id de documento invalido: {doc_id!r}")
    return directorio / f"{doc_id}{_SUFIJO_ESTADO}"


def leer_estado(directorio: Path, doc_id: str) -> dict[str, Any]:
    """Estado de ``doc_id``; ``{}`` si no hay sidecar (documento permanente).
    Un sidecar ilegible es un error explicito (ValueError), nunca un
    documento silenciosamente permanente."""
    ruta = ruta_estado(directorio, doc_id)
    if not ruta.exists():
        return {}
    try:
        estado = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"estado de documento ilegible: {ruta.name}") from exc
    if not isinstance(estado, dict):
        raise ValueError(f"estado de documento invalido: {ruta.name}")
    return estado


def guardar_estado(directorio: Path, doc_id: str, estado: dict[str, Any]) -> None:
    """Escritura atomica: un corte a mitad no deja un sidecar a medias."""
    ruta = ruta_estado(directorio, doc_id)
    temporal = ruta.parent / f"{ruta.name}.tmp"
    temporal.write_text(json.dumps(estado, ensure_ascii=False), encoding="utf-8")
    os.replace(temporal, ruta)


def borrar_estado(directorio: Path, doc_id: str) -> None:
    ruta_estado(directorio, doc_id).unlink(missing_ok=True)


def esta_en_papelera(directorio: Path, doc_id: str) -> bool:
    """True solo si el sidecar marca ``papelera``. Ids invalidos -> False."""
    if not doc_id_valido(doc_id):
        return False
    return bool(leer_estado(directorio, doc_id).get("papelera"))


def ids_en_papelera(directorio: Path) -> list[str]:
    """Ids de los documentos en papelera, recorriendo los sidecars del disco."""
    ids = []
    for ruta in sorted(directorio.glob(f"*{_SUFIJO_ESTADO}")):
        doc_id = ruta.name[: -len(_SUFIJO_ESTADO)]
        if esta_en_papelera(directorio, doc_id):
            ids.append(doc_id)
    return ids


def ruta_archivo(directorio: Path, doc_id: str) -> Path | None:
    """Archivo STEP/STL guardado de ``doc_id``, o None si el id es invalido o
    el archivo no existe."""
    if not doc_id_valido(doc_id):
        return None
    for ext in _EXTS_GEOMETRIA:
        ruta = directorio / f"{doc_id}{ext}"
        if ruta.is_file():
            return ruta
    return None


# ---------------------------------------------------------------- borradores
# Un borrador es un documento creado por `ejecutar_script` sin `documento_id` ni
# `permanente=true`: fuera de la galeria por defecto y con caducidad. Su sidecar
# lleva `{"borrador": true, "creado": iso}`; la caducidad se calcula con el mtime
# del archivo (desliza con cada modificacion), no se guarda.


def _leer_entorno_positivo(nombre: str, por_defecto: float) -> float:
    """Numero positivo del entorno. Un valor invalido no tumba el listado: cae al
    por defecto y deja un aviso en el log."""
    crudo = os.environ.get(nombre)
    if crudo is None or crudo.strip() == "":
        return por_defecto
    try:
        valor = float(crudo)
    except ValueError:
        valor = math.nan
    if not math.isfinite(valor) or valor <= 0:
        logger.warning("%s invalido (%r); se usa el por defecto %s", nombre, crudo, por_defecto)
        return por_defecto
    return valor


def horas_borrador() -> float:
    """Horas que vive un borrador desde su ultima modificacion (`FORJA_BORRADOR_HORAS`)."""
    return _leer_entorno_positivo("FORJA_BORRADOR_HORAS", _HORAS_BORRADOR_POR_DEFECTO)


def barrido_minutos() -> float:
    """Cada cuantos minutos corre el barrido periodico (`FORJA_BARRIDO_MIN`)."""
    return _leer_entorno_positivo("FORJA_BARRIDO_MIN", _MINUTOS_BARRIDO_POR_DEFECTO)


def es_borrador(estado: dict[str, Any]) -> bool:
    return bool(estado.get("borrador")) and not estado.get("papelera")


def marcar_borrador(directorio: Path, doc_id: str, ahora: datetime) -> None:
    guardar_estado(directorio, doc_id, {"borrador": True, "creado": ahora.isoformat()})


def quitar_borrador(directorio: Path, doc_id: str) -> bool:
    """Promueve a permanente: borra el sidecar de borrador. True si cambio algo."""
    if not es_borrador(leer_estado(directorio, doc_id)):
        return False
    borrar_estado(directorio, doc_id)
    return True


def expiracion(mtime: float, estado: dict[str, Any]) -> datetime:
    """Caducidad de un borrador: ultima modificacion del archivo + horas_borrador()."""
    base = datetime.fromtimestamp(mtime, timezone.utc)
    creado = estado.get("creado")
    if isinstance(creado, str):
        base = max(base, datetime.fromisoformat(creado))
    return base + timedelta(hours=horas_borrador())


def ids_con_estado(directorio: Path) -> list[str]:
    """Ids que tienen sidecar de estado (borrador o papelera), sin leerlos."""
    return [r.name[: -len(_SUFIJO_ESTADO)] for r in sorted(directorio.glob(f"*{_SUFIJO_ESTADO}"))]
