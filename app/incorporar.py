"""Incorporate one document's geometry into another, in place (same id).

``POST /documentos/{id}/incorporar {desde, piezas?}`` 🔑:

- Destination STL (mesh): its mesh + the source tessellation (every piece or
  the named ones; STEP source = Forja's usual tessellation, STL source = its
  mesh) concatenated with named region metadata, preserved on export and
  restart. Connected shells never define piece identity.
- Destination STEP: the source's named solids are added to the compound and
  to ``solidos.json`` under their names (a clashing name gets a ``_2``,
  ``_3``... suffix). An STL source is added as a mesh body in the document's
  mesh store (``{id}.mallas/``, see ``app/mallas.py``): the STEP and
  ``solidos.json`` stay untouched (fase 3a, galeria ordenada).

``POST /documentos/{id}/incorporar_archivo {ruta, nombre_solido?}`` 🔑 and
``abrir_archivo(ruta, documento_id, nombre_solido)`` bring a file from the
allowed roots into an existing document the same way, without creating a new
document. ``nombre_solido`` names the single STEP solid or the STL body.

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
import mallas
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


class _IncorporarArchivoBody(BaseModel):
    ruta: str = Field(..., max_length=4096)
    nombre_solido: str | None = Field(default=None, max_length=64)


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


def _nombres_ocupados(destino: str) -> set[str]:
    """Nombres de solidos y de cuerpos de malla: un unico espacio de nombres."""
    de_solidos = {e["nombre"] for e in (solids.cargar(destino) or [])
                  if isinstance(e, dict) and isinstance(e.get("nombre"), str)}
    return de_solidos | set(mallas.nombres(documents.DOCUMENTOS_DIR, destino))


def _revertir(destino: str, ruta: Path, previo: dict[str, bytes]) -> None:
    """Vuelve a los bytes previos: STEP/STL, solidos.json y el almacen de mallas."""
    _escribir_atomico(ruta, previo[ruta.name])
    ruta_solidos = solids.ruta_solidos(destino)
    if "solidos.json" in previo:
        _escribir_atomico(ruta_solidos, previo["solidos.json"])
    else:
        ruta_solidos.unlink(missing_ok=True)
    mallas.restaurar_snapshot(documents.DOCUMENTOS_DIR, destino, previo)


def _publicar_cambio(destino: str, ruta: Path, previo: dict[str, bytes]) -> dict[str, Any]:
    """Re-analiza el documento tras escribirlo y publica su revision. Si el
    analisis falla, restaura ``previo`` y responde 422."""
    try:
        analisis = documents._analyze(ruta, ruta.suffix.lower())
    except Exception as exc:
        _revertir(destino, ruta, previo)
        raise HTTPException(status_code=422, detail="No se pudo verificar la incorporación; se restauró el documento.") from exc
    registro = {"id": destino, "nombre": documents._registry[destino]["nombre"], **analisis}
    documents._registry[destino] = registro
    documents._borrar_cache_malla(destino)
    ramas.borrar_cache(destino)
    documents.confirmar_revision(destino)
    return registro


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


def _malla_de_archivo(ruta: Path) -> trimesh.Trimesh:
    """STL de origen validado y limpio; cualquier rechazo es un 400 con una linea."""
    try:
        return mallas.preparar(mallas.desde_stl(ruta.read_bytes()))[0]
    except mallas.MallaInvalida as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _incorporar_mallas(destino: str, incoming: list[tuple[str, trimesh.Trimesh]], origen_nombre: str) -> dict[str, Any]:
    """Anade cada malla como cuerpo del almacen ``{destino}.mallas/``. El STEP y
    ``solidos.json`` no cambian. Los nombres se resuelven contra solidos y mallas."""
    usados = _nombres_ocupados(destino)
    renombradas: dict[str, str] = {}
    nuevos: list[dict[str, Any]] = []
    anadidos: list[dict[str, Any]] = []
    avisos: list[str] = []
    for nombre, malla in incoming:
        libre = _nombre_libre(nombre, usados)
        if libre != nombre:
            renombradas[nombre] = libre
        usados.add(libre)
        try:
            limpia, info = mallas.preparar(malla)
        except mallas.MallaInvalida as exc:
            raise HTTPException(status_code=400, detail=f"{libre}: {exc}") from exc
        nuevos.append({"nombre": libre, "stl": mesh.to_stl_bytes(limpia), "origen": origen_nombre,
                       "estanca": info["estanca"], "triangulos": info["triangulos"], "volumen": info["volumen"]})
        anadidos.append({"nombre": libre, "kind": "malla", "estanca": info["estanca"],
                         "triangulos": info["triangulos"]})
        avisos.extend(f"{libre}: {aviso}" for aviso in info["avisos"])
    mallas.anadir_cuerpos(documents.DOCUMENTOS_DIR, destino, nuevos)
    return {"renombradas": renombradas, "anadidos": anadidos, "avisos": avisos}


def _incorporar_step(destino: str, ruta: Path, traidos: dict[str, list[Any]]) -> dict[str, Any]:
    """Anade los solidos ``traidos`` ({nombre: [Solid]}) al STEP destino y
    reescribe el STEP y ``solidos.json``. Devuelve las renombres."""
    ruta_solidos = solids.ruta_solidos(destino)
    sidecar = ruta_solidos.read_bytes() if ruta_solidos.exists() else None
    try:
        propios = fusion._grupos(ruta.read_bytes(), sidecar)
    except fusion.FusionInvalida as exc:
        raise HTTPException(status_code=400, detail=f"destino: {exc}") from exc
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
    return {"renombradas": renombres}


def _grupos_de_step_archivo(ruta: Path, nombre_solido: str | None) -> dict[str, list[Any]]:
    """Solidos de un STEP suelto (sin documento): ``{nombre: [Solid]}``.
    ``nombre_solido`` solo puede renombrar un STEP de un unico solido."""
    try:
        _analisis, entradas = documents._analizar_con_solidos(ruta, ruta.suffix.lower())
    except Exception as exc:  # noqa: BLE001 - el mensaje lleva la causa
        raise HTTPException(status_code=400, detail=f"STEP no legible: {exc}") from exc
    if not entradas:
        raise HTTPException(status_code=400, detail="el STEP no tiene solidos")
    try:
        grupos = fusion._grupos(ruta.read_bytes(), json.dumps(entradas).encode())
    except fusion.FusionInvalida as exc:
        raise HTTPException(status_code=400, detail=f"STEP: {exc}") from exc
    if nombre_solido is not None:
        if len(grupos) != 1:
            raise HTTPException(
                status_code=400,
                detail=f"nombre_solido solo renombra un STEP de un solo solido (tiene {len(grupos)}); importalo sin nombre_solido",
            )
        (_viejo, lista), = grupos.items()
        grupos = {nombre_solido: lista}
    return grupos


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
        if _es_step(ruta) and _es_step(ruta_origen):
            try:
                traidos = _grupos_origen(origen, ruta_origen, piezas)
                extra = _incorporar_step(destino, ruta, traidos)
            except HTTPException:
                raise
            except Exception as exc:  # noqa: BLE001 - rollback to the previous bytes
                _revertir(destino, ruta, previo)
                raise HTTPException(status_code=422, detail=f"no se pudo incorporar: {exc}") from exc
            nombres = list(traidos)
            mensaje = f"incorporar {', '.join(nombres)} desde {nombre_origen}"
            snapshot = versioning.crear_snapshot(destino, f"antes de {mensaje}", previo)
        elif _es_step(ruta):
            incoming = _mesh_objects(origen, ruta_origen, piezas)
            nombres = [name for name, _ in incoming]
            mensaje = f"incorporar {', '.join(nombres) if nombres else 'todo'} desde {nombre_origen}"
            snapshot = versioning.crear_snapshot(destino, f"antes de {mensaje}", previo)
            try:
                extra = _incorporar_mallas(destino, incoming, nombre_origen)
            except HTTPException:
                _revertir(destino, ruta, previo)
                raise
            except Exception as exc:  # noqa: BLE001 - rollback to the previous bytes
                _revertir(destino, ruta, previo)
                raise HTTPException(status_code=422, detail=f"no se pudo incorporar: {exc}") from exc
        else:
            incoming = _mesh_objects(origen, ruta_origen, piezas)
            nombres = [name for name, _ in incoming]
            mensaje = f"incorporar {', '.join(nombres) if nombres else 'todo'} desde {nombre_origen}"
            snapshot = versioning.crear_snapshot(destino, f"antes de {mensaje}", previo)
            try:
                extra = _incorporar_stl(destino, ruta, incoming)
            except Exception as exc:
                _revertir(destino, ruta, previo)
                if isinstance(exc, HTTPException):
                    raise
                raise HTTPException(status_code=422, detail=f"no se pudo incorporar: {exc}") from exc
        registro = _publicar_cambio(destino, ruta, previo)
    return {**registro, "revision": documents._revisiones.get(destino), "incorporadas": nombres,
            "desde": origen, "snapshot_previo": snapshot, "mensaje": mensaje, **extra}


def incorporar_archivo(destino: str, ruta_texto: str, nombre_solido: str | None) -> dict[str, Any]:
    """Importa un STEP o STL de las raices permitidas dentro de ``destino``, sin
    crear documento nuevo. ``abrir_archivo(ruta, documento_id, nombre_solido)``
    y ``POST /documentos/{id}/incorporar_archivo`` usan esta funcion."""
    _ruta_doc(destino)
    if nombre_solido is not None:
        try:
            solids.validar_nombres([nombre_solido])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    origen = documents._validar_ruta_permitida(ruta_texto)
    if not origen.is_file():
        raise HTTPException(status_code=404, detail="archivo no encontrado")
    if origen.suffix.lower() not in documents._STEP_EXTS + documents._STL_EXTS:
        raise HTTPException(status_code=400, detail="formato no soportado (usar STEP o STL)")
    if origen.stat().st_size > documents._max_upload_bytes():  # mismo tope que una subida
        raise HTTPException(status_code=413, detail="archivo demasiado grande")
    with parametros.bloqueo(destino):
        ruta = _ruta_doc(destino)
        ramas.asegurar(destino)
        previo = ramas._snapshot_actual(destino, ruta)
        traidos: dict[str, list[Any]] = {}
        malla: trimesh.Trimesh | None = None
        if _es_step(origen):
            traidos = _grupos_de_step_archivo(origen, nombre_solido)
            nombres = list(traidos)
        else:
            nombre = nombre_solido or mesh_parts.default_name(origen.stem)
            malla = _malla_de_archivo(origen)
            nombres = [nombre]
        mensaje = f"importar {', '.join(nombres)} desde {origen.name}"
        snapshot = versioning.crear_snapshot(destino, f"antes de {mensaje}", previo)
        try:
            if _es_step(ruta) and _es_step(origen):
                extra = _incorporar_step(destino, ruta, traidos)
                anadidos = [{"nombre": extra["renombradas"].get(n, n), "kind": "solido"} for n in nombres]
            elif _es_step(ruta):
                extra = _incorporar_mallas(destino, [(nombres[0], malla)], origen.name)
                anadidos = extra["anadidos"]
            elif _es_step(origen):
                incoming = [(n, trimesh.util.concatenate([mesh.tessellate_para_exportar(s) for s in g]))
                            for n, g in traidos.items()]
                extra = _incorporar_stl(destino, ruta, incoming)
                anadidos = [{"nombre": extra["renombradas"].get(n, n), "kind": "malla"} for n in nombres]
            else:
                extra = _incorporar_stl(destino, ruta, [(nombres[0], malla)])
                anadidos = [{"nombre": extra["renombradas"].get(nombres[0], nombres[0]), "kind": "malla"}]
        except HTTPException:
            _revertir(destino, ruta, previo)
            raise
        except Exception as exc:  # noqa: BLE001 - rollback to the previous bytes
            _revertir(destino, ruta, previo)
            raise HTTPException(status_code=422, detail=f"no se pudo importar: {exc}") from exc
        _publicar_cambio(destino, ruta, previo)
    return {"id": destino, "anadidos": anadidos, "renombradas": extra["renombradas"],
            "aproximado": False, "avisos": extra.get("avisos", []),
            "revision": documents._revisiones.get(destino), "snapshot_previo": snapshot, "mensaje": mensaje}


@router.post("/documentos/{doc_id}/incorporar", dependencies=[Depends(auth.requiere_token)])
def incorporar_ruta(doc_id: str, body: _IncorporarBody) -> dict[str, Any]:
    return incorporar(doc_id, body.desde, body.piezas)


@router.post("/documentos/{doc_id}/incorporar_archivo", dependencies=[Depends(auth.requiere_token)])
def incorporar_archivo_ruta(doc_id: str, body: _IncorporarArchivoBody) -> dict[str, Any]:
    return incorporar_archivo(doc_id, body.ruta, body.nombre_solido)
