"""Par de engranajes rectos que engranan.

Técnica: el perfil de cada diente es un trapecio (pie, flanco, cabeza) repetido
alrededor de la circunferencia primitiva; se extruye a un polígono de muchos
lados y se le resta el eje. El segundo piñón se coloca a la distancia entre
centros (suma de radios primitivos) y se desfasa medio paso para que los
dientes entren en los huecos. Las dos piezas van separadas en la mesa.
"""
from build123d import *
from math import cos, sin, pi

PARAMETROS = {
    "modulo": {"valor": 2, "min": 1, "max": 4, "unidad": "mm"},
    "dientes_1": {"valor": 20, "min": 10, "max": 40, "unidad": "dientes"},
    "dientes_2": {"valor": 12, "min": 8, "max": 40, "unidad": "dientes"},
    "grosor": {"valor": 6, "min": 2, "max": 20, "unidad": "mm"},
    "diametro_eje": {"valor": 5, "min": 3, "max": 12, "unidad": "mm"},
}


def _engranaje(modulo, dientes, grosor, diametro_eje, desfase):
    r_primitivo = modulo * dientes / 2
    r_cabeza = r_primitivo + modulo
    r_pie = r_primitivo - 1.25 * modulo
    paso = 2 * pi / dientes
    puntos = []
    for i in range(dientes):
        base = i * paso + desfase
        for frac, radio in ((0.0, r_pie), (0.15, r_primitivo), (0.25, r_cabeza),
                            (0.45, r_cabeza), (0.55, r_primitivo), (0.70, r_pie)):
            angulo = base + frac * paso
            puntos.append((radio * cos(angulo), radio * sin(angulo)))
    perfil = Plane.XY * Polygon(*puntos, align=None)
    cuerpo = extrude(perfil, amount=grosor)
    return cuerpo - Cylinder(diametro_eje / 2, grosor, align=(Align.CENTER, Align.CENTER, Align.MIN)), r_primitivo


def construir(p):
    modulo, z1, z2 = p["modulo"], p["dientes_1"], p["dientes_2"]
    grosor, eje = p["grosor"], p["diametro_eje"]
    raiz_menor = modulo * min(z1, z2) / 2 - 1.25 * modulo
    if eje / 2 + 1 >= raiz_menor:
        raise ValueError("el eje es demasiado grande para el dentado")
    engranaje_1, r1 = _engranaje(modulo, z1, grosor, eje, 0.0)
    engranaje_2, r2 = _engranaje(modulo, z2, grosor, eje, pi / z2)
    return {"engranaje_1": engranaje_1, "engranaje_2": Pos(r1 + r2, 0, 0) * engranaje_2}
