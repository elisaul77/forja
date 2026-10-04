"""Phase 6.5 — the committed twin of the mostertruck casquillo pilot.

`plans/evidencia/6/casquillos_mostertruck.py` (gitignored, like every other
phase's evidence) proves through Forja's own path that the real front-axle
bushing set collides with its housing: three `L_C_arriba x L_casquilloP5_*`
choques at 4.910655 mm3 each, and three `casquillo x tuerca` pairs at
distance 0 whose intersection is exactly 0 (contact, not collision). That
evidence depends on a read-only mount of somebody else's FreeCAD assembly, so
it can never run in CI; this module is the self-contained equivalent, with
volumes a human can check on paper.

Two fixtures, deliberately:

* a **bushing pressed into a housing** — a 13x12x20 pin through a 40x20x20
  block (10 x 12 x 20 = 2400 mm3 of overlap, the pin's outer end sticking out
  of the block) with a nut resting flat on that end face (contact: distance
  0, intersection 0). This is the passing twin: what `check_colisiones` must
  keep reporting, at the volume the geometry says, with the contact pair left
  alone.
* **two right-triangle prisms** that tile the very same 20x20x10 box, face to
  face: identical bounding boxes (4000 mm3 of bbox overlap), zero
  intersection. A bbox-based "collision" cannot survive this pair, which is
  what keeps the exact-boolean definition under test.

The cylindrical twin of that same bushing (`Cylinder(6, 44)` through the same
block, pi * 6^2 * 20 = 2261.9467 mm3 of overlap) is the regression this module
exists for. `Shape.distance` is not an exact zero for solids that really
intersect — this pin measures 3.07e-16 — so the old `separacion > 0.0` gate in
`checks/colisiones.py` answered `{"choques": [], "ok": true}` for a genuine
2261.9467 mm3 overlap, and `app/percepcion.py`'s `d > 0.0` gate hid the same
CHOQUE from `percibir`, although the shared
`checks.distancia.volumen_interseccion` (which this test proves returns the
right volume on this very document) was never asked. Both gates now share
`TOLERANCIA_DISTANCIA_CERO_MM` (1e-9 mm) and the volume threshold is what
classifies a pair; this test reds again if either gate returns to an
exact-zero comparison.

Fail-first checked against mutated *copies* of `app/checks/colisiones.py`
(never the file in the repo), each run against this module: dropping
`tolerancia_mm3` from the threshold reds the four other tests, substituting a
bounding-box volume for the exact intersection reds the prism test with
`{'a': 'prisma_abajo', 'b': 'prisma_arriba', 'vol': 4000.0}`, and restoring
the old `separacion > 0.0` gate reds the cylindrical one — the same way it did
before the fix, which is how the fix was proven necessary in the first place.

Same in-process `TestClient` + isolated `FORJA_DATA_DIR` pattern as
`tests/test_captura_colisiones_exportar.py` (and the same `documents`/
`solids`/`kernel` imports `tests/test_solidos.py` already uses).
"""
from __future__ import annotations

import math

from fastapi.testclient import TestClient

import auth
import solids
from checks import distancia
from documents import DOCUMENTOS_DIR
from kernel import b123d_kernel
from main import app

client = TestClient(app)

# `checks/colisiones.py` reports every volume as `round(volumen, 1)`, so an
# assertion against an analytic value has to allow half a tenth.
_REDONDEO_WIRE_MM3 = 0.05

# Bushing: Box(13, 12, 20) placed at x=16.5 -> x in [10, 23] inside a
# Box(40, 20, 20) at the origin (x in [-20, 20]): 10 mm of the pin's length
# sits inside the block, so the overlap is a 10 x 12 x 20 box.
_SOLAPE_ALOJAMIENTO_CASQUILLO_MM3 = 10.0 * 12.0 * 20.0

# Cylindrical twin: Cylinder(6, 44) through the same block, whose full 20 mm
# height is crossed by the pin -> pi * 6^2 * 20.
_RADIO_CASQUILLO_MM = 6.0
_ALTO_ALOJAMIENTO_MM = 20.0
_SOLAPE_CILINDRICO_MM3 = math.pi * _RADIO_CASQUILLO_MM**2 * _ALTO_ALOJAMIENTO_MM


def _headers() -> dict[str, str]:
    return {"X-Forja-Token": auth.obtener_token()}


def _crear(script: str) -> dict:
    resp = client.post("/documentos/script", json={"codigo": script}, headers=_headers())
    assert resp.status_code == 200, resp.text
    return resp.json()


def _colisiones(doc_id: str, tolerancia_mm3: float | None = None) -> dict:
    params = {} if tolerancia_mm3 is None else {"tolerancia_mm3": tolerancia_mm3}
    resp = client.get(f"/documentos/{doc_id}/colisiones", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


_SCRIPT_CASQUILLO_EN_ALOJAMIENTO = (
    "from build123d import Box, Pos\n"
    "resultado = {\n"
    "    'alojamiento': Box(40, 20, 20),\n"
    "    'casquillo': Pos(16.5, 0, 0) * Box(13, 12, 20),\n"
    "    'tuerca': Pos(28, 0, 0) * Box(10, 10, 10),\n"
    "}\n"
)

_SCRIPT_CASQUILLO_CILINDRICO = (
    "from build123d import Box, Cylinder, Pos\n"
    "resultado = {\n"
    "    'alojamiento': Box(40, 20, 20),\n"
    "    'casquillo': Cylinder(6, 44),\n"
    "    'tuerca': Pos(0, 0, 26) * Box(16, 16, 8),\n"
    "}\n"
)

# Both polygons wound counter-clockwise: `extrude` follows the winding, and a
# clockwise one would be extruded towards -z (a stacked pair, whose bbox
# overlap is a plane, not this clamped pair sharing one 20x20x10 box).
# Two R6 pins whose surfaces meet exactly along one line: OCC reports a
# denormal distance (2.20e-15) instead of an exact 0.0, and the boolean
# intersection is exactly 0 -- the contact-shaped case that lives *inside*
# TOLERANCIA_DISTANCIA_CERO_MM and must still not be a choque.
_SCRIPT_CASQUILLOS_TANGENTES = (
    "from build123d import Cylinder, Pos\n"
    "resultado = {\n"
    "    'casquillo_a': Cylinder(6, 44),\n"
    "    'casquillo_b': Pos(12, 0, 0) * Cylinder(6, 20),\n"
    "}\n"
)

_SCRIPT_PRISMAS_ENCAJADOS = (
    "from build123d import Polygon, extrude\n"
    "resultado = {\n"
    "    'prisma_abajo': extrude(Polygon((0, 0), (20, 0), (0, 20)), 10),\n"
    "    'prisma_arriba': extrude(Polygon((20, 20), (0, 20), (20, 0)), 10),\n"
    "}\n"
)


def test_casquillo_en_alojamiento_reporta_el_solape_analitico():
    creado = _crear(_SCRIPT_CASQUILLO_EN_ALOJAMIENTO)
    data = _colisiones(creado["id"])

    assert data["ok"] is False
    assert len(data["choques"]) == 1
    choque = data["choques"][0]
    assert {choque["a"], choque["b"]} == {"alojamiento", "casquillo"}
    assert abs(choque["vol"] - _SOLAPE_ALOJAMIENTO_CASQUILLO_MM3) <= _REDONDEO_WIRE_MM3
    assert data["partidas"] == []
    # Three separate solids, no name repeated and nobody left in the air:
    # the nut hangs off the pin's outer end face alone.
    assert data["flotantes"] == []


def test_tuerca_apoyada_en_el_casquillo_es_toque_y_no_choque():
    creado = _crear(_SCRIPT_CASQUILLO_EN_ALOJAMIENTO)
    data = _colisiones(creado["id"])

    pares = {frozenset((choque["a"], choque["b"])) for choque in data["choques"]}
    assert frozenset(("casquillo", "tuerca")) not in pares, data["choques"]

    # Absence is only meaningful next to positive evidence of contact: the
    # pair is at distance 0.0, which is exactly what makes it a contact
    # (distance 0, intersection 0) and not two solids that never met.
    texto = client.get(f"/documentos/{creado['id']}/percibir").json()["texto"]
    contacto = [linea for linea in texto.splitlines() if "casquillo" in linea and "tuerca" in linea]
    assert contacto, texto
    assert "0.0 mm" in contacto[0], contacto[0]


def test_dos_casquillos_tangentes_en_la_banda_no_son_choque():
    creado = _crear(_SCRIPT_CASQUILLOS_TANGENTES)
    data = _colisiones(creado["id"])

    # The mirror image of the cylindrical twin: this pair's distance (2.20e-15)
    # is also inside TOLERANCIA_DISTANCIA_CERO_MM, so the relaxed gate sends it
    # to the exact boolean too -- and the boolean says 0 mm3. An implementation
    # that read "inside the epsilon" as "collides" (instead of letting
    # `tolerancia_mm3` decide) would report a choque here.
    assert data["choques"] == []
    # They really do touch, which is what makes the line above a statement
    # about contact rather than about two solids that never met.
    assert data["flotantes"] == []
    assert data["ok"] is True


def test_prismas_encajados_no_son_choque_aunque_sus_bbox_se_solapen():
    creado = _crear(_SCRIPT_PRISMAS_ENCAJADOS)
    data = _colisiones(creado["id"])

    # Both prisms measure 20x20x10 in the same box -- 4000 mm3 of
    # bounding-box overlap over a zero-volume contact. Only an exact boolean
    # intersection gets this right.
    assert data["choques"] == []
    assert data["partidas"] == []
    assert data["flotantes"] == []
    assert data["ok"] is True


def test_la_tolerancia_es_el_umbral_del_volumen_reportado():
    creado = _crear(_SCRIPT_CASQUILLO_EN_ALOJAMIENTO)

    # 2400 mm3 of overlap: reported while the threshold is below it, gone the
    # moment the threshold passes it. An implementation that ignored
    # `tolerancia_mm3` (or compared against something other than the computed
    # volume) cannot satisfy both calls.
    assert len(_colisiones(creado["id"], 2300.0)["choques"]) == 1
    assert _colisiones(creado["id"], 2500.0)["choques"] == []


def test_casquillo_cilindrico_choca_con_el_alojamiento():
    creado = _crear(_SCRIPT_CASQUILLO_CILINDRICO)

    # The engine both consumers share already gets this right on this very
    # document -- it is the check's own gate that never asks it.
    shape = b123d_kernel.import_from_step(DOCUMENTOS_DIR / f"{creado['id']}.step")
    grupos, inciertos = solids.solidos_por_indice(shape, solids.cargar(creado["id"]))
    assert inciertos is False
    por_nombre = {nombre: solido for nombre, _indice, solido, _bbox, _volumen in grupos}
    medido = distancia.volumen_interseccion(por_nombre["alojamiento"], por_nombre["casquillo"])
    assert abs(medido - _SOLAPE_CILINDRICO_MM3) < 1e-6

    data = _colisiones(creado["id"])
    assert len(data["choques"]) == 1
    choque = data["choques"][0]
    assert {choque["a"], choque["b"]} == {"alojamiento", "casquillo"}
    assert abs(choque["vol"] - _SOLAPE_CILINDRICO_MM3) <= _REDONDEO_WIRE_MM3

    # `percibir` has its own copy of the same gate (`app/percepcion.py`), and
    # it hid this very CHOQUE line before the fix.
    texto = client.get(f"/documentos/{creado['id']}/percibir").json()["texto"]
    lineas = [
        linea
        for linea in texto.splitlines()
        if linea.startswith("CHOQUE") and "alojamiento" in linea and "casquillo" in linea
    ]
    assert lineas, texto
    assert f"{choque['vol']:.1f} mm3" in lineas[0], lineas[0]
