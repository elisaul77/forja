"""Caja con tapa a presión.

Técnica: la caja es un bloque menos su cavidad, así que la pared tiene el
grosor que pidas. La tapa es una placa del tamaño de la boca, menos holgura,
con un tapón que entra dentro de la caja. Las dos piezas van separadas en la
mesa, con la tapa boca arriba, así que no hacen falta soportes.
"""
from build123d import *

PARAMETROS = {
    "largo": {"valor": 60, "min": 30, "max": 150, "unidad": "mm"},
    "ancho": {"valor": 40, "min": 20, "max": 100, "unidad": "mm"},
    "alto": {"valor": 25, "min": 10, "max": 80, "unidad": "mm"},
    "pared": {"valor": 2.0, "min": 1.2, "max": 4, "unidad": "mm"},
    "holgura": {"valor": 0.25, "min": 0.1, "max": 0.5, "unidad": "mm"},
}

ABAJO = (Align.CENTER, Align.CENTER, Align.MIN)


def construir(p):
    largo, ancho, alto = p["largo"], p["ancho"], p["alto"]
    pared, holgura = p["pared"], p["holgura"]
    tapon_largo = largo - 2 * pared - 2 * holgura
    tapon_ancho = ancho - 2 * pared - 2 * holgura
    if tapon_largo <= 0 or tapon_ancho <= 0:
        raise ValueError("la pared y la holgura no dejan hueco para la tapa")

    caja = Box(largo, ancho, alto, align=ABAJO) - Pos(0, 0, pared) * Box(
        largo - 2 * pared, ancho - 2 * pared, alto, align=ABAJO)

    placa = Box(largo - 2 * holgura, ancho - 2 * holgura, pared, align=ABAJO)
    tapon = Pos(0, 0, pared) * Box(tapon_largo, tapon_ancho, 3, align=ABAJO)
    tapa = Pos(largo + 10, 0, 0) * (placa + tapon)
    return {"caja": caja, "tapa": tapa}
