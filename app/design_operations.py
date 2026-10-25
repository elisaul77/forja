"""Prepared candidates, explicit publish, and verified manufacturing export."""

from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
import numpy as np
import trimesh
from scipy.spatial.transform import Rotation
from fastapi import HTTPException
import design_state as state
import design_geometry as geo
import design_analysis as analysis
import documents, incorporar, mesh_parts, solids, parametros, ramas, versioning
import design_features as features
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


MAX_UTIL_MM = 2000
TESELA = 64
COMUNES = ("scale", "align", "drop_to_bed")  # no dependen de la forma de la pieza
BOOLEANAS = ("union", "subtract", "intersect")


def _validar_util(op):
    """Dimensiones del útil en mm (positivas, con tope). Según la forma:
    box X, Y, Z · cylinder diámetro, reservado, altura · sphere diámetro ·
    cone diámetro de la base, reservado, altura · torus diámetro exterior y
    diámetro del tubo · wedge X, Y, Z (cuña rectángulo) · tube diámetro
    exterior, diámetro interior, altura. Lo no usado puede ser 0."""
    s = op.size
    usadas = {
        "box": (s[0], s[1], s[2]),
        "wedge": (s[0], s[1], s[2]),
        "cylinder": (s[0], s[2]),
        "cone": (s[0], s[2]),
        "sphere": (s[0],),
        "torus": (s[0], s[1]),
        "tube": (s[0], s[1], s[2]),
    }[op.primitive]
    if min(usadas) <= 0:
        raise ValueError("Dimensiones del útil deben ser positivas")
    if max(abs(x) for x in usadas) > MAX_UTIL_MM:
        raise ValueError(f"Dimensiones del útil fuera de límite ({MAX_UTIL_MM} mm)")
    if op.primitive in ("torus", "tube") and s[1] >= s[0]:
        raise ValueError(
            "El diámetro del tubo debe ser menor que el exterior"
            if op.primitive == "torus"
            else "El diámetro interior debe ser menor que el exterior"
        )


def primitiva_malla(op):
    """Útil como malla cerrada, centrada en el origen (la caja envolvente queda
    centrada, como en `primitiva_forma`)."""
    _validar_util(op)
    s = op.size
    if op.primitive == "box":
        return trimesh.creation.box(extents=s)
    if op.primitive == "cylinder":
        return trimesh.creation.cylinder(radius=s[0] / 2, height=s[2], sections=TESELA)
    if op.primitive == "sphere":
        return trimesh.creation.uv_sphere(radius=s[0] / 2, count=[TESELA, TESELA])
    if op.primitive == "cone":
        m = trimesh.creation.cone(radius=s[0] / 2, height=s[2], sections=TESELA)
        m.apply_translation([0, 0, -s[2] / 2])
        return m
    if op.primitive == "torus":
        return trimesh.creation.torus(
            major_radius=(s[0] - s[1]) / 2,
            minor_radius=s[1] / 2,
            major_sections=TESELA,
            minor_sections=32,
        )
    if op.primitive == "tube":
        return trimesh.creation.annulus(
            r_min=s[1] / 2, r_max=s[0] / 2, height=s[2], sections=TESELA
        )
    return _cuna_malla(s)


def _cuna_malla(s):
    """Prisma triangular: perfil rectángulo en XZ con catetos X y Z, extruido en Y.
    Igual que `Wedge(X, Y, Z, 0, 0, 0, Z)` del núcleo B-rep."""
    x, y, z = s[0] / 2, s[1] / 2, s[2] / 2
    v = [
        (-x, -y, -z), (x, -y, -z), (-x, -y, z),
        (-x, y, -z), (x, y, -z), (-x, y, z),
    ]
    faces = [
        (0, 1, 2), (3, 5, 4),  # caras triangulares
        (0, 3, 4), (0, 4, 1),  # base
        (1, 4, 5), (1, 5, 2),  # cara inclinada
        (0, 2, 5), (0, 5, 3),  # cara vertical
    ]
    m = trimesh.Trimesh(vertices=v, faces=faces, process=True)
    m.fix_normals()
    return m


def primitiva_forma(op):
    """Útil B-rep centrado en el origen, mismas dimensiones que `primitiva_malla`."""
    _validar_util(op)
    s = op.size
    dims = {
        "box": s,
        "wedge": s,
        "cylinder": (s[0] / 2, s[2]),
        "sphere": (s[0] / 2,),
        "cone": (s[0] / 2, s[2]),
        "torus": ((s[0] - s[1]) / 2, s[1] / 2),
        "tube": (s[0] / 2, s[1] / 2, s[2]),
    }[op.primitive]
    return b123d_kernel.forma_primitiva(op.primitive, tuple(float(d) for d in dims))


def _limites(obj):
    """Caja envolvente (min, max) de una malla o de un sólido B-rep."""
    if isinstance(obj, trimesh.Trimesh):
        return np.asarray(obj.bounds[0], float), np.asarray(obj.bounds[1], float)
    bb = obj.bounding_box(optimal=True)
    return _punto(bb.min), _punto(bb.max)


def _punto(v):
    return np.array([v.X, v.Y, v.Z], float)


def _mover_rigido(obj, transform):
    if isinstance(obj, trimesh.Trimesh):
        moved = obj.copy()
        moved.apply_transform(transform)
        return moved
    return transform_shape_matrix(obj, transform)


def _matriz_en(rotacion, centro):
    """Giro `rotacion` (3x3) alrededor de `centro`."""
    m = np.eye(4)
    m[:3, :3] = rotacion
    m[:3, 3] = centro - rotacion @ centro
    return m


def _editar_comun(obj, op, auxiliar):
    """Escala, alinear y apoyar sobre cama: iguales para malla y B-rep."""
    if op.kind == "scale":
        lo, hi = _limites(obj)
        factor = np.asarray(op.factor, float)
        if isinstance(obj, trimesh.Trimesh):
            moved = obj.copy()
            moved.apply_transform(_matriz_en(np.diag(factor), (lo + hi) / 2))
            return moved
        return b123d_kernel.escalar(
            obj, tuple(float(f) for f in factor), tuple(float(c) for c in (lo + hi) / 2)
        )
    if op.kind == "align":
        if auxiliar is None:
            raise ValueError("Falta la pieza de referencia")
        lo, hi = _limites(obj)
        olo, ohi = _limites(auxiliar)
        d = np.zeros(3)
        if op.mode == "stack":
            d[2] = ohi[2] - lo[2]
        else:
            for i in (("x", "y", "z").index(a) for a in (op.axes or ["x", "y", "z"])):
                d[i] = {
                    "min": olo[i] - lo[i],
                    "max": ohi[i] - hi[i],
                    "center": (olo[i] + ohi[i]) / 2 - (lo[i] + hi[i]) / 2,
                }[op.mode]
        return _mover_rigido(obj, _traslacion(d))
    # drop_to_bed: gira la cara elegida hacia -Z y deja la base en z = 0.
    down = np.array([0.0, 0.0, -1.0])
    normal = np.asarray(op.normal, float)
    normal = normal / np.linalg.norm(normal)
    if np.allclose(normal, down):
        rotation = np.eye(3)
    else:
        rotation = Rotation.align_vectors([down], [normal])[0].as_matrix()
    lo, hi = _limites(obj)
    turned = _matriz_en(rotation, (lo + hi) / 2)
    zmin = _limites(_mover_rigido(obj, turned))[0][2]
    turned[2, 3] -= zmin
    return _mover_rigido(obj, turned)


def _traslacion(d):
    m = np.eye(4)
    m[:3, 3] = d
    return m


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def transform_shape_matrix(s, transform):
    from build123d import Axis

    vector = Rotation.from_matrix(transform[:3, :3]).as_rotvec()
    angle = np.linalg.norm(vector)
    if angle > 1e-12:
        s = s.rotate(Axis((0, 0, 0), tuple(vector / angle)), float(np.degrees(angle)))
    return s.translate(tuple(transform[:3, 3]))


def transform_shape(s, op):
    return transform_shape_matrix(s, matrix(op))


def matrix(op):
    if op.matrix is not None:
        return np.asarray(op.matrix, dtype=float)
    m = geo.rotation_matrix(op.rotation_deg)
    o = np.asarray(op.origin)
    m[:3, 3] = o - m[:3, :3] @ o + np.asarray(op.translation)
    return m


def edit_mesh(m, op, auxiliar=None):
    """`auxiliar`: malla de la otra pieza cuando la booleana usa `op.other`."""
    m = m.copy()
    if op.kind in COMUNES:
        return _editar_comun(m, op, auxiliar)
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
    if op.kind in features.RASGOS:
        return features.aplicar_malla(m, op)
    if op.other is not None:
        if auxiliar is None:
            raise ValueError("Falta la pieza auxiliar de la booleana")
        tool = auxiliar.copy()
        if not tool.is_volume:
            raise ValueError("La pieza auxiliar debe ser un volumen cerrado y orientado")
    else:
        tool = primitiva_malla(op)
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


def edit_shape(s, op, auxiliar=None):
    from build123d import Compound

    if op.kind in COMUNES:
        return _editar_comun(s, op, auxiliar)
    if op.kind == "transform":
        return transform_shape(s, op)
    if op.kind == "deform_region":
        raise ValueError("Deformación regional solo disponible para STL")
    if op.kind in features.RASGOS:
        return features.aplicar_brep(s, op)
    if op.other is not None:
        if auxiliar is None:
            raise ValueError("Falta la pieza auxiliar de la booleana")
        tool = auxiliar
    else:
        tool = transform_shape(
            primitiva_forma(op),
            op.model_copy(update={"origin": (0, 0, 0), "translation": op.center}),
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


def _limitaciones(operaciones):
    texto = []
    if any(op.kind == "deform_region" for op in operaciones):
        texto.append(
            "Deformación regional conserva conectividad pero requiere revisar autointersecciones"
        )
    if any(op.other is not None and op.kind in BOOLEANAS for op in operaciones):
        texto.append(
            "La pieza auxiliar de la booleana no cambia; para un solo cuerpo, bórrala tras aplicar"
        )
    return texto


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
        if body.intent is not None and not any(
            i.id == body.intent for i in design.intents
        ):
            raise ValueError("Indicación desconocida")
        before = geo.objects(doc_id)
        path = incorporar._ruta_doc(doc_id)
        for op in body.operations:
            if op.piece not in before:
                raise ValueError("Pieza desconocida")
            if op.other is not None and op.other not in before:
                raise ValueError("Pieza auxiliar desconocida")
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
                shapes[op.piece] = edit_shape(
                    shapes[op.piece], op, shapes.get(op.other) if op.other else None
                )
            after = {n: km.tessellate_para_exportar(s) for n, s in shapes.items()}
        else:
            after = {n: m.copy() for n, m in before.items()}
            for op in body.operations:
                after[op.piece] = edit_mesh(
                    after[op.piece], op, after.get(op.other) if op.other else None
                )
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
            preview, preview_entries = mesh_parts.combine(list(after.items()))
            (p / "preview.stl").write_bytes(km.to_stl_bytes(preview))
            # Same FJP1 envelope as /malla so the before/after view colours by piece.
            import piece_mesh

            (p / "preview.piezas").write_bytes(
                piece_mesh.from_stl(
                    preview,
                    bbox=preview.bounds.T.reshape(-1).tolist(),
                    volume=float(abs(preview.volume)),
                    entries=preview_entries,
                )
            )
            result = {
                "id": key,
                "document": doc_id,
                "revision": body.revision,
                "state_revision": body.state_revision,
                "request_hash": body_hash,
                "state": "prepared",
                "intent": body.intent,
                "summary": body.summary,
                "constraints_revision": state.constraints_digest(design),
                "created": _ahora(),
                "geometry_file": out.name,
                "sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
                "checks": checks,
                "integrity": integrity,
                "difference": analysis.compare(before, after),
                "operations": [op.model_dump() for op in body.operations],
                "preview_url": f"/documentos/{doc_id}/design/candidates/{key}/preview",
                "limitations": _limitaciones(body.operations),
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
        if "constraints_revision" in manifest:
            # Intent conversation does not invalidate candidates; roles and
            # requirements do (the checks depend only on those).
            current_state = state.load(doc_id)
            if (
                documents.calcular_revision(doc_id) != manifest["revision"]
                or state.constraints_digest(current_state)
                != manifest["constraints_revision"]
            ):
                raise HTTPException(
                    409,
                    "El documento o sus condiciones cambiaron; vuelve a consultar antes de aplicar.",
                )
        else:
            # Legacy manifest (before constraints_revision): full-state guard.
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
        before_snapshot = versioning.crear_snapshot(
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
                state="applied",
                applied_revision=documents.calcular_revision(doc_id),
                before_snapshot=before_snapshot,
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
