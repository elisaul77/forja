"""Shared UI/MCP workspace contract. Typed requests; revision-bound writes."""

from __future__ import annotations
from collections import OrderedDict
from copy import deepcopy
import hashlib, json
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import Response, FileResponse
import auth, documents, parametros, ramas, versioning, eventos, notes
import design_state as ds
import design_geometry as geo
import design_analysis as analysis
import design_operations as ops
import design_jobs as jobs
import design_assets

router = APIRouter()
_cache = OrderedDict()
from threading import Lock

_cache_lock = Lock()


from design_contracts import Save, Query, Batch, Operation, Prepare


def capabilities():
    return {
        "version": 1,
        "units": "mm",
        "coordinate_frame": "document",
        "queries": list(Query.model_fields["kind"].annotation.__args__),
        "operations": list(Operation.model_fields["kind"].annotation.__args__),
        "limits": {"queries": 16, "points": 256, "jobs": 32, "candidates": 64},
        "supports": {
            "STEP": [
                "analytic_surfaces",
                "rigid_transform",
                "primitive_boolean",
                "mesh_checks",
            ],
            "STL": [
                "selected_point_fit",
                "rigid_transform",
                "primitive_boolean",
                "regional_deformation",
                "mesh_checks",
            ],
        },
        "workflow": [
            "read_design",
            "save_intent",
            "query",
            "prepare_candidate",
            "inspect_candidate",
            "apply_candidate",
            "export_design",
        ],
        "concurrency": "geometry revision + design state revision required for writes",
        "cancellation": "cooperative between native kernel calls; jobs expire on restart",
        "references": "region and face references are revision-bound; stale references are never silently reassigned",
        "schemas": {
            "design": ds.Design.model_json_schema(),
            "query": Query.model_json_schema(),
            "prepare": Prepare.model_json_schema(),
        },
    }


@router.get("/design/capabilities")
def get_capabilities():
    return capabilities()


def read(doc_id):
    ops.current(doc_id)
    s = ds.load(doc_id)
    rev = documents.calcular_revision(doc_id)
    parts = geo.objects(doc_id)
    return {
        "document": doc_id,
        "revision": rev,
        "state_revision": ds.digest(s),
        "design": s.model_dump(),
        "pieces": [
            {
                "name": n,
                "id": hashlib.sha256((doc_id + "\0" + n).encode()).hexdigest()[:24],
                **geo.summary(m),
            }
            for n, m in parts.items()
        ],
        "stale_intents": [
            i.id
            for i in s.intents
            if i.region and (i.region.revision != rev or i.region.piece not in parts)
        ],
        "annotations": notes.resumen(doc_id, detalle=False),
        "images": [
            {"id": key, "url": f"/documentos/{doc_id}/design/images/{key}"}
            for key in sorted({key for item in s.intents for key in item.images})
        ],
    }


@router.get("/documentos/{doc_id}/design")
def get_design(doc_id: str):
    with parametros.bloqueo(doc_id):
        return read(doc_id)


@router.put("/documentos/{doc_id}/design", dependencies=[Depends(auth.requiere_token)])
def save_design(doc_id: str, body: Save):
    with parametros.bloqueo(doc_id):
        ops.expect(doc_id, body.revision, body.state_revision)
        parts = geo.objects(doc_id)
        if (
            set(body.design.parts) - parts.keys()
            or set(body.design.print_rotations) - parts.keys()
        ):
            raise HTTPException(422, "Pieza desconocida en roles o postura")
        for collection in (body.design.intents, body.design.requirements):
            ids = [item.id for item in collection]
            if len(ids) != len(set(ids)):
                raise HTTPException(422, "Identificadores duplicados")
        for item in body.design.intents:
            for key in item.images:
                try:
                    exists = design_assets.path(doc_id, key).exists()
                except ValueError:
                    exists = False
                if not exists:
                    raise HTTPException(422, "Referencia visual inexistente")
            if item.region and (
                item.region.piece not in parts or item.region.revision != body.revision
            ):
                previous = next(
                    (x for x in ds.load(doc_id).intents if x.id == item.id), None
                )
                if previous != item:
                    raise HTTPException(
                        409, "La nueva selección no pertenece a la revisión actual"
                    )
        ramas.asegurar(doc_id)
        versioning.crear_snapshot(
            doc_id,
            "antes de actualizar intención de diseño",
            ramas._snapshot_actual(doc_id, documents._files[doc_id]),
        )
        ds.save(doc_id, body.design)
        eventos.publicar(
            "anotaciones_actualizadas", doc_id, body.revision, ["design", "historial"]
        )
        return read(doc_id)


def run_query(doc_id, q, parts, design, progress):
    a = parts.get(q.piece)
    if q.kind in ("inspect", "clearance", "section", "orientations") and a is None:
        raise ValueError("Selecciona una pieza existente")
    if q.kind == "inspect":
        return geo.inspect(doc_id, q.piece, q.points, q.radius_mm, q.limit)
    if q.kind.startswith("fit_"):
        return geo.fit(q.points, q.kind[4:])
    if q.kind == "align":
        return geo.rigid_matrix(q.points, q.target)
    if q.kind == "clearance":
        if q.other not in parts:
            raise ValueError("Selecciona segunda pieza")
        return geo.clearance(a, parts[q.other])
    if q.kind == "section":
        import numpy as np

        if np.linalg.norm(q.normal) < 1e-12:
            raise ValueError("Normal nula")
        return geo.section(a, q.origin, q.normal, q.limit)
    if q.kind == "evaluate":
        return analysis.evaluate(parts, design, progress)
    if q.kind == "fdm":
        if min(q.bed) <= 0:
            raise ValueError("Cama debe tener dimensiones positivas")
        chosen = {
            n: m
            for n, m in parts.items()
            if design.parts.get(n, ds.PartRole()).role == "design"
            and (not q.piece or n == q.piece)
        }
        if not chosen:
            raise ValueError("Sin piezas imprimibles seleccionadas")
        return analysis.print_check(
            chosen, q.nozzle, q.bed, q.angle, design.print_rotations, progress
        )
    if q.kind == "orientations":
        return analysis.orientations(a, q.bed)
    if q.kind == "compare":
        if not q.candidate:
            raise ValueError("Falta candidato")
        _, manifest = ops.candidate(doc_id, q.candidate)
        if manifest["revision"] != documents.calcular_revision(doc_id):
            raise ValueError("Comparación obsoleta; el documento cambió")
        return {
            "difference": manifest["difference"],
            "checks": manifest["checks"],
            "limitations": manifest["limitations"],
        }
    raise ValueError("Consulta no admitida")


def query(doc_id, body):
    def perform(progress=lambda *_: None):
        # A batch observes one immutable revision. Lock also protects the
        # exact B-rep inspection path which reloads source data on demand.
        with parametros.bloqueo(doc_id):
            if documents.calcular_revision(doc_id) != body.revision:
                raise ValueError("Revisión obsoleta; vuelve a consultar")
            design = ds.load(doc_id)
            key = hashlib.sha256(
                (
                    doc_id
                    + body.revision
                    + ds.digest(design)
                    + body.model_dump_json(exclude={"background"})
                ).encode()
            ).hexdigest()
            with _cache_lock:
                if key in _cache:
                    result = deepcopy(_cache[key])
                    result["cached"] = True
                    return result
            parts = geo.objects(doc_id)
            results = []
            for i, q in enumerate(body.queries):
                progress(i / len(body.queries), q.kind)
                try:
                    results.append(
                        {
                            "kind": q.kind,
                            "result": run_query(
                                doc_id,
                                q,
                                parts,
                                design,
                                lambda v, m: progress((i + v) / len(body.queries), m),
                            ),
                        }
                    )
                except (ValueError, RuntimeError) as exc:
                    results.append(
                        {"kind": q.kind, "error": str(exc), "status": "not_checked"}
                    )
            result = {
                "revision": body.revision,
                "state_revision": ds.digest(design),
                "results": results,
                "cached": False,
            }
            with _cache_lock:
                _cache[key] = deepcopy(result)
                while len(_cache) > 32:
                    _cache.popitem(last=False)
            return result

    if body.background:
        return jobs.submit(doc_id, body.revision, perform)
    return perform()


@router.post(
    "/documentos/{doc_id}/design/query", dependencies=[Depends(auth.requiere_token)]
)
def query_route(doc_id: str, body: Batch):
    try:
        return query(doc_id, body)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.post(
    "/documentos/{doc_id}/design/candidates",
    dependencies=[Depends(auth.requiere_token)],
)
def prepare_route(doc_id: str, body: Prepare):
    try:
        return ops.prepare(doc_id, body)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/documentos/{doc_id}/design/candidates")
def list_candidates(doc_id: str):
    ops.current(doc_id)
    root = ops.directory(doc_id)
    return {
        "candidates": [
            json.loads(p.read_text()) for p in sorted(root.glob("*/manifest.json"))
        ]
    }


@router.get("/documentos/{doc_id}/design/candidates/{key}/preview")
def preview(doc_id: str, key: str):
    try:
        p, _ = ops.candidate(doc_id, key)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return FileResponse(
        p / "preview.stl", media_type="model/stl", filename="candidato.stl"
    )


@router.post(
    "/documentos/{doc_id}/design/candidates/{key}/apply",
    dependencies=[Depends(auth.requiere_token)],
)
def apply_route(doc_id: str, key: str):
    try:
        return ops.apply(doc_id, key)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.delete(
    "/documentos/{doc_id}/design/candidates/{key}",
    dependencies=[Depends(auth.requiere_token)],
)
def discard(doc_id: str, key: str):
    import shutil

    with parametros.bloqueo(doc_id):
        try:
            p, m = ops.candidate(doc_id, key)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        if m["state"] == "applied":
            raise HTTPException(
                409, "Candidato aplicado: usa el historial para restaurar"
            )
        shutil.rmtree(p)
    return {"discarded": key}


@router.get("/design/jobs/{key}")
def job(key: str):
    try:
        return jobs.read(key)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e


@router.post("/design/jobs/{key}/cancel", dependencies=[Depends(auth.requiere_token)])
def cancel(key: str):
    try:
        return jobs.read(key, cancel=True)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/documentos/{doc_id}/design/export/{fmt}")
def export_file(
    doc_id: str, fmt: Literal["stl", "3mf"], pose: bool = True, manifest: bool = False
):
    try:
        data, report = ops.export_design(doc_id, fmt, pose)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if manifest:
        return report
    return Response(
        data,
        media_type="model/stl" if fmt == "stl" else "model/3mf",
        headers={
            "Content-Disposition": f'attachment; filename="design.{fmt}"',
            "X-Geometry-SHA256": report["sha256"],
            "X-Forja-Revision": report["revision"],
        },
    )


@router.post(
    "/documentos/{doc_id}/design/images", dependencies=[Depends(auth.requiere_token)]
)
async def upload_image(doc_id: str, file: UploadFile = File(...)):
    from starlette.concurrency import run_in_threadpool

    ops.current(doc_id)
    data = await file.read(design_assets.MAX_BYTES + 1)

    def save():
        with parametros.bloqueo(doc_id):
            ops.current(doc_id)
            return design_assets.store(doc_id, data)

    try:
        return await run_in_threadpool(save)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/documentos/{doc_id}/design/images/{key}")
def reference_image(doc_id: str, key: str):
    ops.current(doc_id)
    try:
        path = design_assets.path(doc_id, key)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    if not path.exists():
        raise HTTPException(404, "Foto no encontrada")
    return FileResponse(
        path, media_type="image/png", headers={"X-Content-Type-Options": "nosniff"}
    )
