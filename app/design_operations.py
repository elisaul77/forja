"""Prepared candidates, explicit publish, and verified manufacturing export."""

from __future__ import annotations
import hashlib
import json
import numpy as np
import trimesh
from scipy.spatial.transform import Rotation
from fastapi import HTTPException
import design_state as state
import design_geometry as geo
import design_analysis as analysis
import documents, incorporar, mesh_parts, solids, parametros, ramas, versioning
from kernel import b123d_kernel, mesh as km


def directory(doc_id):
    state.path(doc_id)  # validate
    return state.ROOT / ".design-candidates" / doc_id


def candidate(doc_id, key):
    if len(key) != 32 or any(c not in "0123456789abcdef" for c in key):
        raise ValueError("Candidato inválido")
    p = directory(doc_id) / key
    if not (p / "manifest.json").exists():
        raise ValueError("Candidato inexistente")
    return p, json.loads((p / "manifest.json").read_text())


def current(doc_id):
    incorporar._ruta_doc(doc_id)
    return documents.calcular_revision(doc_id), state.digest(state.load(doc_id))


def expect(doc_id, rev, srev):
    if current(doc_id) != (rev, srev):
        raise HTTPException(
            409,
            "El documento o sus condiciones cambiaron; vuelve a consultar antes de aplicar.",
        )


def transform_shape(s, op):
    from build123d import Axis

    transform = matrix(op)
    vector = Rotation.from_matrix(transform[:3, :3]).as_rotvec()
    angle = np.linalg.norm(vector)
    if angle > 1e-12:
        s = s.rotate(Axis((0, 0, 0), tuple(vector / angle)), float(np.degrees(angle)))
    return s.translate(tuple(transform[:3, 3]))


def matrix(op):
    if op.matrix is not None:
        return np.asarray(op.matrix, dtype=float)
    m = geo.rotation_matrix(op.rotation_deg)
    o = np.asarray(op.origin)
    m[:3, 3] = o - m[:3, :3] @ o + np.asarray(op.translation)
    return m


def edit_mesh(m, op):
    m = m.copy()
    if op.kind == "transform":
        m.apply_transform(matrix(op))
        return m
    if op.kind == "deform_region":
        distance = np.linalg.norm(m.vertices - np.asarray(op.center), axis=1)
        weight = np.clip(1 - distance / op.radius_mm, 0, 1) ** 2
        if not np.any(weight):
            raise ValueError("La región no contiene vértices")
        m.vertices += weight[:, None] * np.asarray(op.translation)
        return m
    if min(op.size) <= 0:
        raise ValueError("Dimensiones del útil deben ser positivas")
    tool = (
        trimesh.creation.box(extents=op.size)
        if op.primitive == "box"
        else trimesh.creation.cylinder(
            radius=op.size[0] / 2, height=op.size[2], sections=64
        )
    )
    tool.apply_transform(geo.rotation_matrix(op.rotation_deg))
    tool.apply_translation(op.center)
    fn = {
        "union": trimesh.boolean.union,
        "subtract": trimesh.boolean.difference,
        "intersect": trimesh.boolean.intersection,
    }[op.kind]
    if not m.is_volume:
        raise ValueError("La booleana requiere una malla cerrada y orientada")
    return fn([m, tool], engine="manifold")


def edit_shape(s, op):
    from build123d import Box, Cylinder, Compound

    if op.kind == "transform":
        return transform_shape(s, op)
    if op.kind == "deform_region":
        raise ValueError("Deformación regional solo disponible para STL")
    if min(op.size) <= 0:
        raise ValueError("Dimensiones del útil deben ser positivas")
    tool = (
        Box(*op.size) if op.primitive == "box" else Cylinder(op.size[0] / 2, op.size[2])
    )
    tool = transform_shape(
        tool, op.model_copy(update={"origin": (0, 0, 0), "translation": op.center})
    )
    result = {
        "union": lambda: s + tool,
        "subtract": lambda: s - tool,
        "intersect": lambda: s & tool,
    }[op.kind]()
    if isinstance(result, list):
        result = Compound(children=list(result))
    if not result.solids() or not result.is_valid:
        raise ValueError("La operación produjo CAD vacío o inválido")
    return result


def prepare(doc_id, body):
    from build123d import Compound

    with parametros.bloqueo(doc_id):
        root = directory(doc_id)
        root.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(body.request_id.encode()).hexdigest()[:32]
        p = root / key
        body_hash = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
        if (p / "manifest.json").exists():
            _, previous = candidate(doc_id, key)
            if previous["request_hash"] != body_hash:
                raise HTTPException(
                    409, "Identificador de petición reutilizado con otro contenido"
                )
            return previous
        expect(doc_id, body.revision, body.state_revision)
        if len(list(root.iterdir())) >= 64:
            raise ValueError(
                "Máximo 64 candidatos por documento; descarta candidatos antes de crear más"
            )
        design = state.load(doc_id)
        before = geo.objects(doc_id)
        path = incorporar._ruta_doc(doc_id)
        for op in body.operations:
            if op.piece not in before:
                raise ValueError("Pieza desconocida")
            role = design.parts.get(op.piece, state.PartRole())
            if role.locked or role.role != "design":
                raise ValueError(f"Pieza protegida: {op.piece}")
        is_step = incorporar._es_step(path)
        shapes = {}
        if is_step:
            shapes = {
                n: Compound(children=ss)
                for n, ss in incorporar._grupos_origen(doc_id, path, None).items()
            }
            for op in body.operations:
                shapes[op.piece] = edit_shape(shapes[op.piece], op)
            after = {n: km.tessellate_para_exportar(s) for n, s in shapes.items()}
        else:
            after = {n: m.copy() for n, m in before.items()}
            for op in body.operations:
                after[op.piece] = edit_mesh(after[op.piece], op)
        if any(
            not len(m.faces) or not np.isfinite(m.vertices).all()
            for m in after.values()
        ):
            raise ValueError("Resultado vacío o no finito")
        checks = analysis.evaluate(after, design)
        integrity = {
            n: geo.summary(m)
            for n, m in after.items()
            if n in {op.piece for op in body.operations}
        }
        p.mkdir()
        try:
            if is_step:
                out = p / "geometry.step"
                b123d_kernel.export_to_step(
                    b123d_kernel.combinar_nombrados(shapes), out
                )
                entries = solids.construir_entradas(
                    b123d_kernel.import_from_step(out), list(shapes)
                )
            else:
                out = p / "geometry.stl"
                combined, entries = mesh_parts.combine(list(after.items()))
                out.write_bytes(km.to_stl_bytes(combined))
            (p / "solidos.json").write_text(json.dumps(entries))
            if is_step:
                import export as exporter

                exported, uncertain = exporter.objetos_3mf(
                    b123d_kernel.import_from_step(out), entries
                )
                if uncertain:
                    raise ValueError(
                        "No se pudo conservar la identidad de piezas del candidato"
                    )
                after = dict(exported)
            else:
                after = dict(mesh_parts.objects(km.load_stl(out), entries, "candidate"))
            checks = analysis.evaluate(after, design)
            integrity = {
                n: geo.summary(m)
                for n, m in after.items()
                if n in {op.piece for op in body.operations}
            }
            preview, _ = mesh_parts.combine(list(after.items()))
            (p / "preview.stl").write_bytes(km.to_stl_bytes(preview))
            result = {
                "id": key,
                "document": doc_id,
                "revision": body.revision,
                "state_revision": body.state_revision,
                "request_hash": body_hash,
                "state": "prepared",
                "geometry_file": out.name,
                "sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
                "checks": checks,
                "integrity": integrity,
                "difference": analysis.compare(before, after),
                "operations": [op.model_dump() for op in body.operations],
                "preview_url": f"/documentos/{doc_id}/design/candidates/{key}/preview",
                "limitations": [
                    "Deformación regional conserva conectividad pero requiere revisar autointersecciones"
                ]
                if any(op.kind == "deform_region" for op in body.operations)
                else [],
            }
            (p / "manifest.json").write_text(json.dumps(result))
            return result
        except BaseException:
            import shutil

            shutil.rmtree(p)
            raise


def apply(doc_id, key):
    with parametros.bloqueo(doc_id):
        p, manifest = candidate(doc_id, key)
        if manifest["state"] == "applied":
            return manifest
        expect(doc_id, manifest["revision"], manifest["state_revision"])
        if manifest["checks"]["failed"]:
            raise HTTPException(
                409,
                "El candidato incumple requisitos; revisa el diseño antes de aplicarlo",
            )
        if any(not x["closed"] for x in manifest["integrity"].values()):
            raise HTTPException(409, "El candidato tiene piezas modificadas abiertas")
        out = p / manifest["geometry_file"]
        if hashlib.sha256(out.read_bytes()).hexdigest() != manifest["sha256"]:
            raise ValueError("Integridad del candidato alterada")
        path = incorporar._ruta_doc(doc_id)
        ramas.asegurar(doc_id)
        previous, _, _ = ramas.capturar(doc_id)
        versioning.crear_snapshot(
            doc_id,
            "antes de aplicar candidato de diseño",
            ramas._snapshot_actual(doc_id, path),
        )
        old_record = documents._registry[doc_id].copy()
        try:
            incorporar._escribir_atomico(path, out.read_bytes())
            incorporar._escribir_atomico(
                solids.ruta_solidos(doc_id), (p / "solidos.json").read_bytes()
            )
            documents._registry[doc_id] = {
                **old_record,
                **documents._analyze(path, path.suffix.lower()),
            }
            meta_path = documents._ruta_meta(doc_id)
            meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
            if meta.get("script"):
                meta["geometria_editada"] = {
                    "desde": key,
                    "piezas": sorted({op["piece"] for op in manifest["operations"]}),
                }
                incorporar._escribir_atomico(meta_path, json.dumps(meta).encode())
            # Candidate edits cannot reuse old face selections as if nothing
            # moved. STEP resolves fingerprints; STL selections become stale.
            import notes, naming

            annotations = notes.cargar(doc_id)
            shape = (
                b123d_kernel.import_from_step(path)
                if incorporar._es_step(path)
                else None
            )
            for collection in ("notas", "trazos"):
                for item in annotations.get(collection, []):
                    reference = item.get("referencia")
                    if reference is None:
                        continue
                    if shape is not None:
                        resolved = naming.resolver_referencia(shape, reference)
                        item["referencia_perdida"] = resolved.pop(
                            "referencia_perdida", False
                        )
                        item["ambigua"] = resolved.pop("ambigua", False)
                        item["referencia"] = resolved
                    else:
                        item["referencia_perdida"] = True
            if notes.ruta_notas(doc_id).exists():
                notes.guardar(doc_id, annotations)
            documents._borrar_cache_malla(doc_id)
            ramas.borrar_cache(doc_id)
            documents.confirmar_revision(doc_id)
            manifest.update(
                state="applied", applied_revision=documents.calcular_revision(doc_id)
            )
            incorporar._escribir_atomico(
                p / "manifest.json", json.dumps(manifest).encode()
            )
        except BaseException:
            ramas._materializar(doc_id, path, previous)
            documents._registry[doc_id] = old_record
            documents.confirmar_revision(doc_id)
            raise
        return manifest


def export_design(doc_id, fmt="3mf", pose=True):
    import export as exporter

    with parametros.bloqueo(doc_id):
        design = state.load(doc_id)
        parts = geo.objects(doc_id)
        included = {
            n: m.copy()
            for n, m in parts.items()
            if design.parts.get(n, state.PartRole()).role == "design"
        }
        if not included:
            raise ValueError("No hay piezas de diseño para exportar")
        for n, m in included.items():
            if pose:
                m.apply_transform(
                    geo.rotation_matrix(design.print_rotations.get(n, (0, 0, 0)))
                )
                m.apply_translation([0, 0, -m.bounds[0, 2]])
            if not m.is_volume:
                raise ValueError(f"La pieza {n} no es un volumen cerrado orientado")
        if fmt == "stl":
            data = km.to_stl_bytes(trimesh.util.concatenate(list(included.values())))
        elif fmt == "3mf":
            # Stable package timestamps make the verification manifest apply
            # to a later download of the same revision.
            import io, zipfile

            raw = exporter.bytes_3mf(list(included.items()))
            buf = io.BytesIO()
            with (
                zipfile.ZipFile(io.BytesIO(raw)) as src,
                zipfile.ZipFile(buf, "w") as dst,
            ):
                for name in src.namelist():
                    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    dst.writestr(info, src.read(name))
            data = buf.getvalue()
        else:
            raise ValueError("Formato de fabricación no admitido")
        return data, {
            "pieces": list(included),
            "excluded": sorted(parts.keys() - included.keys()),
            "units": "mm",
            "format": fmt,
            "print_pose": pose,
            "revision": documents.calcular_revision(doc_id),
            "state_revision": state.digest(design),
            "sha256": hashlib.sha256(data).hexdigest(),
            "geometry": [{"piece": n, **geo.summary(m)} for n, m in included.items()],
        }
