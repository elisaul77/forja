"""Thin adapter around build123d/OpenCascade (ADR-0001).

No other module in Forja should ``import build123d`` or ``import OCP``
directly; solid-modeling operations (STEP import/export, analytic
volume/bbox/validity) go through this module. Mesh-side operations
(tessellation, STL) live in ``mesh.py``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from build123d import Box, Compound, Shape, export_step, import_step


@dataclass
class ShapeInfo:
    volumen: float
    bbox: tuple[float, float, float, float, float, float]
    solidos: int
    valido: bool


def make_box(x: float = 10.0, y: float = 10.0, z: float = 10.0) -> Shape:
    """Build a simple axis-aligned solid box (used by the kernel smoke test)."""
    return Box(x, y, z)


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
