"""FDM design fixes used INSIDE user scripts (fdm-C).

Pure geometry on build123d, no I/O, no FastAPI: imported by the sandbox
(`sandbox/bootstrap.py` binds these functions to the injected ``PERFIL``)
and by tests. Every function either returns valid geometry or leaves the
input untouched and records an ``aviso`` -- it never breaks the script for a
cosmetic fix.

- ``agujero_gota``: horizontal hole with a 45 degree pointed roof (prints
  without support), diameter compensated as a horizontal hole.
- ``chaflan_base``: chamfer of the bed-face edges (elephant foot).
- ``puente_sacrificio``: one-layer disc that closes a hole above a
  counterbore printed face down (drilled/broken out afterwards).
- ``partir_para_cama``: split a part that doesn't fit the bed with planes,
  joined by pins (compensated holes + separate pins) or dovetails.

Not provided: an automatic "overhangs to 45 degrees" -- rewriting arbitrary
downward faces is not robust on B-rep; use ``agujero_gota``/chamfers by hand.
"""
from __future__ import annotations

import math
from itertools import product
from typing import Any

from build123d import (
    Axis,
    Box,
    Cylinder,
    Face,
    Location,
    Plane,
    Polyline,
    Pos,
    Vector,
    Wire,
    Edge,
    chamfer,
    extrude,
)

import perfil_fdm

CAMA_POR_DEFECTO = (220.0, 220.0, 250.0)
MARGEN_CAMA_MM = 2.0
PARED_MIN_MM = 1.6
_TOL = 1e-6

# Last avisos, also readable by scripts as the global ``AVISOS_FDM``.
AVISOS: list[str] = []


def _avisar(texto: str, avisos: list | None) -> None:
    AVISOS.append(texto)
    if avisos is not None and avisos is not AVISOS:
        avisos.append(texto)


def _perfil(perfil: dict | None) -> dict:
    return perfil if isinstance(perfil, dict) else perfil_fdm.SEMILLA


# ----------------------------------------------------------- agujero_gota

def perfil_gota(r: float) -> Face:
    """Teardrop face in local XY (Y = up): circle of radius ``r`` whose top
    is replaced by two 45 degree tangents meeting at ``(0, r*sqrt(2))``."""
    s = r / math.sqrt(2.0)
    arco = Edge.make_three_point_arc((s, s, 0), (0, -r, 0), (-s, s, 0))
    techo = Polyline((-s, s, 0), (0, r * math.sqrt(2.0), 0), (s, s, 0))
    return Face(Wire([arco, *techo.edges()]))


def agujero_gota(
    d: float,
    largo: float,
    eje: str = "X",
    centro: tuple[float, float, float] = (0.0, 0.0, 0.0),
    perfil: dict | None = None,
    compensar: bool = True,
):
    """Solid to SUBTRACT: horizontal hole of printed diameter ``d`` along
    ``eje`` ("X" or "Y"), ``largo`` long, centred on ``centro``, with a
    pointed roof at 45 degrees (no support). ``compensar``: model the
    profile's horizontal-hole diameter so it prints as ``d``."""
    eje = str(eje).upper()
    if eje not in ("X", "Y"):
        raise ValueError("agujero_gota: eje debe ser 'X' o 'Y' (para agujeros verticales usa agujero(d))")
    if d <= 0 or largo <= 0:
        raise ValueError("agujero_gota: d y largo deben ser > 0")
    dm = perfil_fdm.agujero(d, _perfil(perfil), horizontal=True) if compensar else float(d)
    plano = Plane.YZ if eje == "X" else Plane.XZ  # local Y is global Z in both
    cara = plano.from_local_coords(perfil_gota(dm / 2.0))
    solido = extrude(cara, amount=largo / 2.0, both=True)
    return Pos(*centro) * solido


# ----------------------------------------------------------- chaflan_base

def _zmin(pieza) -> float:
    return float(pieza.bounding_box().min.Z)


def caras_base(pieza, tol: float = 1e-3) -> list:
    """Planar faces facing down that lie on the part's lowest Z."""
    z0 = _zmin(pieza)
    caras = []
    for cara in pieza.faces():
        if cara.geom_type.name != "PLANE":
            continue
        bb = cara.bounding_box()
        if abs(bb.min.Z - z0) > tol or abs(bb.max.Z - z0) > tol:
            continue
        if cara.normal_at().Z < -0.99:
            caras.append(cara)
    return caras


def area_base(pieza) -> float:
    return float(sum(c.area for c in caras_base(pieza)))


def chaflan_base(pieza, alto: float = 0.5, avisos: list | None = None):
    """Chamfer (45 degrees, ``alto`` mm, 0.2-1.0) every edge of the face(s)
    resting on the bed, against elephant foot. On any failure returns the
    piece UNCHANGED and records an aviso (``AVISOS_FDM``); never raises."""
    try:
        alto = float(alto)
        if not 0.2 <= alto <= 1.0:
            _avisar(f"chaflan_base: alto {alto} fuera de 0.2-1.0 mm; pieza sin cambios", avisos)
            return pieza
        caras = caras_base(pieza)
        if not caras:
            _avisar("chaflan_base: no hay cara plana apoyada en la cama; pieza sin cambios", avisos)
            return pieza
        aristas = []
        vistos = set()
        for cara in caras:
            for arista in cara.edges():
                clave = arista.wrapped.__hash__()
                if clave not in vistos:
                    vistos.add(clave)
                    aristas.append(arista)
        nueva = chamfer(aristas, alto)
        vol0 = float(pieza.volume)
        if not nueva.is_valid or not (0 < float(nueva.volume) < vol0 + _TOL):
            raise ValueError("resultado invalido")
        bb0, bb1 = pieza.bounding_box(), nueva.bounding_box()
        if (bb1.size - bb0.size).length > 1e-3:
            raise ValueError("el chaflan cambio la caja de la pieza")
        return nueva
    except Exception as exc:  # noqa: BLE001 - a cosmetic fix never breaks the script
        _avisar(f"chaflan_base: no se pudo aplicar ({type(exc).__name__}: {exc}); pieza sin cambios", avisos)
        return pieza


# ------------------------------------------------------ puente_sacrificio

def puente_sacrificio(
    d: float,
    z: float,
    centro: tuple[float, float] = (0.0, 0.0),
    perfil: dict | None = None,
    capas: int = 1,
    solape: float = 0.4,
):
    """Solid to ADD (fuse) after cutting a counterbore printed FACE DOWN:
    a disc ``capas`` layers thick (profile ``altura_capa``) from ``z`` (the
    counterbore's ceiling) upward, wide enough (``d`` of the through hole +
    ``2*solape``) to anchor in the wall. The slicer bridges it on top of the
    counterbore; drill/poke it out after printing.
    Ex.: ``pieza = pieza - cbore - taladro + puente_sacrificio(3.4, 3)``."""
    if d <= 0 or capas < 1:
        raise ValueError("puente_sacrificio: d > 0 y capas >= 1")
    capa = float(_perfil(perfil).get("altura_capa", 0.2)) * int(capas)
    r = d / 2.0 + max(0.0, float(solape))
    return Pos(centro[0], centro[1], z + capa / 2.0) * Cylinder(r, capa)


# ------------------------------------------------------- partir_para_cama

_EJES = ("X", "Y", "Z")


def _coord(v: Vector, i: int) -> float:
    return (v.X, v.Y, v.Z)[i]


def _caja(mn: list[float], mx: list[float]):
    tam = [b - a for a, b in zip(mn, mx)]
    return Pos(*[(a + b) / 2.0 for a, b in zip(mn, mx)]) * Box(*tam)


def _solidos(forma) -> list:
    try:
        return list(forma.solids())
    except Exception:  # noqa: BLE001
        return []


def _cortes(dims: tuple[float, float, float], util: tuple[float, float, float]) -> list[int]:
    return [max(1, math.ceil(d / u - 1e-9)) for d, u in zip(dims, util)]


def _cilindro_eje(r: float, largo: float, centro: Vector, i: int):
    rot = [(0, 90, 0), (90, 0, 0), (0, 0, 0)][i]
    return Pos(centro) * Cylinder(r, largo, rotation=rot)


def _candidatos(cara: Face, i: int, radio: float, maximo: int = 400) -> list[Vector]:
    """Points of the planar section ``cara`` (normal along axis ``i``) at
    least ``radio`` away from its boundary, on a grid."""
    bb = cara.bounding_box()
    otros = [j for j in range(3) if j != i]
    a0, a1 = _coord(bb.min, otros[0]), _coord(bb.max, otros[0])
    b0, b1 = _coord(bb.min, otros[1]), _coord(bb.max, otros[1])
    area = max((a1 - a0) * (b1 - b0), _TOL)
    paso = max(1.0, math.sqrt(area / maximo))
    na, nb = int((a1 - a0) / paso), int((b1 - b0) / paso)
    plano_c = _coord(bb.min, i)
    borde = cara.outer_wire().edges() + [e for w in cara.inner_wires() for e in w.edges()]
    puntos = []
    from build123d import Vertex

    for ka, kb in product(range(na + 1), range(nb + 1)):
        p = [0.0, 0.0, 0.0]
        p[i] = plano_c
        p[otros[0]] = a0 + (a1 - a0) * (ka + 0.5) / (na + 1)
        p[otros[1]] = b0 + (b1 - b0) * (kb + 0.5) / (nb + 1)
        v = Vector(*p)
        if not cara.is_inside(v, tolerance=1e-3):
            continue
        vert = Vertex(*p)
        if min(vert.distance_to(e) for e in borde) >= radio:
            puntos.append(v)
    return puntos


def _cara_comun(celda, i: int, c: float) -> list[Face]:
    caras = []
    for cara in celda.faces():
        if cara.geom_type.name != "PLANE":
            continue
        bb = cara.bounding_box()
        if abs(_coord(bb.min, i) - c) < 1e-4 and abs(_coord(bb.max, i) - c) < 1e-4:
            caras.append(cara)
    return caras


def _dentro(volumen, celda) -> bool:
    """``volumen`` entirely inside ``celda``'s material."""
    fuera = volumen - celda
    return sum(float(s.volume) for s in _solidos(fuera)) < 1e-3 * max(float(volumen.volume), 1.0)


def partir_para_cama(
    pieza,
    cama: tuple[float, float, float] = CAMA_POR_DEFECTO,
    union: str = "pasadores",
    nombre: str = "pieza",
    perfil: dict | None = None,
    d_pasador: float | None = None,
    prof: float | None = None,
    holgura: str = "eje_presion",
    avisos: list | None = None,
) -> dict[str, Any]:
    """Split ``pieza`` with axis-aligned planes into parts that fit ``cama``
    (X, Y, Z mm; X/Y are swapped by a 90 degree turn in Z if that needs
    fewer parts) and add joints at every cut:

    - ``union="pasadores"``: up to 2 pins per cut face; holes in both parts
      ``prof`` deep, diameter compensated with the profile for ``holgura``
      (``"eje_presion"``/``"eje_deslizante"``; teardrop when horizontal),
      and separate pins (``eje(d_pasador)``, ``2*prof - 1`` long) laid out
      standing beside the parts as ``<nombre>_pasadorN``.
    - ``union="cola_milano"``: a dovetail tongue (15 degrees, profile
      ``holgura_deslizante`` clearance) for X/Y cuts, sliding in Z; Z cuts
      fall back to pins.

    Returns ``{"<nombre>_parte1": Solid, ..., "<nombre>_pasador1": ...}`` in
    their assembled place, ready for ``resultado``. Fits already -> ``{nombre:
    pieza}``. Problems (a cut face too small for a pin...) go to avisos."""
    if union not in ("pasadores", "cola_milano"):
        raise ValueError("partir_para_cama: union debe ser 'pasadores' o 'cola_milano'")
    if holgura not in ("eje_presion", "eje_deslizante"):
        raise ValueError("partir_para_cama: holgura debe ser 'eje_presion' o 'eje_deslizante'")
    cama = tuple(float(c) for c in cama)
    if len(cama) != 3 or min(cama) <= 2 * MARGEN_CAMA_MM:
        raise ValueError("partir_para_cama: cama=(X, Y, Z) en mm")
    perfil = _perfil(perfil)
    sol = _solidos(pieza)
    if not sol:
        raise ValueError("partir_para_cama: la pieza no tiene solidos")

    bb = pieza.bounding_box()
    dims = (bb.size.X, bb.size.Y, bb.size.Z)
    if all(d <= c for d, c in zip(dims, cama)):
        return {nombre: pieza}
    if dims[1] <= cama[0] and dims[0] <= cama[1] and dims[2] <= cama[2]:
        c = bb.center()
        _avisar("partir_para_cama: cabe girando 90 grados en Z; no se partio", avisos)
        return {nombre: Pos(c) * (Location((0, 0, 0), (0, 0, 90)) * (Pos(-c) * pieza))}

    lengua = 0.0
    if union == "cola_milano":
        lengua = min(10.0, 0.25 * min(cama[0], cama[1]))
    util = tuple(c - MARGEN_CAMA_MM - (lengua if k < 2 else 0.0) for k, c in enumerate(cama))
    girar = math.prod(_cortes((dims[1], dims[0], dims[2]), util)) < math.prod(_cortes(dims, util))
    if girar:
        c = bb.center()
        pieza = Pos(c) * (Location((0, 0, 0), (0, 0, 90)) * (Pos(-c) * pieza))
        bb = pieza.bounding_box()
        dims = (bb.size.X, bb.size.Y, bb.size.Z)
        _avisar("partir_para_cama: pieza girada 90 grados en Z para usar menos partes", avisos)
    n = _cortes(dims, util)
    mn = [bb.min.X, bb.min.Y, bb.min.Z]
    paso = [d / k for d, k in zip(dims, n)]
    holgado = 1.0

    celdas: dict[tuple[int, int, int], Any] = {}
    for idx in product(*(range(k) for k in n)):
        lo = [mn[j] + paso[j] * idx[j] - (holgado if idx[j] == 0 else 0.0) for j in range(3)]
        hi = [mn[j] + paso[j] * (idx[j] + 1) + (holgado if idx[j] == n[j] - 1 else 0.0) for j in range(3)]
        trozo = pieza & _caja(lo, hi)
        if _solidos(trozo) and float(trozo.volume) > 1e-6:
            celdas[idx] = trozo

    pasadores: list = []
    d_def = d_pasador
    for idx in sorted(celdas):
        for i in range(3):
            if idx[i] + 1 >= n[i]:
                continue
            vecino = tuple(idx[j] + (1 if j == i else 0) for j in range(3))
            if vecino not in celdas:
                continue
            c = mn[i] + paso[i] * (idx[i] + 1)
            caras = _cara_comun(celdas[idx], i, c)
            if not caras:
                continue
            if union == "cola_milano" and i < 2:
                hecho = _cola_milano(celdas, idx, vecino, i, c, caras, lengua, perfil, avisos)
                if hecho:
                    continue
            _pasadores(celdas, idx, vecino, i, c, caras, paso, perfil, d_def, prof, holgura, pasadores, avisos)

    salida: dict[str, Any] = {}
    for k, idx in enumerate(sorted(celdas), start=1):
        salida[f"{nombre}_parte{k}"] = celdas[idx]
    # Pins standing (axis Z) in a row past the parts, on the bed level.
    bbt = pieza.bounding_box()
    x = bbt.max.X + 10.0
    for k, (dp, largo) in enumerate(pasadores, start=1):
        r = perfil_fdm.eje(dp, perfil) / 2.0
        salida[f"{nombre}_pasador{k}"] = Pos(x + r, bbt.min.Y + r, bbt.min.Z + largo / 2.0) * Cylinder(r, largo)
        x += 2 * r + 5.0
    return salida


def _pasadores(celdas, idx, vecino, i, c, caras, paso, perfil, d_def, prof, holgura, pasadores, avisos) -> None:
    horizontal = i < 2
    cara = max(caras, key=lambda f: f.area)
    bbf = cara.bounding_box()
    otros = [j for j in range(3) if j != i]
    menor = min(_coord(bbf.size, j) for j in otros)
    for dp in ([d_def] if d_def else [5.0, 4.0, 3.0]):
        dh = perfil_fdm.ajuste(holgura, dp, perfil) if not horizontal else perfil_fdm.agujero(
            dp + perfil_fdm.ajuste(holgura, None, perfil), perfil, horizontal=True
        )
        p = float(prof) if prof else 2.0 * dp + 2.0
        p = min(p, paso[i] - PARED_MIN_MM - 0.5)
        if p < dp:
            continue
        radio = dh / 2.0 * (math.sqrt(2.0) if horizontal else 1.0) + PARED_MIN_MM
        if menor < 2 * radio:
            continue
        candidatos = _candidatos(cara, i, radio)
        validos = []
        for v in candidatos:
            agujeros = _agujeros(v, i, dh, p, perfil)
            if all(_dentro(_cilindro_eje(dh / 2 + PARED_MIN_MM, p, _desplazar(v, i, s * p / 2), i), celdas[k])
                   for s, k in ((-1, idx), (1, vecino))):
                validos.append((v, agujeros))
                if len(validos) >= 60:
                    break
        if not validos:
            continue
        elegidos = [validos[0]]
        if len(validos) > 1:
            a, b = max(
                ((x, y) for x in validos for y in validos),
                key=lambda par: (par[0][0] - par[1][0]).length,
            )
            if (a[0] - b[0]).length >= 2 * radio + 2.0:
                elegidos = [a, b]
            else:
                elegidos = [a]
        for _v, (ag_a, ag_b) in elegidos:
            celdas[idx] = celdas[idx] - ag_a
            celdas[vecino] = celdas[vecino] - ag_b
            pasadores.append((dp, round(2 * p - 1.0, 3)))
        return
    _avisar(
        f"partir_para_cama: el corte {_EJES[i]}={c:.1f} no admite pasadores (cara pequena o hueca); "
        "las partes quedan sin union",
        avisos,
    )


def _desplazar(v: Vector, i: int, delta: float) -> Vector:
    p = [v.X, v.Y, v.Z]
    p[i] += delta
    return Vector(*p)


def _agujeros(v: Vector, i: int, dh: float, p: float, perfil: dict):
    """Two blind holes, ``p`` deep each side of the cut (+0.01 overlap)."""
    hecho = []
    for s in (-1, 1):
        centro = _desplazar(v, i, s * (p / 2.0 - 0.005))
        largo = p + 0.01
        if i < 2:
            hecho.append(agujero_gota(dh, largo, eje=_EJES[i], centro=(centro.X, centro.Y, centro.Z),
                                      perfil=perfil, compensar=False))
        else:
            hecho.append(_cilindro_eje(dh / 2.0, largo, centro, i))
    return hecho


def _cola_milano(celdas, idx, vecino, i, c, caras, lengua, perfil, avisos) -> bool:
    """Dovetail from cell ``idx`` into ``vecino`` across plane ``c`` on axis
    ``i`` (X or Y), extruded along Z over the cut face's height."""
    cara = max(caras, key=lambda f: f.area)
    bbf = cara.bounding_box()
    j = 1 - i  # the other horizontal axis
    ancho = _coord(bbf.size, j)
    holg = float(perfil.get("holgura_deslizante", 0.25)) / 2.0
    cuello = min(20.0, 0.3 * ancho)
    tan15 = math.tan(math.radians(15))
    boca = cuello + 2 * lengua * tan15
    if boca + 2 * PARED_MIN_MM > ancho or lengua < 3:
        return False
    centro_j = (_coord(bbf.min, j) + _coord(bbf.max, j)) / 2.0
    z0, z1 = bbf.min.Z, bbf.max.Z

    def prisma(extra: float, prof_extra: float):
        pts = [(0.0, -cuello / 2 - extra), (lengua + prof_extra, -boca / 2 - extra),
               (lengua + prof_extra, boca / 2 + extra), (0.0, cuello / 2 + extra)]
        pts = [(-0.01, -cuello / 2 - extra)] + pts[1:3] + [(-0.01, cuello / 2 + extra)]
        q = []
        for a, b in pts:
            p = [0.0, 0.0, z0 - 0.5]
            p[i] = c + a
            p[j] = centro_j + b
            q.append(tuple(p))
        cara_t = Face(Wire(Polyline(*q, close=True).edges()))
        return extrude(cara_t, amount=(z1 - z0) + 1.0)

    lengueta = prisma(0.0, 0.0) & celdas[vecino]
    if sum(float(s.volume) for s in _solidos(lengueta)) < 0.5 * float(prisma(0.0, 0.0).volume) * 0.9:
        return False
    hueco = prisma(holg, holg)
    nuevo_b = celdas[vecino] - hueco
    nuevo_a = celdas[idx] + lengueta
    if len(_solidos(nuevo_a)) != 1 or not nuevo_a.is_valid or not nuevo_b.is_valid:
        _avisar(f"partir_para_cama: cola de milano fallo en {_EJES[i]}={c:.1f}; se usan pasadores", avisos)
        return False
    celdas[idx], celdas[vecino] = nuevo_a, nuevo_b
    return True
