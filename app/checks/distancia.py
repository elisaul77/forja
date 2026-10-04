"""Shared bbox-prefiltered exact-OCP distance helpers (Phase 5E).

`checks/colisiones.py` (choques/partidas/flotantes) and `app/percepcion.py`
(`percibir`'s `contactos` layer: support/neighbours/unsupported) both need
the same "cheap bbox prefilter, then an exact OpenCascade distance call only
for genuinely close candidate pairs" pattern over a document's named
solids — extracted here once instead of duplicated (Phase 5E plan's graph
note), so both consumers walk the exact same candidate-pair logic.

Never imports build123d/OCP itself (ADR-0001): every function here only
duck-types `.distance`/`.distance_to_with_closest_points` on whatever
Shape/Solid objects the caller hands it, same discipline as
`checks/colisiones.py` and `app/solids.py`.
"""
from __future__ import annotations

from typing import Any


def bbox_se_acercan(bbox_a: list[float], bbox_b: list[float], margen: float) -> bool:
    """True if two ``[xmin, xmax, ymin, ymax, zmin, zmax]`` bboxes could be
    within ``margen`` mm of each other — a cheap prefilter before any exact
    OCP distance call keeps the O(n^2) pair count down to genuinely close
    candidates."""
    ax0, ax1, ay0, ay1, az0, az1 = bbox_a
    bx0, bx1, by0, by1, bz0, bz1 = bbox_b
    return (
        ax0 - margen <= bx1
        and bx0 - margen <= ax1
        and ay0 - margen <= by1
        and by0 - margen <= ay1
        and az0 - margen <= bz1
        and bz0 - margen <= az1
    )


def con_tope(lista: list[Any], tope: int) -> tuple[list[Any], int | None]:
    """Cap ``lista`` at ``tope`` entries; returns ``(lista_recortada,
    cuantos_de_mas_o_None)`` — the ``+K mas`` tail pattern used across
    `checks/colisiones.py`, `app/solids.py`, and `app/percepcion.py`."""
    if len(lista) <= tope:
        return lista, None
    return lista[:tope], len(lista) - tope


def volumen_interseccion(solido_a: Any, solido_b: Any) -> float:
    """Boolean intersection volume (mm3) between two solids — ``0.0`` if
    ``Shape.intersect`` finds no actual overlap. Shared by
    `checks/colisiones.verificar` (its ``choques`` definition/threshold)
    and `app/percepcion.py` (which needs the IDENTICAL definition for its
    own ``CHOQUE`` lines) — `percepcion.py` computes this only for the
    handful of pairs its own single pairwise sweep already found at
    distance 0 (`checks/colisiones.py` never even gets called from there),
    which is what keeps a `percibir` call on a real multi-room document
    under its performance budget: a second full O(n^2) sweep just to
    re-derive choques would otherwise roughly double the cost of the
    single most expensive part of `percibir` (an exact OpenCascade call
    against a large, many-faced solid)."""
    interseccion = solido_a.intersect(solido_b)
    return sum(s.volume for s in interseccion) if interseccion else 0.0


def distancia_y_puntos(solido_a: Any, solido_b: Any) -> tuple[float, Any, Any]:
    """Exact OCP distance plus the closest point ON EACH solid
    (``Shape.distance_to_with_closest_points``, build123d — never `OCP`
    directly here, ADR-0001): ``(distancia_mm, punto_en_a, punto_en_b)``.

    `checks/colisiones.py` only ever needed the scalar distance
    (`Shape.distance`); `app/percepcion.py` additionally needs the two
    closest points to derive a contact DIRECTION (which axis dominates the
    vector between them) — this is the one extra piece of information that
    justifies a separate helper rather than colisiones.py's plain
    `Shape.distance` call.
    """
    return solido_a.distance_to_with_closest_points(solido_b)
