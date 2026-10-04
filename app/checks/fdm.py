"""FDM printability checks (Phase 5C): catch print problems before slicing.

Every named piece is checked on its OWN, as it would be printed: in its
current orientation, resting on the bed at its own lowest Z (a dollhouse
chair is printed alone, not at its height inside the house). Only problems
are reported, compact, per named solid; an empty ``problemas`` means
nothing was found. All lengths in mm, areas in mm2, angles in degrees.

Checks (all on the tessellation, `kernel.mesh`, tolerance 0.1 mm):

- ``cama``: bbox vs the bed volume; when it doesn't fit, suggests a 90
  degree turn around Z (or lying it down) if that would fit.
- ``voladizo``: area of downward-facing triangles steeper than
  ``angulo_max`` from vertical (angle = asin(-n_z): a vertical wall is 0,
  a flat ceiling 90), excluding triangles lying on the bed; reported with
  the worst connected region (its area, bbox and max angle) and only above
  ``UMBRAL_VOLADIZO_MM2``. Bridges (a ceiling spanning two supports) count
  as overhang here too -- the check can't know they are bridgeable.
- ``pared_fina``: sampled local thickness -- rays cast inward along the
  surface normal from area-weighted surface samples (trimesh's pure-numpy
  ray intersector, no embree needed), first hit = local thickness. Flagged
  below ``2 * boquilla`` when at least ``_MIN_MUESTRAS_FINAS`` samples agree
  (one grazing ray near an edge is not a wall). Knife edges (an acute
  angle between two faces) legitimately show up here.
- ``base``: first-layer contact area (downward triangles on the bed);
  flagged below ``UMBRAL_BASE_MM2`` (0 = nothing flat touches the bed).
- ``diminuto``: a bbox dimension smaller than the nozzle.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import trimesh

import solids
from kernel import mesh as kernel_mesh

TOLERANCIA_TESSELLATION_MM = 0.1
BOQUILLA_POR_DEFECTO = 0.4
CAMA_POR_DEFECTO = (220.0, 220.0, 250.0)
ANGULO_MAX_POR_DEFECTO = 45.0

UMBRAL_VOLADIZO_MM2 = 2.0
UMBRAL_BASE_MM2 = 10.0
_TOL_CAMA_Z_MM = 0.05
_MIN_MUESTRAS_FINAS = 3
_MUESTRAS_POR_SOLIDO_MAX = 1500
_MUESTRAS_TOTAL_MAX = 12000
_TOPE_PIEZAS = 15


def parsear_cama(texto: str) -> tuple[float, float, float]:
    """``"220x220x250"`` -> ``(220.0, 220.0, 250.0)``; ValueError otherwise."""
    partes = texto.lower().replace("*", "x").split("x")
    if len(partes) != 3:
        raise ValueError("'cama' debe tener la forma XxYxZ en mm, p.ej. 220x220x250")
    try:
        dims = tuple(float(p) for p in partes)
    except ValueError as exc:
        raise ValueError("'cama' debe tener la forma XxYxZ en mm, p.ej. 220x220x250") from exc
    if any(not math.isfinite(d) or d <= 0 or d > 5000 for d in dims):
        raise ValueError("'cama': cada dimension debe estar entre 0 y 5000 mm")
    return dims  # type: ignore[return-value]


def _r(x: float) -> float:
    return round(float(x), 1)


def _bbox_de(puntos: np.ndarray) -> list[float]:
    mn = puntos.min(axis=0)
    mx = puntos.max(axis=0)
    return [_r(mn[0]), _r(mx[0]), _r(mn[1]), _r(mx[1]), _r(mn[2]), _r(mx[2])]


def _check_cama(dims: np.ndarray, cama: tuple[float, float, float]) -> dict[str, Any] | None:
    dx, dy, dz = (float(d) for d in dims)
    cx, cy, cz = cama
    if dx <= cx and dy <= cy and dz <= cz:
        return None
    problema: dict[str, Any] = {"dims": [_r(dx), _r(dy), _r(dz)]}
    if dy <= cx and dx <= cy and dz <= cz:
        problema["sugerencia"] = "rotar 90 en Z"
    elif (dx <= cx and dz <= cy and dy <= cz) or (dz <= cx and dx <= cy and dy <= cz):
        problema["sugerencia"] = "tumbar (rotar 90 en X)"
    elif (dz <= cx and dy <= cy and dx <= cz) or (dy <= cx and dz <= cy and dx <= cz):
        problema["sugerencia"] = "tumbar (rotar 90 en Y)"
    else:
        problema["sugerencia"] = "no cabe en ninguna orientacion: partir la pieza o escalar"
    return problema


def _check_voladizo_y_base(
    malla: trimesh.Trimesh, angulo_max: float
) -> tuple[dict[str, Any] | None, float]:
    """Returns ``(voladizo_problem_or_None, contacto_cama_mm2)``."""
    normales = malla.face_normals
    areas = malla.area_faces
    zmin = float(malla.bounds[0][2])
    z_tri = malla.vertices[malla.faces][:, :, 2]
    en_cama = np.all(np.abs(z_tri - zmin) <= _TOL_CAMA_Z_MM, axis=1)

    contacto = float(areas[en_cama & (normales[:, 2] < -0.99)].sum())

    nz = np.clip(normales[:, 2], -1.0, 1.0)
    angulo = np.degrees(np.arcsin(np.clip(-nz, 0.0, 1.0)))
    marcados = (nz < 0) & (angulo > angulo_max + 1e-6) & ~en_cama
    total = float(areas[marcados].sum())
    if total < UMBRAL_VOLADIZO_MM2:
        return None, contacto

    indices = np.nonzero(marcados)[0]
    adyacencia = malla.face_adjacency
    mascara = marcados[adyacencia[:, 0]] & marcados[adyacencia[:, 1]]
    componentes = trimesh.graph.connected_components(
        adyacencia[mascara], nodes=indices, min_len=1
    )
    peor = max(componentes, key=lambda c: areas[c].sum())
    puntos = malla.vertices[malla.faces[peor].ravel()]
    return (
        {
            "mm2": _r(total),
            "peor": {
                "mm2": _r(areas[peor].sum()),
                "bbox": _bbox_de(puntos),
                "ang": _r(angulo[peor].max()),
            },
        },
        contacto,
    )


def _check_pared_fina(
    malla: trimesh.Trimesh, umbral: float, n_muestras: int
) -> dict[str, Any] | None:
    if n_muestras <= 0 or len(malla.faces) == 0:
        return None
    puntos, caras = trimesh.sample.sample_surface(malla, n_muestras, seed=0)
    normales = malla.face_normals[caras]
    origen = puntos - normales * 1e-3
    direccion = -normales
    impactos, rayos, _tris = malla.ray.intersects_location(origen, direccion, multiple_hits=False)
    if len(rayos) == 0:
        return None
    distancias = np.linalg.norm(impactos - origen[rayos], axis=1) + 1e-3
    validos = distancias > 2e-3
    distancias = distancias[validos]
    rayos = rayos[validos]
    finos = distancias < umbral
    if int(finos.sum()) < _MIN_MUESTRAS_FINAS:
        return None
    return {
        "min_mm": round(float(distancias[finos].min()), 2),
        "bbox": _bbox_de(puntos[rayos[finos]]),
        "muestras": int(finos.sum()),
    }


def verificar(
    shape: Any,
    entradas: list[dict[str, Any]],
    boquilla: float = BOQUILLA_POR_DEFECTO,
    cama: tuple[float, float, float] = CAMA_POR_DEFECTO,
    angulo_max: float = ANGULO_MAX_POR_DEFECTO,
) -> dict[str, Any]:
    """``{ok, problemas: {pieza: {cama?, voladizo?, pared_fina?, base?,
    diminuto?}}[, problemas_mas][, nombres_inciertos]}`` — plus the
    parameters used, echoed once so the answer is self-describing."""
    grupos, nombres_inciertos = solids.solidos_por_indice(shape, entradas)
    por_nombre: dict[str, list[Any]] = {}
    for nombre, _indice, solido, _bbox, _vol in grupos:
        por_nombre.setdefault(nombre, []).append(solido)

    mallas: dict[str, trimesh.Trimesh] = {}
    for nombre, piezas in por_nombre.items():
        partes = [kernel_mesh.tessellate_to_trimesh(p, TOLERANCIA_TESSELLATION_MM) for p in piezas]
        malla = trimesh.util.concatenate(partes) if len(partes) > 1 else partes[0]
        malla.merge_vertices()
        mallas[nombre] = malla

    area_total = sum(float(m.area) for m in mallas.values()) or 1.0
    presupuesto = min(_MUESTRAS_TOTAL_MAX, _MUESTRAS_POR_SOLIDO_MAX * len(mallas))

    problemas: dict[str, dict[str, Any]] = {}
    for nombre, malla in mallas.items():
        if len(malla.faces) == 0:
            continue
        dims = malla.extents
        propios: dict[str, Any] = {}

        cama_prob = _check_cama(dims, cama)
        if cama_prob:
            propios["cama"] = cama_prob

        if float(dims.min()) < boquilla:
            propios["diminuto"] = {"dim_min_mm": round(float(dims.min()), 2)}

        voladizo, contacto = _check_voladizo_y_base(malla, angulo_max)
        if voladizo:
            propios["voladizo"] = voladizo
        if contacto < UMBRAL_BASE_MM2:
            propios["base"] = {"contacto_mm2": _r(contacto)}

        n = int(round(presupuesto * float(malla.area) / area_total))
        n = max(200, min(_MUESTRAS_POR_SOLIDO_MAX, n))
        fina = _check_pared_fina(malla, 2.0 * boquilla, n)
        if fina:
            propios["pared_fina"] = fina

        if propios:
            problemas[nombre] = propios

    salida: dict[str, Any] = {
        "ok": not problemas,
        "boquilla": boquilla,
        "cama": [_r(c) for c in cama],
        "angulo_max": _r(angulo_max),
    }
    nombres = list(problemas)
    salida["problemas"] = {n: problemas[n] for n in nombres[:_TOPE_PIEZAS]}
    if len(nombres) > _TOPE_PIEZAS:
        salida["problemas_mas"] = len(nombres) - _TOPE_PIEZAS
    if nombres_inciertos:
        salida["nombres_inciertos"] = True
    return salida
