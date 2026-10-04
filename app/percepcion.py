"""Spatial perception as compact TEXT, not a picture (Phase 5E, `percibir`).

The user's framing: the model should perceive a scene as measured spatial
relations ("como el agua que rodea el objeto" — what touches what, and at
what distance), never by *interpreting* a rendered image. A picture is
genuinely ambiguous about depth/occlusion/millimetres — real evidence from
this project: a `captura` render of `casa_v2.py` made a coffee table look
like it was resting on a rug, while the EXACT distance (`check_colisiones`,
Phase 5B) found a real 0.4 mm air gap underneath it. Every line this module
produces carries a measured number.

Three layers, each independently selectable via `capas` (comma-separated,
default ``"contactos"``):

- ``contactos`` (default): per NAMED piece (not per raw solid — a name that
  produced several disconnected fragments, see `app/solids.py`'s
  "partidas", is treated as ONE piece here), a support relation (what it
  rests on, with the gap `h` in mm), OR, for the scene's lowest/largest-
  footprint piece, a `base` marker instead (it has nothing to rest on by
  definition — without this it would wrongly show up as `SIN APOYO`). Side
  neighbours within `proximidad_mm` get their own capped list, sorted by
  gap. A piece with no support below within `proximidad_mm` is `SIN APOYO`,
  reported with whatever IS nearest (any direction) as a fallback.
  Penetrations (`CHOQUE` lines) reuse the exact same intersection-volume
  definition/threshold as `checks/colisiones.verificar`, via the shared
  `checks/distancia.volumen_interseccion` helper — but computed from the
  pairwise distances THIS layer's own sweep already found, rather than by
  calling `colisiones.verificar` a second time: casa_v2.py's 21 solids
  include one large, many-faced shape (the house shell), against which an
  exact OpenCascade distance call is the genuinely expensive part of this
  whole layer, so doing that walk twice (once here, once inside
  `colisiones.verificar`) would have pushed a real multi-room scene over
  its performance budget (< 5 s) for no benefit — both computations reuse
  the identical `checks/distancia.py` helpers either way.
- ``cortes``: one ASCII occupancy grid per value in `cortes_z`, a
  horizontal or vertical section (`eje` picks the cut's normal axis) over
  a `resolucion`-wide grid, never a full raster/PNG. CONSERVATIVE coverage
  (5E fix-review): each solid's real planar section is computed ONCE
  (`kernel.b123d_kernel.seccion_plana`, OCCT `BRepAlgoAPI_Section`) and
  rasterized so a cell gets a solid's letter if ANY of that solid's
  section intersects the cell (every cell a section edge crosses, plus
  every cell whose centre is inside by an even-odd scanline fill) — the
  earlier one-`is_inside`-sample-per-cell version made whole thin pieces
  (casa_v2's `mesa`, `silla_2`, `silla_4` at Z=8: 2 mm legs in 5.4 mm
  cells) vanish from the grid AND the legend, and was ~3x slower.
- ``huecos``: NOT IMPLEMENTED in this phase (thin walls / enclosed
  cavities) — a clear stub message, backlog item.

The name-level pairwise distance engine (`_pares_cercanos_por_nombre`) is
built on the SAME bbox-prefiltered exact-OCP-distance pattern as
`checks/colisiones.py`, sharing its actual prefilter/cap helpers via
`checks/distancia.py` rather than duplicating them (Phase 5E plan's graph
note) — it additionally keeps the closest POINTS (not just the scalar
distance) to derive a contact direction, which `colisiones.py` never
needed.

Never imports build123d/OCP directly (ADR-0001): only duck-types
`.solids()`/`.bounding_box()`/`.is_inside()`/
`.distance_to_with_closest_points()` on whatever Shape/Solid objects the
caller (`app/documents.py`) hands over, same discipline as `app/solids.py`
and `checks/colisiones.py`; the one OCCT-only operation it needs (a planar
section) goes through the kernel adapter, `kernel.b123d_kernel.seccion_plana`.
"""
from __future__ import annotations

import math
from typing import Any

import solids
from checks import colisiones, distancia
from kernel import b123d_kernel

# Same "genuinely touching" tolerance `checks/colisiones.py` uses (single
# source of truth: imported, never redefined) — a gap this small or smaller
# prints `h=0.0` with no "(NO toca)" suffix.
TOLERANCIA_TOQUE_MM = colisiones.TOLERANCIA_TOQUE_MM
# Same "OCC's distance is really zero" epsilon `checks/colisiones.py` uses to
# decide whether a close pair deserves the exact boolean (single source of
# truth: imported, never redefined) — see that constant's comment for the
# evidence behind the value. Without it this module's `d > 0.0` gate hid a
# CHOQUE the shared engine had already measured correctly (Phase 6.5).
TOLERANCIA_DISTANCIA_CERO_MM = colisiones.TOLERANCIA_DISTANCIA_CERO_MM

PROXIMIDAD_MM_POR_DEFECTO = 5.0
TOPE_VECINOS = 25
RESOLUCION_MIN = 10
RESOLUCION_MAX = 100
# Input bounds (5E fix-review): each cut is a real OCCT section per solid,
# and `proximidad_mm` widens the O(n^2) exact-distance sweep's prefilter --
# both are capped so one request can't turn into an unbounded job.
CORTES_MAX = 6
PROXIMIDAD_MM_MAX = 50.0
ALTURA_MAX_FILAS = 30

_CAPAS_VALIDAS = ("contactos", "cortes", "huecos")

_TEXTO_HUECOS_NO_IMPLEMENTADO = (
    "huecos: no implementado aun (Fase 5E) -- paredes finas y cavidades "
    "cerradas quedan en el backlog"
)

# Fixed/in-plane axis layout per cut normal: `eje` picks the FIXED axis (the
# cut plane's normal); the other two are the grid's (horizontal, vertical)
# in-plane axes, indexed 0=X/1=Y/2=Z into a
# `[xmin, xmax, ymin, ymax, zmin, zmax]` bbox list.
_EJE_INDICE = {"x": 0, "y": 1, "z": 2}
_EJES_EN_PLANO = {
    "x": (1, 2),  # cut normal to X -> grid is (Y horizontal, Z vertical)
    "y": (0, 2),  # cut normal to Y -> grid is (X horizontal, Z vertical)
    "z": (0, 1),  # cut normal to Z -> grid is (X horizontal, Y vertical)
}
_ALFABETO = "abcdefghijklmnopqrstuvwxyz"


# --------------------------------------------------------------- capas


def _normalizar_capas(capas: str | list[str]) -> list[str]:
    if isinstance(capas, str):
        items = [c.strip().lower() for c in capas.split(",") if c.strip()]
    else:
        items = [str(c).strip().lower() for c in capas if str(c).strip()]
    if not items:
        items = ["contactos"]
    desconocidas = [c for c in items if c not in _CAPAS_VALIDAS]
    if desconocidas:
        raise ValueError(
            f"capa(s) desconocida(s) en 'capas': {desconocidas} "
            f"(validas: {', '.join(_CAPAS_VALIDAS)})"
        )
    return items


# ------------------------------------------------------- named-piece setup


def _cargar_grupos(
    shape: Any, entradas: list[dict[str, Any]], solidos_filtro: list[str] | None
) -> tuple[list[tuple[str, int, Any, list[float], float]], list[str], bool]:
    """Like `render.grupos_desde_shape`'s name filtering, but keeps the raw
    per-SOLID entries (not merged meshes) — `percibir` needs each solid's
    own bbox/geometry for the pairwise distance engine below."""
    grupos_indexados, nombres_inciertos = solids.solidos_por_indice(shape, entradas)
    orden_completo: list[str] = []
    vistos: set[str] = set()
    for nombre, *_ in grupos_indexados:
        if nombre not in vistos:
            vistos.add(nombre)
            orden_completo.append(nombre)

    if solidos_filtro is not None:
        faltantes = [n for n in solidos_filtro if n not in vistos]
        if faltantes:
            raise ValueError(f"nombres de solido inexistentes en este documento: {faltantes}")
        permitidos = set(solidos_filtro)
        grupos_indexados = [g for g in grupos_indexados if g[0] in permitidos]
        orden = [n for n in orden_completo if n in permitidos]
    else:
        orden = orden_completo
    return grupos_indexados, orden, nombres_inciertos


def _bbox_por_nombre(
    grupos_indexados: list[tuple[str, int, Any, list[float], float]],
) -> dict[str, list[float]]:
    """Combined bbox per NAME (union across every fragment of a "partida"
    piece) -- ``[xmin, xmax, ymin, ymax, zmin, zmax]``."""
    resultado: dict[str, list[float]] = {}
    for nombre, _idx, _solido, bbox, _vol in grupos_indexados:
        if nombre not in resultado:
            resultado[nombre] = list(bbox)
        else:
            actual = resultado[nombre]
            actual[0] = min(actual[0], bbox[0])
            actual[1] = max(actual[1], bbox[1])
            actual[2] = min(actual[2], bbox[2])
            actual[3] = max(actual[3], bbox[3])
            actual[4] = min(actual[4], bbox[4])
            actual[5] = max(actual[5], bbox[5])
    return resultado


def _piezas_por_nombre(
    grupos_indexados: list[tuple[str, int, Any, list[float], float]], orden: list[str]
) -> dict[str, list[Any]]:
    resultado: dict[str, list[Any]] = {n: [] for n in orden}
    for nombre, _idx, solido, _bbox, _vol in grupos_indexados:
        if nombre in resultado:
            resultado[nombre].append(solido)
    return resultado


# ------------------------------------------------------ pairwise distances


def _vector(punto_a: Any, punto_b: Any) -> tuple[float, float, float]:
    return (punto_b.X - punto_a.X, punto_b.Y - punto_a.Y, punto_b.Z - punto_a.Z)


def _eje_dominante(vector: tuple[float, float, float]) -> str:
    """``+X``/``-X``/``+Y``/``-Y``/``+Z``/``-Z`` -- whichever axis has the
    largest absolute component, signed by that component's own sign. Used
    for a genuinely NON-touching pair, where the closest-points vector is
    meaningful on its own (two solids with real footprint overlap, e.g. a
    table leg 0.4 mm above a rug, have their closest points nearly
    vertically aligned, so the dominant axis comes out ``Z`` without
    needing an actual face-normal computation)."""
    dx, dy, dz = vector
    letra, valor = max((("X", dx), ("Y", dy), ("Z", dz)), key=lambda t: abs(t[1]))
    signo = "+" if valor >= 0 else "-"
    return f"{signo}{letra}"


# Offset (mm) used to probe just off either side of a genuine touching
# point -- see `_eje_de_contacto`.
_EPS_CONTACTO_MM = 0.05


def _eje_de_contacto(solido_a: Any, solido_b: Any, punto: Any) -> str | None:
    """Direction FROM `solido_a` TO `solido_b`, for a pair that is
    genuinely TOUCHING (see `_eje_del_par`). OCP's own closest points
    degenerate to the SAME coordinates on both sides for a touching pair
    (verified against a box resting exactly on another:
    `distance_to_with_closest_points` returns identical points for both),
    which carries no direction at all -- and the two solids' overall bbox
    CENTRES are equally useless here: a small piece resting on the floor of
    a large, irregularly-shaped multi-storey house shell is nowhere near
    that shell's own bbox centre (verified: it put the direction from a
    ground-floor sofa to the house shell as `+Z`, as if the sofa were
    hanging below the house, when the sofa is very much sitting on top of
    the ground floor).

    Instead this probes `Shape.is_inside` (build123d) at two points just
    off either side of the ACTUAL shared touch point, along each axis in
    turn: whichever axis has `solido_a` occupying exactly one side and
    `solido_b` the other is the real local contact direction, independent
    of either solid's overall size or shape complexity. Returns ``None``
    (falls back to `_eje_dominante` in `_eje_del_par`) if no axis cleanly
    separates them -- should not happen for a genuine touching pair, but
    never crashes if it does."""
    x, y, z = punto.X, punto.Y, punto.Z
    for letra, mas, menos in (
        ("X", (x + _EPS_CONTACTO_MM, y, z), (x - _EPS_CONTACTO_MM, y, z)),
        ("Y", (x, y + _EPS_CONTACTO_MM, z), (x, y - _EPS_CONTACTO_MM, z)),
        ("Z", (x, y, z + _EPS_CONTACTO_MM), (x, y, z - _EPS_CONTACTO_MM)),
    ):
        a_en_mas = _es_interior_seguro(solido_a, mas)
        a_en_menos = _es_interior_seguro(solido_a, menos)
        b_en_mas = _es_interior_seguro(solido_b, mas)
        b_en_menos = _es_interior_seguro(solido_b, menos)
        if a_en_mas and b_en_menos and not (a_en_menos or b_en_mas):
            return f"-{letra}"
        if a_en_menos and b_en_mas and not (a_en_mas or b_en_menos):
            return f"+{letra}"
    return None


def _eje_del_par(d: float, punto_a: Any, punto_b: Any, solido_a: Any, solido_b: Any) -> str:
    """Direction FROM A TO B for one pairwise-engine entry -- probing-based
    for a touching pair (see `_eje_de_contacto`), the plain closest-points
    vector otherwise (see `_eje_dominante`)."""
    if d <= TOLERANCIA_TOQUE_MM:
        eje = _eje_de_contacto(solido_a, solido_b, punto_a)
        if eje is not None:
            return eje
    return _eje_dominante(_vector(punto_a, punto_b))


_ParInfo = tuple[float, Any, Any, Any, Any]


def _pares_cercanos_por_nombre(
    grupos_indexados: list[tuple[str, int, Any, list[float], float]], margen_mm: float
) -> dict[tuple[str, str], _ParInfo]:
    """Minimum exact distance (plus its closest points AND the two actual
    solids involved) between every pair of DIFFERENTLY-named solids within
    `margen_mm`, aggregated to NAME granularity (the smallest gap wins when
    a name has several fragments). Same O(n^2)-but-bbox-prefiltered walk as
    `checks/colisiones.verificar`, over the SAME per-solid entries
    (`checks/distancia.py`'s helpers) — just keeping the closest points and
    solids too (`_eje_del_par` needs both for a touching pair). Keys are
    alphabetically ordered ``(nombre_menor, nombre_mayor)``, with every
    stored value reordered to match (``_de_menor``, ``_de_mayor``)."""
    mejor: dict[tuple[str, str], _ParInfo] = {}
    n = len(grupos_indexados)
    for i in range(n):
        nombre_a, _ia, solido_a, bbox_a, _va = grupos_indexados[i]
        for j in range(i + 1, n):
            nombre_b, _ib, solido_b, bbox_b, _vb = grupos_indexados[j]
            if nombre_a == nombre_b:
                continue  # fragments of the same "partida" piece
            if not distancia.bbox_se_acercan(bbox_a, bbox_b, margen_mm):
                continue
            try:
                d, pa, pb = distancia.distancia_y_puntos(solido_a, solido_b)
            except Exception:  # noqa: BLE001 - a boolean/distance failure on one pair never aborts the rest
                continue
            if d > margen_mm:
                continue
            if nombre_a <= nombre_b:
                clave, candidato = (nombre_a, nombre_b), (d, pa, pb, solido_a, solido_b)
            else:
                clave, candidato = (nombre_b, nombre_a), (d, pb, pa, solido_b, solido_a)
            actual = mejor.get(clave)
            if actual is None or d < actual[0]:
                mejor[clave] = candidato
    return mejor


def _relacion_desde_par(nombre: str, a: str, b: str, info: _ParInfo) -> tuple[str, str] | None:
    """``(otro_nombre, eje_desde_nombre_hacia_otro)`` if `nombre` is one of
    the two names in this pair, else ``None``."""
    d, pa, pb, solido_a, solido_b = info
    if a == nombre:
        return b, _eje_del_par(d, pa, pb, solido_a, solido_b)
    if b == nombre:
        return a, _eje_del_par(d, pb, pa, solido_b, solido_a)
    return None


# Extra clearance (mm) added past the already-measured gap `d` when probing
# for real material under a candidate support -- see `_otro_soporta_a`.
_EPS_APOYO_MM = 0.1
# Footprint inset (fraction of each side) for the support probes: small
# enough that a leg at a TRUE corner (a 2 mm leg on a 40 mm chair is 5% of
# the side) is still under a probe, large enough that a wall merely touching
# a piece's SIDE face never counts as "material underneath" it.
_INSET_APOYO = 0.02
_INSET_APOYO_MIN_MM = 0.05


def _rect_huella(bbox: list[float]) -> tuple[float, float, float, float]:
    """`bbox`'s XY footprint shrunk by `_INSET_APOYO` (at least
    `_INSET_APOYO_MIN_MM`, never past the centre) -- ``(x0, x1, y0, y1)``."""
    x0, x1, y0, y1 = bbox[0], bbox[1], bbox[2], bbox[3]
    dx = min(max((x1 - x0) * _INSET_APOYO, _INSET_APOYO_MIN_MM), (x1 - x0) / 2)
    dy = min(max((y1 - y0) * _INSET_APOYO, _INSET_APOYO_MIN_MM), (y1 - y0) / 2)
    return x0 + dx, x1 - dx, y0 + dy, y1 - dy


def _puntos_muestra_xy(bbox: list[float]) -> list[tuple[float, float]]:
    """Cheap fast-path probes: the footprint centre plus its four corners
    (inset only `_INSET_APOYO`, so legs at the true corners are hit). A miss
    here is NOT final -- `_otro_soporta_a` then falls back to the exact
    section test, which also catches a single off-centre contact or a line
    contact (a box on a wedge's ridge) that no fixed probe grid can."""
    x0, x1, y0, y1 = _rect_huella(bbox)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return [(cx, cy), (x0, y0), (x1, y0), (x0, y1), (x1, y1)]


def _segmentos_seccion(
    fragmento: Any, eje: str, valor: float, deflexion: float, cache: dict | None = None
) -> list[tuple[float, float, float, float]]:
    """`fragmento`'s planar section at ``eje = valor`` as 2D segments in
    that cut's in-plane axes (`_EJES_EN_PLANO`). Raises whatever the kernel
    raises (callers decide the fallback). Cached per ``(fragment, eje,
    valor)`` when a `cache` dict is given -- many pieces rest on the same
    floor, i.e. probe the same big house shell at the same height."""
    clave = (id(fragmento), eje, round(valor, 6))
    if cache is not None and clave in cache:
        return cache[clave]
    idx_h, idx_v = _EJES_EN_PLANO[eje]
    segmentos: list[tuple[float, float, float, float]] = []
    for polilinea in b123d_kernel.seccion_plana(fragmento, eje, valor, deflexion):
        for p, q in zip(polilinea, polilinea[1:]):
            segmentos.append((p[idx_h], p[idx_v], q[idx_h], q[idx_v]))
    if cache is not None:
        cache[clave] = segmentos
    return segmentos


def _segmento_toca_rect(
    seg: tuple[float, float, float, float], rx0: float, rx1: float, ry0: float, ry1: float
) -> bool:
    """Liang-Barsky: does segment `seg` intersect the closed rectangle?"""
    x0, y0, x1, y1 = seg
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x0 - rx0), (dx, rx1 - x0), (-dy, y0 - ry0), (dy, ry1 - y0)):
        if p == 0.0:
            if q < 0.0:
                return False
            continue
        t = q / p
        if p < 0.0:
            if t > t1:
                return False
            t0 = max(t0, t)
        else:
            if t < t0:
                return False
            t1 = min(t1, t)
    return True


def _cruces_horizontales(y: float, segmentos: list[tuple[float, float, float, float]]) -> list[float]:
    """Sorted X of every crossing between the line ``Y = y`` and the
    segments (half-open rule, so a vertex is counted once) -- the even-odd
    scanline over a section's closed loops, holes included."""
    xs = []
    for x0, y0, x1, y1 in segmentos:
        if (y0 > y) != (y1 > y):
            xs.append(x0 + (y - y0) * (x1 - x0) / (y1 - y0))
    xs.sort()
    return xs


def _dentro_par_impar(x: float, y: float, segmentos: list[tuple[float, float, float, float]]) -> bool:
    return sum(1 for xc in _cruces_horizontales(y, segmentos) if xc > x) % 2 == 1


def _seccion_toca_rect(
    segmentos: list[tuple[float, float, float, float]], rect: tuple[float, float, float, float]
) -> bool:
    """Non-empty intersection between a section (closed loops given as
    segments) and a rectangle: a boundary segment crosses/lies in it, or the
    rectangle sits wholly inside a loop (its centre is inside, even-odd)."""
    rx0, rx1, ry0, ry1 = rect
    if any(_segmento_toca_rect(seg, rx0, rx1, ry0, ry1) for seg in segmentos):
        return True
    return _dentro_par_impar((rx0 + rx1) / 2, (ry0 + ry1) / 2, segmentos)


def _otro_soporta_a(
    bbox_nombre: list[float], d: float, fragmentos_otro: list[Any], cache: dict | None = None
) -> bool:
    """True if `otro` (`fragmentos_otro`, one or more solids) has real
    material just below `nombre`'s own footprint, `d` mm beneath its lowest
    point -- the direct geometric test for "otro supports nombre" (a
    +Z-facing surface right under it), never trusting whatever single
    closest point OCP happens to return for a touching pair (that
    degenerated on a real `casa_v2.py` cylinder-based toilet, whose closest
    point sat on a curved silhouette edge).

    Two stages (5E fix-review): a cheap `is_inside` fast path at the
    footprint centre + 4 corners (`_puntos_muestra_xy`, 2% inset); on a
    miss, the EXACT test -- `otro`'s planar section at that height
    (`kernel.b123d_kernel.seccion_plana`) intersected with `nombre`'s
    inset footprint rectangle. The earlier probe-only version (centre + 4
    corners inset 10%) could miss legs at the true corners and any single
    off-centre or line contact (false `SIN APOYO`)."""
    z_prueba = bbox_nombre[4] - d - _EPS_APOYO_MM
    if any(
        _es_interior_seguro(frag, (x, y, z_prueba))
        for x, y in _puntos_muestra_xy(bbox_nombre)
        for frag in fragmentos_otro
    ):
        return True
    rect = _rect_huella(bbox_nombre)
    for frag in fragmentos_otro:
        try:
            segmentos = _segmentos_seccion(frag, "z", z_prueba, 0.05, cache)
        except Exception:  # noqa: BLE001 - a failed section only disables the exact fallback for this fragment
            continue
        if segmentos and _seccion_toca_rect(segmentos, rect):
            return True
    return False


def _buscar_soporte(
    nombre: str,
    bbox_por_nombre: dict[str, list[float]],
    piezas_por_nombre: dict[str, list[Any]],
    mejor: dict[tuple[str, str], _ParInfo],
    pares_choque: set[frozenset[str]],
    cache: dict | None = None,
) -> tuple[float, str] | None:
    """Nearest OTHER piece with real material directly below `nombre`
    (`_otro_soporta_a`), or ``None`` if none of the candidates the pairwise
    engine found within its own search radius actually support it."""
    candidatos: list[tuple[float, str]] = []
    for (a, b), info in mejor.items():
        if frozenset((a, b)) in pares_choque:
            continue
        if a == nombre:
            otro = b
        elif b == nombre:
            otro = a
        else:
            continue
        candidatos.append((info[0], otro))
    candidatos.sort(key=lambda t: t[0])

    bbox_nombre = bbox_por_nombre[nombre]
    for d, otro in candidatos:
        if _otro_soporta_a(bbox_nombre, d, piezas_por_nombre[otro], cache):
            return (d, otro)
    return None


def _buscar_mas_cercano(
    nombre: str,
    mejor: dict[tuple[str, str], _ParInfo],
    pares_choque: set[frozenset[str]],
) -> tuple[float, str, str] | None:
    """Nearest OTHER piece in ANY direction -- the `SIN APOYO` fallback."""
    mejor_candidato: tuple[float, str, str] | None = None
    for (a, b), info in mejor.items():
        if frozenset((a, b)) in pares_choque:
            continue
        relacion = _relacion_desde_par(nombre, a, b, info)
        if relacion is None:
            continue
        otro, eje_dir = relacion
        d = info[0]
        if mejor_candidato is None or d < mejor_candidato[0]:
            mejor_candidato = (d, otro, eje_dir)
    return mejor_candidato


def _detectar_base(orden: list[str], bbox_por_nombre: dict[str, list[float]]) -> str:
    """The scene's ground/reference piece: lowest ``z_min``, largest XY
    footprint breaks a tie -- see the module docstring for why this piece
    is exempt from the `SIN APOYO` check rather than a false positive."""

    def area_xy(nombre: str) -> float:
        b = bbox_por_nombre[nombre]
        return (b[1] - b[0]) * (b[3] - b[2])

    z_min_global = min(bbox_por_nombre[n][4] for n in orden)
    candidatos = [n for n in orden if bbox_por_nombre[n][4] <= z_min_global + 1e-6]
    return max(candidatos, key=area_xy)


# --------------------------------------------------------------- contactos


def _detectar_choques(
    mejor: dict[tuple[str, str], _ParInfo], tolerancia_mm3: float
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(choques, errores)`` -- reuses the distances the pairwise engine
    ALREADY computed (no extra OCP distance call): only the handful of
    pairs found touching/overlapping (``d <= TOLERANCIA_DISTANCIA_CERO_MM``)
    get the one additional `checks/distancia.volumen_interseccion` call (the
    exact same
    intersection-volume helper `checks/colisiones.verificar` uses for its
    own ``choques``, same threshold), so a `percibir` call never pays for a
    SECOND full O(n^2) sweep over the document just to re-derive
    penetrations -- see the module docstring for why that mattered."""
    choques: list[dict[str, Any]] = []
    errores: list[dict[str, Any]] = []
    for (a, b), (d, _pa, _pb, solido_a, solido_b) in mejor.items():
        if d > TOLERANCIA_DISTANCIA_CERO_MM:
            continue
        try:
            volumen = distancia.volumen_interseccion(solido_a, solido_b)
        except Exception as exc:  # noqa: BLE001 - one bad pair never aborts the rest
            errores.append({"a": a, "b": b, "mensaje": str(exc)})
            continue
        if volumen > tolerancia_mm3:
            choques.append({"a": a, "b": b, "vol": round(volumen, 1)})
    return choques, errores


def _texto_contactos(
    orden: list[str],
    bbox_por_nombre: dict[str, list[float]],
    piezas_por_nombre: dict[str, list[Any]],
    grupos_indexados: list[tuple[str, int, Any, list[float], float]],
    proximidad_mm: float,
    tolerancia_mm3: float,
    nombres_inciertos: bool,
) -> str:
    n_nombres = len(orden)
    if n_nombres == 0:
        return "0 solido(s) -- nada que analizar"

    bx0 = min(bbox_por_nombre[n][0] for n in orden)
    bx1 = max(bbox_por_nombre[n][1] for n in orden)
    by0 = min(bbox_por_nombre[n][2] for n in orden)
    by1 = max(bbox_por_nombre[n][3] for n in orden)
    bz0 = min(bbox_por_nombre[n][4] for n in orden)
    bz1 = max(bbox_por_nombre[n][5] for n in orden)
    encabezado = (
        f"{n_nombres} solido(s) | bbox X[{bx0:.1f},{bx1:.1f}] "
        f"Y[{by0:.1f},{by1:.1f}] Z[{bz0:.1f},{bz1:.1f}] mm"
    )
    if nombres_inciertos:
        encabezado += " (nombres inciertos)"
    lineas = [encabezado]

    base = _detectar_base(orden, bbox_por_nombre)
    mejor = _pares_cercanos_por_nombre(grupos_indexados, proximidad_mm)

    # Penetrations: derived from the SAME pairwise pass above (already
    # `solidos_filtro`-scoped, since `grupos_indexados`/`mejor` only ever
    # cover the requested subset) rather than a second full sweep -- see
    # `_detectar_choques`.
    choques, errores = _detectar_choques(mejor, tolerancia_mm3)
    pares_choque = {frozenset((c["a"], c["b"])) for c in choques}

    usados_como_soporte: set[frozenset[str]] = set()
    filas_sin_apoyo: list[str] = []
    cache_secciones: dict = {}
    for nombre in orden:
        if nombre == base:
            lineas.append(f"{nombre}  base")
            continue
        soporte = _buscar_soporte(
            nombre, bbox_por_nombre, piezas_por_nombre, mejor, pares_choque, cache_secciones
        )
        if soporte is not None:
            gap_mm, otro = soporte
            usados_como_soporte.add(frozenset((nombre, otro)))
            if gap_mm <= TOLERANCIA_TOQUE_MM:
                lineas.append(f"{nombre}  sobre {otro}  h=0.0")
            else:
                lineas.append(f"{nombre}  sobre {otro}  h={gap_mm:.1f} (NO toca)")
            continue
        cercano = _buscar_mas_cercano(nombre, mejor, pares_choque)
        if cercano is None:
            filas_sin_apoyo.append(
                f"SIN APOYO: {nombre} (sin vecinos a <= {proximidad_mm:g} mm)"
            )
        else:
            gap_mm, otro, eje_dir = cercano
            filas_sin_apoyo.append(
                f"SIN APOYO: {nombre} (mas cercano: {otro} a {gap_mm:.1f} mm, dir {eje_dir})"
            )

    filas_vecinos: list[tuple[float, str]] = []
    for (a, b), (d, pa, pb, solido_a, solido_b) in mejor.items():
        clave = frozenset((a, b))
        if clave in pares_choque or clave in usados_como_soporte:
            continue
        eje_dir = _eje_del_par(d, pa, pb, solido_a, solido_b)
        filas_vecinos.append((d, f"{a} <-> {b}  {d:.1f} mm  ({eje_dir})"))
    filas_vecinos.sort(key=lambda t: t[0])
    textos_vecinos = [t[1] for t in filas_vecinos]
    recortadas, extra = distancia.con_tope(textos_vecinos, TOPE_VECINOS)
    if recortadas:
        lineas.append(f"--- vecinos (<= {proximidad_mm:g} mm) ---")
        lineas.extend(recortadas)
        if extra:
            lineas.append(f"+{extra} mas")

    if filas_sin_apoyo:
        lineas.append("--- sin apoyo ---")
        lineas.extend(filas_sin_apoyo)

    if choques:
        lineas.append("--- choques ---")
        for c in choques:
            lineas.append(f"CHOQUE: {c['a']} x {c['b']} {c['vol']:.1f} mm3")

    if errores:
        lineas.append("--- errores ---")
        for e in errores:
            lineas.append(f"ERROR: {e['a']} x {e['b']}: {e['mensaje']}")

    return "\n".join(lineas)


# ------------------------------------------------------------------ cortes


def _letra_para(nombre: str, letras: dict[str, str]) -> str:
    if nombre not in letras:
        i = len(letras)
        letras[nombre] = (
            _ALFABETO[i] if i < 26 else _ALFABETO[i // 26 - 1] + _ALFABETO[i % 26]
        )
    return letras[nombre]


def _punto_3d(eje: str, valor_fijo: float, h: float, v: float) -> tuple[float, float, float]:
    if eje == "x":
        return (valor_fijo, h, v)
    if eje == "y":
        return (h, valor_fijo, v)
    return (h, v, valor_fijo)  # eje == "z"


def _es_interior_seguro(fragmento: Any, punto: tuple[float, float, float]) -> bool:
    try:
        return bool(fragmento.is_inside(punto))
    except Exception:  # noqa: BLE001 - a single degenerate sample never aborts the whole cut
        return False


def _celdas_por_muestreo(
    fragmento: Any,
    bbox: list[float],
    eje: str,
    valor: float,
    rejilla: tuple[float, float, float, float, int, int],
) -> set[tuple[int, int]]:
    """Fallback ONLY for a fragment whose OCCT section failed: the original
    one-`is_inside`-sample-per-cell-centre classification (can miss
    features thinner than a cell -- hence not the default path)."""
    h_min, v_max, celda_h, celda_v, columnas, filas = rejilla
    idx_h, idx_v = _EJES_EN_PLANO[eje]
    c0 = max(0, int((bbox[idx_h * 2] - h_min) / celda_h))
    c1 = min(columnas - 1, int((bbox[idx_h * 2 + 1] - h_min) / celda_h))
    r0 = max(0, int((v_max - bbox[idx_v * 2 + 1]) / celda_v))
    r1 = min(filas - 1, int((v_max - bbox[idx_v * 2]) / celda_v))
    celdas: set[tuple[int, int]] = set()
    for r in range(r0, r1 + 1):
        v = v_max - (r + 0.5) * celda_v
        for c in range(c0, c1 + 1):
            h = h_min + (c + 0.5) * celda_h
            if _es_interior_seguro(fragmento, _punto_3d(eje, valor, h, v)):
                celdas.add((r, c))
    return celdas


def _celdas_de_seccion(
    segmentos: list[tuple[float, float, float, float]],
    rejilla: tuple[float, float, float, float, int, int],
) -> set[tuple[int, int]]:
    """Conservative rasterization of one section (closed loops as 2D
    segments): a cell is covered if the section intersects it at all --
    (1) every cell a boundary segment passes through (Liang-Barsky against
    the cell rectangle, shrunk by a hair so an edge lying exactly ON a
    grid line doesn't smear into the neighbour), so a 2 mm leg in a 5.4 mm
    cell always marks >= 1 cell; (2) every cell whose centre is inside
    (even-odd scanline per row), which covers the cells a large region
    fills without any edge crossing them. Any cell the section overlaps
    either contains a piece of its boundary or lies wholly inside it, so
    (1) + (2) is exact any-overlap coverage. A row with an odd crossing
    count (open polyline, not closed loops) gets no fill -- only its edge
    cells from (1)."""
    h_min, v_max, celda_h, celda_v, columnas, filas = rejilla
    eps_h, eps_v = celda_h * 1e-6, celda_v * 1e-6
    celdas: set[tuple[int, int]] = set()
    for seg in segmentos:
        x0, y0, x1, y1 = seg
        c0 = max(0, int((min(x0, x1) - h_min) / celda_h))
        c1 = min(columnas - 1, int((max(x0, x1) - h_min) / celda_h))
        r0 = max(0, int((v_max - max(y0, y1)) / celda_v))
        r1 = min(filas - 1, int((v_max - min(y0, y1)) / celda_v))
        for r in range(r0, r1 + 1):
            ry0 = v_max - (r + 1) * celda_v + eps_v
            ry1 = v_max - r * celda_v - eps_v
            for c in range(c0, c1 + 1):
                if (r, c) in celdas:
                    continue
                rx0 = h_min + c * celda_h + eps_h
                rx1 = h_min + (c + 1) * celda_h - eps_h
                if _segmento_toca_rect(seg, rx0, rx1, ry0, ry1):
                    celdas.add((r, c))
    for r in range(filas):
        y = v_max - (r + 0.5) * celda_v
        xs = _cruces_horizontales(y, segmentos)
        if len(xs) % 2:
            # Odd crossing count = the section isn't a set of closed loops
            # on this row (open polyline / degenerate OCCT output): pairing
            # the crossings would fill the wrong span, so only the edge
            # cells marked above are kept for this row.
            continue
        for k in range(0, len(xs) - 1, 2):
            c_ini = max(0, math.ceil((xs[k] - h_min) / celda_h - 0.5))
            c_fin = min(columnas - 1, math.floor((xs[k + 1] - h_min) / celda_h - 0.5))
            for c in range(c_ini, c_fin + 1):
                celdas.add((r, c))
    return celdas


def _un_corte_texto(
    orden: list[str],
    bbox_por_nombre: dict[str, list[float]],
    piezas_por_nombre: dict[str, list[Any]],
    eje: str,
    valor: float,
    resolucion: int,
) -> str:
    idx_fijo = _EJE_INDICE[eje]
    idx_h, idx_v = _EJES_EN_PLANO[eje]

    candidatos = [
        n
        for n in orden
        if bbox_por_nombre[n][idx_fijo * 2] - 1e-6 <= valor <= bbox_por_nombre[n][idx_fijo * 2 + 1] + 1e-6
    ]
    if not candidatos:
        return f"corte {eje.upper()}={valor:g} mm -- ningun solido cruza este plano"

    h_min = min(bbox_por_nombre[n][idx_h * 2] for n in orden)
    h_max = max(bbox_por_nombre[n][idx_h * 2 + 1] for n in orden)
    v_min = min(bbox_por_nombre[n][idx_v * 2] for n in orden)
    v_max = max(bbox_por_nombre[n][idx_v * 2 + 1] for n in orden)
    rango_h = max(h_max - h_min, 1e-9)
    rango_v = max(v_max - v_min, 1e-9)

    columnas = max(int(resolucion), 1)
    filas = max(1, min(ALTURA_MAX_FILAS, round(columnas * rango_v / rango_h)))
    celda_h = rango_h / columnas
    celda_v = rango_v / filas
    rejilla_geo = (h_min, v_max, celda_h, celda_v, columnas, filas)
    deflexion = min(celda_h, celda_v) / 4

    rejilla = [["." for _ in range(columnas)] for _ in range(filas)]
    letras: dict[str, str] = {}

    for nombre in candidatos:
        celdas: set[tuple[int, int]] = set()
        for fragmento in piezas_por_nombre[nombre]:
            try:
                bb = fragmento.bounding_box()
                lo = (bb.min.X, bb.min.Y, bb.min.Z)[idx_fijo]
                hi = (bb.max.X, bb.max.Y, bb.max.Z)[idx_fijo]
                if not (lo - 1e-6 <= valor <= hi + 1e-6):
                    continue  # this fragment of a "partida" piece doesn't reach the plane
                segmentos = _segmentos_seccion(fragmento, eje, valor, deflexion)
                celdas |= _celdas_de_seccion(segmentos, rejilla_geo)
            except Exception:  # noqa: BLE001 - a failed section degrades to point sampling, never aborts the cut
                celdas |= _celdas_por_muestreo(
                    fragmento, bbox_por_nombre[nombre], eje, valor, rejilla_geo
                )
        if not celdas:
            continue
        letra = _letra_para(nombre, letras)
        for r, c in celdas:
            rejilla[r][c] = letra if rejilla[r][c] == "." else "#"

    encabezado = (
        f"corte {eje.upper()}={valor:g} mm | {columnas}x{filas} celdas, "
        f"{celda_h:.2f}x{celda_v:.2f} mm/celda"
    )
    lineas = [encabezado]
    if letras:
        leyenda = " ".join(f"{letra}={nombre}" for nombre, letra in letras.items())
        lineas.append(f"leyenda: {leyenda}")
    lineas.extend("".join(fila) for fila in rejilla)
    return "\n".join(lineas)


def _texto_cortes(
    orden: list[str],
    bbox_por_nombre: dict[str, list[float]],
    piezas_por_nombre: dict[str, list[Any]],
    eje: str,
    cortes_z: list[float] | None,
    resolucion: int,
) -> str:
    if eje not in _EJE_INDICE:
        raise ValueError(f"eje invalido {eje!r} (usar x, y o z)")
    if not cortes_z:
        return "cortes: no se indico ningun valor en 'cortes_z'"
    if len(cortes_z) > CORTES_MAX:
        raise ValueError(f"'cortes_z' admite como maximo {CORTES_MAX} valores ({len(cortes_z)} recibidos)")
    if not orden:
        return "cortes: 0 solido(s) -- nada que analizar"
    return "\n\n".join(
        _un_corte_texto(orden, bbox_por_nombre, piezas_por_nombre, eje, float(v), resolucion)
        for v in cortes_z
    )


# --------------------------------------------------------------- fachada


def percibir_texto(
    shape: Any,
    entradas: list[dict[str, Any]],
    *,
    capas: str | list[str] = "contactos",
    cortes_z: list[float] | None = None,
    eje: str = "z",
    resolucion: int = 40,
    solidos_filtro: list[str] | None = None,
    proximidad_mm: float = PROXIMIDAD_MM_POR_DEFECTO,
    tolerancia_mm3: float = colisiones.TOLERANCIA_MM3_POR_DEFECTO,
) -> tuple[str, bool]:
    """Build the compact perception TEXT for a STEP document's named
    solids. Returns ``(texto, nombres_inciertos)`` -- see the module
    docstring for the layer contracts.

    Raises ``ValueError`` (caller turns it into an HTTP 400) for an unknown
    layer name, an unknown `eje`, or a name in `solidos_filtro` that does
    not exist in this document."""
    capas_pedidas = _normalizar_capas(capas)
    grupos_indexados, orden, nombres_inciertos = _cargar_grupos(shape, entradas, solidos_filtro)
    bbox_por_nombre = _bbox_por_nombre(grupos_indexados)
    piezas_por_nombre = _piezas_por_nombre(grupos_indexados, orden)

    secciones: list[str] = []
    if "contactos" in capas_pedidas:
        secciones.append(
            _texto_contactos(
                orden,
                bbox_por_nombre,
                piezas_por_nombre,
                grupos_indexados,
                proximidad_mm,
                tolerancia_mm3,
                nombres_inciertos,
            )
        )
    if "cortes" in capas_pedidas:
        secciones.append(
            _texto_cortes(orden, bbox_por_nombre, piezas_por_nombre, eje, cortes_z, resolucion)
        )
    if "huecos" in capas_pedidas:
        secciones.append(_TEXTO_HUECOS_NO_IMPLEMENTADO)

    return "\n\n".join(secciones), nombres_inciertos
