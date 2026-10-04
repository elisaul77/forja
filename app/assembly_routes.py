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
import tempfile
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
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
from kernel import b123d_kernel as kernel

router = APIRouter(prefix="/documentos/{doc_id}/ensamble")


class Definition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    articulaciones: list[dict[str, Any]] = Field(min_length=1, max_length=60)


class Pose(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    valores: dict[str, float]


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


@router.get("")
def get_assembly(doc_id: str):
    _path(doc_id)
    state = _estado_actual(doc_id)
    entries = solids.cargar(doc_id) or []
    return {"id": doc_id, "piezas": list(dict.fromkeys(e["nombre"] for e in entries)),
            "articulaciones": (
                [_articulacion_compacta(joint) for joint in state["articulaciones"]]
                if state else []
            ),
            "valores": state["valores"] if state else {},
            "obsoleto": assemblies.is_stale(doc_id) if state else False}


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
        return get_assembly(doc_id)


@router.delete("", dependencies=[Depends(auth.requiere_token)])
def clear_assembly(doc_id: str):
    _path(doc_id)  # rejected before the lock (see `_path`)
    with parametros.bloqueo(doc_id):
        path = _path(doc_id)  # re-check under the lock: the document may be gone
        versioning.crear_snapshot(doc_id, "antes de quitar articulaciones (conservar posición)", _files(doc_id, path))
        assemblies.clear(doc_id)
        eventos.publicar("ensamble_actualizado", doc_id, cambios=["ensamble", "historial"])
        return get_assembly(doc_id)


@router.post("/pose", dependencies=[Depends(auth.requiere_token)])
def apply_pose(doc_id: str, body: Pose):
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
