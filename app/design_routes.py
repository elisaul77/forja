"""Shared UI/MCP workspace contract. Typed requests; revision-bound writes."""

from __future__ import annotations
from collections import OrderedDict
from copy import deepcopy
import hashlib, json
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query as ParametroURL, UploadFile, File
from fastapi.responses import Response, FileResponse
from datetime import datetime, timezone
import uuid
import auth, documents, parametros, papelera, ramas, versioning, eventos, notes
import design_state as ds
import design_geometry as geo
import design_analysis as analysis
import design_operations as ops
import design_features as features
import design_jobs as jobs
import design_assets

router = APIRouter()
_cache = OrderedDict()
from threading import Lock

_cache_lock = Lock()


from design_contracts import (
    Save,
    Query,
    Batch,
    Operation,
    Prepare,
    IntentCreate,
    IntentUserAction,
    IntentAgent,
)

MAX_INTENTS = 256
MAX_MESSAGES = 100
MAX_MARKS = 64
MAX_IMAGES = 6


def capabilities():
    return {
        "version": 1,
        "units": "mm",
        "coordinate_frame": "document",
        "queries": list(Query.model_fields["kind"].annotation.__args__),
        "operations": list(Operation.model_fields["kind"].annotation.__args__),
        "primitives": list(Operation.model_fields["primitive"].annotation.__args__),
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


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _candidatos(doc_id):
    root = ops.directory(doc_id)
    if not root.exists():
        return []
    return [json.loads(p.read_text()) for p in sorted(root.glob("*/manifest.json"))]


def _estado_candidato(c, rev) -> str:
    """prepared | applied | reverted. An applied candidate is only "applied"
    while the document is still at the revision it produced; after a restore
    or any later geometry change it is "reverted" (never resolved)."""
    if c["state"] == "applied" and c.get("applied_revision") != rev:
        return "reverted"
    return c["state"]


def _atencion(intent, vinculados, rev):
    """Attention shown to the user, derived from stored data only. Stored
    status is what a person or agent set explicitly; the rest is derived."""
    if intent.status == "dismissed":
        return "dismissed"
    if intent.status in ("resolved", "accepted"):
        return "resolved"
    ultimo = intent.messages[-1] if intent.messages else None
    if ultimo is not None and ultimo.author == "user":
        # A user reply newer than every linked candidate is waiting for the agent.
        if all(ultimo.created > c.get("created", "") for c in vinculados):
            return "waiting_agent"
    estados = [_estado_candidato(c, rev) for c in vinculados]
    if "applied" in estados:
        return "applied"
    if "prepared" in estados:
        return "proposal"
    if "reverted" in estados:
        return "reverted"
    if intent.question:
        return "question"
    if intent.interpretation:
        return "understood"
    return "waiting_agent"


def _candidato_obsoleto(c, rev, estado) -> bool:
    """A prepared candidate is stale when the geometry moved or its checks'
    constraints (roles/requirements) changed. Legacy manifests without
    `constraints_revision` fall back to the full state revision."""
    if c["state"] != "prepared":
        return False
    if c["revision"] != rev:
        return True
    if "constraints_revision" in c:
        return c["constraints_revision"] != ds.constraints_digest(estado)
    return c.get("state_revision") != ds.digest(estado)


def _vista_indicacion(intent, rev, estado, parts, marcas, candidatos):
    por_id = {m["n"]: m for m in marcas}
    vinculados = [c for c in candidatos if c.get("intent") == intent.id]
    region = intent.region
    stale = bool(region) and (region.revision != rev or region.piece not in parts)
    stale = stale or any(
        por_id[m]["referencia_perdida"] for m in intent.marks if m in por_id
    )
    return {
        "id": intent.id,
        "text": intent.text,
        "status": intent.status,
        "attention": _atencion(intent, vinculados, rev),
        "stale": stale,
        "piece": region.piece if region else None,
        "region": region.model_dump() if region else None,
        "marks": list(intent.marks),
        "marks_detail": [por_id[m] for m in intent.marks if m in por_id],
        "missing_marks": [m for m in intent.marks if m not in por_id],
        "images": list(intent.images),
        "interpretation": intent.interpretation,
        "question": intent.question,
        "messages": [m.model_dump() for m in intent.messages],
        "candidates": [
            {
                "id": c["id"],
                "state": _estado_candidato(c, rev),
                "summary": c.get("summary", ""),
                "checks": {
                    k: c["checks"][k]
                    for k in ("passed", "failed", "not_checked", "approval")
                },
                "before_snapshot": c.get("before_snapshot"),
                "stale": _candidato_obsoleto(c, rev, estado),
                "created": c.get("created"),
            }
            for c in vinculados
        ],
    }


def read(doc_id):
    ops.current(doc_id)
    s = ds.load(doc_id)
    rev = documents.calcular_revision(doc_id)
    parts = geo.objects(doc_id)
    marcas = notes.resumen(doc_id, detalle=False)
    candidatos = _candidatos(doc_id)
    enlazadas = {m for i in s.intents for m in i.marks}
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
        "annotations": marcas,
        "intents_view": [
            _vista_indicacion(i, rev, s, parts, marcas, candidatos) for i in s.intents
        ],
        "loose_marks": [m for m in marcas if m["n"] not in enlazadas],
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
        # Guardar el diseno es intencion deliberada: un borrador se conserva (fase 2).
        papelera.quitar_borrador(documents.DOCUMENTOS_DIR, doc_id)
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
        result = ops.prepare(doc_id, body)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    eventos.publicar(
        "anotaciones_actualizadas", doc_id, documents.calcular_revision(doc_id),
        ["design", "historial"],
    )
    return result


@router.get("/documentos/{doc_id}/design/referencias")
def referencias_route(
    doc_id: str,
    pieza: str = ParametroURL(min_length=1, max_length=128),
    tipo: Literal["arista", "cara"] = "arista",
):
    """Aristas o caras de una pieza B-rep con su huella (solo lectura, ADR-0029)."""
    try:
        return features.referencias(doc_id, pieza, tipo)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/documentos/{doc_id}/design/candidates")
def list_candidates(doc_id: str):
    rev, _ = ops.current(doc_id)
    estado = ds.load(doc_id)
    return {
        "candidates": [
            {
                **c,
                "manifest_state": c["state"],
                "state": _estado_candidato(c, rev),
                "stale": _candidato_obsoleto(c, rev, estado),
            }
            for c in _candidatos(doc_id)
        ]
    }


def _preview_bundle(p):
    """FJP1 bundle for candidates prepared before preview.piezas existed."""
    import json as _json
    import piece_mesh
    from kernel import b123d_kernel, mesh as km

    entries = _json.loads((p / "solidos.json").read_text())
    if (p / "geometry.step").is_file():
        shape = b123d_kernel.import_from_step(p / "geometry.step")
        info = b123d_kernel.analyze(shape)
        return piece_mesh.from_step(shape, entries, metadata_available=True,
                                    bbox=list(info.bbox), volume=info.volumen)
    tri = km.load_stl(p / "geometry.stl")
    return piece_mesh.from_stl(tri, bbox=tri.bounds.T.reshape(-1).tolist(),
                               volume=float(abs(tri.volume)), entries=entries)


@router.get("/documentos/{doc_id}/design/candidates/{key}/preview")
def preview(doc_id: str, key: str, piezas: bool = False):
    try:
        p, _ = ops.candidate(doc_id, key)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    # ?piezas=1: FJP1 bundle with per-piece ranges (older candidates fall back to STL).
    if piezas:
        bundle = p / "preview.piezas"
        if not bundle.is_file():
            try:
                bundle.write_bytes(_preview_bundle(p))
            except Exception:  # sin desglose fiable: STL plano como antes
                bundle = None
        if bundle is not None:
            return FileResponse(bundle, media_type="application/octet-stream")
    return FileResponse(
        p / "preview.stl", media_type="model/stl", filename="candidato.stl"
    )


@router.post(
    "/documentos/{doc_id}/design/candidates/{key}/apply",
    dependencies=[Depends(auth.requiere_token)],
)
def apply_route(doc_id: str, key: str):
    try:
        result = ops.apply(doc_id, key)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    eventos.publicar(
        "anotaciones_actualizadas", doc_id, documents.calcular_revision(doc_id),
        ["design", "historial"],
    )
    return result


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
    eventos.publicar(
        "anotaciones_actualizadas", doc_id, documents.calcular_revision(doc_id),
        ["design", "historial"],
    )
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


# ------------------------------------------------------------ intents (ADR-0019)
# The user routes are token-free like notes/strokes: explaining an intent is
# editor content, bounded by the Pydantic models. Only /agent and every
# geometry-changing route keep `auth.requiere_token`.


def _doc_existe(doc_id: str) -> None:
    if not notes._existe_documento(doc_id):
        raise HTTPException(404, "documento no encontrado")


def _buscar_indicacion(design, iid: str):
    intent = next((i for i in design.intents if i.id == iid), None)
    if intent is None:
        raise HTTPException(404, "Indicación no encontrada")
    return intent


def _marcas_validas(doc_id: str, marcas: list[str], design, iid: str | None) -> None:
    datos = notes.cargar(doc_id)
    existentes = {x["n"] for x in datos["notas"] + datos["trazos"]}
    for marca in marcas:
        if marca not in existentes:
            raise HTTPException(422, f"Marca inexistente: {marca}")
        if any(i.id != iid and marca in i.marks for i in design.intents):
            raise HTTPException(409, "La marca ya pertenece a otra indicación")


def _mutar_indicacion(doc_id: str, mensaje: str, cambiar) -> dict:
    """Snapshot BEFORE the change (like PUT /design). `cambiar` validates and
    mutates the loaded state in place; nothing is written if it raises."""
    with parametros.bloqueo(doc_id):
        rev, _ = ops.current(doc_id)
        design = ds.load(doc_id)
        cambiar(design)
        ramas.asegurar(doc_id)
        versioning.crear_snapshot(
            doc_id,
            mensaje,
            ramas._snapshot_actual(doc_id, documents._files[doc_id]),
        )
        ds.save(doc_id, design)
        eventos.publicar(
            "anotaciones_actualizadas", doc_id, rev, ["design", "historial"]
        )
        return read(doc_id)


@router.post("/documentos/{doc_id}/design/intents")
def crear_indicacion(doc_id: str, body: IntentCreate) -> dict:
    _doc_existe(doc_id)
    if not body.text.strip() and not body.marks:
        raise HTTPException(422, "Escribe la indicación o marca algo en el modelo")
    if body.points and not body.piece:
        raise HTTPException(422, "Una región necesita pieza")
    iid = uuid.uuid4().hex[:12]

    def cambiar(design):
        if len(design.intents) >= MAX_INTENTS:
            raise HTTPException(422, "Demasiadas indicaciones en este documento")
        marcas = list(dict.fromkeys(body.marks))
        if len(marcas) > MAX_MARKS:
            raise HTTPException(422, "Demasiadas marcas en una indicación")
        _marcas_validas(doc_id, marcas, design, None)
        for key in body.images:
            try:
                existe = design_assets.path(doc_id, key).exists()
            except ValueError:
                existe = False
            if not existe:
                raise HTTPException(422, "Referencia visual inexistente")
        region = None
        if body.piece:
            if body.piece not in geo.objects(doc_id):
                raise HTTPException(422, "Pieza desconocida")
            region = ds.Region(
                piece=body.piece,
                revision=ops.current(doc_id)[0],
                points=body.points,
                radius_mm=body.radius_mm,
            )
        design.intents.append(
            ds.Intent(
                id=iid,
                text=body.text.strip(),
                region=region,
                marks=marcas,
                images=body.images,
            )
        )

    result = _mutar_indicacion(doc_id, "crear indicación de diseño", cambiar)
    return {**result, "created_intent": iid}


@router.post("/documentos/{doc_id}/design/intents/{iid}/user")
def accion_usuario(doc_id: str, iid: str, body: IntentUserAction) -> dict:
    _doc_existe(doc_id)
    accion = body.action
    texto = (body.text or "").strip()
    if accion in ("reply", "edit") and not texto:
        raise HTTPException(422, "Escribe un texto")
    if accion == "attach" and not (body.marks or body.images):
        raise HTTPException(422, "Indica al menos una marca o una foto")

    def cambiar(design):
        intent = _buscar_indicacion(design, iid)
        if accion == "reply":
            if len(intent.messages) >= MAX_MESSAGES:
                raise HTTPException(422, "Demasiados mensajes en esta indicación")
            intent.messages.append(
                ds.Message(author="user", text=texto, created=_ahora())
            )
        elif accion == "edit":
            intent.text = texto
        elif accion == "attach":
            marcas = list(dict.fromkeys(intent.marks + body.marks))
            if len(marcas) > MAX_MARKS:
                raise HTTPException(422, "Demasiadas marcas en una indicación")
            _marcas_validas(doc_id, body.marks, design, iid)
            fotos = list(dict.fromkeys(intent.images + body.images))
            if len(fotos) > MAX_IMAGES:
                raise HTTPException(422, "Máximo 6 fotos por indicación")
            for key in body.images:
                try:
                    existe = design_assets.path(doc_id, key).exists()
                except ValueError:
                    existe = False
                if not existe:
                    raise HTTPException(422, "Referencia visual inexistente")
            intent.marks = marcas
            intent.images = fotos
        elif accion == "resolve":
            intent.status = "resolved"
        elif accion == "dismiss":
            intent.status = "dismissed"
        elif accion == "reopen":
            intent.status = "pending"

    return _mutar_indicacion(doc_id, f"indicación {iid}: {accion}", cambiar)


@router.post(
    "/documentos/{doc_id}/design/intents/{iid}/agent",
    dependencies=[Depends(auth.requiere_token)],
)
def respuesta_agente(doc_id: str, iid: str, body: IntentAgent) -> dict:
    _doc_existe(doc_id)
    interpretacion = (body.interpretation or "").strip() or None
    pregunta = (body.question or "").strip() or None
    mensaje = (body.message or "").strip() or None
    if not (interpretacion or pregunta or mensaje):
        raise HTTPException(422, "Falta interpretación, pregunta o mensaje")

    def cambiar(design):
        intent = _buscar_indicacion(design, iid)
        if len(intent.messages) >= MAX_MESSAGES:
            raise HTTPException(422, "Demasiados mensajes en esta indicación")
        if interpretacion is not None:
            intent.interpretation = interpretacion
        intent.question = pregunta
        texto = mensaje or "\n".join(x for x in (interpretacion, pregunta) if x)
        intent.messages.append(
            ds.Message(author="agent", text=texto, created=_ahora())
        )

    return _mutar_indicacion(doc_id, f"respuesta del agente a indicación {iid}", cambiar)
