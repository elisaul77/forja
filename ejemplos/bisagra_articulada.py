"""Bisagra de pasador: dos hojas y un pasador, listos para imprimir.

Técnica: cada hoja es una placa con nudillos cilíndricos que alternan entre
las dos (la hoja A tiene los de los extremos y la B el del centro). Cada
nudillo lleva su taladro con holgura y el pasador lo atraviesa sin tocarlo.
La placa de cada hoja se recorta donde está la otra hoja, con la misma
holgura, para que las piezas no se toquen y puedan girar. Van separadas.
"""
from build123d import *

PARAMETROS = {
    "largo": {"valor": 40, "min": 20, "max": 80, "unidad": "mm"},
    "ancho_hoja": {"valor": 18, "min": 10, "max": 30, "unidad": "mm"},
    "grosor": {"valor": 2.5, "min": 1.5, "max": 5, "unidad": "mm"},
    "diametro_pasador": {"valor": 3, "min": 2, "max": 6, "unidad": "mm"},
    "holgura": {"valor": 0.3, "min": 0.1, "max": 0.6, "unidad": "mm"},
}


def construir(p):
    largo, ancho, grosor = p["largo"], p["ancho_hoja"], p["grosor"]
    diametro, holgura = p["diametro_pasador"], p["holgura"]
    radio_pasador = diametro / 2
    radio = radio_pasador + grosor + 2 * holgura  # radio exterior del nudillo
    tercio = largo / 3
    seg = tercio - holgura  # cada nudillo queda separado del vecino por `holgura`
    centro = tercio + holgura  # hueco central que deja la hoja contraria
    franja = 2 * radio + 2 * holgura  # ancho en Y que ocupan los nudillos

    def bloque(x0, x1, y0, y1):
        return Pos((x0 + x1) / 2, (y0 + y1) / 2, grosor / 2) * Box(x1 - x0, y1 - y0, grosor + 2)

    def nudillo(xc):
        return Pos(xc, 0, radio) * Cylinder(radio, seg, rotation=(0, 90, 0))

    def taladro(r):
        return Pos(0, 0, radio) * Cylinder(r, largo + 2, rotation=(0, 90, 0))

    placa_a = Pos(0, ancho / 2, grosor / 2) * Box(largo, ancho, grosor)
    placa_a = placa_a - bloque(-centro / 2, centro / 2, -franja / 2, franja / 2)
    placa_b = Pos(0, -ancho / 2, grosor / 2) * Box(largo, ancho, grosor)
    placa_b = placa_b - bloque(-largo / 2 - 1, -centro / 2, -franja / 2, franja / 2)
    placa_b = placa_b - bloque(centro / 2, largo / 2 + 1, -franja / 2, franja / 2)

    hoja_a = placa_a + nudillo(-tercio) + nudillo(tercio)
    hoja_b = placa_b + nudillo(0)
    hoja_a = hoja_a - taladro(radio_pasador + holgura)
    hoja_b = hoja_b - taladro(radio_pasador + holgura)
    pasador = Pos(0, 0, radio) * Cylinder(radio_pasador, largo, rotation=(0, 90, 0))
    assert hoja_a.is_valid and hoja_b.is_valid and pasador.is_valid
    return {"hoja_a": hoja_a, "hoja_b": hoja_b, "pasador": pasador}
