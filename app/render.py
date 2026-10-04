"""Headless PNG render of a document's named solids (Phase 5B, feedback
item 1). Replaces the Phase 3 ``captura`` stub.

Phase 5B fix-review 2 (this version): a real per-pixel numpy software
**z-buffer rasterizer**, replacing the Phase 5B fix-review's sorted
``matplotlib.collections.PolyCollection`` painter's-algorithm renderer.

That renderer's whole premise -- pick a single draw order and rely on later
polygons painting over earlier ones -- is fundamentally unsound for a scene
with long or interpenetrating triangles (a house shell's walls/floor/roof,
seen at any oblique angle): ANY single per-triangle depth key (centroid,
average, nearest-vertex -- all were tried across Phase 5B's two prior
rounds) can be wrong for *part* of a triangle while being right for another
part, because a whole triangle only ever gets ONE position in the draw
order. The real casa_v2.py render kept showing sawtooth artifacts (rows of
small furniture-coloured triangles poking through the front-right and
lower-right walls, a magenta shard over the wardrobe face) precisely
because nearest-vertex depth sorting, even after subdividing oversized
triangles, still occasionally misorders two triangles whose depth *ranges*
overlap along the view direction. A per-pixel z-buffer has no such failure
mode: visibility is resolved independently for every pixel by comparing
actual interpolated depth, so triangle draw ORDER never matters at all --
this is the standard, correct fix, not a further heuristic tweak.

Pipeline:

- Project every (already back-face-UNculled, see below) triangle with the
  same hand-built orthographic camera as before (``_base_camara``,
  azimut/elevacion) to 2D pixel coordinates + a scalar depth along the view
  direction.
- Flat-shade each triangle once (``_AMBIENTE + _DIFUSO * |normal . luz|``,
  sign-independent so a mis-oriented/backwards face from tessellation never
  renders pitch black).
- Rasterize at ``_SS``x supersampled resolution: for every triangle, walk
  only its own screen-space pixel bounding box, compute barycentric
  coordinates for that sub-grid (vectorized per triangle), and — where a
  pixel is inside the triangle AND its interpolated depth is nearer than
  whatever is already in the z-buffer there — overwrite the z-buffer and
  colour buffer. This is an exact, order-independent visibility test: it
  does not matter what order triangles are processed in, since a pixel's
  final colour only ever comes from whichever triangle covering it has the
  largest depth (nearest the camera).
- No back-face culling: unlike the previous version, EVERY triangle is
  rasterized (front and back). A single-walled shell (casa_v2's exterior
  walls, cut open by door/window boolean subtractions) needs its INSIDE
  face drawable too — that's exactly what's visible looking into the house
  through an opening — and a z-buffer resolves the resulting visibility
  correctly regardless; culling was only ever a painter's-algorithm
  triangle-count optimization, not a correctness requirement, for a
  per-pixel test.
- Box-downscale the supersampled buffer back to the requested size (cheap
  anti-aliasing — no triangle-edge hairlines from adjacent co-planar
  triangles, since there is no per-polygon edge antialiasing to fight
  anymore; the whole canvas is one array).
- A cheap depth-discontinuity outline pass (``_dibujar_bordes``) darkens
  pixels next to a silhouette or a sharp crease in the z-buffer — a couple
  of numpy diffs over the whole canvas, negligible cost, real readability
  win on an otherwise flat-shaded render.
- Legend drawn with Pillow ``ImageDraw`` directly onto the final image
  (translucent dark panel, capped at ``TOPE_LEYENDA`` names + "+K mas") —
  matplotlib is no longer used anywhere in this module.

``renderizar_png`` itself only ever touches already-tessellated
``trimesh.Trimesh`` objects (one per named group) — never build123d/OCP
directly (ADR-0001's kernel boundary lives in ``grupos_desde_shape`` below,
which is the only function here that calls into ``kernel.mesh``/``solids``).
"""
from __future__ import annotations

import colorsys
import io
from typing import Any

import numpy as np
import trimesh

from kernel import mesh as kernel_mesh

# Coarser than `kernel.mesh`'s 0.1 mm default tessellation tolerance: a
# render only needs to look right at ~800x800 px, not survive a print-bed
# check, and casa_v2 (21 solids, ~225 primitives) must render well under
# the Phase 5B time budget (~3 s).
TOLERANCIA_RENDER_MM = 0.6

LIMITE_PNG_BYTES = 150_000
TOPE_LEYENDA = 20

# Triangle ceiling (Phase 5E, folded in from the 5B review): the rasterizer
# below is plain numpy over each triangle's own screen-space bounding box --
# correct and fast for casa_v2-scale scenes (~225 primitives at the default
# tolerance), but a document with hundreds of thousands of triangles (a very
# fine tolerance, or a script with unusually complex geometry) would make a
# single `captura` call slow enough to matter. One retry at a 3x coarser
# tessellation tolerance is attempted first (a render only needs to look
# right on screen, not survive a print-bed check); if that STILL exceeds the
# ceiling, `grupos_desde_shape` refuses with a clear message rather than
# silently taking tens of seconds -- `solidos=[...]` (render a subset) is
# the documented way around it.
LIMITE_TRIANGULOS = 150_000
_FACTOR_TESELACION_GRUESA = 3.0

# 2x2 supersampling, box-downscaled at the end -- the anti-aliasing strategy
# for the z-buffer rasterizer (there is no per-polygon edge antialiasing to
# reason about anymore, unlike the old PolyCollection renderer).
_SS = 2

_LUZ = np.array([0.5, -0.4, 0.9])
_LUZ = _LUZ / np.linalg.norm(_LUZ)
_AMBIENTE = 0.35
_DIFUSO = 0.65

_FONDO = (0x10 / 255.0, 0x10 / 255.0, 0x14 / 255.0)  # dark background, #101014

# z-buffer "nothing rasterized here yet" sentinel -- comfortably below any
# real projected depth (world units are millimetres; scenes here are never
# anywhere near this large), and finite so plain float comparisons/arithmetic
# never risk inf-inf/inf*0 NaNs.
_SENTINEL_Z = -1.0e18

# Depth-discontinuity outline (silhouettes + creases): a jump in the
# z-buffer bigger than this fraction of the scene's own bounding-box
# diagonal gets a darkened outline pixel either side of it.
_UMBRAL_BORDE_FRACCION = 0.006
_OSCURECER_BORDE = 0.35

_ANGULO_DORADO = 0.6180339887498949  # golden ratio conjugate


def _color_por_indice(indice: int) -> tuple[float, float, float]:
    """Deterministic RGB in [0, 1] for the ``indice``-th solid in a
    document's own natural order (Phase 5B fix-review — replaces the
    previous name-hash palette, which could and did collide: two real
    names hashed to the exact same hue in manual testing).

    Golden-ratio hue spacing (the classic "golden angle" sequential-colour
    trick: ``hue_n = (n * phi_conjugate) mod 1``) guarantees well-separated
    hues for consecutive indices regardless of how many solids there are —
    unlike a hash, which only spreads hues well in the *statistical
    average*, not for any specific small N. Saturation/value additionally
    alternate on a period-4 pattern tied to the index, so that even in the
    rare case two NON-adjacent indices land on similar-looking hues after
    wraparound, immediate NEIGHBOURS (index i vs i+1 — typically
    spatially/thematically related pieces in a script's dict order, e.g. a
    bed next to its bedside table, or four chairs in a row) always differ
    in saturation at least, since that alone flips every single step."""
    matiz = (indice * _ANGULO_DORADO) % 1.0
    saturacion = 0.85 if indice % 2 == 0 else 0.55
    valor = 0.95 if (indice % 4) < 2 else 0.75
    return colorsys.hsv_to_rgb(matiz, saturacion, valor)


def _base_camara(azimut: float, elevacion: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Orthographic camera basis ``(adelante, derecha, arriba)`` from
    azimuth/elevation in degrees (same convention Matplotlib's own
    ``Axes3D.view_init`` uses) — ``adelante`` points from the scene toward
    the camera. Falls back to a world-Y "up" reference when looking almost
    straight down/up (``adelante`` nearly parallel to world Z) to avoid a
    degenerate cross product."""
    az = np.radians(azimut)
    el = np.radians(elevacion)
    adelante = np.array([np.cos(el) * np.cos(az), np.cos(el) * np.sin(az), np.sin(el)])
    arriba_mundo = np.array([0.0, 0.0, 1.0])
    if abs(np.dot(adelante, arriba_mundo)) > 0.999:
        arriba_mundo = np.array([0.0, 1.0, 0.0])
    derecha = np.cross(adelante, arriba_mundo)
    derecha = derecha / np.linalg.norm(derecha)
    arriba = np.cross(derecha, adelante)
    return adelante, derecha, arriba


def _comprimir_si_excede(datos: bytes, limite: int = LIMITE_PNG_BYTES) -> bytes:
    """Re-encode ``datos`` (a PNG straight out of Pillow) smaller if it
    exceeds ``limite`` bytes: palette-quantize first, downscale as a last
    resort. Never raises — a render that can't be shrunk below the cap is
    still returned as-is rather than failing the whole tool call."""
    if len(datos) <= limite:
        return datos
    from PIL import Image

    imagen = Image.open(io.BytesIO(datos)).convert("RGB")
    paletizada = imagen.quantize(colors=128, method=Image.MEDIANCUT)
    buffer = io.BytesIO()
    paletizada.save(buffer, format="PNG", optimize=True)
    resultado = buffer.getvalue()
    if len(resultado) <= limite:
        return resultado

    escala = max(0.3, (limite / max(len(resultado), 1)) ** 0.5)
    nuevo_tamano = (max(1, int(imagen.width * escala)), max(1, int(imagen.height * escala)))
    chica = imagen.resize(nuevo_tamano, Image.LANCZOS).quantize(colors=64)
    buffer2 = io.BytesIO()
    chica.save(buffer2, format="PNG", optimize=True)
    return buffer2.getvalue()


def grupos_desde_shape(
    shape: Any,
    entradas: list[dict[str, Any]],
    solidos_filtro: list[str] | None = None,
    tolerancia: float = TOLERANCIA_RENDER_MM,
) -> tuple[dict[str, trimesh.Trimesh], dict[str, tuple[float, float, float]], bool]:
    """Tessellate a STEP document's named solids into ``{nombre: Trimesh}``,
    one merged mesh per name (a piece split into several disconnected
    solids gets all its fragments concatenated into one mesh — no boolean
    union needed just to render triangles). Returns
    ``(grupos, colores, nombres_inciertos)``: ``colores`` maps EVERY solid
    name in the document's own natural order to its stable colour
    (`_color_por_indice`) — computed over the FULL, unfiltered order so a
    ``solidos_filtro`` subset render keeps the same colours the full render
    would have used, not a re-indexed palette of just the kept names.
    ``nombres_inciertos`` (Phase 5B fix-review) is true when
    `solids.solidos_por_indice` couldn't confidently match the stored names
    to this reimport's geometry.

    ``solidos_filtro``, when given, keeps only those names (the cheap way to
    "see the interior": omit the house shell's name to look inside it) —
    raises ``ValueError`` if any given name doesn't exist in this document.
    """
    import solids as solids_mod  # local import: keep ADR-0001's boundary explicit

    grupos_indexados, nombres_inciertos = solids_mod.solidos_por_indice(shape, entradas)
    piezas_por_nombre: dict[str, list[Any]] = {}
    orden_completo: list[str] = []
    for nombre, _indice, solido, _bbox, _volumen in grupos_indexados:
        if nombre not in piezas_por_nombre:
            piezas_por_nombre[nombre] = []
            orden_completo.append(nombre)
        piezas_por_nombre[nombre].append(solido)

    colores = {nombre: _color_por_indice(i) for i, nombre in enumerate(orden_completo)}

    orden = orden_completo
    if solidos_filtro is not None:
        faltantes = [n for n in solidos_filtro if n not in piezas_por_nombre]
        if faltantes:
            raise ValueError(f"nombres de solido inexistentes en este documento: {faltantes}")
        orden = [n for n in orden_completo if n in solidos_filtro]

    resultado, total_triangulos = _tesela_grupos(piezas_por_nombre, orden, tolerancia)
    if total_triangulos > LIMITE_TRIANGULOS:
        resultado, total_triangulos = _tesela_grupos(
            piezas_por_nombre, orden, tolerancia * _FACTOR_TESELACION_GRUESA
        )
        if total_triangulos > LIMITE_TRIANGULOS:
            raise ValueError(
                f"el documento tiene demasiados triangulos para renderizar "
                f"({total_triangulos} > {LIMITE_TRIANGULOS}, incluso con una tesalacion "
                "3 veces mas gruesa); probar con 'solidos=[...]' para renderizar solo un subconjunto"
            )
    return resultado, colores, nombres_inciertos


def _tesela_grupos(
    piezas_por_nombre: dict[str, list[Any]], orden: list[str], tolerancia: float
) -> tuple[dict[str, trimesh.Trimesh], int]:
    """Tessellate every named group at ``tolerancia``, merging a "partida"
    piece's disconnected fragments into one mesh. Returns ``(grupos,
    total_triangulos)`` -- the total is what `grupos_desde_shape` compares
    against `LIMITE_TRIANGULOS` to decide whether a coarser retry (or an
    outright refusal) is needed."""
    resultado: dict[str, trimesh.Trimesh] = {}
    total_triangulos = 0
    for nombre in orden:
        vertices_totales: list[np.ndarray] = []
        faces_totales: list[np.ndarray] = []
        offset = 0
        for pieza in piezas_por_nombre[nombre]:
            tri_mesh = kernel_mesh.tessellate_to_trimesh(pieza, tolerancia)
            if len(tri_mesh.faces) == 0:
                continue
            vertices_totales.append(tri_mesh.vertices)
            faces_totales.append(tri_mesh.faces + offset)
            offset += len(tri_mesh.vertices)
        if not faces_totales:
            continue
        resultado[nombre] = trimesh.Trimesh(
            vertices=np.concatenate(vertices_totales),
            faces=np.concatenate(faces_totales),
            process=False,
        )
        total_triangulos += len(resultado[nombre].faces)
    return resultado, total_triangulos


def grupo_desde_malla(
    malla: trimesh.Trimesh, solidos_filtro: list[str] | None = None
) -> tuple[dict[str, trimesh.Trimesh], dict[str, tuple[float, float, float]], bool]:
    """A mesh-only (STL) document has no per-solid names — one unnamed
    group covering the whole mesh, never uncertain naming (there was never
    a name to be uncertain about). Raises ``ValueError`` if a
    ``solidos_filtro`` was given anyway (nothing to filter by)."""
    if solidos_filtro:
        raise ValueError(
            "este documento no tiene solidos con nombre (es una malla STL); "
            "no se puede filtrar por 'solidos'"
        )
    if len(malla.faces) > LIMITE_TRIANGULOS:
        # No tessellation tolerance to coarsen here (it is already a fixed
        # STL mesh, not a build123d solid) -- refuse outright rather than a
        # slow render, same ceiling as the STEP path above.
        raise ValueError(
            f"la malla STL tiene demasiados triangulos para renderizar "
            f"({len(malla.faces)} > {LIMITE_TRIANGULOS})"
        )
    return {"documento": malla}, {"documento": _color_por_indice(0)}, False


def _rasterizar(
    triangulos_px: np.ndarray,
    profundidades: np.ndarray,
    colores: np.ndarray,
    ancho_px: int,
    alto_px: int,
    fondo: tuple[float, float, float] = _FONDO,
) -> tuple[np.ndarray, np.ndarray]:
    """Software z-buffer rasterizer. ``triangulos_px`` is ``(N, 3, 2)``
    ``(columna, fila)`` pixel coordinates (float, not yet rounded),
    ``profundidades`` is ``(N, 3)`` (larger = nearer the camera, matching
    ``adelante``'s convention), ``colores`` is ``(N, 3)`` one flat RGB per
    triangle. Returns ``(color_buffer, zbuffer)``, both ``(alto_px,
    ancho_px, ...)``.

    Visibility is resolved independently per pixel (compare against
    whatever is already in ``zbuffer`` there) — triangle processing order
    never affects the result, unlike a painter's-algorithm sort. Each
    triangle only ever touches its own screen-space bounding box, so cost
    scales with total triangle screen coverage, not canvas size times
    triangle count.
    """
    zbuffer = np.full((alto_px, ancho_px), _SENTINEL_Z, dtype=np.float64)
    color_buffer = np.empty((alto_px, ancho_px, 3), dtype=np.float64)
    color_buffer[:, :] = fondo

    # Pull per-triangle scalars into plain Python lists up front: repeated
    # scalar access into a numpy array (`triangulos_px[i, 0, 0]` etc, tens
    # of thousands of times) is markedly slower than plain Python float
    # arithmetic -- the inner loop below only ever uses numpy for the
    # actual per-pixel grid math, which is where vectorization pays off.
    coords = triangulos_px.tolist()
    profs = profundidades.tolist()
    cols = colores.tolist()

    for (v0, v1, v2), (z0, z1, z2), color in zip(coords, profs, cols):
        x0, y0 = v0
        x1, y1 = v1
        x2, y2 = v2

        denom = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
        if -1e-9 < denom < 1e-9:
            continue  # degenerate (zero-area, or edge-on to the camera) triangle

        min_x = max(int(min(x0, x1, x2)), 0)
        max_x = min(int(max(x0, x1, x2)) + 1, ancho_px - 1)
        min_y = max(int(min(y0, y1, y2)), 0)
        max_y = min(int(max(y0, y1, y2)) + 1, alto_px - 1)
        if min_x > max_x or min_y > max_y:
            continue  # fully off-canvas

        xs = np.arange(min_x, max_x + 1, dtype=np.float64) + 0.5
        ys = np.arange(min_y, max_y + 1, dtype=np.float64) + 0.5
        px = xs[None, :]
        py = ys[:, None]

        w0 = ((y1 - y2) * (px - x2) + (x2 - x1) * (py - y2)) / denom
        w1 = ((y2 - y0) * (px - x2) + (x0 - x2) * (py - y2)) / denom
        w2 = 1.0 - w0 - w1

        dentro = (w0 >= 0.0) & (w1 >= 0.0) & (w2 >= 0.0)
        if not dentro.any():
            continue

        profundidad_px = w0 * z0 + w1 * z1 + w2 * z2

        ventana_z = zbuffer[min_y : max_y + 1, min_x : max_x + 1]
        mas_cerca = dentro & (profundidad_px > ventana_z)
        if not mas_cerca.any():
            continue

        ventana_z[mas_cerca] = profundidad_px[mas_cerca]
        color_buffer[min_y : max_y + 1, min_x : max_x + 1][mas_cerca] = color

    return color_buffer, zbuffer


def _dibujar_bordes(color_buffer: np.ndarray, zbuffer: np.ndarray, diagonal_escena: float) -> None:
    """Cheap depth-discontinuity outline, in-place on ``color_buffer``:
    darkens pixels next to a silhouette (rasterized vs. background) or a
    sharp crease (both pixels rasterized, but their z-buffer depths differ
    by more than ``_UMBRAL_BORDE_FRACCION`` of the scene's own bounding-box
    diagonal). A handful of numpy diffs over the whole canvas — negligible
    cost, a real readability win on an otherwise flat-shaded render."""
    valido = zbuffer > (_SENTINEL_Z / 2.0)
    if not valido.any():
        return
    umbral = max(diagonal_escena * _UMBRAL_BORDE_FRACCION, 1e-9)

    borde = np.zeros(zbuffer.shape, dtype=bool)

    salto_h = valido[:, :-1] != valido[:, 1:]
    cresta_h = valido[:, :-1] & valido[:, 1:] & (np.abs(zbuffer[:, :-1] - zbuffer[:, 1:]) > umbral)
    marca_h = salto_h | cresta_h
    borde[:, :-1] |= marca_h
    borde[:, 1:] |= marca_h

    salto_v = valido[:-1, :] != valido[1:, :]
    cresta_v = valido[:-1, :] & valido[1:, :] & (np.abs(zbuffer[:-1, :] - zbuffer[1:, :]) > umbral)
    marca_v = salto_v | cresta_v
    borde[:-1, :] |= marca_v
    borde[1:, :] |= marca_v

    aplicar = borde & valido
    color_buffer[aplicar] *= _OSCURECER_BORDE


def _dibujar_leyenda(imagen: Any, leyenda: dict[str, tuple[float, float, float]]) -> None:
    """Draw a capped, translucent name legend directly onto the final
    Pillow ``imagen`` (top-left corner, dark panel) — the matplotlib
    ``Axes.legend()`` this replaces is gone along with the rest of
    matplotlib in this module."""
    if not leyenda:
        return
    from PIL import Image, ImageDraw, ImageFont

    nombres = list(leyenda.keys())
    mostrados = nombres[:TOPE_LEYENDA]
    filas: list[tuple[str, tuple[float, float, float]]] = [(n, leyenda[n]) for n in mostrados]
    if len(nombres) > TOPE_LEYENDA:
        filas.append((f"+{len(nombres) - TOPE_LEYENDA} mas", (0.4, 0.4, 0.4)))

    escala = max(imagen.width, imagen.height) / 800.0
    alto_fila = max(11, round(15 * escala))
    swatch = max(7, round(10 * escala))
    margen = max(6, round(8 * escala))

    overlay = Image.new("RGBA", imagen.size, (0, 0, 0, 0))
    dibujo = ImageDraw.Draw(overlay)
    fuente = ImageFont.load_default()

    ancho_texto = max(dibujo.textlength(nombre, font=fuente) for nombre, _c in filas)
    ancho_caja = int(ancho_texto + swatch + 3 * margen)
    alto_caja = int(alto_fila * len(filas) + margen)

    dibujo.rectangle(
        [margen // 2, margen // 2, margen // 2 + ancho_caja, margen // 2 + alto_caja],
        fill=(0x10, 0x10, 0x14, 200),
    )
    for i, (nombre, color) in enumerate(filas):
        y0 = margen + i * alto_fila
        x0 = margen
        rgb = tuple(max(0, min(255, int(round(c * 255)))) for c in color)
        dibujo.rectangle([x0, y0, x0 + swatch, y0 + swatch], fill=(*rgb, 255))
        dibujo.text((x0 + swatch + margen // 2, y0 - 1), nombre, font=fuente, fill=(255, 255, 255, 255))

    compuesta = Image.alpha_composite(imagen.convert("RGBA"), overlay).convert("RGB")
    imagen.paste(compuesta)


def renderizar_png(
    grupos: dict[str, trimesh.Trimesh],
    colores: dict[str, tuple[float, float, float]],
    *,
    azimut: float = 45.0,
    elevacion: float = 25.0,
    ancho: int = 800,
    alto: int = 800,
    colores_por_solido: bool = True,
    leyenda_visible: bool = True,
    fondo: tuple[float, float, float] | None = None,
) -> tuple[bytes, str]:
    """Render ``grupos`` (``{nombre: Trimesh}``, already tessellated — see
    `grupos_desde_shape`/`grupo_desde_malla`) to a small PNG via a software
    z-buffer rasterizer (see the module docstring). Returns ``(png_bytes,
    texto_caption)``. Raises ``ValueError`` if every group is empty (no
    faces at all)."""
    if not grupos:
        raise ValueError("no hay solidos para renderizar")

    adelante, derecha, arriba = _base_camara(azimut, elevacion)

    listas_pts: list[np.ndarray] = []
    listas_normales: list[np.ndarray] = []
    listas_colores: list[np.ndarray] = []
    leyenda: dict[str, tuple[float, float, float]] = {}
    total_triangulos = 0

    for nombre, tri_mesh in grupos.items():
        if len(tri_mesh.faces) == 0:
            continue
        color_base = colores.get(nombre, (0.8, 0.8, 0.8)) if colores_por_solido else (0.75, 0.75, 0.8)
        leyenda[nombre] = color_base
        pts = tri_mesh.vertices[tri_mesh.faces]  # (M, 3, 3)
        total_triangulos += pts.shape[0]
        listas_pts.append(pts)
        listas_normales.append(tri_mesh.face_normals)
        listas_colores.append(np.tile(np.asarray(color_base, dtype=np.float64), (pts.shape[0], 1)))

    if not listas_pts:
        raise ValueError("los solidos seleccionados no produjeron ninguna cara")

    pts = np.concatenate(listas_pts, axis=0)
    normales = np.concatenate(listas_normales, axis=0)
    color_base_tri = np.concatenate(listas_colores, axis=0)

    # Two-sided shading, no back-face culling: a z-buffer resolves
    # visibility correctly per pixel regardless of triangle winding, and a
    # thin single-walled shell (casa_v2's exterior walls, cut open by
    # door/window boolean subtractions) needs its INSIDE face drawable too
    # -- exactly what's visible looking into the house through an opening.
    # `abs(normal . luz)` keeps a mis-oriented/backwards face from
    # tessellation from ever rendering pitch black.
    intensidad = _AMBIENTE + _DIFUSO * np.abs(normales @ _LUZ)
    intensidad = np.clip(intensidad, 0.0, 1.0)
    color_sombreado = np.clip(color_base_tri * intensidad[:, None], 0.0, 1.0)

    proyeccion_x = pts @ derecha
    proyeccion_y = pts @ arriba
    profundidad = pts @ adelante

    vertices_planos = pts.reshape(-1, 3)
    diagonal_escena = float(np.linalg.norm(vertices_planos.max(axis=0) - vertices_planos.min(axis=0)))

    min_x, max_x = float(proyeccion_x.min()), float(proyeccion_x.max())
    min_y, max_y = float(proyeccion_y.min()), float(proyeccion_y.max())
    margen_x = 0.03 * max(max_x - min_x, 1e-6)
    margen_y = 0.03 * max(max_y - min_y, 1e-6)
    min_x -= margen_x
    max_x += margen_x
    min_y -= margen_y
    max_y += margen_y
    extent_x = max(max_x - min_x, 1e-6)
    extent_y = max(max_y - min_y, 1e-6)

    ancho_ss, alto_ss = ancho * _SS, alto * _SS
    # Equal-aspect fit (both axes are the same orthographic projection, so
    # a single shared scale is exact, not approximate) -- letterboxed and
    # centred in the canvas, matching the old `ax.set_aspect("equal")`
    # behaviour rather than stretching either axis independently.
    escala = min(ancho_ss / extent_x, alto_ss / extent_y)
    despl_x = (ancho_ss - extent_x * escala) / 2.0
    despl_y = (alto_ss - extent_y * escala) / 2.0

    pixel_x = despl_x + (proyeccion_x - min_x) * escala
    # Row 0 is the TOP of the image; world "arriba" increases upward, so
    # the largest projected y must map to the smallest row.
    pixel_y = despl_y + (max_y - proyeccion_y) * escala
    triangulos_px = np.stack([pixel_x, pixel_y], axis=-1)  # (N, 3, 2)

    color_buffer, zbuffer = _rasterizar(triangulos_px, profundidad, color_sombreado, ancho_ss, alto_ss,
                                        fondo if fondo is not None else _FONDO)
    _dibujar_bordes(color_buffer, zbuffer, diagonal_escena)

    imagen_ss = np.clip(color_buffer, 0.0, 1.0).reshape(alto, _SS, ancho, _SS, 3).mean(axis=(1, 3))
    imagen_u8 = np.round(imagen_ss * 255.0).astype(np.uint8)

    from PIL import Image

    imagen = Image.fromarray(imagen_u8, mode="RGB")
    if leyenda_visible:
        _dibujar_leyenda(imagen, leyenda)

    buffer = io.BytesIO()
    imagen.save(buffer, format="png")
    png_bytes = _comprimir_si_excede(buffer.getvalue())

    caption = (
        f"{len(grupos)} solido(s), {total_triangulos} triangulos, "
        f"vista azimut={azimut:g} elevacion={elevacion:g}"
    )
    return png_bytes, caption
