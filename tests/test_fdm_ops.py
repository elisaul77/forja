"""fdm-C tests: automatic FDM design fixes (`app/fdm_ops.py`), their
injection into the sandbox and the `sugerencias` of check_fdm."""
from __future__ import annotations

import math
import shutil

import pytest
from build123d import Box, Cylinder, Pos, import_step
from fastapi.testclient import TestClient

import auth
import fdm_ops
import perfil_fdm
import scripts_runner
from main import app

client = TestClient(app)
SEMILLA = perfil_fdm.SEMILLA


def _vol(forma) -> float:
    return float(forma.volume)


# ------------------------------------------------------------ agujero_gota

def test_gota_techo_a_45_y_diametro_compensado():
    d = 5.0
    gota = fdm_ops.agujero_gota(d, 30, eje="X", centro=(0, 0, 10))
    dm = perfil_fdm.agujero(d, SEMILLA, horizontal=True)
    assert dm == pytest.approx(5.2)
    bb = gota.bounding_box()
    assert gota.is_valid
    assert bb.size.X == pytest.approx(30)
    assert bb.size.Y == pytest.approx(dm, abs=1e-3)
    assert bb.min.Z == pytest.approx(10 - dm / 2, abs=1e-3)
    assert bb.max.Z == pytest.approx(10 + dm / 2 * math.sqrt(2), abs=1e-3)  # punta
    techos = [f for f in gota.faces() if f.geom_type.name == "PLANE" and abs(f.normal_at().X) < 0.5]
    assert len(techos) == 2
    for cara in techos:
        assert abs(cara.normal_at().Z) == pytest.approx(math.sqrt(0.5), abs=1e-6)  # 45 grados
    # area = 3/4 de circulo + triangulo (r^2) => volumen
    r = dm / 2
    assert _vol(gota) == pytest.approx((0.75 * math.pi * r * r + r * r) * 30, rel=1e-4)


def test_gota_en_y_y_sin_compensar():
    gota = fdm_ops.agujero_gota(4, 20, eje="y", compensar=False)
    bb = gota.bounding_box()
    assert (bb.size.X, bb.size.Y) == (pytest.approx(4), pytest.approx(20))
    with pytest.raises(ValueError):
        fdm_ops.agujero_gota(4, 20, eje="Z")


def test_gota_sin_voladizo_en_check_fdm():
    from checks import fdm as fdm_checks
    from kernel import mesh as kernel_mesh

    bloque = Box(40, 20, 20)
    redondo = kernel_mesh.tessellate_to_trimesh(bloque - Cylinder(4, 50, rotation=(0, 90, 0)), 0.1)
    gota = kernel_mesh.tessellate_to_trimesh(bloque - fdm_ops.agujero_gota(8, 50, "X"), 0.1)
    assert fdm_checks._check_voladizo_y_base(redondo, 45.0)[0] is not None
    assert fdm_checks._check_voladizo_y_base(gota, 45.5)[0] is None


# ------------------------------------------------------------ chaflan_base

def test_chaflan_base_reduce_area_y_conserva_caja():
    pieza = Box(30, 30, 10) - Pos(0, 0, -5) * Cylinder(3, 4)
    nueva = fdm_ops.chaflan_base(pieza, 0.5)
    assert nueva.is_valid
    assert fdm_ops.area_base(nueva) < fdm_ops.area_base(pieza) - 50
    assert _vol(nueva) < _vol(pieza)
    assert (nueva.bounding_box().size - pieza.bounding_box().size).length < 1e-6


def test_chaflan_base_fallido_devuelve_pieza_y_aviso():
    pieza = Box(10, 10, 0.3)  # chaflan de 0.5 mas alto que la pieza
    avisos: list[str] = []
    assert fdm_ops.chaflan_base(pieza, 0.5, avisos=avisos) is pieza
    assert avisos and "sin cambios" in avisos[0]
    esfera_like = Cylinder(5, 10, rotation=(90, 0, 0))  # sin cara plana abajo
    assert fdm_ops.chaflan_base(esfera_like, 0.5, avisos=avisos) is esfera_like
    assert fdm_ops.chaflan_base(Box(10, 10, 10), 5, avisos=avisos).volume == pytest.approx(1000)


# ------------------------------------------------------ puente_sacrificio

def test_puente_sacrificio_una_capa_sobre_el_contrataladro():
    disco = fdm_ops.puente_sacrificio(3.4, 3.0, centro=(5, 0))
    bb = disco.bounding_box()
    assert (bb.min.Z, bb.max.Z) == (pytest.approx(3.0), pytest.approx(3.2))
    assert bb.size.X == pytest.approx(3.4 + 0.8)
    pieza = Box(20, 20, 8, ) .moved(Pos(0, 0, 4))
    taladro = Pos(5, 0, 4) * Cylinder(1.7, 8)
    cbore = Pos(5, 0, 1.5) * Cylinder(3, 3)
    sin = pieza - taladro - cbore
    con = sin + disco
    assert con.is_valid and len(con.solids()) == 1
    assert _vol(con) - _vol(sin) == pytest.approx(math.pi * 1.7 ** 2 * 0.2, rel=1e-3)


# ------------------------------------------------------- partir_para_cama

def _caja_larga():
    return Box(300, 100, 40) - fdm_ops.agujero_gota(8, 320, "X")


def test_partir_con_pasadores_cabe_y_conserva_volumen():
    pieza = _caja_larga()
    partes = fdm_ops.partir_para_cama(pieza, nombre="caja")
    nombres = sorted(partes)
    assert nombres == ["caja_parte1", "caja_parte2", "caja_pasador1", "caja_pasador2"]
    for nombre in nombres:
        forma = partes[nombre]
        assert forma.is_valid and len(forma.solids()) == 1
        s = forma.bounding_box().size
        assert s.X <= 220 and s.Y <= 220 and s.Z <= 250
    p1, p2 = partes["caja_parte1"], partes["caja_parte2"]
    assert p1.bounding_box().max.X == pytest.approx(0, abs=1e-6)  # corte en el medio
    # Agujeros: 2 por pasador y lado, gota compensada con holgura eje_presion.
    d = 5.0
    dh = perfil_fdm.agujero(d + SEMILLA["holgura_presion"], SEMILLA, horizontal=True)
    prof = 2 * d + 2
    r = dh / 2
    area_gota = 0.75 * math.pi * r * r + r * r
    hueco = _vol(pieza) - _vol(p1) - _vol(p2)
    assert hueco == pytest.approx(4 * area_gota * (prof + 0.01), rel=0.01)
    # Pasadores: diametro compensado con eje(), mas cortos que los 2 agujeros.
    for k in (1, 2):
        pas = partes[f"caja_pasador{k}"].bounding_box().size
        assert pas.X == pytest.approx(perfil_fdm.eje(d, SEMILLA), abs=1e-3)
        assert pas.Z == pytest.approx(2 * prof - 1)
        assert pas.X < dh  # entra en el agujero
    # Ninguna parte toca el agujero central (pared respetada): la gota sigue igual.
    assert len(p1.solids()) == 1


def test_partir_cola_de_milano_y_casos_triviales():
    pieza = _caja_larga()
    partes = fdm_ops.partir_para_cama(pieza, union="cola_milano", nombre="c")
    assert sorted(partes) == ["c_parte1", "c_parte2"]
    a, b = partes["c_parte1"], partes["c_parte2"]
    assert a.bounding_box().max.X > 1  # la lengueta asoma
    for forma in (a, b):
        assert forma.is_valid and forma.bounding_box().size.X <= 220
    perdido = _vol(pieza) - _vol(a) - _vol(b)
    assert 0 < perdido < 0.002 * _vol(pieza)  # solo la holgura
    # Ya cabe -> igual; cabe girada -> girada sin partir.
    corta = Box(100, 50, 10)
    assert fdm_ops.partir_para_cama(corta, nombre="x") == {"x": corta}
    girada = fdm_ops.partir_para_cama(Box(200, 230, 10), cama=(250, 210, 250), nombre="g")
    s = girada["g"].bounding_box().size
    assert (s.X, s.Y) == (pytest.approx(230), pytest.approx(200))


def test_partir_en_dos_ejes():
    pieza = Box(300, 300, 20)
    partes = fdm_ops.partir_para_cama(pieza, nombre="p")
    piezas = [k for k in partes if "_parte" in k]
    assert len(piezas) == 4
    assert len([k for k in partes if "_pasador" in k]) == 8  # 4 cortes x 2
    for k in piezas:
        s = partes[k].bounding_box().size
        assert s.X <= 220 and s.Y <= 220


# ---------------------------------------------------------------- sandbox + check_fdm

_SCRIPT = '''\
from build123d import Box
caja = Box(300, 100, 40) - agujero_gota(8, 320, eje="X")
caja = chaflan_base(caja, 0.4)
resultado = partir_para_cama(caja, nombre="caja")
assert isinstance(AVISOS_FDM, list)
'''


def test_sandbox_inyecta_fdm_ops():
    salida, _ = scripts_runner.ejecutar_script(_SCRIPT, timeout=120)
    try:
        forma = import_step(str(salida))
        assert len(forma.solids()) == 4
        assert forma.bounding_box().size.X > 300  # pasadores al lado
    finally:
        shutil.rmtree(salida.parent, ignore_errors=True)
    # Una variable del usuario con el mismo nombre gana.
    salida, _ = scripts_runner.ejecutar_script(
        "from build123d import Box\nresultado = Box(1, 1, agujero_gota)\n", timeout=60,
        variables={"agujero_gota": 7},
    )
    try:
        assert import_step(str(salida)).bounding_box().size.Z == pytest.approx(7)
    finally:
        shutil.rmtree(salida.parent, ignore_errors=True)


def _crear(script: str) -> dict:
    resp = client.post("/documentos/script", json={"codigo": script},
                       headers={"X-Forja-Token": auth.obtener_token()})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_check_fdm_sugerencias():
    creado = _crear(
        "from build123d import *\n"
        "resultado = {'viga': Box(300, 50, 20) - Cylinder(3, 400, rotation=(0, 90, 0)),\n"
        "             'buena': Pos(0, 100, 0) * (Box(40, 20, 20) - agujero_gota(6, 50, 'X'))}\n"
    )
    data = client.get(f"/documentos/{creado['id']}/fdm").json()
    sug = data["sugerencias"]
    assert {"pieza": "viga", "funcion": "partir_para_cama"}.items() <= sug[0].items()
    funciones = {(s["pieza"], s["funcion"]) for s in sug}
    assert ("viga", "agujero_gota") in funciones
    assert not any(s["pieza"] == "buena" for s in sug)
    limpio = client.get(f"/documentos/{_crear('from build123d import Box' + chr(10) + 'resultado = {\"b\": Box(9, 9, 9)}')['id']}/fdm").json()
    assert "sugerencias" not in limpio
