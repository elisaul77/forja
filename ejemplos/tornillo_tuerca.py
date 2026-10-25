"""Tornillo y tuerca roscados, paramétricos.

Técnica: la rosca es un barrido helicoidal (`sweep` a lo largo de una `Helix`)
de un perfil triangular, recortado por un cilindro envolvente. La tuerca es un
hexágono al que se le resta el mismo filete con holgura radial, así que
encaja sin apretarse en la impresora. Ambas piezas van separadas en la mesa.
"""
from build123d import *
from math import sqrt

PARAMETROS = {
    "diametro": {"valor": 8, "min": 6, "max": 20, "unidad": "mm"},
    "longitud": {"valor": 30, "min": 12, "max": 60, "unidad": "mm"},
    "paso": {"valor": 1.25, "min": 1, "max": 3, "unidad": "mm"},
    "holgura_radial": {"valor": 0.2, "min": 0.05, "max": 0.4, "unidad": "mm"},
}


def construir(p):
    d, length, pitch, clearance = (p[k] for k in ("diametro", "longitud", "paso", "holgura_radial"))
    radius = d / 2
    depth = 0.60 * pitch
    root = radius - depth
    head_h = 0.675 * d
    nut_h = 0.8125 * d
    across = 1.625 * d

    def threaded(extra):
        path = Helix(pitch, length + 2 * pitch, root + extra)
        profile = Plane.XZ * Polygon(
            (root + extra - 0.04, -0.44 * pitch),
            (radius + extra, -0.09 * pitch),
            (radius + extra, 0.09 * pitch),
            (root + extra - 0.04, 0.44 * pitch), align=None)
        ridge = Pos(0, 0, -pitch) * sweep(profile, path=path, is_frenet=True)
        core = Cylinder(root + extra, length, align=(Align.CENTER, Align.CENTER, Align.MIN))
        envelope = Cylinder(radius + extra + 0.01, length, align=(Align.CENTER, Align.CENTER, Align.MIN))
        return core + (ridge & envelope)

    head = extrude(RegularPolygon(across / sqrt(3), 6), amount=head_h)
    head = chamfer(head.edges().filter_by(Axis.Z, reverse=True), length=0.45)
    bolt = head + Pos(0, 0, head_h - 0.01) * threaded(0)
    nut = extrude(RegularPolygon(across / sqrt(3), 6), amount=nut_h)
    nut = chamfer(nut.edges().filter_by(Axis.Z, reverse=True), length=0.45)
    nut = nut - threaded(clearance)
    nut = nut - Cone(radius + clearance + 0.35, root + clearance, 0.8, align=(Align.CENTER, Align.CENTER, Align.MIN))
    nut = nut - Pos(0, 0, nut_h - 0.8) * Cone(root + clearance, radius + clearance + 0.35, 0.8, align=(Align.CENTER, Align.CENTER, Align.MIN))
    assert bolt.is_valid and nut.is_valid
    assert len(bolt.solids()) == 1 and len(nut.solids()) == 1
    return {"tornillo": bolt, "tuerca": Pos(across + 9, 0, 0) * nut}
