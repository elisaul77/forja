"""Checks return evidence and unknowns, never a universal print approval."""

import numpy as np
import design_geometry as geo
from checks import fdm


def evaluate(parts, state, progress=lambda *_: None):
    results = []
    for i, r in enumerate(state.requirements):
        progress(i / max(1, len(state.requirements)), r.id)
        row = {"id": r.id, "description": r.description, "status": "not_checked"}
        a = parts.get(r.piece)
        try:
            if a is None:
                row["reason"] = "Pieza ausente"
            elif r.kind == "manual":
                row["reason"] = "Requiere medida o revisión física"
            elif r.kind == "closed":
                row.update(
                    status="pass" if a.is_watertight else "fail",
                    measured=bool(a.is_watertight),
                    method="mesh_topology",
                )
            elif r.kind == "single_body":
                row.update(
                    status="pass" if a.body_count == 1 else "fail",
                    measured=int(a.body_count),
                    method="mesh_connectivity",
                )
            elif r.kind == "preserve":
                row.update(
                    status=("pass" if geo.signature(a) == r.baseline else "fail")
                    if r.baseline
                    else "not_checked",
                    method="ordered_triangle_fingerprint",
                )
            elif r.kind == "dimension":
                value = float(a.extents["xyz".index(r.axis)])
                row.update(
                    status="pass" if abs(value - r.value) <= r.tolerance else "fail",
                    measured_mm=value,
                    target_mm=r.value,
                    tolerance_mm=r.tolerance,
                    method="mesh_axis_aligned_bounds",
                )
            elif r.kind == "clearance":
                if r.other not in parts:
                    row["reason"] = "Segunda pieza ausente"
                else:
                    evidence = geo.clearance(a, parts[r.other])
                    row["evidence"] = evidence
                    if evidence["interference"]:
                        row["status"] = "fail"
                    elif not evidence["solid_check_complete"]:
                        row["reason"] = "Malla abierta: contención no verificable"
                    else:
                        row["status"] = (
                            "pass"
                            if evidence["distance_mm"] + r.tolerance >= r.value
                            else "fail"
                        )
        except (ValueError, RuntimeError) as e:
            row["reason"] = str(e)
        results.append(row)
    return {
        "requirements": results,
        "passed": sum(x["status"] == "pass" for x in results),
        "failed": sum(x["status"] == "fail" for x in results),
        "not_checked": sum(x["status"] == "not_checked" for x in results),
        "approval": "not_requested"
        if not results
        else (
            "pass" if all(x["status"] == "pass" for x in results) else "needs_review"
        ),
    }


def print_check(
    parts,
    nozzle=0.4,
    bed=(220, 220, 250),
    angle=45,
    rotations=None,
    progress=lambda *_: None,
):
    report = {}
    for i, (name, source) in enumerate(parts.items()):
        progress(i / max(1, len(parts)), name)
        m = source.copy()
        if rotations and name in rotations:
            m.apply_transform(geo.rotation_matrix(rotations[name]))
        issues = {}
        fit = fdm._check_cama(m.extents, bed)
        if fit:
            issues["bed"] = fit
        overhang, contact = fdm._check_voladizo_y_base(m, angle)
        if overhang:
            issues["overhang"] = overhang
        if contact < fdm.UMBRAL_BASE_MM2:
            issues["base"] = {"contact_mm2": contact}
        if not m.is_volume:
            issues["integrity"] = "La malla no encierra un volumen orientado válido"
        thin = fdm._check_pared_fina(m, 2 * nozzle, 400)
        if thin:
            issues["thin_wall"] = thin
        report[name] = {
            "issues": issues,
            "dimensions_mm": m.extents.tolist(),
            "contact_mm2": float(contact),
            "rotation_deg": list((rotations or {}).get(name, (0, 0, 0))),
        }
    return {
        "parts": report,
        "ok": all(not p["issues"] for p in report.values()),
        "method": "mesh_heuristics",
        "nozzle_mm": nozzle,
        "bed_mm": list(bed),
        "limitations": [
            "Muestreo de espesor: puede omitir paredes locales",
            "No calcula trayectorias ni garantiza impresión; revisar en laminador",
        ],
    }


def orientations(part, bed):
    rows = []
    for angles in [
        (0, 0, 0),
        (90, 0, 0),
        (-90, 0, 0),
        (180, 0, 0),
        (0, 90, 0),
        (0, -90, 0),
    ]:
        m = part.copy()
        m.apply_transform(geo.rotation_matrix(angles))
        overhang, contact = fdm._check_voladizo_y_base(m, 45)
        rows.append(
            {
                "rotation_deg": angles,
                "dimensions_mm": m.extents.tolist(),
                "fits": bool(np.all(m.extents <= np.array(bed))),
                "contact_mm2": float(contact),
                "overhang": overhang,
            }
        )
    return {
        "options": rows,
        "method": "six_axis_aligned_orientations",
        "note": "Comparación parcial; no busca el óptimo entre todas las orientaciones.",
    }


def compare(before, after):
    return {
        "added": sorted(after.keys() - before.keys()),
        "removed": sorted(before.keys() - after.keys()),
        "changed": [
            {
                "piece": n,
                "before": geo.summary(before[n]),
                "after": geo.summary(after[n]),
            }
            for n in before.keys() & after.keys()
            if geo.signature(before[n]) != geo.signature(after[n])
        ],
    }
