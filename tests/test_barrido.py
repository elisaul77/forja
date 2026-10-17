"""Continuous joint sweep (Phase 18): conservative advancement + read-only REST.

Pure-engine tests build the posed solids directly; REST tests use the same
hinge/slide document shape as `test_assembly_routes.py`. Every sweep here is
read-only: no STEP, history entry or snapshot may change.
"""
from __future__ import annotations

import hashlib
import math

import pydantic
import pytest
from build123d import Axis, Box, Cylinder
from fastapi.testclient import TestClient

import assemblies
import assembly_routes
import auth
import documents
from checks import barrido, pose_clearance
from kernel import b123d_kernel as kernel
from kernel.assembly_kernel import transform_rigid
from main import app

SCRIPT = """from build123d import Box, Pos
PARAMETROS = {'offset': 10}
def construir(p):
    return {'base': Box(2, 2, 2), 'arm': Pos(p['offset'], 0, 0) * Box(2, 2, 2)}
"""
JOINT = dict(id="slide", tipo="deslizamiento", padre="base", hijo="arm",
             origen=[0, 0, 0], eje=[1, 0, 0], limites=[-5, 20], valor=0)


def _evaluador_giro(arm, otros):
    def evaluar(valor):
        posed = {"arm": transform_rigid(arm, [0, 0, 0], [0, 0, 1], valor, 0)}
        posed.update(otros)
        return posed
    return evaluar


def _contacto_analitico_grados(radio_x: float, altura: float, pared_y: float) -> float:
    """Angle at which the arm corner (radio_x, altura/2) reaches y = pared_y."""
    rho = math.hypot(radio_x, altura / 2)
    phi = math.atan2(altura / 2, radio_x)
    return math.degrees(math.asin(pared_y / rho) - phi)


def test_giro_contra_pared_primer_choque_en_el_angulo_analitico():
    """(a) Hinge swinging into a wall: the first contact is at the analytic
    angle (bisected to the 0.05 deg tolerance) and the guarantee is 'continua'."""
    arm = kernel.make_box(16, 2, 2).translate((8, 0, 0))
    wall = kernel.make_box(40, 2, 40).translate((0, 11, 0))
    evaluar = _evaluador_giro(arm, {"wall": wall})
    tasa = barrido.tasa_giro({"arm": arm}, {"arm"}, [0, 0, 0], [0, 0, 1])

    resultado = barrido.barrer(evaluar, {"arm"}, 0.0, 90.0, tasa, unidad="grados")

    analitico = _contacto_analitico_grados(16, 2, 10)
    assert resultado["ok"] is False
    assert resultado["primer_choque"]["par"] == ["arm", "wall"]
    assert abs(resultado["primer_choque"]["valor"] - analitico) < 0.5
    assert resultado["ultimo_seguro"] < resultado["primer_choque"]["valor"]
    assert resultado["garantia"] == "continua"
    assert resultado["completo"] is True
    assert resultado["tolerancia"] == pytest.approx(0.05)


def test_plancha_fina_que_el_muestreo_de_12_pasos_salta():
    """(b) Motivating case: a 0.2 mm plate sits in a window of ~2 deg that
    lies between the 12 samples of the old route (37.5 and 45 deg). The
    sampled check reports nothing; the continuous sweep stops at the contact."""
    arm = kernel.make_box(16, 0.4, 6).translate((8, 0, 0))
    placa = kernel.make_box(0.2, 0.2, 6).translate((12 * math.cos(math.radians(41.25)),
                                                    12 * math.sin(math.radians(41.25)), 0))
    evaluar = _evaluador_giro(arm, {"placa": placa})

    # The old route: 12 equal steps, flagged only by the volume rule of check_scene.
    muestras = [pose_clearance.check_scene(evaluar(90.0 * i / 12), {"arm"})["choques"]
                for i in range(13)]
    assert all(choques == [] for choques in muestras)  # the sampled route misses it

    tasa = barrido.tasa_giro({"arm": arm}, {"arm"}, [0, 0, 0], [0, 0, 1])
    resultado = barrido.barrer(evaluar, {"arm"}, 0.0, 90.0, tasa)
    assert resultado["ok"] is False
    assert resultado["primer_choque"]["par"] == ["arm", "placa"]
    assert 37.5 < resultado["primer_choque"]["valor"] < 45.0  # inside the skipped window
    assert resultado["garantia"] == "continua"


def test_deslizamiento_con_camino_libre_es_ok_y_holgura_exacta():
    """(c) A slide with a clear path: ok, continua, and the constant 3 mm gap."""
    carro = kernel.make_box(2, 2, 2)
    guia = kernel.make_box(40, 2, 2).translate((0, 5, 0))

    def evaluar(valor):
        return {"carro": transform_rigid(carro, [0, 0, 0], [1, 0, 0], 0, valor),
                "guia": guia}

    resultado = barrido.barrer(evaluar, {"carro"}, -5.0, 15.0, tasa=1.0, unidad="mm")

    assert resultado["ok"] is True
    assert resultado["garantia"] == "continua"
    assert resultado["completo"] is True
    assert resultado["primer_choque"] is None
    assert resultado["holgura_minima_mm"] == pytest.approx(3.0, abs=1e-6)
    assert resultado["pares_en_contacto_inicial"] == []


def test_pasador_en_su_agujero_no_falla_y_queda_como_contacto_inicial():
    """(d) A pin sitting in its bore starts in contact: it is reported in
    `pares_en_contacto_inicial`, not as a collision, and the sweep stays ok.
    The label is `muestreada`: an initial-contact pair is sampled, never proven
    (review r1 #1)."""
    bloque = kernel.make_box(10, 10, 10).cut(Cylinder(2, 10))
    pasador = Cylinder(2, 8)

    def evaluar(valor):
        return {"pasador": transform_rigid(pasador, [0, 0, 0], [0, 0, 1], valor, 0),
                "bloque": bloque}

    resultado = barrido.barrer(evaluar, {"pasador"}, 0.0, 90.0, tasa=0.1)

    assert resultado["ok"] is True
    assert resultado["primer_choque"] is None
    assert resultado["pares_en_contacto_inicial"] == [["pasador", "bloque"]]
    assert resultado["garantia"] == "muestreada"


def test_contacto_inicial_con_aleta_de_0_2_mm_no_es_continua_y_se_detecta():
    """Regression (review r1 #1): an arm resting on a base whose 0.2 mm fin sits
    at 41.25 deg. The pair starts in contact, so the label must be `muestreada`,
    the sampling must be dense, and the fin must be detected in its window."""
    arm = kernel.make_box(16, 0.4, 6).translate((8, 0, 0))
    base = kernel.make_box(16, 0.2, 6).translate((8, -0.34, 0))  # 0.04 mm under the arm
    fin = kernel.make_box(0.2, 0.2, 6).translate((12 * math.cos(math.radians(41.25)),
                                                  12 * math.sin(math.radians(41.25)), 0))
    evaluar = _evaluador_giro(arm, {"base": base.fuse(fin)})
    tasa = barrido.tasa_giro({"arm": arm}, {"arm"}, [0, 0, 0], [0, 0, 1])

    resultado = barrido.barrer(evaluar, {"arm"}, 0.0, 90.0, tasa)

    assert resultado["pares_en_contacto_inicial"] == [["arm", "base"]]
    assert resultado["garantia"] == "muestreada"
    assert resultado["pasos"] > 20  # dense: a 0.2 mm fin cannot hide between two samples
    assert resultado["ok"] is False
    assert resultado["primer_choque"]["par"] == ["arm", "base"]
    assert 38.0 < resultado["primer_choque"]["valor"] < 44.0


def test_holgura_constante_de_0_055_no_es_contacto_falso():
    """Regression (review r1 #2): a slider parallel to a wall at a constant 0.055 mm
    clearance is never in contact. The old proximity band reported it at 0 travel."""
    carro = kernel.make_box(2, 2, 2)
    pared = kernel.make_box(40, 2, 2).translate((0, 2.055, 0))  # faces 0.055 mm apart

    def evaluar(valor):
        return {"carro": transform_rigid(carro, [0, 0, 0], [1, 0, 0], 0, valor), "pared": pared}

    resultado = barrido.barrer(evaluar, {"carro"}, -2.0, 2.0, tasa=1.0, unidad="mm")

    assert resultado["primer_choque"] is None
    assert resultado["ok"] is True
    assert resultado["garantia"] == "continua"
    assert resultado["holgura_minima_mm"] == pytest.approx(0.055, abs=1e-6)


def test_holgura_constante_larga_no_se_declara_choque_y_queda_sin_resolver():
    """A long parallel run at 0.055 mm needs one step per tolerance: it runs out of
    budget. That is reported as unresolved (`muestreada`, `ok` false), never as contact."""
    carro = kernel.make_box(2, 2, 2)
    pared = kernel.make_box(40, 2, 2).translate((0, 2.055, 0))

    def evaluar(valor):
        return {"carro": transform_rigid(carro, [0, 0, 0], [1, 0, 0], 0, valor), "pared": pared}

    resultado = barrido.barrer(evaluar, {"carro"}, -5.0, 8.0, tasa=1.0, unidad="mm")

    assert resultado["primer_choque"] is None
    assert resultado["garantia"] == "muestreada"
    assert resultado["ok"] is False
    assert resultado["agotado"] == "evaluaciones"


def test_cierre_lento_contacto_dentro_de_la_tolerancia_del_eje():
    """Regression (review r1 #2): a wall tilted so the gap closes at 0.01 mm per mm.
    The reported contact must be the real touch (gap <= 0.05 mm), bracketed by the
    last safe value to the joint tolerance, not an early band hit."""
    carro = kernel.make_box(2, 2, 2)
    pared = kernel.make_box(120, 2, 2).rotate(Axis.Z, -0.5729).translate((0, 2.5, 0))

    def evaluar(valor):
        return {"carro": transform_rigid(carro, [0, 0, 0], [1, 0, 0], 0, valor), "pared": pared}

    resultado = barrido.barrer(evaluar, {"carro"}, 0.0, 50.0, tasa=1.0, unidad="mm")
    choque = resultado["primer_choque"]
    tol = resultado["tolerancia"]

    assert resultado["ok"] is False
    assert choque["par"] == ["carro", "pared"]
    assert 43.5 < choque["valor"] < 44.5  # far corner reaches gap 0.05; the band hits near 43
    assert resultado["ultimo_seguro"] < choque["valor"]
    assert choque["valor"] - resultado["ultimo_seguro"] <= tol + 1e-9
    assert evaluar(choque["valor"])["pared"].distance(evaluar(choque["valor"])["carro"]) <= 0.05
    assert evaluar(resultado["ultimo_seguro"])["pared"].distance(
        evaluar(resultado["ultimo_seguro"])["carro"]) > 0.05


def test_presupuesto_agotado_durante_el_procesado_de_pares():
    """Regression (review r1 #3): the shared budget counts pair queries; exhausting
    it while processing pairs is `muestreada`, `ok` false, with the phase named."""
    carro = kernel.make_box(2, 2, 2)
    guia = kernel.make_box(40, 2, 2).translate((0, 5, 0))

    def evaluar(valor):
        return {"carro": transform_rigid(carro, [0, 0, 0], [1, 0, 0], 0, valor), "guia": guia}

    sin_limite = barrido.Presupuesto(evaluaciones=10**6)
    barrido.barrer(evaluar, {"carro"}, -5.0, 15.0, tasa=1.0, unidad="mm", presupuesto=sin_limite)
    limite = sin_limite.marcas["pares"]
    presupuesto = barrido.Presupuesto(evaluaciones=limite)

    resultado = barrido.barrer(evaluar, {"carro"}, -5.0, 15.0, tasa=1.0, unidad="mm",
                               presupuesto=presupuesto)

    assert resultado["agotado"] == "evaluaciones"
    assert resultado["agotado_en"] == "pares"
    assert resultado["garantia"] == "muestreada"
    assert resultado["completo"] is False
    assert resultado["ok"] is False


def test_presupuesto_agotado_durante_la_biseccion():
    """Regression (review r1 #3): running out while bisecting the contact is not a
    contact result; it is unresolved and says so."""
    arm = kernel.make_box(16, 2, 2).translate((8, 0, 0))
    wall = kernel.make_box(40, 2, 40).translate((0, 11, 0))
    evaluar = _evaluador_giro(arm, {"wall": wall})
    tasa = barrido.tasa_giro({"arm": arm}, {"arm"}, [0, 0, 0], [0, 0, 1])

    sin_limite = barrido.Presupuesto(evaluaciones=10**6)
    barrido.barrer(evaluar, {"arm"}, 0.0, 90.0, tasa, presupuesto=sin_limite)
    presupuesto = barrido.Presupuesto(evaluaciones=sin_limite.marcas["biseccion"])

    resultado = barrido.barrer(evaluar, {"arm"}, 0.0, 90.0, tasa, presupuesto=presupuesto)

    assert resultado["agotado_en"] == "biseccion"
    assert resultado["garantia"] == "muestreada"
    assert resultado["ok"] is False
    assert "presupuesto" in resultado["frase"]


def test_presupuesto_de_tiempo_agotado_da_muestreada():
    """Wall-clock deadline: an expired deadline is reported as `tiempo`, unresolved."""
    arm = kernel.make_box(16, 2, 2).translate((8, 0, 0))
    wall = kernel.make_box(40, 2, 40).translate((0, 11, 0))
    evaluar = _evaluador_giro(arm, {"wall": wall})
    tasa = barrido.tasa_giro({"arm": arm}, {"arm"}, [0, 0, 0], [0, 0, 1])

    resultado = barrido.barrer(evaluar, {"arm"}, 0.0, 90.0, tasa,
                               presupuesto=barrido.Presupuesto(plazo_s=0.0))

    assert resultado["agotado"] == "tiempo"
    assert resultado["garantia"] == "muestreada"
    assert resultado["ok"] is False


def test_presupuesto_compartido_entre_dos_barridos():
    """One budget object is shared: the second sweep finds it already spent."""
    carro = kernel.make_box(2, 2, 2)
    guia = kernel.make_box(40, 2, 2).translate((0, 5, 0))

    def evaluar(valor):
        return {"carro": transform_rigid(carro, [0, 0, 0], [1, 0, 0], 0, valor), "guia": guia}

    sin_limite = barrido.Presupuesto(evaluaciones=10**6)
    barrido.barrer(evaluar, {"carro"}, -5.0, 15.0, tasa=1.0, unidad="mm", presupuesto=sin_limite)
    gastado = sin_limite.consumido
    compartido = barrido.Presupuesto(evaluaciones=gastado + 5)

    primero = barrido.barrer(evaluar, {"carro"}, -5.0, 15.0, tasa=1.0, unidad="mm",
                             presupuesto=compartido)
    segundo = barrido.barrer(evaluar, {"carro"}, -5.0, 15.0, tasa=1.0, unidad="mm",
                             presupuesto=compartido)

    assert primero["garantia"] == "continua"
    assert segundo["agotado"] == "evaluaciones"
    assert segundo["garantia"] == "muestreada"


def test_error_de_occ_no_expone_el_detalle_al_cliente():
    """Regression (review r1 #6): the client gets a generic message; the OCC detail
    stays in the server log."""
    cuerpo = kernel.make_box(2, 2, 2).translate((3, 0, 0))
    pared = kernel.make_box(2, 2, 2)

    def evaluar(_valor):
        return {"cuerpo": _Roto(cuerpo), "pared": pared}

    resultado = barrido.barrer(evaluar, {"cuerpo"}, 0.0, 10.0, tasa=1.0, unidad="mm")

    assert resultado["errores"][0]["mensaje"] == "error de geometría al evaluar el par"
    assert "no disponible" not in str(resultado)


def test_tope_de_evaluaciones_da_muestreada_y_no_pasa_en_silencio():
    """(e) Cap hit -> `muestreada`, `completo: false`, and `ok` is false."""
    arm = kernel.make_box(16, 2, 2).translate((8, 0, 0))
    wall = kernel.make_box(40, 2, 40).translate((0, 11, 0))
    evaluar = _evaluador_giro(arm, {"wall": wall})
    tasa = barrido.tasa_giro({"arm": arm}, {"arm"}, [0, 0, 0], [0, 0, 1])

    resultado = barrido.barrer(evaluar, {"arm"}, 0.0, 90.0, tasa, cap=2)

    assert resultado["completo"] is False
    assert resultado["garantia"] == "muestreada"
    assert resultado["ok"] is False


class _Roto:
    """A solid whose exact distance query fails, like an OCC error would."""

    def __init__(self, shape):
        self._shape = shape

    def bounding_box(self):
        return self._shape.bounding_box()

    def distance(self, _other):
        raise RuntimeError("OCC: distancia no disponible")


def test_error_de_occ_nunca_es_continua():
    """(f) One failing exact distance is reported in `errores` and blocks the label."""
    cuerpo = kernel.make_box(2, 2, 2).translate((3, 0, 0))
    pared = kernel.make_box(2, 2, 2)

    def evaluar(_valor):
        return {"cuerpo": _Roto(cuerpo), "pared": pared}

    resultado = barrido.barrer(evaluar, {"cuerpo"}, 0.0, 10.0, tasa=1.0, unidad="mm")

    assert resultado["errores"] and resultado["errores"][0]["a"] == "cuerpo"
    assert resultado["garantia"] == "muestreada"
    assert resultado["completo"] is False
    assert resultado["ok"] is False


def test_tasa_giro_es_cota_superior_del_movimiento():
    """The advancement bound is conservative: the rate covers the farthest
    bbox corner from the axis, so no point can move faster than the bound."""
    arm = kernel.make_box(16, 2, 2).translate((8, 0, 0))
    tasa = barrido.tasa_giro({"arm": arm}, {"arm"}, [0, 0, 0], [0, 0, 1])
    assert tasa >= math.hypot(16, 1) * math.pi / 180 - 1e-9
    assert barrido.tasa_deslizamiento([2, 0, 0]) == pytest.approx(2.0)


# --- REST boundary ----------------------------------------------------------

def _headers():
    return {"X-Forja-Token": auth.obtener_token()}


@pytest.fixture
def documento_con_ensamble():
    client = TestClient(app)
    record = client.post("/documentos/script", json={"codigo": SCRIPT}, headers=_headers())
    assert record.status_code == 200, record.text
    doc_id = record.json()["id"]
    definido = client.post(f"/documentos/{doc_id}/ensamble", json={"articulaciones": [JOINT]},
                           headers=_headers())
    assert definido.status_code == 200, definido.text
    yield client, doc_id
    client.delete(f"/documentos/{doc_id}", headers=_headers())


def _hashes(doc_id):
    dato = {}
    for directorio in (documents.DOCUMENTOS_DIR / doc_id,
                       documents.DOCUMENTOS_DIR / ".ensambles" / doc_id):
        for ruta in sorted(directorio.rglob("*")) if directorio.exists() else []:
            if ruta.is_file():
                dato[str(ruta)] = hashlib.sha256(ruta.read_bytes()).hexdigest()
    return dato


def test_rest_barrido_responde_y_no_muta_nada(documento_con_ensamble):
    """(h) Read-only: file hashes and history length are identical before/after."""
    client, doc_id = documento_con_ensamble
    antes_archivos = _hashes(doc_id)
    antes_historial = len(client.get(f"/documentos/{doc_id}/historial").json())

    response = client.post(f"/documentos/{doc_id}/ensamble/barrido",
                           json={"articulaciones": ["slide"]})

    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    cuerpo = response.json()
    assert set(cuerpo) >= {"ok", "garantia", "completo", "barrido", "frase"}
    assert cuerpo["barrido"]["slide"]["garantia"] in {"continua", "muestreada"}
    assert _hashes(doc_id) == antes_archivos
    assert len(client.get(f"/documentos/{doc_id}/historial").json()) == antes_historial


def test_rest_barrido_fuera_de_limites_es_422(documento_con_ensamble):
    """(g) Values outside `limites` are refused before any evaluation."""
    client, doc_id = documento_con_ensamble
    response = client.post(f"/documentos/{doc_id}/ensamble/barrido",
                           json={"articulaciones": ["slide"], "desde": {"slide": 25}})
    assert response.status_code == 422


def test_rest_barrido_rechaza_campos_desconocidos_y_sin_ensamble(documento_con_ensamble):
    client, doc_id = documento_con_ensamble
    extra = client.post(f"/documentos/{doc_id}/ensamble/barrido", json={"valores": {"slide": 1}})
    assert extra.status_code == 422
    assert client.post(f"/documentos/{doc_id}/ensamble/barrido", json={}).status_code == 200
    client.delete(f"/documentos/{doc_id}/ensamble", headers=_headers())
    assert client.post(f"/documentos/{doc_id}/ensamble/barrido", json={}).status_code == 422


FIJA_SCRIPT = """from build123d import Box, Pos
PARAMETROS = {'offset': 10}
def construir(p):
    return {'base': Box(2, 2, 2), 'arm': Pos(p['offset'], 0, 0) * Box(2, 2, 2),
            'tapa': Pos(0, 6, 0) * Box(2, 2, 2)}
"""
FIJA = dict(id="tapa_fija", tipo="fijo", padre="base", hijo="tapa", origen=[0, 0, 0], eje=[0, 0, 1])


def _documento(client, script, articulaciones):
    record = client.post("/documentos/script", json={"codigo": script}, headers=_headers())
    assert record.status_code == 200, record.text
    doc_id = record.json()["id"]
    definido = client.post(f"/documentos/{doc_id}/ensamble",
                           json={"articulaciones": articulaciones}, headers=_headers())
    assert definido.status_code == 200, definido.text
    return doc_id


def test_todas_omite_las_fijas_y_una_fija_nombrada_da_422():
    """Regression (review r1 #4): implicit selection skips fijo joints and lists them
    under `omitidas`; naming a fijo joint explicitly stays 422."""
    client = TestClient(app)
    doc_id = _documento(client, FIJA_SCRIPT, [JOINT, FIJA])
    try:
        implicito = client.post(f"/documentos/{doc_id}/ensamble/barrido", json={})
        nombrada = client.post(f"/documentos/{doc_id}/ensamble/barrido",
                               json={"articulaciones": ["tapa_fija"]})
    finally:
        client.delete(f"/documentos/{doc_id}", headers=_headers())

    assert implicito.status_code == 200, implicito.text
    assert set(implicito.json()["barrido"]) == {"slide"}
    assert implicito.json()["omitidas"] == ["tapa_fija"]
    assert nombrada.status_code == 422


def test_solo_fijas_es_ok_con_explicacion():
    """Regression (review r1 #4): an assembly with only fixed joints has nothing to
    sweep: ok, an empty barrido, and a sentence that says why."""
    client = TestClient(app)
    doc_id = _documento(client, FIJA_SCRIPT, [FIJA])
    try:
        respuesta = client.post(f"/documentos/{doc_id}/ensamble/barrido", json={})
    finally:
        client.delete(f"/documentos/{doc_id}", headers=_headers())

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["ok"] is True
    assert respuesta.json()["barrido"] == {}
    assert respuesta.json()["omitidas"] == ["tapa_fija"]
    assert "Ninguna articulación móvil" in respuesta.json()["frase"]


def test_frase_limitada_de_cobertura_cuando_ok(documento_con_ensamble):
    """Regression (review r1 #5): when ok, the sentence keeps the coverage limits
    instead of a bare 'Sin choques ni contactos'."""
    client, doc_id = documento_con_ensamble
    respuesta = client.post(f"/documentos/{doc_id}/ensamble/barrido",
                            json={"articulaciones": ["slide"]})

    frase = respuesta.json()["frase"]
    assert respuesta.json()["ok"] is True
    assert "no cubre varias articulaciones" in frase
    assert "se muestrean" in frase


def test_desde_con_mas_de_60_claves_es_422():
    """Regression (review r1 #6): the desde/hasta maps are capped at 60 keys."""
    claves = {f"j{i}": 0.0 for i in range(61)}
    with pytest.raises(pydantic.ValidationError):
        assembly_routes.Sweep.model_validate({"desde": claves})
