"""Thin adapter around build123d/OpenCascade (ADR-0001).

No other module in Forja should ``import build123d`` or ``import OCP``
directly; solid-modeling operations (STEP import/export, analytic
volume/bbox/validity) go through this module. Mesh-side operations
(tessellation, STL) live in ``mesh.py``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from build123d import (
    Box,
    Compound,
    Cone,
    Cylinder,
    GeomType,
    Shape,
    Sphere,
    Torus,
    Wedge,
    export_step,
    import_step,
)


@dataclass
class ShapeInfo:
    volumen: float
    bbox: tuple[float, float, float, float, float, float]
    solidos: int
    valido: bool


def make_box(x: float = 10.0, y: float = 10.0, z: float = 10.0) -> Shape:
    """Build a simple axis-aligned solid box (used by the kernel smoke test)."""
    return Box(x, y, z)


def forma_primitiva(tipo: str, dims: tuple[float, ...]) -> Shape:
    """Útil centrado en el origen con dimensiones ya resueltas en mm:
    box (x, y, z) · cylinder (radio, altura) · sphere (radio) ·
    cone (radio de la base, altura; punta) · torus (radio mayor, radio del tubo) ·
    wedge (x, y, z; cuña de cateto x y cateto z) · tube (radio exterior, radio
    interior, altura)."""
    if tipo == "box":
        return Box(*dims)
    if tipo == "cylinder":
        return Cylinder(dims[0], dims[1])
    if tipo == "sphere":
        return Sphere(dims[0])
    if tipo == "cone":
        return Cone(dims[0], 0, dims[1])
    if tipo == "torus":
        return Torus(dims[0], dims[1])
    if tipo == "wedge":
        return Wedge(dims[0], dims[1], dims[2], 0, 0, 0, dims[2])
    if tipo == "tube":
        return Cylinder(dims[0], dims[2]) - Cylinder(dims[1], dims[2])
    raise ValueError(f"Forma de útil desconocida: {tipo}")


def escalar(forma: Shape, factor: tuple[float, float, float], centro: tuple[float, float, float]) -> Shape:
    """Escala por eje en torno a `centro`, como B-rep nuevo (copia).

    `Shape.scale` de build123d da el volumen correcto en memoria pero al
    exportar a STEP se pierde (el archivo conserva la geometría original),
    así que la transformación se hace con BRepBuilderAPI_GTransform."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_GTransform
    from OCP.gp import gp_GTrsf, gp_Mat, gp_XYZ

    fx, fy, fz = factor
    trsf = gp_GTrsf()
    trsf.SetVectorialPart(gp_Mat(fx, 0, 0, 0, fy, 0, 0, 0, fz))
    cx, cy, cz = centro
    trsf.SetTranslationPart(gp_XYZ(cx - fx * cx, cy - fy * cy, cz - fz * cz))
    escalada = Shape.cast(BRepBuilderAPI_GTransform(forma.wrapped, trsf, True).Shape())
    if not escalada.is_valid:
        raise ValueError("La escala produjo CAD inválido")
    return escalada


def volumen_preciso(shape: Shape, eps: float = 1e-3) -> float:
    """Volumen en mm3 de ``shape`` integrado sobre el B-rep con cuadratura adaptativa
    de OCC (``BRepGProp``, ``eps`` = tolerancia de integración), sin teselar (valor absoluto).

    ``.volume`` da ~0,9 % alto en un cilindro escalado en no uniforme (GTransform deja
    caras BSPLINE); con ``eps`` 1e-3 esta integral queda en ~0,004 % del valor analítico,
    en fracciones de milisegundo en el cilindro y ~0,4 s en un loft de 240 secciones.
    Con 1e-5 el error baja a ~0,0002 % pero el loft tarda ~3 s por medida (ADR-0026)."""
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    propiedades = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape.wrapped, propiedades, eps, False)
    return float(abs(propiedades.Mass()))


def analyze(shape: Shape) -> ShapeInfo:
    """Analytic volume/bbox/solid-count/validity straight from the B-rep."""
    bb = shape.bounding_box()
    return ShapeInfo(
        volumen=shape.volume,
        bbox=(bb.min.X, bb.max.X, bb.min.Y, bb.max.Y, bb.min.Z, bb.max.Z),
        solidos=len(shape.solids()),
        valido=shape.is_valid,
    )


def combinar_nombrados(nombrados: dict[str, Shape]) -> Shape:
    """Merge a script's ``{nombre: Shape}`` result into one Compound,
    tagging each child's ``.label`` with its dict key (Phase 5A named
    solids). build123d's STEP exporter DOES write ``.label`` as the real
    STEP PRODUCT name (see ``exporters3d.py``'s XCAF ``set_name_and_color``)
    — the exported file genuinely carries e.g. "silla_1"/"mesa" as product
    names, valuable for anyone opening it in another CAD tool.

    That guarantee stops at export, though: it is NOT what Forja's own
    bookkeeping relies on to know the names back. ``export_step`` also
    always turns on OCCT's auto-naming (hardcoded ``auto_naming=True``, not
    something this module controls), which fills in a generic placeholder
    label (e.g. ``"COMPOUND"``) for every child that never got an explicit
    one — making a *re-imported* ``.label`` unable to tell "genuinely named
    by the user" from "OCCT filled in a placeholder" (verified against a
    real multi-part script with no explicit naming: every child came back
    labelled ``"COMPOUND"``). So the caller (``scripts_runner``'s sandboxed
    subprocess) never reads labels back either — it passes the *original*
    ordered dict-key list forward through its own explicit, re-validated
    side channel (see ``scripts_runner._leer_nombres_confiables`` and
    ``app/solids.py``'s module docstring for the full rationale). Caller is
    expected to have already validated the names via
    ``solids.validar_nombres`` before calling this.
    """
    hijos = []
    for nombre, figura in nombrados.items():
        figura.label = nombre
        hijos.append(figura)
    return Compound(children=hijos)


def export_to_step(shape: Shape, path: str | Path) -> None:
    export_step(shape, str(path))


def import_from_step(path: str | Path) -> Shape:
    return import_step(str(path))


_NORMALES_EJE = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}


def seccion_plana(
    forma: Shape, eje: str, valor: float, deflexion: float = 0.1
) -> list[list[tuple[float, float, float]]]:
    """Planar section of `forma` by the axis-aligned plane ``eje = valor``
    (Phase 5E fix-review, `percibir`'s `cortes` and support test), as a list
    of 3D polylines — one per section edge, discretized with at most
    `deflexion` mm chord error (straight edges stay 2 points). The edges of
    a closed solid's section form closed loops, so an even-odd crossing
    count over ALL returned segments together classifies in-plane points
    correctly (holes included) without having to assemble the loops.

    Computed once per solid with OpenCascade's `BRepAlgoAPI_Section`
    against an infinite plane — far cheaper than classifying a grid of
    points one by one with `is_inside`. Raises ``RuntimeError`` if OCCT
    reports the section failed (caller falls back to point sampling)."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
    from OCP.GCPnts import GCPnts_TangentialDeflection
    from OCP.gp import gp_Dir, gp_Pln, gp_Pnt
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    nx, ny, nz = _NORMALES_EJE[eje]
    plano = gp_Pln(gp_Pnt(nx * valor, ny * valor, nz * valor), gp_Dir(nx, ny, nz))
    seccion = BRepAlgoAPI_Section(forma.wrapped, plano, False)
    seccion.ComputePCurveOn1(False)
    seccion.Approximation(False)
    seccion.Build()
    if not seccion.IsDone():
        raise RuntimeError("la seccion plana de OpenCascade fallo")

    polilineas: list[list[tuple[float, float, float]]] = []
    explorador = TopExp_Explorer(seccion.Shape(), TopAbs_EDGE)
    while explorador.More():
        arista = TopoDS.Edge(explorador.Current())
        explorador.Next()
        if BRep_Tool.Degenerated_s(arista):
            continue
        curva = BRepAdaptor_Curve(arista)
        muestreo = GCPnts_TangentialDeflection(curva, 0.2, max(deflexion, 1e-4))
        puntos = []
        for i in range(1, muestreo.NbPoints() + 1):
            p = muestreo.Value(i)
            puntos.append((p.X(), p.Y(), p.Z()))
        if len(puntos) >= 2:
            polilineas.append(puntos)
    return polilineas


# ---------------------------------------------------------------- F23b (ADR-0025)
# Feature recognition and the primitives behind the live dimensions of imported
# documents. OCC-level code stays in this adapter (ADR-0001); `app/cotas_geometria.py`
# composes these helpers and owns the rules (what is recognised, what is verified).

EJES = ("x", "y", "z")
TOLERANCIA_GIRO = 1e-3  # a hole is a full turn of cylinder faces, up to this slack (radians)
TOLERANCIA_MARGEN_MM = 0.01  # how far beyond a face a point must lie to classify as outside


def _global_axis(direccion: tuple[float, float, float]) -> str | None:
    for eje, idx in zip(EJES, range(3)):
        if abs(abs(direccion[idx]) - 1.0) < 1e-6:
            return eje
    return None


def plane_axes(eje: str) -> tuple[str, str]:
    """The two in-plane axes of a view looking along `eje`, in order."""
    return tuple(e for e in EJES if e != eje)  # type: ignore[return-value]


def inside_material(shape: Shape, punto: tuple[float, float, float]) -> bool:
    """True when `punto` lies strictly inside some solid of `shape`."""
    return any(_point_in_solid(solido, punto) for solido in shape.solids())


def global_axis_holes(shape: Shape) -> tuple[list[dict], int]:
    """Closed holes of `shape`: full-turn cylinder faces in empty space, with an
    axis parallel to a global axis. Returns ``(agujeros, omitidas)``. Each hole:
    ``{eje, d, c, loc, dir, t0, t1}``: `c` is the raw centre in the two in-plane
    axes (``plane_axes(eje)`` order), `loc` a point on the axis, `dir` its unit
    direction, and ``[t0, t1]`` the axial extent measured from `loc`. Cylinders
    whose axis is not global are counted in `omitidas`, never guessed. A partial
    cylinder (a fillet or a round) covers less than a turn and is not a hole."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepTools import BRepTools
    from OCP.GeomAbs import GeomAbs_Cylinder

    solidos = list(shape.solids())
    grupos: dict[tuple, dict] = {}
    for cara in shape.faces():
        if cara.geom_type != GeomType.CYLINDER:
            continue
        superficie = BRepAdaptor_Surface(cara.wrapped)
        if superficie.GetType() != GeomAbs_Cylinder:
            continue
        cylinder_solid = superficie.Cylinder()
        eje_ax = cylinder_solid.Axis()
        d = eje_ax.Direction()
        loc = eje_ax.Location()
        dvec = (d.X(), d.Y(), d.Z())
        # A hole has no material on its axis at the face's height; a boss does.
        c = cara.center()
        t = (c.X - loc.X()) * dvec[0] + (c.Y - loc.Y()) * dvec[1] + (c.Z - loc.Z()) * dvec[2]
        punto = (loc.X() + t * dvec[0], loc.Y() + t * dvec[1], loc.Z() + t * dvec[2])
        if any(_point_in_solid(s, punto) for s in solidos):
            continue
        radio = cylinder_solid.Radius()
        eje = _global_axis(dvec)
        if eje is None:
            clave: tuple = (
                "oblicuo", round(dvec[0], 3), round(dvec[1], 3), round(dvec[2], 3),
                round(loc.X(), 3), round(loc.Y(), 3), round(loc.Z(), 3), round(radio, 3),
            )
            entrada = grupos.setdefault(clave, {"eje": None, "giro": 0.0, "t0": math.inf, "t1": -math.inf})
        else:
            coord = {"x": loc.X(), "y": loc.Y(), "z": loc.Z()}
            ejes = plane_axes(eje)
            c1, c2 = coord[ejes[0]], coord[ejes[1]]
            clave = (eje, round(c1, 3), round(c2, 3), round(2 * radio, 3))
            entrada = grupos.setdefault(clave, {
                "eje": eje, "d": round(2 * radio, 3), "c": (c1, c2), "giro": 0.0,
                "loc": (loc.X(), loc.Y(), loc.Z()), "dir": dvec, "t0": math.inf, "t1": -math.inf,
            })
        umin, umax, vmin, vmax = BRepTools.UVBounds_s(cara.wrapped)
        entrada["giro"] += float(umax - umin)
        entrada["t0"] = min(entrada["t0"], float(vmin))
        entrada["t1"] = max(entrada["t1"], float(vmax))
    agujeros: list[dict] = []
    omitidas = 0
    for entrada in grupos.values():
        if entrada["giro"] < 2.0 * math.pi - TOLERANCIA_GIRO:
            continue  # a fillet or a round: part of a turn, not a hole
        if entrada["eje"] is None:
            omitidas += 1
            continue
        agujeros.append({k: entrada[k] for k in ("eje", "d", "c", "loc", "dir", "t0", "t1")})
    return agujeros, omitidas


def _point_in_solid(solido: Shape, punto) -> bool:
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.gp import gp_Pnt
    from OCP.TopAbs import TopAbs_IN

    return BRepClass3d_SolidClassifier(solido.wrapped, gp_Pnt(*punto), 1e-6).State() == TopAbs_IN


def point_on_axis(loc: tuple[float, float, float], direccion: tuple[float, float, float], t: float):
    return tuple(loc[i] + t * direccion[i] for i in range(3))


def cylinder_solid(base: tuple[float, float, float], direccion: tuple[float, float, float], radio: float, longitud: float) -> Shape:
    """Solid cylinder of `radio` from `base` along the unit `direccion`, `longitud` long."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    eje = gp_Ax2(gp_Pnt(*base), gp_Dir(*direccion))
    return Shape.cast(BRepPrimAPI_MakeCylinder(eje, radio, longitud).Shape())


def scale_axis(shape: Shape, eje: str, factor: float, origen: float) -> Shape:
    """Non-uniform scale of `shape` along `eje` only, about the plane ``eje = origen``
    (the bbox minimum keeps its place). Other axes are untouched."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_GTransform
    from OCP.gp import gp_GTrsf, gp_Mat, gp_XYZ

    idx = EJES.index(eje)
    matriz = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    matriz[idx * 4] = factor
    desplazamiento = [0.0, 0.0, 0.0]
    desplazamiento[idx] = origen * (1.0 - factor)
    trsf = gp_GTrsf(gp_Mat(*matriz), gp_XYZ(*desplazamiento))
    return Shape.cast(BRepBuilderAPI_GTransform(shape.wrapped, trsf, True).Shape())


def parallel_planar_pairs(shape: Shape, eje: str, caras_max: int) -> list[dict]:
    """Pairs of planar faces facing each other across material along `eje`:
    ``[{lo, hi}]`` where the lower face (outward normal -eje) sits at `lo` and the
    upper one (outward +eje) at `hi`. Only pairs whose projections overlap and
    whose overlap holds material (a 3x3 probe grid, so a through hole in the
    overlap does not hide the slab) are returned. Caps the work: more than
    `caras_max` planar faces on the axis raises `ValueError`."""
    idx = EJES.index(eje)
    otros = [i for i in range(3) if i != idx]
    inferiores, superiores = [], []
    for cara in shape.faces():
        if cara.geom_type != GeomType.PLANE:
            continue
        normal = cara.normal_at()
        comp = (normal.X, normal.Y, normal.Z)[idx]
        if abs(abs(comp) - 1.0) > 1e-6:
            continue
        bb = cara.bounding_box()
        marco = ((bb.min.X, bb.max.X), (bb.min.Y, bb.max.Y), (bb.min.Z, bb.max.Z))
        posicion = (cara.center().X, cara.center().Y, cara.center().Z)[idx]
        (inferiores if comp < 0 else superiores).append((posicion, marco))
        if len(inferiores) + len(superiores) > caras_max:
            raise ValueError(f"demasiadas caras planas ({caras_max} como maximo)")
    pares = []
    for lo, marco_lo in inferiores:
        for hi, marco_hi in superiores:
            if hi - lo <= 1e-3:
                continue
            solape = []
            for i in otros:
                a = max(marco_lo[i][0], marco_hi[i][0])
                b = min(marco_lo[i][1], marco_hi[i][1])
                if b - a <= 1e-3:
                    break
                solape.append((a, b))
            if len(solape) != 2:
                continue
            medio = (lo + hi) / 2.0
            if any(
                inside_material(shape, _probe_point(idx, medio, fx, fy))
                for fx in _probe_grid(solape[0]) for fy in _probe_grid(solape[1])
            ):
                pares.append({"lo": lo, "hi": hi})
    return sorted(pares, key=lambda p: (p["lo"], p["hi"]))


def _probe_grid(intervalo: tuple[float, float]) -> list[float]:
    a, b = intervalo
    return [a + (b - a) * k / 4.0 for k in (1, 2, 3)]


def _probe_point(idx: int, valor_eje: float, a: float, b: float) -> tuple[float, float, float]:
    otros = [i for i in range(3) if i != idx]
    punto = [0.0, 0.0, 0.0]
    punto[idx] = valor_eje
    punto[otros[0]] = a
    punto[otros[1]] = b
    return tuple(punto)


def faces_on_plane(shape: Shape, eje: str, valor: float) -> list[Any]:
    """Planar faces of `shape` lying on the plane ``eje = valor`` whose outward
    normal is +eje (the top of the material below that plane)."""
    idx = EJES.index(eje)
    hallados = []
    for cara in shape.faces():
        if cara.geom_type != GeomType.PLANE:
            continue
        normal = cara.normal_at()
        if (normal.X, normal.Y, normal.Z)[idx] < 1.0 - 1e-6:
            continue
        if abs((cara.center().X, cara.center().Y, cara.center().Z)[idx] - valor) <= 1e-6:
            hallados.append(cara)
    return hallados


def halfspace(shape: Shape, eje: str, valor: float, por_encima: bool) -> Shape:
    """`shape` clipped to the half-space ``eje <= valor`` (or ``>= valor``)."""
    from build123d import Box, Pos

    bb = shape.bounding_box()
    margen = 1.0
    lo = [bb.min.X - margen, bb.min.Y - margen, bb.min.Z - margen]
    hi = [bb.max.X + margen, bb.max.Y + margen, bb.max.Z + margen]
    idx = EJES.index(eje)
    if por_encima:
        lo[idx] = valor
    else:
        hi[idx] = valor
    tam = [hi[i] - lo[i] for i in range(3)]
    centro = [(hi[i] + lo[i]) / 2.0 for i in range(3)]
    return shape & (Pos(*centro) * Box(*tam))


def translate_along(shape: Shape, eje: str, delta: float) -> Shape:
    from build123d import Pos

    vec = [0.0, 0.0, 0.0]
    vec[EJES.index(eje)] = delta
    return Pos(*vec) * shape


def union_extruded(shape: Shape, caras: list[Any], delta: float, eje: str) -> Shape:
    """`shape` plus the prisms extruded from the faces `caras` by `delta` along `eje`."""
    from build123d import Solid, Vector

    vec = [0.0, 0.0, 0.0]
    vec[EJES.index(eje)] = delta
    prismas = [Solid.extrude(cara, Vector(*vec)) for cara in caras]
    return shape.fuse(*prismas)


def area_of(caras: list[Any]) -> float:
    return float(sum(cara.area for cara in caras))


# ---------------- Fase 24d: rasgos de modificación (ADR-0029) ----------------

_NORMAL_EJE = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}
# x_dir perpendicular a la normal del eje: build123d exige un marco completo.
_X_EJE = {"x": (0.0, 1.0, 0.0), "y": (1.0, 0.0, 0.0), "z": (1.0, 0.0, 0.0)}


def plano_de_eje(eje: str, desplazamiento: float):
    """Plano perpendicular a `eje` (x|y|z) desplazado `desplazamiento` mm
    a lo largo de ese mismo eje."""
    from build123d import Plane

    normal = _NORMAL_EJE[eje]
    origen = tuple(desplazamiento * c for c in normal)
    return Plane(origin=origen, x_dir=_X_EJE[eje], z_dir=normal)


def filete_aristas(solido: Shape, aristas: list[Any], radio: float) -> Shape:
    return solido.fillet(radio, aristas)


def chaflan_aristas(solido: Shape, aristas: list[Any], longitud: float) -> Shape:
    return solido.chamfer(longitud, None, aristas)


def vaciar_caras(solido: Shape, caras: list[Any], espesor: float) -> Shape:
    """Vacía el sólido hacia dentro dejando `caras` abiertas (offset negativo)."""
    from build123d import offset

    return offset(solido, amount=-espesor, openings=caras)


def desfasar_cara(solido: Shape, cara: Any, distancia: float) -> Shape:
    """Empuja (`distancia` > 0, añade material) o tira (< 0, quita material)
    una cara plana a lo largo de su normal exterior."""
    from build123d import Solid, Vector

    normal = cara.normal_at(cara.center()) * distancia
    prisma = Solid.extrude(cara, Vector(normal.X, normal.Y, normal.Z))
    return solido.fuse(prisma) if distancia > 0 else solido.cut(prisma)


def espejar(solido: Shape, eje: str, desplazamiento: float) -> Shape:
    return solido.mirror(plano_de_eje(eje, desplazamiento))


def dividir_por_plano(solido: Shape, eje: str, desplazamiento: float) -> Shape:
    from build123d import Keep, split

    return split(solido, bisect_by=plano_de_eje(eje, desplazamiento), keep=Keep.BOTH)


def trasladar_eje(solido: Shape, eje: str, distancia: float) -> Shape:
    from build123d import Pos

    return Pos(*(c * distancia for c in _NORMAL_EJE[eje])) * solido


def girar_eje(solido: Shape, eje: str, grados: float) -> Shape:
    """Gira alrededor del eje global que pasa por el origen."""
    from build123d import Axis

    return solido.rotate(Axis((0, 0, 0), _NORMAL_EJE[eje]), grados)


def unir_formas(base: Shape, otras: list[Shape]) -> Shape:
    return base.fuse(*otras) if otras else base


def compuesto(solidos: list[Any]) -> Shape:
    """Compound de sólidos (sin fusionarlos)."""
    return Compound(children=list(solidos))
