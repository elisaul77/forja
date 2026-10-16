"""Document assembly endpoints: shared REST contract for the viewer and MCP.

Phase 6.3 wire vocabulary — Spanish, like every other payload in the project
(no English aliases: nothing had shipped on the old keys). Read response
``{id, piezas, articulaciones, valores, obsoleto}``; joint ``{id, tipo:
fijo|giro|deslizamiento, padre, hijo, origen, eje, limites, valor}``;
reference ``{tipo, id, punto}``. The internal ``huella`` fingerprint is still
*accepted and validated* at definition time (it proves the reference belongs
to that face/edge) and is never echoed back in a response.

A 409 with `assemblies.MENSAJE_FORMATO` answers every path that meets a
`state.json` this build cannot read (a pre-6.3 one, `version: 1`): there is
nothing to migrate, and `DELETE …/ensamble` — which never reads the state —
is the recovery path.
"""
from __future__ import annotations

import copy
import json
import tempfile
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

import assemblies
import auth
import documents
import eventos
import naming
import notes
import parametros
import solids
import versioning
from checks import pose_clearance
from kernel import b123d_kernel as kernel

router = APIRouter(prefix="/documentos/{doc_id}/ensamble")


class Definition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    articulaciones: list[dict[str, Any]] = Field(min_length=1, max_length=60)


class Pose(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    valores: dict[str, float]


class SceneCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    revision: str = Field(min_length=1)
    valores: dict[str, float]
    articulaciones_activas: list[str] = Field(min_length=1, max_length=60)


class MotionCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    revision: str = Field(min_length=1)
    valores_desde: dict[str, float]
    valores_hasta: dict[str, float]
    muestras: int = Field(default=12, ge=2, le=24)


# The wire shape of a joint: exactly these keys, in this vocabulary, plus a
# sanitized reference per side when the definition carried one.
_CLAVES_ARTICULACION = ("id", "tipo", "padre", "hijo", "origen", "eje", "limites", "valor")
_CLAVES_REFERENCIA = ("tipo", "id", "punto")


def _referencia_compacta(referencia: dict[str, Any]) -> dict[str, Any]:
    """``{tipo, id, punto}`` — the documented reference shape. ``huella`` is
    the internal geometric fingerprint a definition is validated against
    (`assemblies._reference`), never part of a response: it is large
    (centroid/normal/area) and meaningless to a consumer, and
    `naming.resolver_referencia` echoes it back, so returning the stored
    dict as-is leaked it through `GET /ensamble` (Phase 6.3)."""
    return {clave: referencia.get(clave) for clave in _CLAVES_REFERENCIA}


def _articulacion_compacta(joint: dict[str, Any]) -> dict[str, Any]:
    proyectada = {clave: joint[clave] for clave in _CLAVES_ARTICULACION}
    for side in ("padre", "hijo"):
        key = f"referencia_{side}"
        if joint.get(key) is not None:
            proyectada[key] = _referencia_compacta(joint[key])
    return proyectada


def _estado_actual(doc_id: str) -> dict[str, Any] | None:
    """`assemblies.load` with the unreadable-state hazard mapped to a 409
    carrying its own message (Phase 6.3 fix round): a `state.json` from a
    build before the vocabulary rename is not migrated — `DELETE …/ensamble`
    removes it and it is defined again — and it must never surface as a bare
    500 from a missing key."""
    try:
        return assemblies.load(doc_id)
    except assemblies.EstadoIncompatible as exc:
        raise HTTPException(409, str(exc)) from exc


def _path(doc_id: str) -> Path:
    """The document-existence check every route here already performs
    (`documents._ruta_step_o_404`: 404 unknown id, 404 missing file, 400 for
    an STL document). Read-only routes call it once; the three mutations
    call it *before* and *inside* `parametros.bloqueo`: the lock table is
    created-and-kept per id and never evicted, so an id that names no
    document must not grow it (Phase 6.3, the Phase 6.2 residual — mirrors
    `app/notes.py`), while the inner call keeps today's observable result
    when the document disappears between the two (a 404, never a mutation
    through the stale path)."""
    return documents._ruta_step_o_404(doc_id)


def _files(doc_id: str, path: Path) -> dict[str, bytes]:
    result = {path.name: path.read_bytes()}
    for name, file in [("notas.json", notes.ruta_notas(doc_id)), ("solidos.json", solids.ruta_solidos(doc_id))]:
        if file.exists():
            result[name] = file.read_bytes()
    result = documents._con_parametros_snapshot(result, doc_id)
    result.update(assemblies.snapshot_files(doc_id))
    return result


def _assembly_payload(doc_id: str):
    state = _estado_actual(doc_id)
    entries = solids.cargar(doc_id) or []
    return {"id": doc_id, "piezas": list(dict.fromkeys(e["nombre"] for e in entries)),
            "articulaciones": (
                [_articulacion_compacta(joint) for joint in state["articulaciones"]]
                if state else []
            ),
            "valores": state["valores"] if state else {},
            "obsoleto": assemblies.is_stale(doc_id) if state else False}


@router.get("")
def get_assembly(doc_id: str, response: Response):
    _path(doc_id)  # unknown ids never create a lock entry
    with parametros.bloqueo(doc_id):
        _path(doc_id)
        payload = _assembly_payload(doc_id)
        # The viewer only previews a pose when this revision matches the
        # component mesh. Keep the body stable for existing REST/MCP clients.
        response.headers["X-Forja-Revision"] = documents._revisiones.get(doc_id, "")
        response.headers["Cache-Control"] = "no-store"
        return payload


def _scenes_path(doc_id: str) -> Path:
    return documents.DOCUMENTOS_DIR / f"{doc_id}.escenas.json"


@router.get("/escenas")
def get_scenes(doc_id: str, response: Response):
    """Scripted multi-step motions (keyframes + captions) for the viewer.

    Read-only: `{escenas: [{nombre, descripcion, claves: [{t, valores,
    rotulo}]}]}` from `<doc>.escenas.json`; an empty list when absent."""
    _path(doc_id)
    response.headers["Cache-Control"] = "no-store"
    file = _scenes_path(doc_id)
    if not file.exists():
        return {"escenas": []}
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(422, "El archivo de escenas no es JSON válido.") from exc
    return {"escenas": data.get("escenas", []) if isinstance(data, dict) else []}


@router.get("/comprobar-posicion")
def check_assembly_position(
    doc_id: str,
    response: Response,
    articulacion: str = Query(min_length=1),
    valor: float = Query(),
    revision: str = Query(min_length=1),
):
    """Exact local collision/clearance at a sampled pose, without writes.

    Client supplies the geometry revision it previewed. The check uses the
    same immutable rest geometry and joint transform as `/pose`, under the
    document lock, but never exports STEP or creates a history entry.
    """
    _path(doc_id)
    with parametros.bloqueo(doc_id):
        _path(doc_id)
        if revision != documents._revisiones.get(doc_id):
            raise HTTPException(409, "la geometría cambió; actualiza la vista")
        state = _estado_actual(doc_id)
        if state is None:
            raise HTTPException(422, "el documento no tiene ensamble")
        if assemblies.is_stale(doc_id):
            raise HTTPException(409, "el ensamble está desactualizado")
        try:
            moving = pose_clearance.moving_names(state["articulaciones"], articulacion)
            named = assemblies.pose_shapes(doc_id, {articulacion: valor})
            result = pose_clearance.check_pose(named, moving)
        except (ValueError, OverflowError) as exc:
            raise HTTPException(422, str(exc)) from exc
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Forja-Revision"] = revision
        return {"articulacion": articulacion, "valor": valor, **result}


def _scene_context(doc_id: str, revision: str):
    _path(doc_id)
    if revision != documents._revisiones.get(doc_id):
        raise HTTPException(409, "la geometría cambió; actualiza la vista")
    if assemblies.is_stale(doc_id):
        raise HTTPException(409, "el ensamble está desactualizado")
    try:
        return assemblies.rest_shapes(doc_id)
    except (ValueError, OverflowError) as exc:
        raise HTTPException(422, str(exc)) from exc


def _moving_for(state: dict, joint_ids: list[str]) -> set[str]:
    if len(set(joint_ids)) != len(joint_ids):
        raise ValueError("articulaciones repetidas")
    moving: set[str] = set()
    for joint_id in joint_ids:
        moving.update(pose_clearance.moving_names(state["articulaciones"], joint_id))
    return moving


@router.post("/comprobar-escena")
def check_assembly_scene(doc_id: str, body: SceneCheck, response: Response):
    """Read-only exact check of one pose with several driven joints."""
    _path(doc_id)
    with parametros.bloqueo(doc_id):
        state, rest = _scene_context(doc_id, body.revision)
        try:
            moving = _moving_for(state, body.articulaciones_activas)
            named = assemblies.pose_shapes_from_rest(state, rest, body.valores)
            result = pose_clearance.check_scene(named, moving)
        except (ValueError, OverflowError) as exc:
            raise HTTPException(422, str(exc)) from exc
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Forja-Revision"] = body.revision
        return result


@router.post("/comprobar-recorrido")
def check_assembly_motion(doc_id: str, body: MotionCheck, response: Response):
    """Sample a coordinated rigid motion; report the first NEW penetration.

    A clean result covers only these discrete samples, never continuous time.
    No STEP is modified and the rest geometry is imported just once.
    """
    _path(doc_id)
    with parametros.bloqueo(doc_id):
        state, rest = _scene_context(doc_id, body.revision)
        if not body.valores_desde or set(body.valores_desde) != set(body.valores_hasta):
            raise HTTPException(422, "elige las mismas articulaciones para inicio y fin")
        try:
            moving = _moving_for(state, list(body.valores_desde))
            start = assemblies._values(state, body.valores_desde)
            end = assemblies._values(state, body.valores_hasta)
            baseline = None
            last_safe = 0.0
            first_hit = None
            checked = 0
            complete = True
            initial_hits = 0
            last_result = None
            for index in range(body.muestras + 1):
                fraction = index / body.muestras
                values = {joint_id: start[joint_id] + (end[joint_id] - start[joint_id]) * fraction
                          for joint_id in start}
                scene = assemblies.pose_shapes_from_rest(state, rest, values)
                result = pose_clearance.check_scene(scene, moving, max_exact_pairs=100)
                last_result = result
                checked += 1
                if not result["completo"] or result["choques_mas"]:
                    complete = False
                    break
                volumes = {tuple(sorted((hit["a"], hit["b"]))): hit["volumen_mm3"]
                           for hit in result["choques"]}
                if baseline is None:
                    baseline = volumes
                    initial_hits = len(volumes)
                else:
                    new_hits = [hit for hit in result["choques"]
                                if hit["volumen_mm3"] > baseline.get(
                                    tuple(sorted((hit["a"], hit["b"]))), 0.0
                                ) + pose_clearance.colisiones.TOLERANCIA_MM3_POR_DEFECTO]
                    if new_hits:
                        first_hit = {"fraccion": fraction, "par": new_hits[0]}
                        break
                    last_safe = fraction
        except (ValueError, OverflowError) as exc:
            raise HTTPException(422, str(exc)) from exc
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Forja-Revision"] = body.revision
        return {"completo": complete, "muestras_solicitadas": body.muestras + 1,
                "muestras_comprobadas": checked, "primer_choque": first_hit,
                "fraccion_segura_muestreada": last_safe,
                "choques_iniciales": initial_hits, "ultima_posicion": last_result}


@router.post("", dependencies=[Depends(auth.requiere_token)])
def define_assembly(doc_id: str, body: Definition):
    _path(doc_id)  # rejected before the lock (see `_path`)
    with parametros.bloqueo(doc_id):
        path = _path(doc_id)  # re-check under the lock: the document may be gone
        before = _files(doc_id, path)
        try:
            assemblies.define(doc_id, path, solids.cargar(doc_id) or [], body.articulaciones)
        except assemblies.EstadoIncompatible as exc:
            raise HTTPException(409, str(exc)) from exc
        except (ValueError, OverflowError) as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            versioning.crear_snapshot(doc_id, "antes de definir articulaciones", before)
        except Exception:
            assemblies.restore_files(doc_id, before)
            raise
        eventos.publicar("ensamble_actualizado", doc_id, cambios=["ensamble", "historial"])
        return _assembly_payload(doc_id)


@router.delete("", dependencies=[Depends(auth.requiere_token)])
def clear_assembly(doc_id: str):
    _path(doc_id)  # rejected before the lock (see `_path`)
    with parametros.bloqueo(doc_id):
        path = _path(doc_id)  # re-check under the lock: the document may be gone
        versioning.crear_snapshot(doc_id, "antes de quitar articulaciones (conservar posición)", _files(doc_id, path))
        assemblies.clear(doc_id)
        eventos.publicar("ensamble_actualizado", doc_id, cambios=["ensamble", "historial"])
        return _assembly_payload(doc_id)


@router.post("/pose", dependencies=[Depends(auth.requiere_token)])
def apply_pose(doc_id: str, body: Pose):
    import design_state
    design_state.require_unprotected_legacy_edit(doc_id)
    start = time.perf_counter()
    _path(doc_id)  # rejected before the lock (see `_path`)
    with parametros.bloqueo(doc_id):
        path = _path(doc_id)  # re-check under the lock: the document may be gone
        if _estado_actual(doc_id) is not None and assemblies.is_stale(doc_id):
            raise HTTPException(409, "la geometria cambio; revisar y redefinir las articulaciones")
        before = _files(doc_id, path)
        old_record = copy.deepcopy(documents._registry[doc_id])
        try:
            named = assemblies.pose_shapes(doc_id, body.valores)
            shape = kernel.combinar_nombrados(named)
            with tempfile.TemporaryDirectory(prefix="forja-pose-") as directory:
                output = Path(directory) / "pose.step"
                kernel.export_to_step(shape, output)
                rebuilt = kernel.import_from_step(output)
                analysis = kernel.analyze(rebuilt)
                if not analysis.valido or analysis.solidos != len(named):
                    raise ValueError("la pose produjo geometria invalida")
                content = output.read_bytes()
            entries = solids.construir_entradas(rebuilt, list(named))
        except (ValueError, OverflowError) as exc:
            raise HTTPException(422, str(exc)) from exc

        versioning.crear_snapshot(doc_id, "antes de mover articulaciones", before)
        try:
            path.write_bytes(content)
            solids.guardar(doc_id, entries)
            annotations = notes.cargar(doc_id)
            for collection in ("notas", "trazos"):
                for item in annotations.get(collection, []):
                    if item.get("referencia"):
                        resolved = naming.resolver_referencia(rebuilt, item["referencia"])
                        item["referencia_perdida"] = resolved.pop("referencia_perdida")
                        item["ambigua"] = resolved.pop("ambigua", False)
                        item["referencia"] = resolved
            notes.guardar(doc_id, annotations)
            state = assemblies.set_revision(doc_id, content, body.valores)
            record = {**old_record, "bbox": list(analysis.bbox), "volumen": analysis.volumen,
                      "solidos": analysis.solidos, "valido": analysis.valido}
            documents._registry[doc_id] = record
        except Exception:
            path.write_bytes(before[path.name])
            for name, target in [("notas.json", notes.ruta_notas(doc_id)), ("solidos.json", solids.ruta_solidos(doc_id))]:
                if name in before:
                    target.write_bytes(before[name])
                else:
                    target.unlink(missing_ok=True)
            assemblies.restore_files(doc_id, before)
            documents._registry[doc_id] = old_record
            raise
        documents.confirmar_revision(doc_id)
        eventos.publicar("ensamble_actualizado", doc_id, cambios=["ensamble"])
        return {**record, "revision": documents._revisiones.get(doc_id),
                "valores": state["valores"], "ms": round(1000 * (time.perf_counter() - start))}
