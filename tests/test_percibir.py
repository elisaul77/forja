"""Phase 5E tests: `percibir` (`app/percepcion.py`, `GET
/documentos/{id}/percibir`) -- spatial perception as compact text.

Same in-process `TestClient` + isolated `FORJA_DATA_DIR` pattern as
`tests/test_captura_colisiones_exportar.py`.
"""
from __future__ import annotations

import io

from fastapi.testclient import TestClient
from PIL import Image

import auth
import percepcion
from main import app

client = TestClient(app)


def _headers() -> dict[str, str]:
    return {"X-Forja-Token": auth.obtener_token()}


def _crear(script: str) -> dict:
    resp = client.post("/documentos/script", json={"codigo": script}, headers=_headers())
    assert resp.status_code == 200, resp.text
    return resp.json()


def _percibir(doc_id: str, **params) -> dict:
    resp = client.get(f"/documentos/{doc_id}/percibir", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


_A = "align=(Align.CENTER, Align.CENTER, Align.MIN)"

# --- (a)/(b)/(c)/(d)/(e): small hand-built scenes with exact expected gaps

_SCRIPT_CAJA_SOBRE_BASE_TOCANDO = (
    "from build123d import Box, Pos, Align\n"
    f"A = {_A}\n"
    "resultado = {\n"
    "    'base': Box(40, 40, 10, align=A),\n"
    "    'caja': Pos(0, 0, 10) * Box(10, 10, 10, align=A),\n"
    "}\n"
)

_SCRIPT_CAJA_FLOTANDO_0_4MM = (
    "from build123d import Box, Pos, Align\n"
    f"A = {_A}\n"
    "resultado = {\n"
    "    'base': Box(40, 40, 10, align=A),\n"
    "    'caja': Pos(0, 0, 10.4) * Box(10, 10, 10, align=A),\n"
    "}\n"
)

_SCRIPT_TIRADOR_SIN_APOYO = (
    "from build123d import Box, Pos, Align\n"
    f"A = {_A}\n"
    "resultado = {\n"
    "    'base': Box(30, 30, 10, align=A),\n"
    "    'cajon': Pos(0, 0, 10) * Box(30, 30, 15, align=A),\n"
    "    'tirador': Pos(17.4, 0, 15) * Box(2, 10, 5, align=A),\n"
    "}\n"
)

_SCRIPT_DOS_CAJAS_3MM = (
    "from build123d import Box, Pos, Align\n"
    f"A = {_A}\n"
    "resultado = {\n"
    "    'base': Box(100, 100, 10, align=A),\n"
    "    'caja_a': Pos(-6.5, 0, 10) * Box(10, 10, 10, align=A),\n"
    "    'caja_b': Pos(6.5, 0, 10) * Box(10, 10, 10, align=A),\n"
    "}\n"
)

_SCRIPT_CHOQUE = (
    "from build123d import Box, Pos\n"
    "resultado = {'caja_a': Box(10, 10, 10), 'caja_b': Pos(5, 0, 0) * Box(10, 10, 10)}\n"
)

_SCRIPT_CORTE_DOS_CAJAS = (
    "from build123d import Box, Pos, Align\n"
    f"A = {_A}\n"
    "resultado = {\n"
    "    'caja_a': Pos(-10, 0, 0) * Box(10, 10, 10, align=A),\n"
    "    'caja_b': Pos(10, 0, 0) * Box(10, 10, 10, align=A),\n"
    "}\n"
)


def test_a_caja_tocando_base_h_0():
    creado = _crear(_SCRIPT_CAJA_SOBRE_BASE_TOCANDO)
    data = _percibir(creado["id"])
    texto = data["texto"]
    assert "base  base" in texto
    assert "caja  sobre base  h=0.0" in texto
    assert "NO toca" not in texto


def test_b_caja_flotando_0_4mm_no_toca():
    creado = _crear(_SCRIPT_CAJA_FLOTANDO_0_4MM)
    data = _percibir(creado["id"])
    texto = data["texto"]
    assert "caja  sobre base  h=0.4 (NO toca)" in texto


def test_c_tirador_sin_apoyo_reporta_mas_cercano():
    creado = _crear(_SCRIPT_TIRADOR_SIN_APOYO)
    data = _percibir(creado["id"])
    texto = data["texto"]
    assert "cajon  sobre base  h=0.0" in texto
    assert "--- sin apoyo ---" in texto
    assert "SIN APOYO: tirador" in texto
    assert "cajon a 1.4 mm" in texto
    assert "+X" in texto


def test_d_vecinos_3mm_eje_correcto():
    creado = _crear(_SCRIPT_DOS_CAJAS_3MM)
    data = _percibir(creado["id"])
    texto = data["texto"]
    assert "caja_a  sobre base  h=0.0" in texto
    assert "caja_b  sobre base  h=0.0" in texto
    assert "--- vecinos" in texto
    assert "caja_a <-> caja_b  3.0 mm  (+X)" in texto


def test_e_choque_reusa_check_colisiones():
    creado = _crear(_SCRIPT_CHOQUE)
    data = _percibir(creado["id"])
    texto = data["texto"]
    assert "--- choques ---" in texto
    assert "CHOQUE: caja_a x caja_b" in texto
    # overlap: 5 x 10 x 10 = 500 mm3 (within 1%)
    import re

    m = re.search(r"CHOQUE: caja_a x caja_b ([\d.]+) mm3", texto)
    assert m, texto
    assert abs(float(m.group(1)) - 500.0) / 500.0 < 0.01


def test_f_corte_z_dos_cajas_letras_correctas():
    creado = _crear(_SCRIPT_CORTE_DOS_CAJAS)
    data = _percibir(creado["id"], capas="cortes", cortes_z=5, eje="z", resolucion=20)
    texto = data["texto"]
    assert "corte Z=5" in texto
    assert "leyenda: a=caja_a b=caja_b" in texto
    lineas_rejilla = [l for l in texto.splitlines() if set(l) <= set("ab#.")]
    assert any("a" in l for l in lineas_rejilla)
    assert any("b" in l for l in lineas_rejilla)
    # caja_a is to the left (-X) of caja_b in every row that has both
    for l in lineas_rejilla:
        if "a" in l and "b" in l:
            assert l.index("a") < l.index("b")


def test_g_tope_de_tokens_en_escena_sintetica_de_40_solidos():
    piezas = ["    'base': Box(500, 500, 10, align=A),\n"]
    for i in range(40):
        x = (i % 10) * 12
        y = (i // 10) * 12
        piezas.append(f"    'pieza_{i}': Pos({x}, {y}, 10) * Box(10, 10, 10, align=A),\n")
    script = (
        "from build123d import Box, Pos, Align\n"
        f"A = {_A}\n"
        "resultado = {\n" + "".join(piezas) + "}\n"
    )
    creado = _crear(script)
    data = _percibir(creado["id"])
    assert len(data["texto"]) <= 3200, len(data["texto"])
    assert data["tokens_aprox"] <= 800


def test_h_captura_default_512():
    creado = _crear(_SCRIPT_CHOQUE)
    resp = client.get(f"/documentos/{creado['id']}/captura")
    assert resp.status_code == 200, resp.text
    imagen = Image.open(io.BytesIO(resp.content))
    assert imagen.size == (512, 512)


def test_capa_desconocida_400():
    creado = _crear(_SCRIPT_CHOQUE)
    resp = client.get(f"/documentos/{creado['id']}/percibir", params={"capas": "no-existe"})
    assert resp.status_code == 400


def test_percibir_documento_stl_400():
    from kernel import b123d_kernel, mesh

    box = b123d_kernel.make_box(6, 6, 6)
    tri_mesh = mesh.tessellate_to_trimesh(box, tolerance=0.05)
    box_stl = client.post(
        "/documentos",
        files={"file": ("cubo.stl", io.BytesIO(mesh.to_stl_bytes(tri_mesh)), "application/sla")},
    )
    doc_id = box_stl.json()["id"]
    resp = client.get(f"/documentos/{doc_id}/percibir")
    assert resp.status_code == 400


def test_percibir_documento_inexistente_404():
    resp = client.get("/documentos/no-existe/percibir")
    assert resp.status_code == 404


def test_percibir_nombre_de_solido_inexistente_400():
    creado = _crear(_SCRIPT_CHOQUE)
    resp = client.get(f"/documentos/{creado['id']}/percibir", params={"solidos": "no-existe"})
    assert resp.status_code == 400


def test_capas_multiples_contactos_y_cortes():
    creado = _crear(_SCRIPT_CORTE_DOS_CAJAS)
    data = _percibir(creado["id"], capas="contactos,cortes", cortes_z=5)
    assert "solido(s) | bbox" in data["texto"]
    assert "corte Z=5" in data["texto"]


def test_huecos_stub_no_implementado():
    creado = _crear(_SCRIPT_CHOQUE)
    data = _percibir(creado["id"], capas="huecos")
    assert "no implementado" in data["texto"]


# --- 5E fix-review: conservative `cortes` coverage, input bounds, denser
# --- support detection

# A 1.5 mm post whose footprint contains NO cell centre at resolucion=20
# (5 mm cells from X=-50; ~5.3 mm rows from Y=11.7): the old one-sample-per-
# cell-centre grid dropped it from both the grid and the legend.
_SCRIPT_POSTE_FINO = (
    "from build123d import Box, Pos, Align\n"
    f"A = {_A}\n"
    "resultado = {\n"
    "    'bloque': Pos(0, -15, 0) * Box(100, 10, 10, align=A),\n"
    "    'poste': Pos(0.95, 10.95, 0) * Box(1.5, 1.5, 20, align=A),\n"
    "}\n"
)


def test_corte_poste_fino_1_5mm_aparece_en_rejilla_y_leyenda():
    creado = _crear(_SCRIPT_POSTE_FINO)
    data = _percibir(creado["id"], capas="cortes", cortes_z=5, eje="z", resolucion=20)
    texto = data["texto"]
    assert "leyenda: a=bloque b=poste" in texto, texto
    rejilla = [l for l in texto.splitlines() if l and set(l) <= set("ab#.")]
    assert sum(l.count("b") for l in rejilla) >= 1, texto
    # the thin post is at the top-centre of the scene, the block at the bottom
    assert "b" in rejilla[0], texto
    assert "a" in rejilla[-1], texto


def test_cortes_z_mas_de_6_valores_422():
    creado = _crear(_SCRIPT_CORTE_DOS_CAJAS)
    resp = client.get(
        f"/documentos/{creado['id']}/percibir",
        params={"capas": "cortes", "cortes_z": [1, 2, 3, 4, 5, 6, 7]},
    )
    assert resp.status_code == 422, resp.text


def test_proximidad_mm_1000_422():
    creado = _crear(_SCRIPT_CORTE_DOS_CAJAS)
    resp = client.get(f"/documentos/{creado['id']}/percibir", params={"proximidad_mm": 1000})
    assert resp.status_code == 422, resp.text


# Chair: 40x40 seat on four 2x2 mm legs at the TRUE corners, standing on a
# frame-shaped base whose hole is under the seat centre -- only the legs'
# own spots have material underneath (the old 10%-inset probes all fell in
# the hole).
_SCRIPT_SILLA_PATAS_EN_ESQUINAS = (
    "from build123d import Box, Pos, Align\n"
    f"A = {_A}\n"
    "silla = Pos(0, 0, 30) * Box(40, 40, 3, align=A)\n"
    "for x in (-19, 19):\n"
    "    for y in (-19, 19):\n"
    "        silla += Pos(x, y, 10) * Box(2, 2, 20, align=A)\n"
    "resultado = {\n"
    "    'base': Box(44, 44, 10, align=A) - Box(36, 36, 10, align=A),\n"
    "    'silla': silla,\n"
    "}\n"
)

# Box resting on the ridge of a 45-degree-rotated square prism: a LINE
# contact at X=3, away from every fixed probe (centre / corners).
_SCRIPT_CAJA_SOBRE_CUNA = (
    "from build123d import Box, Pos, Rotation, Align\n"
    f"A = {_A}\n"
    "R = 7.0710678118654755\n"
    "resultado = {\n"
    "    'soporte': Pos(3, 0, 10) * Rotation(0, 45, 0) * Box(10, 40, 10),\n"
    "    'caja': Pos(0, 0, 10 + R) * Box(10, 10, 5, align=A),\n"
    "}\n"
)

_SCRIPT_TABLA_SOBRE_DOS_APOYOS = (
    "from build123d import Box, Pos, Align\n"
    f"A = {_A}\n"
    "resultado = {\n"
    "    'suelo': Box(100, 100, 10, align=A),\n"
    "    'apoyo_a': Pos(-7, 0, 10) * Box(6, 20, 10, align=A),\n"
    "    'apoyo_b': Pos(7, 0, 10) * Box(6, 20, 10, align=A),\n"
    "    'tabla': Pos(0, 0, 20) * Box(24, 10, 2, align=A),\n"
    "}\n"
)


def test_silla_patas_en_esquinas_sobre_base_h_0():
    creado = _crear(_SCRIPT_SILLA_PATAS_EN_ESQUINAS)
    texto = _percibir(creado["id"])["texto"]
    assert "base  base" in texto, texto
    assert "silla  sobre base  h=0.0" in texto, texto
    assert "SIN APOYO" not in texto, texto


def test_caja_sobre_cuna_contacto_en_linea_apoyada():
    creado = _crear(_SCRIPT_CAJA_SOBRE_CUNA)
    texto = _percibir(creado["id"])["texto"]
    assert "soporte  base" in texto, texto
    assert "caja  sobre soporte  h=0.0" in texto, texto
    assert "SIN APOYO" not in texto, texto


def test_tabla_sobre_dos_apoyos_nombra_uno_y_el_otro_es_vecino():
    import re

    creado = _crear(_SCRIPT_TABLA_SOBRE_DOS_APOYOS)
    texto = _percibir(creado["id"])["texto"]
    assert "SIN APOYO" not in texto, texto
    m = re.search(r"^tabla  sobre (apoyo_[ab])  h=0\.0$", texto, re.MULTILINE)
    assert m, texto
    otro = "apoyo_b" if m.group(1) == "apoyo_a" else "apoyo_a"
    assert f"{otro} <-> tabla  0.0 mm" in texto, texto


def test_celdas_de_seccion_polilinea_abierta_no_rellena_filas_impares():
    """Three open vertical strokes (x=1, 5, 9) cross every row 3 times: the
    even-odd fill must not pair the first two crossings and paint x=1..5 --
    only the cells the strokes pass through are marked."""
    segmentos = [(1.5, 0.0, 1.5, 10.0), (5.5, 0.0, 5.5, 10.0), (9.5, 0.0, 9.5, 10.0)]
    rejilla = (0.0, 10.0, 1.0, 1.0, 10, 10)
    celdas = percepcion._celdas_de_seccion(segmentos, rejilla)  # noqa: SLF001
    assert celdas == {(r, c) for r in range(10) for c in (1, 5, 9)}

    # A closed square still gets its interior filled (regression guard).
    cuadrado = [(2.5, 2.5, 7.5, 2.5), (7.5, 2.5, 7.5, 7.5), (7.5, 7.5, 2.5, 7.5), (2.5, 7.5, 2.5, 2.5)]
    lleno = percepcion._celdas_de_seccion(cuadrado, rejilla)  # noqa: SLF001
    assert (5, 5) in lleno and (4, 4) in lleno
