"""Soporte de teléfono, versión simple y paramétrica.

Técnica: el apoyo es una cuña de perfil triangular extruida a lo ancho del
teléfono. La hipotenusa es la superficie donde descansa el teléfono, con la
inclinación que elijas. Un tope en la parte baja evita que resbale. La cuña
se imprime sobre su base plana: no tiene voladizos.
"""
from build123d import *
from math import tan, radians

PARAMETROS = {
    "ancho_telefono": {"valor": 78, "min": 60, "max": 95, "unidad": "mm"},
    "fondo": {"valor": 40, "min": 25, "max": 70, "unidad": "mm"},
    "inclinacion": {"valor": 60, "min": 40, "max": 80, "unidad": "grados"},
    "tope_alto": {"valor": 8, "min": 4, "max": 15, "unidad": "mm"},
}

MARGEN = 3  # mm a cada lado del teléfono


def construir(p):
    ancho = p["ancho_telefono"] + 2 * MARGEN
    fondo, tope = p["fondo"], p["tope_alto"]
    alto = fondo * tan(radians(p["inclinacion"]))
    perfil = Plane.YZ * Polygon((-fondo / 2, 0), (fondo / 2, 0), (fondo / 2, alto), align=None)
    cuna = Pos(-ancho / 2, 0, 0) * extrude(perfil, amount=ancho)
    tope_frontal = Pos(0, -fondo / 2 + 2, 0) * Box(ancho, 4, tope, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return {"soporte": cuna + tope_frontal}
