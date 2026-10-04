"""Phase 5B fix-review tests: z-buffer occlusion correctness (real pixel
sampling, not just triangle counts), the index-order golden-ratio palette,
and the `solidos_por_indice` reimport-mismatch safeguard.

Fix-review round 2 replaced the sorted-`PolyCollection` painter's-algorithm
renderer with a per-pixel software z-buffer (see `app/render.py`'s module
docstring) — section 1 below now tests real occlusion (an object hidden
behind an opaque solid must never show through, in EITHER direction) plus
the two harder cases a naive sort still gets wrong: two long triangles
whose depth ranges overlap/cross, and a thin wall hiding something directly
behind it.

Same in-process `TestClient` + isolated `FORJA_DATA_DIR` pattern as
`tests/test_captura_colisiones_exportar.py`.
"""
from __future__ import annotations

import io

import numpy as np
from build123d import Box, Compound, Pos
from fastapi.testclient import TestClient
from PIL import Image

import auth
import render
import solids as solids_mod
from kernel.b123d_kernel import combinar_nombrados
from main import app

client = TestClient(app)


def _headers() -> dict[str, str]:
    return {"X-Forja-Token": auth.obtener_token()}


def _crear(script: str) -> dict:
    resp = client.post("/documentos/script", json={"codigo": script}, headers=_headers())
    assert resp.status_code == 200, resp.text
    return resp.json()


def _color_255(color_01: tuple[float, float, float], intensidad: float) -> np.ndarray:
    return np.array([round(c * intensidad * 255) for c in color_01])


# --------------------------------------------------------- 1. z-buffer occlusion


def _aparece(recorte: np.ndarray, color: np.ndarray, tolerancia: int = 25) -> bool:
    return bool(np.any(np.all(np.abs(recorte - color) <= tolerancia, axis=-1)))


def _aparece_en_algun_sombreado(recorte: np.ndarray, color_01: tuple[float, float, float], tolerancia: int = 20) -> bool:
    """A tilted/rotated solid shows more than one face, each at its own
    shading intensity -- true if ``color_01`` shows up at ANY plausible
    intensity along [0.25, 1.0] (the achievable range of
    `_AMBIENTE + _DIFUSO * |n . luz|`)."""
    return any(
        _aparece(recorte, _color_255(color_01, intensidad))
        for intensidad in np.linspace(0.25, 1.0, 20)
    )


def test_captura_objeto_cercano_no_queda_tapado_por_plano_grande_lejano():
    """(b) A small object correctly IN FRONT of a big flat panel must stay
    visible -- the z-buffer must not let the panel's large triangles paint
    over it, regardless of triangle size."""
    script = (
        "from build123d import Box, Pos\n"
        "resultado = {\n"
        "    'piso': Pos(0, 0, -60) * Box(300, 300, 4),\n"
        "    'caja': Pos(0, 0, 20) * Box(20, 20, 20),\n"
        "}\n"
    )
    creado = _crear(script)
    resp = client.get(f"/documentos/{creado['id']}/captura?azimut=45&elevacion=30&ancho=400&alto=400")
    assert resp.status_code == 200, resp.text

    imagen = Image.open(io.BytesIO(resp.content)).convert("RGB")
    arr = np.array(imagen).astype(int)
    ancho, alto = imagen.width, imagen.height
    # Legend sits in the top-left corner -- exclude it.
    recorte = arr[alto // 4 :, ancho // 4 :]

    # Both 'piso' and 'caja' present the SAME (0, 0, 1) top-face normal to
    # the same fixed light, so they render at the identical shading
    # intensity -- only their index-based base hue differs.
    intensidad = render._AMBIENTE + render._DIFUSO * abs(np.dot([0.0, 0.0, 1.0], render._LUZ))
    color_piso = _color_255(render._color_por_indice(0), intensidad)
    color_caja = _color_255(render._color_por_indice(1), intensidad)

    assert _aparece(recorte, color_piso), "no se ve el color del piso (deberia llenar casi todo el cuadro)"
    assert _aparece(recorte, color_caja), "no se ve el color de 'caja', estando en frente del piso"


def test_captura_objeto_lejano_queda_tapado_por_plano_grande_cercano():
    """(a) The mirror case: the same small object now BEHIND the big flat
    panel (panel nearer the camera) must be fully occluded -- its colour
    must never appear anywhere in the render."""
    script = (
        "from build123d import Box, Pos\n"
        "resultado = {\n"
        "    'piso': Pos(0, 0, 60) * Box(300, 300, 4),\n"
        "    'caja': Pos(0, 0, -20) * Box(20, 20, 20),\n"
        "}\n"
    )
    creado = _crear(script)
    resp = client.get(f"/documentos/{creado['id']}/captura?azimut=45&elevacion=30&ancho=400&alto=400")
    assert resp.status_code == 200, resp.text

    imagen = Image.open(io.BytesIO(resp.content)).convert("RGB")
    arr = np.array(imagen).astype(int)
    ancho, alto = imagen.width, imagen.height
    # Legend sits in the top-left corner and always lists 'caja' by name
    # (occlusion doesn't remove a group from the legend) -- exclude it, the
    # RENDERED geometry is what must never show 'caja's colour.
    recorte = arr[alto // 4 :, ancho // 4 :]

    assert not _aparece_en_algun_sombreado(recorte, render._color_por_indice(1)), (
        "el color de 'caja' aparece en la imagen: quedo visible pese a estar detras del piso"
    )


def test_captura_casa_v2_sintetica_sin_franjas_de_color_incorrectas():
    """A smaller stand-in for the real casa_v2.py bug report (streak through
    a slanted roof, sliver across the floor): a big TILTED panel (so its
    triangle spans a wide depth range) must not incorrectly occlude a small
    object sitting in the middle of that depth range."""
    script = (
        "from build123d import Box, Pos, Rotation\n"
        "resultado = {\n"
        "    'techo': Pos(0, 0, 0) * Rotation(35, 0, 0) * Box(200, 150, 3),\n"
        "    'objeto': Pos(0, 0, 40) * Box(15, 15, 15),\n"
        "}\n"
    )
    creado = _crear(script)
    resp = client.get(f"/documentos/{creado['id']}/captura?azimut=30&elevacion=20&ancho=400&alto=400")
    assert resp.status_code == 200, resp.text

    imagen = Image.open(io.BytesIO(resp.content)).convert("RGB")
    arr = np.array(imagen).astype(int)
    ancho, alto = imagen.width, imagen.height
    recorte = arr[alto // 4 :, ancho // 4 :]

    assert _aparece_en_algun_sombreado(recorte, render._color_por_indice(1)), (
        "el objeto no aparece visible: posible franja incorrecta del panel grande"
    )


def test_captura_franjas_interpenetradas_en_cruz_se_ocluyen_por_lado():
    """(c) Two long slabs sharing the same centre, tilted by +25/-25 degrees
    around the vertical (Z) axis, cross each other's depth range like an
    "X": with the camera looking along world +Y (azimut=90, elevacion=0,
    so depth = world y, screen-x = world x), rotating 'a' by +25 degrees
    means its local +X end swings toward +Y (nearer camera) while its local
    -X end swings toward -Y (farther) -- so 'a' is the NEARER slab on the
    right half of the screen (positive world x) and 'b' (the mirrored -25
    degree tilt) is the nearer slab on the left half. Any single
    per-triangle depth key (centroid, average, nearest-vertex -- the three
    painter's-algorithm heuristics tried across Phase 5B) can only place a
    WHOLE long triangle on one side of a sort; a z-buffer must resolve the
    correct winner independently on each side of the crossing line."""
    script = (
        "from build123d import Box, Pos, Rotation\n"
        "resultado = {\n"
        "    'a': Pos(0, 0, 0) * Rotation(0, 0, 25) * Box(100, 6, 12),\n"
        "    'b': Pos(0, 0, 0) * Rotation(0, 0, -25) * Box(100, 6, 12),\n"
        "}\n"
    )
    creado = _crear(script)
    resp = client.get(f"/documentos/{creado['id']}/captura?azimut=90&elevacion=0&ancho=400&alto=400")
    assert resp.status_code == 200, resp.text

    imagen = Image.open(io.BytesIO(resp.content)).convert("RGB")
    arr = np.array(imagen).astype(int)
    ancho, alto = imagen.width, imagen.height
    mitad_alto = alto // 2

    columna_izquierda = arr[mitad_alto - 20 : mitad_alto + 20, ancho // 4 : ancho // 4 + 10]
    columna_derecha = arr[mitad_alto - 20 : mitad_alto + 20, 3 * ancho // 4 - 10 : 3 * ancho // 4]

    color_a = render._color_por_indice(0)
    color_b = render._color_por_indice(1)

    assert _aparece_en_algun_sombreado(columna_izquierda, color_b), (
        "en el lado izquierdo deberia ganar 'b' (mas cerca de la camara ahi)"
    )
    assert _aparece_en_algun_sombreado(columna_derecha, color_a), (
        "en el lado derecho deberia ganar 'a' (mas cerca de la camara ahi)"
    )


def test_captura_mueble_detras_de_pared_delgada_nunca_se_asoma():
    """(d) A synthetic house-like scene: a thin wall directly in front of a
    small piece of "furniture" fully within the wall's screen footprint --
    the furniture must never show through, exactly the reported sawtooth
    artifact (small furniture-coloured triangles poking through a wall)."""
    script = (
        "from build123d import Box, Pos\n"
        "resultado = {\n"
        "    'pared': Pos(0, 0, 0) * Box(80, 3, 60),\n"
        "    'mueble': Pos(0, -15, 0) * Box(20, 15, 25),\n"
        "}\n"
    )
    creado = _crear(script)
    resp = client.get(f"/documentos/{creado['id']}/captura?azimut=90&elevacion=0&ancho=400&alto=400")
    assert resp.status_code == 200, resp.text

    imagen = Image.open(io.BytesIO(resp.content)).convert("RGB")
    arr = np.array(imagen).astype(int)
    ancho, alto = imagen.width, imagen.height
    # Same legend caveat as the occlusion test above -- exclude it.
    recorte = arr[alto // 4 :, ancho // 4 :]

    assert not _aparece_en_algun_sombreado(recorte, render._color_por_indice(1)), (
        "el color de 'mueble' se asoma a traves de 'pared'"
    )


# --------------------------------------------------------------- 3. palette


def test_color_por_indice_es_deterministico():
    assert render._color_por_indice(3) == render._color_por_indice(3)
    assert render._color_por_indice(0) != render._color_por_indice(1)


def test_color_por_indice_alterna_saturacion_entre_vecinos():
    import colorsys

    for i in range(12):
        _h_i, s_i, _v_i = colorsys.rgb_to_hsv(*render._color_por_indice(i))
        _h_j, s_j, _v_j = colorsys.rgb_to_hsv(*render._color_por_indice(i + 1))
        assert abs(s_i - s_j) > 0.1, f"indices {i} y {i + 1} no alternan saturacion"


def test_color_depende_del_orden_del_documento_no_del_nombre():
    """Phase 5B fix-review, task 3: the palette is index-order based, not a
    name hash -- the SAME name gets a DIFFERENT colour if it sits at a
    different position in a different document's own order."""
    figura_ab = combinar_nombrados({"alfa": Box(5, 5, 5), "beta": Box(6, 6, 6)})
    entradas_ab = solids_mod.construir_entradas(figura_ab, ["alfa", "beta"])
    _grupos_ab, colores_ab, inciertos_ab = render.grupos_desde_shape(figura_ab, entradas_ab)

    figura_ba = combinar_nombrados({"beta": Box(6, 6, 6), "alfa": Box(5, 5, 5)})
    entradas_ba = solids_mod.construir_entradas(figura_ba, ["beta", "alfa"])
    _grupos_ba, colores_ba, inciertos_ba = render.grupos_desde_shape(figura_ba, entradas_ba)

    assert inciertos_ab is False
    assert inciertos_ba is False
    assert colores_ab["alfa"] == render._color_por_indice(0)
    assert colores_ab["beta"] == render._color_por_indice(1)
    assert colores_ba["beta"] == render._color_por_indice(0)
    assert colores_ba["alfa"] == render._color_por_indice(1)
    assert colores_ab["alfa"] != colores_ba["alfa"], "el mismo nombre debe cambiar de color si cambia su orden"


# --------------------------------------------- 4. solidos_por_indice safeguard


def test_solidos_por_indice_recupera_orden_permutado_sin_ambiguedad():
    pequena = Box(4, 4, 4)  # volume 64
    grande = Pos(50, 0, 0) * Box(10, 10, 10)  # volume 1000, far away
    figura = Compound(children=[pequena, grande])

    entradas_correctas = solids_mod.construir_entradas(figura)  # flat solido_1/solido_2, in shape.solids() order
    # Deliberately swap the two entries' claimed identity/indice so the
    # POSITIONAL mapping (entry->shape.solids()[indice]) is wrong.
    entradas_permutadas = [
        {**entradas_correctas[1], "indice": 0, "nombre": "grande"},
        {**entradas_correctas[0], "indice": 1, "nombre": "pequena"},
    ]

    resultado, nombres_inciertos = solids_mod.solidos_por_indice(figura, entradas_permutadas)
    assert nombres_inciertos is False, "debia recuperar el orden correcto, no rendirse"
    por_nombre = {nombre: solido for nombre, _idx, solido, _bbox, _vol in resultado}
    assert abs(por_nombre["grande"].volume - 1000.0) < 1.0
    assert abs(por_nombre["pequena"].volume - 64.0) < 1.0


def test_reasignar_por_similitud_rechaza_asignacion_ambigua():
    """Two geometrically IDENTICAL solids: any assignment between them is
    equally valid, so `_reasignar_por_similitud` must refuse to guess
    (return ``None``) rather than silently pick one — this is the private
    helper `solidos_por_indice` calls when the fast path disagrees with the
    reimported geometry."""
    a = Box(10, 10, 10)
    b = Box(10, 10, 10)
    entradas = [
        {"nombre": "x", "indice": 0, "bbox": [-5.0, 5.0, -5.0, 5.0, -5.0, 5.0], "volumen": 1000.0},
        {"nombre": "y", "indice": 1, "bbox": [-5.0, 5.0, -5.0, 5.0, -5.0, 5.0], "volumen": 1000.0},
    ]
    assert solids_mod._reasignar_por_similitud(entradas, [a, b]) is None


def test_solidos_por_indice_cae_a_solido_n_si_no_hay_match_confiable():
    """Entries whose stored volume/bbox match NEITHER real solid within
    tolerance: the safeguard must fall back to flat solido_N naming with
    `nombres_inciertos=True` rather than force a wrong assignment."""
    a = Box(10, 10, 10)
    b = Pos(50, 0, 0) * Box(20, 20, 20)
    figura = Compound(children=[a, b])
    entradas_invalidas = [
        {"nombre": "x", "indice": 0, "bbox": [0.0, 1.0, 0.0, 1.0, 0.0, 1.0], "volumen": 1.0},
        {"nombre": "y", "indice": 1, "bbox": [0.0, 1.0, 0.0, 1.0, 0.0, 1.0], "volumen": 1.0},
    ]
    resultado, nombres_inciertos = solids_mod.solidos_por_indice(figura, entradas_invalidas)
    assert nombres_inciertos is True
    assert {nombre for nombre, *_ in resultado} == {"solido_1", "solido_2"}


def test_solidos_por_indice_desacuerdo_en_cantidad_sigue_lanzando_error():
    """A genuinely different solid COUNT is a structurally different
    document, not a naming ambiguity -- must still raise, never guess."""
    import pytest

    figura = Compound(children=[Box(5, 5, 5)])
    entradas = [
        {"nombre": "a", "indice": 0, "bbox": [0, 1, 0, 1, 0, 1], "volumen": 1.0},
        {"nombre": "b", "indice": 1, "bbox": [0, 1, 0, 1, 0, 1], "volumen": 1.0},
    ]
    with pytest.raises(ValueError):
        solids_mod.solidos_por_indice(figura, entradas)


# --------------------------------------------------- nombres_inciertos surfacing


def test_check_colisiones_surfaces_nombres_inciertos():
    a = Box(10, 10, 10)
    b = Pos(50, 0, 0) * Box(20, 20, 20)
    figura = Compound(children=[a, b])
    entradas_invalidas = [
        {"nombre": "x", "indice": 0, "bbox": [0.0, 1.0, 0.0, 1.0, 0.0, 1.0], "volumen": 1.0},
        {"nombre": "y", "indice": 1, "bbox": [0.0, 1.0, 0.0, 1.0, 0.0, 1.0], "volumen": 1.0},
    ]
    from checks import colisiones as colisiones_mod

    salida = colisiones_mod.verificar(figura, entradas_invalidas)
    assert salida["nombres_inciertos"] is True
