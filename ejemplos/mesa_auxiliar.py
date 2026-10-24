"""Mesa auxiliar: cinco piezas con nombre, una por cada parte.

Técnica: `resultado` es un diccionario {nombre: Shape}. Cada nombre aparece en
el panel de Piezas y en las indicaciones, así que puedes decir «ajusta la pata
2» en lugar de señalar una cara. Las patas son cilindros de sección cuadrada
sin voladizos, apoyadas en la mesa; el tablero descansa sobre ellas.
"""
from build123d import *

PARAMETROS = {
    "largo": {"valor": 120, "min": 60, "max": 200, "unidad": "mm"},
    "ancho": {"valor": 60, "min": 30, "max": 120, "unidad": "mm"},
    "alto": {"valor": 70, "min": 30, "max": 110, "unidad": "mm"},
    "grosor_tablero": {"valor": 4, "min": 2, "max": 10, "unidad": "mm"},
    "grosor_pata": {"valor": 6, "min": 3, "max": 12, "unidad": "mm"},
}

ABAJO = (Align.CENTER, Align.CENTER, Align.MIN)


def construir(p):
    largo, ancho, alto = p["largo"], p["ancho"], p["alto"]
    grosor_tablero, grosor_pata = p["grosor_tablero"], p["grosor_pata"]
    if grosor_pata * 2 >= min(largo, ancho):
        raise ValueError("las patas no caben en el tablero")
    tablero = Pos(0, 0, alto) * Box(largo, ancho, grosor_tablero, align=ABAJO)
    margen = grosor_pata / 2 + 4
    esquinas = {
        "pata_1": (-largo / 2 + margen, -ancho / 2 + margen),
        "pata_2": (largo / 2 - margen, -ancho / 2 + margen),
        "pata_3": (largo / 2 - margen, ancho / 2 - margen),
        "pata_4": (-largo / 2 + margen, ancho / 2 - margen),
    }
    resultado = {"tablero": tablero}
    for nombre, (x, y) in esquinas.items():
        resultado[nombre] = Pos(x, y, 0) * Box(grosor_pata, grosor_pata, alto, align=ABAJO)
    return resultado
