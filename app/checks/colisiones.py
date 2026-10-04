"""Pairwise collision / split-piece / floating-piece checks for a
named-solid STEP document (Phase 5B, feedback item 2).

Only reports PROBLEMS (``choques``/``partidas``/``flotantes``/``errores``);
``ok: true`` means every list came back empty. A bbox pre-filter (with a
small margin) keeps the O(n^2) exact OpenCascade calls
(``Shape.distance``/``Shape.intersect``, via build123d — never `OCP`
directly here, ADR-0001) down to genuinely close candidate pairs, since a
21-solid document is 210 possible pairs.

The bbox prefilter and the "+K mas" list-capping helper are shared with
`app/percepcion.py` (Phase 5E's `percibir`) via `checks/distancia.py` rather
than duplicated — this module keeps using its own plain `Shape.distance`
call below (it never needs the closest POINTS `percepcion.py` uses to derive
a contact direction).
"""
from __future__ import annotations

from typing import Any

import solids
from checks import distancia

TOLERANCIA_MM3_POR_DEFECTO = 0.5
# Pre-filter slack (mm): a pair whose bboxes are farther apart than this can
# never be touching or overlapping, so it is skipped before any exact OCP
# call. Generous on purpose (cheap bbox math) relative to the actual
# touching tolerance below.
MARGEN_BBOX_MM = 1.0
TOLERANCIA_TOQUE_MM = 0.05
# Epsilon (mm) for "OCC says the distance is zero": a pair closer than this
# still gets the exact boolean below, so `tolerancia_mm3` — never OCC's
# floating-point noise floor — is what classifies it. `Shape.distance` is not
# an exact zero for solids that REALLY intersect: a cylindrical pin pressed
# straight through a 40x20x20 block measures 3.0678e-16 mm (Phase 6.5
# finding, tests/test_colisiones_casquillos.py), and a `> 0.0` gate silently
# answered `{"choques": [], "ok": true}` for its very real 2261.9467 mm3 of
# overlap. 1e-9 mm = 1 nm sits ~6.5 orders of magnitude above that noise
# floor and ~10 below the smallest genuine gap measured in a real assembly
# (7.159 mm, between the mostertruck front-axle casquillos), so no plausible
# pair is misgraded by it. The largest denormal this phase measured on real
# geometry — 2.3867e-12 mm, the mostertruck casquillo x tuerca contacts — is
# still 419x (2.6 orders) BELOW the epsilon, so those contacts keep their
# exact boolean too, which is what returns 0 mm3 and no choque for them. The
# only cost is the exact boolean for a pair that was already within
# `TOLERANCIA_TOQUE_MM` of touching.
TOLERANCIA_DISTANCIA_CERO_MM = 1e-9
TOPE_LISTA = 30


def _bbox_se_acercan(bbox_a: list[float], bbox_b: list[float], margen: float) -> bool:
    return distancia.bbox_se_acercan(bbox_a, bbox_b, margen)


def _con_tope(lista: list[Any], tope: int = TOPE_LISTA) -> tuple[list[Any], int | None]:
    return distancia.con_tope(lista, tope)


def verificar(
    shape: Any,
    entradas: list[dict[str, Any]],
    tolerancia_mm3: float = TOLERANCIA_MM3_POR_DEFECTO,
) -> dict[str, Any]:
    """Run all three checks against ``shape``'s named solids (``entradas``
    from `solids.cargar`). Returns a compact dict — see the module
    docstring for the empty-lists/``ok`` contract. Adds
    ``nombres_inciertos: true`` (Phase 5B fix-review) when
    `solids.solidos_por_indice` couldn't confidently match the stored names
    to this reimport's geometry and fell back to flat ``solido_N`` naming."""
    grupos, nombres_inciertos = solids.solidos_por_indice(shape, entradas)
    n = len(grupos)

    choques: list[dict[str, Any]] = []
    errores: list[dict[str, Any]] = []
    toca_a_alguien = [False] * n

    for i in range(n):
        nombre_a, _idx_a, solido_a, bbox_a, _vol_a = grupos[i]
        for j in range(i + 1, n):
            nombre_b, _idx_b, solido_b, bbox_b, _vol_b = grupos[j]
            if not _bbox_se_acercan(bbox_a, bbox_b, MARGEN_BBOX_MM):
                continue
            try:
                separacion = solido_a.distance(solido_b)
            except Exception as exc:  # noqa: BLE001 - OCP boolean/distance failures reported, never crash
                errores.append({"a": nombre_a, "b": nombre_b, "mensaje": str(exc)})
                continue

            if separacion > TOLERANCIA_TOQUE_MM:
                continue
            toca_a_alguien[i] = True
            toca_a_alguien[j] = True

            if nombre_a == nombre_b:
                # Fragments of the SAME named piece are expected to touch or
                # overlap (that is what makes them "one piece") — never a
                # choque between a piece and itself. `partidas` below is
                # what flags this case instead.
                continue
            if separacion > TOLERANCIA_DISTANCIA_CERO_MM:
                # A near-miss below the touch tolerance but not truly
                # overlapping: no boolean common volume to report. A pair at
                # or below the epsilon is NOT a proven near-miss (OCC reports
                # denormals for solids that really intersect), so it goes on
                # to the exact boolean and `tolerancia_mm3` decides.
                continue
            try:
                volumen = distancia.volumen_interseccion(solido_a, solido_b)
            except Exception as exc:  # noqa: BLE001
                errores.append({"a": nombre_a, "b": nombre_b, "mensaje": str(exc)})
                continue
            if volumen > tolerancia_mm3:
                choques.append({"a": nombre_a, "b": nombre_b, "vol": round(volumen, 1)})

    conteo_por_nombre: dict[str, int] = {}
    for nombre, *_ in grupos:
        conteo_por_nombre[nombre] = conteo_por_nombre.get(nombre, 0) + 1
    partidas = [
        {"pieza": nombre, "solidos": conteo}
        for nombre, conteo in conteo_por_nombre.items()
        if conteo > 1
    ]

    flotantes: list[str] = []
    if n > 1:
        # A piece split into several solids "touches itself" by construction
        # (see the `nombre_a == nombre_b` skip above) so its own fragments
        # never make each other float; only a genuine lack of contact with
        # anything else in the document counts.
        flotantes = sorted({grupos[i][0] for i in range(n) if not toca_a_alguien[i]})

    choques, mas_choques = _con_tope(choques)
    partidas, mas_partidas = _con_tope(partidas)
    flotantes, mas_flotantes = _con_tope(flotantes)

    salida: dict[str, Any] = {
        "choques": choques,
        "partidas": partidas,
        "flotantes": flotantes,
        "ok": not (choques or partidas or flotantes),
    }
    if mas_choques:
        salida["choques_mas"] = mas_choques
    if mas_partidas:
        salida["partidas_mas"] = mas_partidas
    if mas_flotantes:
        salida["flotantes_mas"] = mas_flotantes
    if errores:
        salida["errores"] = errores
    if nombres_inciertos:
        # `app/solids.py`'s `solidos_por_indice` couldn't confidently zip the
        # stored names back to this reimport's geometry (a volume/bbox
        # mismatch it also couldn't resolve unambiguously) and fell back to
        # flat `solido_N` naming — every name above is that fallback, not
        # necessarily what the script actually called its pieces.
        salida["nombres_inciertos"] = True
    return salida
