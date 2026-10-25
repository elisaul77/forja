"""F19 vista explosionada: la función pura `explosion.desplazamientos` es el
oráculo; la vista JS (`app/web/static/js/explosion.js`) debe producir lo mismo
sobre la misma tabla (`tests/fixtures/explosion_offsets.json`).

Los números del caso de tres piezas en línea están calculados a mano en el
comentario de `test_tres_piezas_en_linea_numeros_a_mano`.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

import explosion

FIXTURES = Path(__file__).parent / "fixtures" / "explosion_offsets.json"


def _caja(x0, x1, y0=0.0, y1=10.0, z0=0.0, z1=10.0):
    return [x0, x1, y0, y1, z0, z1]


def _cerca(a, b, tol=1e-6):
    return all(math.isclose(x, y, abs_tol=tol) for x, y in zip(a, b, strict=True))


def test_tres_piezas_en_linea_numeros_a_mano():
    # Cajas de 10 mm en Y/Z; X: A 0..10, B 20..30, C 40..50. Sin articulaciones.
    # Union: 0..50 x 0..10 x 0..10 -> centro (25, 5, 5), diagonal sqrt(2700).
    # B coincide con el centro del conjunto solo en X; su centro (25,5,5) es
    # exactamente el del conjunto, por eso va a +Z.
    # Direccion: A -X (centro a -20), C +X (a +20); B coincide -> +Z (sin NaN).
    # Semiextensiones sobre su direccion: A 5, B 5, C 5.
    # Orden por clave (A 20, B 0, C 20; empate por nombre): B, A, C.
    # G: B 5 | A 5+5+5=15 | C 15+5+5=25 ; Gmax=25.
    # t = factor * 0.5 * diag * G / Gmax, con factor 1 y diag = sqrt(2700):
    #   B = 0.1 * diag = 5.196 ; A = 0.3 * diag = 15.588 ; C = 0.5 * diag = 25.981
    cajas = {"A": _caja(0, 10), "B": _caja(20, 30), "C": _caja(40, 50)}
    diag = math.sqrt(2700.0)
    d = explosion.desplazamientos(cajas, [], 1.0)
    assert _cerca(d["B"], [0.0, 0.0, 0.1 * diag])
    assert _cerca(d["A"], [-0.3 * diag, 0.0, 0.0])
    assert _cerca(d["C"], [0.5 * diag, 0.0, 0.0])


def test_maximo_desplazamiento_es_medio_diagonal_por_factor():
    cajas = {"A": _caja(0, 10), "B": _caja(20, 30), "C": _caja(40, 50)}
    diag = math.sqrt(2700.0)
    for factor in (0.25, 0.5, 1.0):
        d = explosion.desplazamientos(cajas, [], factor)
        maximo = max(math.hypot(*v) for v in d.values())
        assert math.isclose(maximo, factor * 0.5 * diag, rel_tol=1e-9)


def test_factor_cero_no_mueve_nada():
    cajas = {"A": _caja(0, 10), "B": _caja(20, 30)}
    d = explosion.desplazamientos(cajas, [], 0.0)
    assert all(v == [0.0, 0.0, 0.0] for v in d.values())


def test_escala_lineal_con_el_factor():
    cajas = {"A": _caja(0, 10), "B": _caja(20, 30), "C": _caja(40, 50)}
    completo = explosion.desplazamientos(cajas, [], 1.0)
    medio = explosion.desplazamientos(cajas, [], 0.5)
    for nombre in cajas:
        assert _cerca(medio[nombre], [x * 0.5 for x in completo[nombre]])


def test_eje_de_articulacion_fija_el_sentido_alejandose_del_padre():
    # Hijo Q (x 20..30) de P (x 0..10) con deslizamiento sobre -X: el signo
    # se corrige para apuntar lejos del padre, es decir +X.
    cajas = {"P": _caja(0, 10), "Q": _caja(20, 30)}
    art = [{"tipo": "deslizamiento", "padre": "P", "hijo": "Q", "eje": [-1.0, 0.0, 0.0]}]
    d = explosion.desplazamientos(cajas, art, 1.0)
    assert d["Q"][0] > 0 and d["Q"][1] == 0.0 and d["Q"][2] == 0.0
    assert d["P"][0] < 0


def test_eje_perpendicular_usa_el_signo_positivo_del_eje():
    # Eje +Y, perpendicular a la separación: producto punto 0 -> sentido +Y,
    # no el de la regla por centro (que daría +X).
    cajas = {"P": _caja(0, 10), "Q": _caja(20, 30)}
    art = [{"tipo": "giro", "padre": "P", "hijo": "Q", "eje": [0.0, 1.0, 0.0]}]
    d = explosion.desplazamientos(cajas, art, 1.0)
    assert d["Q"][1] > 0 and d["Q"][0] == 0.0 and d["Q"][2] == 0.0


def test_articulacion_fija_no_dirige_la_pieza():
    cajas = {"P": _caja(0, 10), "Q": _caja(20, 30)}
    sin = explosion.desplazamientos(cajas, [], 1.0)
    con_fijo = explosion.desplazamientos(
        cajas, [{"tipo": "fijo", "padre": "P", "hijo": "Q", "eje": [0.0, 1.0, 0.0]}], 1.0)
    assert _cerca(sin["Q"], con_fijo["Q"])


def test_centro_coincidente_va_a_mas_z_sin_nan():
    # Conjunto = caja B; A tiene el mismo centro que el conjunto.
    cajas = {"A": _caja(-5, 5, -5, 5, -5, 5), "B": _caja(-20, 20, -20, 20, -20, 20)}
    d = explosion.desplazamientos(cajas, [], 1.0)
    for vector in d.values():
        assert all(math.isfinite(x) for x in vector)
    assert d["A"][0] == 0.0 and d["A"][1] == 0.0 and d["A"][2] > 0.0


def test_orden_de_entrada_no_cambia_el_resultado():
    cajas = {"A": _caja(0, 10), "B": _caja(20, 30), "C": _caja(40, 50)}
    invertido = dict(reversed(list(cajas.items())))
    assert explosion.desplazamientos(cajas, [], 0.7) == explosion.desplazamientos(invertido, [], 0.7)


@pytest.mark.parametrize("factor", [-0.1, 1.5, math.nan, math.inf, True, "0.5", None])
def test_factor_fuera_de_rango_o_no_numerico_rechazado(factor):
    with pytest.raises(ValueError, match="entre 0 y 1"):
        explosion.validar_factor(factor)


def test_caja_no_finita_o_invertida_rechazada():
    with pytest.raises(ValueError):
        explosion.desplazamientos({"A": _caja(0, math.nan)}, [], 0.5)
    with pytest.raises(ValueError):
        explosion.desplazamientos({"A": _caja(10, 0)}, [], 0.5)


def test_tope_de_piezas():
    cajas = {f"p{i}": _caja(i * 20, i * 20 + 10) for i in range(explosion.MAX_PIEZAS + 1)}
    with pytest.raises(ValueError, match="demasiadas piezas"):
        explosion.desplazamientos(cajas, [], 0.5)


def test_cajas_desde_entradas_une_fragmentos_del_mismo_nombre():
    entradas = [
        {"nombre": "pieza", "indice": 0, "bbox": _caja(0, 5), "volumen": 1.0},
        {"nombre": "pieza", "indice": 1, "bbox": _caja(3, 9, 0, 10, 0, 10), "volumen": 1.0},
        {"nombre": "otra", "indice": 2, "bbox": _caja(20, 30), "volumen": 1.0},
    ]
    cajas = explosion.cajas_desde_entradas(entradas)
    assert cajas["pieza"] == [0, 9, 0, 10, 0, 10]
    assert cajas["otra"] == _caja(20, 30)


def test_tabla_compartida_con_la_vista_js():
    casos = json.loads(FIXTURES.read_text(encoding="utf-8"))
    assert len(casos) >= 5
    for caso in casos:
        obtenido = explosion.desplazamientos(caso["cajas"], caso["articulaciones"], caso["factor"])
        assert set(obtenido) == set(caso["esperado"]), caso["nombre"]
        for nombre, vector in caso["esperado"].items():
            assert _cerca(obtenido[nombre], vector, tol=1e-9), (caso["nombre"], nombre)
