"""Incorporate one document's geometry into another, in place (same id).

``POST /documentos/{id}/incorporar {desde, piezas?}`` 🔑:

- Destination STL (mesh): its mesh + the source tessellation (every piece or
  the named ones; STEP source = Forja's usual tessellation, STL source = its
  mesh) concatenated with named region metadata, preserved on export and
  restart. Connected shells never define piece identity.
- Destination STEP: the source's named solids are added to the compound and
  to ``solidos.json`` under their names (a clashing name gets a ``_2``,
  ``_3``... suffix). An STL source has no B-rep solids -> 400.

Both run under the destination's per-document lock, with a snapshot of the
previous state first (``antes de incorporar ...``, restorable), so the
middleware leaves the step «incorporar <piezas> desde <nombre origen>».
The source document is only read, never written.
"""
from __future__ import annotations

import json
import contextlib
import os
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import trimesh
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import auth
import documents
import fusion
import notes
import parametros
import ramas
import solids
import mesh_parts
import versioning
from kernel import b123d_kernel, mesh

router = APIRouter()


class _IncorporarBody(BaseModel):
    desde: str = Field(..., max_length=64)
    piezas: list[str] | None = Field(default=None, max_length=200)


def _ruta_doc(doc_id: str) -> Path:
    if not notes._doc_id_valido(doc_id) or doc_id not in documents._registry:
        raise HTTPException(status_code=404, detail="documento no encontrado")
    ruta = documents._files.get(doc_id)
    if ruta is None or not ruta.exists():
        raise HTTPException(status_code=404, detail="archivo del documento no encontrado")
    return ruta


def _es_step(ruta: Path) -> bool:
    return ruta.suffix.lower() in documents._STEP_EXTS


def _grupos_origen(origen: str, ruta: Path, piezas: list[str] | None) -> dict[str, list[Any]]:
    """``{nombre: [Solid]}`` of the STEP source, filtered to ``piezas``."""
    ruta_solidos = solids.ruta_solidos(origen)
    sidecar = ruta_solidos.read_bytes() if ruta_solidos.exists() else None
    try:
        grupos = fusion._grupos(ruta.read_bytes(), sidecar)
    except fusion.FusionInvalida as exc:
        raise HTTPException(status_code=400, detail=f"origen: {exc}") from exc
    if piezas:
        faltan = [p for p in piezas if p not in grupos]
        if faltan:
            raise HTTPException(status_code=400, detail=f"piezas desconocidas en el origen: {', '.join(faltan)}")
        grupos = {p: grupos[p] for p in dict.fromkeys(piezas)}
    return grupos


def _mesh_objects(origen: str, ruta: Path, piezas: list[str] | None) -> list[tuple[str, trimesh.Trimesh]]:
    if not _es_step(ruta):
        try:
            objects = mesh_parts.objects(mesh.load_stl(ruta), mesh_parts.load(origen),
                                         Path(documents._registry[origen]["nombre"]).stem)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if piezas:
            by_name = dict(objects)
            if any(name not in by_name for name in piezas):
                raise HTTPException(status_code=400, detail="piezas desconocidas en el origen")
            objects = [(name, by_name[name]) for name in dict.fromkeys(piezas)]
        return objects
    groups = _grupos_origen(origen, ruta, piezas)
    return [(mesh_parts.default_name(Path(documents._registry[origen]["nombre"]).stem)
             if len(groups) == 1 and name == "solido_1" and not piezas else name,
             trimesh.util.concatenate([mesh.tessellate_para_exportar(s) for s in group]))
            for name, group in groups.items()]


def _escribir_atomico(ruta: Path, contenido: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=ruta.parent, prefix=f".{ruta.stem}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(contenido)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, ruta)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _nombre_libre(nombre: str, usados: set[str]) -> str:
    if nombre not in usados:
        return nombre
    n = 2
    while f"{nombre[:60]}_{n}" in usados:
        n += 1
    return f"{nombre[:60]}_{n}"


def _incorporar_stl(destino: str, ruta: Path, incoming: list[tuple[str, trimesh.Trimesh]]) -> dict[str, Any]:
    actual = mesh.load_stl(ruta)
    objects = mesh_parts.objects(actual, mesh_parts.load(destino),
                                 Path(documents._registry[destino]["nombre"]).stem)
    used = {name for name, _ in objects}
    renamed = {}
    for name, part in incoming:
        free = _nombre_libre(name, used)
        if free != name:
            renamed[name] = free
        used.add(free)
        objects.append((free, part))
    combined, entries = mesh_parts.combine(objects)
    # Sidecar and geometry are published under the document lock. Caller
    # restores BOTH on error; snapshots already carry solidos.json.
    _escribir_atomico(ruta, mesh.to_stl_bytes(combined))
    _escribir_atomico(solids.ruta_solidos(destino), json.dumps(entries).encode())
    return {"triangulos_antes": int(len(actual.faces)),
            "triangulos_despues": int(len(combined.faces)), "renombradas": renamed}


def _incorporar_step(destino: str, ruta: Path, origen: str, ruta_origen: Path,
                     piezas: list[str] | None) -> tuple[dict[str, Any], list[str]]:
    if not _es_step(ruta_origen):
        raise HTTPException(status_code=400, detail="no se puede incorporar una malla STL en un documento STEP (sin solidos B-rep); usa un destino STL")
    ruta_solidos = solids.ruta_solidos(destino)
    sidecar = ruta_solidos.read_bytes() if ruta_solidos.exists() else None
    try:
        propios = fusion._grupos(ruta.read_bytes(), sidecar)
    except fusion.FusionInvalida as exc:
        raise HTTPException(status_code=400, detail=f"destino: {exc}") from exc
    traidos = _grupos_origen(origen, ruta_origen, piezas)
    from build123d import Compound
    nombrados: dict[str, Any] = {}
    for nombre, lista in propios.items():
        nombrados[nombre] = lista[0] if len(lista) == 1 else Compound(children=lista)
    renombres: dict[str, str] = {}
    for nombre, lista in traidos.items():
        libre = _nombre_libre(nombre, set(nombrados))
        if libre != nombre:
            renombres[nombre] = libre
        nombrados[libre] = lista[0] if len(lista) == 1 else Compound(children=lista)
    with tempfile.TemporaryDirectory(prefix="forja-incorporar-") as tmp:
        salida = Path(tmp) / "incorporar.step"
        b123d_kernel.export_to_step(b123d_kernel.combinar_nombrados(nombrados), salida)
        forma = b123d_kernel.import_from_step(salida)
        entradas = solids.construir_entradas(forma, list(nombrados))
        contenido = salida.read_bytes()
    _escribir_atomico(ruta, contenido)
    _escribir_atomico(ruta_solidos, json.dumps(entradas).encode())
    return {"renombradas": renombres}, list(traidos)


def incorporar(destino: str, origen: str, piezas: list[str] | None) -> dict[str, Any]:
    _ruta_doc(destino)
    ruta_origen = _ruta_doc(origen)
    if origen == destino:
        raise HTTPException(status_code=400, detail="el origen y el destino son el mismo documento")
    nombre_origen = documents._registry[origen]["nombre"]
    with contextlib.ExitStack() as locks:
        for doc_id in sorted([destino, origen]):
            locks.enter_context(parametros.bloqueo(doc_id))
        ruta_origen = _ruta_doc(origen)
        ruta = _ruta_doc(destino)
        ramas.asegurar(destino)
        previo = ramas._snapshot_actual(destino, ruta)
        if _es_step(ruta):
            ruta_solidos = solids.ruta_solidos(destino)
            try:
                extra, nombres = _incorporar_step(destino, ruta, origen, ruta_origen, piezas)
            except HTTPException:
                raise
            except Exception as exc:  # noqa: BLE001 - rollback to the previous bytes
                _escribir_atomico(ruta, previo[ruta.name])
                if "solidos.json" in previo:
                    _escribir_atomico(ruta_solidos, previo["solidos.json"])
                raise HTTPException(status_code=422, detail=f"no se pudo incorporar: {exc}") from exc
            mensaje = f"incorporar {', '.join(nombres)} desde {nombre_origen}"
            snapshot = versioning.crear_snapshot(destino, f"antes de {mensaje}", previo)
        else:
            incoming = _mesh_objects(origen, ruta_origen, piezas)
            nombres = [name for name, _ in incoming]
            mensaje = f"incorporar {', '.join(nombres) if nombres else 'todo'} desde {nombre_origen}"
            snapshot = versioning.crear_snapshot(destino, f"antes de {mensaje}", previo)
            try:
                extra = _incorporar_stl(destino, ruta, incoming)
            except Exception as exc:
                _escribir_atomico(ruta, previo[ruta.name])
                sidecar_path = solids.ruta_solidos(destino)
                if "solidos.json" in previo:
                    _escribir_atomico(sidecar_path, previo["solidos.json"])
                else:
                    sidecar_path.unlink(missing_ok=True)
                if isinstance(exc, HTTPException):
                    raise
                raise HTTPException(status_code=422, detail=f"no se pudo incorporar: {exc}") from exc
        try:
            analisis = documents._analyze(ruta, ruta.suffix.lower())
        except Exception as exc:
            _escribir_atomico(ruta, previo[ruta.name])
            sidecar_path = solids.ruta_solidos(destino)
            if "solidos.json" in previo:
                _escribir_atomico(sidecar_path, previo["solidos.json"])
            else:
                sidecar_path.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail="No se pudo verificar la incorporación; se restauró el documento.") from exc
        registro = {"id": destino, "nombre": documents._registry[destino]["nombre"], **analisis}
        documents._registry[destino] = registro
        documents._borrar_cache_malla(destino)
        ramas.borrar_cache(destino)
        documents.confirmar_revision(destino)
    return {**registro, "revision": documents._revisiones.get(destino), "incorporadas": nombres,
            "desde": origen, "snapshot_previo": snapshot, "mensaje": mensaje, **extra}


@router.post("/documentos/{doc_id}/incorporar", dependencies=[Depends(auth.requiere_token)])
def incorporar_ruta(doc_id: str, body: _IncorporarBody) -> dict[str, Any]:
    return incorporar(doc_id, body.desde, body.piezas)
