"""Read-only, exact local clearance check for an assembly pose.

Only pairs crossing the moving subtree boundary are relevant to one joint
motion: relative placement inside either rigid group is unchanged. Bounding
boxes reject pairs beyond the stated search radius; remaining distances and
intersection volumes come from build123d/OpenCascade. This is a sampled
configuration check, not a continuous collision or dynamics solver.
"""
from __future__ import annotations

import math
from typing import Any

from checks import colisiones, distancia

SEARCH_MM = 1.0
MAX_EXACT_PAIRS = 250
MAX_REPORTED = 30


def moving_names(joints: list[dict], joint_id: str) -> set[str]:
    children = {joint["padre"]: [] for joint in joints}
    selected = None
    for joint in joints:
        children.setdefault(joint["padre"], []).append(joint["hijo"])
        if joint["id"] == joint_id:
            selected = joint["hijo"]
    if selected is None:
        raise ValueError("articulación desconocida")
    moving = set()
    pending = [selected]
    while pending:
        name = pending.pop()
        if name not in moving:
            moving.add(name)
            pending.extend(children.get(name, ()))
    return moving


def _bbox(shape: Any) -> list[float]:
    box = shape.bounding_box()
    return [box.min.X, box.max.X, box.min.Y, box.max.Y, box.min.Z, box.max.Z]


def _lower_bound(a: list[float], b: list[float]) -> float:
    gaps = [max(a[i] - b[i + 1], b[i] - a[i + 1], 0.0) for i in (0, 2, 4)]
    return math.hypot(*gaps)


def check_pose(named: dict[str, Any], moving: set[str], radius_mm: float = SEARCH_MM,
               max_exact_pairs: int = MAX_EXACT_PAIRS) -> dict[str, Any]:
    """Evaluate cross-group pairs in one posed, named assembly.

    `completo` means all pairs with bbox lower bound <= `radius_mm` were
    checked. It never means the entire motion path was checked.
    """
    if not 0 < radius_mm <= 10 or not math.isfinite(radius_mm):
        raise ValueError("radio de búsqueda inválido")
    if not moving or not moving <= named.keys():
        raise ValueError("piezas móviles desconocidas")
    stationary = set(named) - moving
    boxes = {name: _bbox(shape) for name, shape in named.items()}
    candidates = []
    for a in sorted(moving):
        for b in sorted(stationary):
            # This compares one moving rigid group with its surroundings.
            lower = _lower_bound(boxes[a], boxes[b])
            if lower <= radius_mm:
                candidates.append((lower, a, b))
    candidates.sort()
    hits, contacts, errors = [], [], []
    minimum = None
    checked = 0
    for _lower, a, b in candidates[:max_exact_pairs]:
        checked += 1
        try:
            gap = named[a].distance(named[b])
            if gap <= colisiones.TOLERANCIA_DISTANCIA_CERO_MM:
                volume = distancia.volumen_interseccion(named[a], named[b])
                if volume > colisiones.TOLERANCIA_MM3_POR_DEFECTO:
                    hits.append({"a": a, "b": b, "volumen_mm3": round(volume, 3)})
                else:
                    contacts.append({"a": a, "b": b, "holgura_mm": 0.0})
            elif gap <= radius_mm:
                minimum = gap if minimum is None else min(minimum, gap)
                if gap <= colisiones.TOLERANCIA_TOQUE_MM:
                    contacts.append({"a": a, "b": b, "holgura_mm": round(gap, 6)})
        except Exception as exc:  # noqa: BLE001 - failed OCP query is not a clean result
            errors.append({"a": a, "b": b, "mensaje": str(exc)})
    complete = checked == len(candidates) and not errors
    return {
        "radio_busqueda_mm": radius_mm,
        "umbral_choque_mm3": colisiones.TOLERANCIA_MM3_POR_DEFECTO,
        "piezas_moviles": sorted(moving),
        "pares_en_radio": len(candidates),
        "pares_comprobados": checked,
        "completo": complete,
        "choques": hits[:MAX_REPORTED],
        "choques_mas": max(0, len(hits) - MAX_REPORTED),
        "contactos": contacts[:MAX_REPORTED],
        "contactos_mas": max(0, len(contacts) - MAX_REPORTED),
        "holgura_minima_mm": round(minimum, 6) if minimum is not None else None,
        "errores": errors[:MAX_REPORTED],
    }


def check_scene(named: dict[str, Any], moving: set[str], radius_mm: float = SEARCH_MM,
                max_exact_pairs: int = MAX_EXACT_PAIRS) -> dict[str, Any]:
    """Check all relevant pairs, including two independently moving groups.

    Reuses the exact OCP pair evaluator above by checking one moving solid at
    a time. Pair deduplication prevents double counting and avoids testing
    unrelated stationary-stationary pairs. The cap applies to the entire scene.
    """
    if not moving or not moving <= named.keys():
        raise ValueError("piezas móviles desconocidas")
    pairs = []
    boxes = {name: _bbox(shape) for name, shape in named.items()}
    names = sorted(named)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if a in moving or b in moving:
                lower = _lower_bound(boxes[a], boxes[b])
                if lower <= radius_mm:
                    pairs.append((lower, a, b))
    pairs.sort()
    hits, contacts, errors = [], [], []
    minimum = None
    for _lower, a, b in pairs[:max_exact_pairs]:
        try:
            gap = named[a].distance(named[b])
            if gap <= colisiones.TOLERANCIA_DISTANCIA_CERO_MM:
                volume = distancia.volumen_interseccion(named[a], named[b])
                if volume > colisiones.TOLERANCIA_MM3_POR_DEFECTO:
                    hits.append({"a": a, "b": b, "volumen_mm3": round(volume, 3)})
                else:
                    contacts.append({"a": a, "b": b, "holgura_mm": 0.0})
            elif gap <= radius_mm:
                minimum = gap if minimum is None else min(minimum, gap)
                if gap <= colisiones.TOLERANCIA_TOQUE_MM:
                    contacts.append({"a": a, "b": b, "holgura_mm": round(gap, 6)})
        except Exception as exc:  # noqa: BLE001 - an OCP failure is incomplete
            errors.append({"a": a, "b": b, "mensaje": str(exc)})
    checked = min(len(pairs), max_exact_pairs)
    return {"radio_busqueda_mm": radius_mm,
            "umbral_choque_mm3": colisiones.TOLERANCIA_MM3_POR_DEFECTO,
            "piezas_moviles": sorted(moving), "pares_en_radio": len(pairs),
            "pares_comprobados": checked,
            "completo": checked == len(pairs) and not errors,
            "choques": hits[:MAX_REPORTED], "choques_mas": max(0, len(hits) - MAX_REPORTED),
            "contactos": contacts[:MAX_REPORTED], "contactos_mas": max(0, len(contacts) - MAX_REPORTED),
            "holgura_minima_mm": round(minimum, 6) if minimum is not None else None,
            "errores": errors[:MAX_REPORTED]}
