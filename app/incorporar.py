"""Incorporate one document's geometry into another, in place (same id).

``POST /documentos/{id}/incorporar {desde, piezas?}`` 🔑:

- Destination STL (mesh): its mesh + the source tessellation (every piece or
  the named ones; STEP source = Forja's usual tessellation, STL source = its
  mesh) concatenated and written atomically.
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


def _malla_origen(origen: str, ruta: Path, piezas: list[str] | None) -> tuple[trimesh.Trimesh, list[str]]:
    if not _es_step(ruta):
        if piezas:
            raise HTTPException(status_code=400, detail="un origen STL no tiene piezas con nombre (omite piezas)")
        return mesh.load_stl(ruta), []
    if not piezas:
        forma = b123d_kernel.import_from_step(ruta)
        nombres = documents._nombres_piezas(origen)
        return mesh.tessellate_to_trimesh(forma), nombres
    grupos = _grupos_origen(origen, ruta, piezas)
    mallas = [mesh.tessellate_to_trimesh(s) for lista in grupos.values() for s in lista]
    return trimesh.util.concatenate(mallas), list(grupos)


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
    while f"{nombre}_{n}" in usados:
        n += 1
    return f"{nombre}_{n}"


def _incorporar_stl(ruta: Path, malla_nueva: trimesh.Trimesh) -> dict[str, Any]:
    actual = mesh.load_stl(ruta)
    combinada = trimesh.Trimesh(
        vertices=np.concatenate([actual.vertices, malla_nueva.vertices]),
        faces=np.concatenate([actual.faces, malla_nueva.faces + len(actual.vertices)]),
        process=False,
    )
    _escribir_atomico(ruta, mesh.to_stl_bytes(combinada))
    return {"triangulos_antes": int(len(actual.faces)), "triangulos_despues": int(len(combinada.faces))}


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
    with parametros.bloqueo(destino):
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
            malla_nueva, nombres = _malla_origen(origen, ruta_origen, piezas)
            mensaje = f"incorporar {', '.join(nombres) if nombres else 'todo'} desde {nombre_origen}"
            snapshot = versioning.crear_snapshot(destino, f"antes de {mensaje}", previo)
            extra = _incorporar_stl(ruta, malla_nueva)
        analisis = documents._analyze(ruta, ruta.suffix.lower())
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
