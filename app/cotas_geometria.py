"""Live dimensions of imported documents (F23b, ADR-0025).

A dimension of an imported STEP/STL is editable when a recognised feature
produces it:

- a group of closed cylinders (a hole diameter) -> ``rellenar_y_taladrar``:
  fill each hole with a solid cylinder of the old diameter and drill the new
  one on the same axis and depth (`REDRILL`);
- two parallel planar faces facing each other across material (a distance)
  -> ``mover_cara``: split at mid-thickness, translate the upper half and
  close the gap with the extruded section; the translation is resolved by
  bisection until the re-measured distance equals the target (`STRETCH`);
- an overall size per axis (a medida total) -> ``escalar_eje``: a non-uniform
  scale of that axis only (`SCALE`).

Everything is computed in memory first. `apply_edit` returns the new shape or
raises `EditRejected`; the caller writes nothing on a refusal. The result is
verified (valid, same solid count, expected volume change, bbox moved only
along the edited axis, re-measured value within 0.01 mm) before it is returned.

Pure geometry: no HTTP, no files. Work draws from one `Presupuesto`
(app/checks/barrido.py), and the caps below bound the size of what is edited.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import trimesh
from build123d import Compound

from checks.barrido import Presupuesto, _Agotado
from cotas import (
    OPERATION_REDRILL,
    OPERATION_SCALE_AXIS,
    OPERATION_STRETCH,
    REASON_SEVERAL_SOLIDS,
)
from kernel import b123d_kernel, mesh as km

EJES = b123d_kernel.EJES
VISTA_DE_EJE = {"x": "frontal", "y": "superior", "z": "frontal"}

# Caps (security): size of what can be edited and time of one edit.
MAX_BREP_FACES = 5000
MAX_MESH_FACES = 1_000_000
MAX_HOLES_PER_EDIT = 40
BUDGET_OPERATIONS = 200  # booleans and probes per edit (shared `Presupuesto`)
BUDGET_SECONDS = 30.0

TOLERANCE_MM = 0.01  # re-measured value vs requested value, and bbox drift
VOLUME_TOLERANCE_REL = 0.01  # volume change vs its analytic value
WALL_MARGIN_MM = 0.1  # material that must stay around a re-drilled hole
WALL_SAMPLES = 24
WALL_PROBE_MM = 0.2  # a point inside the wall, to find the owning solid
END_PROBE_MM = 0.01  # classifies the hole mouth: air beyond it means open
OVERSHOOT_MM = 1.0  # the drill runs past an open mouth (into air only)
BISECT_STEPS = 40
REASON_WALL = "el agujero rompe la pared"


class EditRejected(Exception):
    """A refused edit: nothing has been written. `razon` is set for the
    wall-breaking case, which the viewer names explicitly."""

    def __init__(self, mensaje: str, razon: str | None = None) -> None:
        super().__init__(mensaje)
        self.razon = razon


@dataclass
class Edit:
    """A verified result. `forma` is the new B-rep or mesh; `editada` is the
    per-dimension report; `escala` (meshes only) is the `(eje, factor, origen)`
    of the scale, so the caller can apply it to each part of a multi-part mesh."""

    forma: Any
    editada: dict[str, Any]
    escala: tuple[str, float, float] | None = None


@dataclass
class _Probe:
    delta: float
    forma: Any
    area: float
    medido: float

    def residuo(self, valor: float) -> float:
        return self.medido - valor


# ---------------------------------------------------------------- listing


def parallel_face_descriptors(forma: Any) -> tuple[list[dict[str, Any]], str | None]:
    """One `distancia` descriptor per pair of parallel planar faces (B-rep only).
    Returns ``(descriptores, aviso)``; `aviso` says why none could be recognised
    when the shape has more than `MAX_BREP_FACES` planar faces on an axis."""
    descriptores: list[dict[str, Any]] = []
    try:
        for eje in EJES:
            for par in b123d_kernel.parallel_planar_pairs(forma, eje, MAX_BREP_FACES):
                valor = round(par["hi"] - par["lo"], 3)
                descriptores.append({
                    "id": f"distancia:{eje}:{valor:.3f}:{par['lo']:.3f}",
                    "tipo": "distancia",
                    "valor_mm": valor,
                    "eje": eje,
                    "vista": VISTA_DE_EJE[eje],
                    "origen": "reconocido",
                    "parametro": None,
                    "operacion": None,
                    "lo": round(par["lo"], 3),
                    "hi": round(par["hi"], 3),
                })
    except ValueError as exc:
        return [], f"distancias no reconocidas: {exc}"
    return descriptores, None


# ---------------------------------------------------------------- entry point


def apply_edit(forma: Any, cota: dict[str, Any], valor: float, *, malla: bool) -> Edit:
    """Apply one live-dimension edit (already validated by `cotas.resolve_geometric`).
    Raises `EditRejected` with a Spanish message; any other failure of the kernel is
    reported as a refusal too, never swallowed."""
    presupuesto = Presupuesto(evaluaciones=BUDGET_OPERATIONS, plazo_s=BUDGET_SECONDS)
    try:
        if malla:
            return _scale_mesh(forma, cota, valor, presupuesto)
        operacion = cota.get("operacion")
        if operacion == OPERATION_SCALE_AXIS:
            return _scale_brep(forma, cota, valor, presupuesto)
        if operacion == OPERATION_REDRILL:
            return _redrill(forma, cota, valor, presupuesto)
        if operacion == OPERATION_STRETCH:
            return _stretch(forma, cota, valor, presupuesto)
        raise EditRejected(f"la cota no admite edición geométrica: {operacion}")
    except _Agotado as exc:
        raise EditRejected(
            f"presupuesto de cálculo agotado ({exc}): la pieza es demasiado costosa para esta edición"
        ) from exc
    except EditRejected:
        raise
    except Exception as exc:  # noqa: BLE001 - kernel failure becomes an explicit refusal
        raise EditRejected(f"la operación geométrica falló: {exc}") from exc


# ---------------------------------------------------------------- helpers


def _extent(forma: Any) -> tuple[list[float], list[float]]:
    if isinstance(forma, trimesh.Trimesh):
        lo, hi = forma.bounds
        return [float(v) for v in lo], [float(v) for v in hi]
    bb = forma.bounding_box()
    return [bb.min.X, bb.min.Y, bb.min.Z], [bb.max.X, bb.max.Y, bb.max.Z]


def _tamano(forma: Any, idx: int) -> float:
    lo, hi = _extent(forma)
    return hi[idx] - lo[idx]


def _cap_brep(forma: Any) -> None:
    caras = len(list(forma.faces()))
    if caras > MAX_BREP_FACES:
        raise EditRejected(f"la pieza tiene {caras} caras; el maximo para editar cotas es {MAX_BREP_FACES}")


def _check_result(
    antes: Any, despues: Any, *, permitidos: set[int], solidos: int, delta_volumen: float,
    volumen: Any = None,
) -> None:
    """Refuses any result that is not valid, that changed the solid count, whose
    volume change differs from the analytic one, or whose bbox moved outside the
    axes allowed to change. `volumen` mide un cuerpo (por defecto el B-rep)."""
    if not despues.is_valid:
        raise EditRejected("la geometría resultante no es válida")
    n = len(despues.solids())
    if n != solidos:
        raise EditRejected(f"la pieza pasó de {solidos} a {n} sólidos")
    medir = volumen or (lambda s: s.volume)
    real = medir(despues) - medir(antes)
    if abs(real - delta_volumen) > VOLUME_TOLERANCE_REL * abs(delta_volumen) + 1e-6:
        raise EditRejected(
            f"el volumen cambió {real:.3f} mm3 y debía cambiar {delta_volumen:.3f} mm3: la geometría no es la esperada"
        )
    _check_bbox(antes, despues, permitidos)


def _check_bbox(antes: Any, despues: Any, permitidos: set[int]) -> None:
    lo0, hi0 = _extent(antes)
    lo1, hi1 = _extent(despues)
    for i in range(3):
        if i in permitidos:
            continue
        if abs(lo1[i] - lo0[i]) > TOLERANCE_MM or abs(hi1[i] - hi0[i]) > TOLERANCE_MM:
            raise EditRejected("la edición movió la pieza fuera del eje de la cota")


# ---------------------------------------------------------------- scale (escalar_eje)


def scale_mesh(malla: trimesh.Trimesh, eje: str, factor: float, origen: float) -> trimesh.Trimesh:
    """Mesh vertices scaled along `eje` about the plane ``eje = origen``."""
    idx = EJES.index(eje)
    vertices = np.array(malla.vertices, dtype=float, copy=True)
    vertices[:, idx] = origen + (vertices[:, idx] - origen) * factor
    return trimesh.Trimesh(vertices=vertices, faces=np.array(malla.faces), process=False)


def _scale_mesh(malla: Any, cota: dict[str, Any], valor: float, presupuesto: Presupuesto) -> Edit:
    if len(malla.faces) > MAX_MESH_FACES:
        raise EditRejected(f"la malla tiene {len(malla.faces)} caras; el maximo para editar cotas es {MAX_MESH_FACES}")
    eje = cota["eje"]
    idx = EJES.index(eje)
    presupuesto.gastar(1)
    lo, _ = _extent(malla)
    factor = valor / _tamano(malla, idx)
    nueva = scale_mesh(malla, eje, factor, lo[idx])
    lo0, hi0 = _extent(malla)
    lo1, hi1 = _extent(nueva)
    for i in range(3):
        if i != idx and (abs(lo1[i] - lo0[i]) > TOLERANCE_MM or abs(hi1[i] - hi0[i]) > TOLERANCE_MM):
            raise EditRejected("la edición movió la malla fuera del eje de la cota")
    medido = hi1[idx] - lo1[idx]
    if abs(medido - valor) > TOLERANCE_MM:
        raise EditRejected(f"la malla medida tras la edición es {medido:.3f} mm y se pidieron {valor:g} mm")
    return Edit(
        forma=nueva,
        editada=_informe(cota, valor, medido),
        escala=(eje, factor, lo[idx]),
    )


def _scale_brep(forma: Any, cota: dict[str, Any], valor: float, presupuesto: Presupuesto) -> Edit:
    _cap_brep(forma)
    eje = cota["eje"]
    idx = EJES.index(eje)
    lo, _ = _extent(forma)
    factor = valor / _tamano(forma, idx)
    presupuesto.gastar(2)
    nueva = b123d_kernel.scale_axis(forma, eje, factor, lo[idx])
    # Cuerpo escalado: el volumen de OCC sale ~0,9 % alto (BSPLINE), así que la
    # comprobación usa la malla fina (F24b).
    volumen_original = km.volumen_malla(forma)
    _check_result(
        forma, nueva, permitidos={idx}, solidos=len(forma.solids()),
        delta_volumen=volumen_original * (factor - 1.0), volumen=km.volumen_malla,
    )
    medido = _tamano(nueva, idx)
    if abs(medido - valor) > TOLERANCE_MM:
        raise EditRejected(f"la medida tras la edición es {medido:.3f} mm y se pidieron {valor:g} mm")
    return Edit(forma=nueva, editada=_informe(cota, valor, medido))


# ---------------------------------------------------------------- cylinders (rellenar_y_taladrar)


def _redrill(forma: Any, cota: dict[str, Any], valor: float, presupuesto: Presupuesto) -> Edit:
    _cap_brep(forma)
    n = int(cota.get("cantidad", 1))
    if n > MAX_HOLES_PER_EDIT:
        raise EditRejected(f"el grupo tiene {n} agujeros; el maximo por edicion es {MAX_HOLES_PER_EDIT}")
    eje = cota["eje"]
    holes, _ = b123d_kernel.global_axis_holes(forma)
    grupo = [h for h in holes if h["eje"] == eje and abs(h["d"] - cota["valor_mm"]) <= 0.0005]
    if len(grupo) != n:
        raise EditRejected("el grupo de agujeros cambió; vuelve a leer las cotas")

    radio_nuevo = valor / 2.0
    solidos_previos = list(forma.solids())
    solidos = list(solidos_previos)
    delta_teorico = 0.0
    for h in grupo:
        presupuesto.gastar(4)
        loc, direccion = h["loc"], h["dir"]
        t0, t1 = h["t0"], h["t1"]
        radio_viejo = h["d"] / 2.0
        indice = _owner_index(solidos, h, t0, t1, radio_viejo, eje)
        actual = Compound(children=solidos)
        abre_bajo = not b123d_kernel.inside_material(actual, b123d_kernel.point_on_axis(loc, direccion, t0 - END_PROBE_MM))
        abre_alto = not b123d_kernel.inside_material(actual, b123d_kernel.point_on_axis(loc, direccion, t1 + END_PROBE_MM))
        relleno = b123d_kernel.cylinder_solid(b123d_kernel.point_on_axis(loc, direccion, t0), direccion, radio_viejo, t1 - t0)
        ini = t0 - (OVERSHOOT_MM if abre_bajo else 0.0)
        fin = t1 + (OVERSHOOT_MM if abre_alto else 0.0)
        taladro = b123d_kernel.cylinder_solid(b123d_kernel.point_on_axis(loc, direccion, ini), direccion, radio_nuevo, fin - ini)
        partes = list(solidos[indice].fuse(relleno).cut(taladro).clean().solids())
        if len(partes) != 1:
            raise EditRejected("el agujero parte la pieza en varios sólidos")
        if not _wall_holds(partes[0], loc, direccion, t0, t1, radio_nuevo, eje):
            raise EditRejected(
                f"el diámetro {valor:g} mm deja menos de {WALL_MARGIN_MM:g} mm de pared alrededor del agujero",
                razon=REASON_WALL,
            )
        solidos[indice] = partes[0]
        delta_teorico += math.pi * (radio_viejo ** 2 - radio_nuevo ** 2) * (t1 - t0)

    resultado = Compound(children=solidos)
    holes_nuevos, _ = b123d_kernel.global_axis_holes(resultado)
    medidos = [h for h in holes_nuevos if h["eje"] == eje and abs(h["d"] - valor) <= 0.0005]
    previos = sorted((round(h["c"][0], 3), round(h["c"][1], 3)) for h in grupo)
    nuevos = sorted((round(h["c"][0], 3), round(h["c"][1], 3)) for h in medidos)
    if len(medidos) != n or any(abs(a[0] - b[0]) > 1e-3 or abs(a[1] - b[1]) > 1e-3 for a, b in zip(previos, nuevos)):
        raise EditRejected("no se pudo volver a medir el agujero tras la edición")
    _check_result(forma, resultado, permitidos=set(), solidos=len(solidos_previos), delta_volumen=delta_teorico)
    informe = _informe(cota, valor, valor)
    informe["afectadas"] = n
    return Edit(forma=resultado, editada=informe)


def _perpendiculares(eje: str) -> tuple[np.ndarray, np.ndarray]:
    idx = EJES.index(eje)
    u = np.zeros(3)
    v = np.zeros(3)
    u[(idx + 1) % 3] = 1.0
    v[(idx + 2) % 3] = 1.0
    return u, v


def _owner_index(solidos: list[Any], h: dict[str, Any], t0: float, t1: float, radio: float, eje: str) -> int:
    """Index of the solid whose material surrounds the hole (a point inside the wall)."""
    u, v = _perpendiculares(eje)
    centro = np.array(h["loc"], dtype=float) + ((t0 + t1) / 2.0) * np.array(h["dir"], dtype=float)
    for angulo in np.linspace(0.0, 2 * math.pi, 8, endpoint=False):
        p = centro + (radio + WALL_PROBE_MM) * (math.cos(angulo) * u + math.sin(angulo) * v)
        for indice, solido in enumerate(solidos):
            if b123d_kernel.inside_material(solido, tuple(float(c) for c in p)):
                return indice
    raise EditRejected("no se localizó el sólido que contiene el agujero")


def _wall_holds(solido: Any, loc, direccion, t0: float, t1: float, radio: float, eje: str) -> bool:
    """True when the circle of `radio + WALL_MARGIN_MM` at mid-depth stays in material."""
    u, v = _perpendiculares(eje)
    centro = np.array(loc, dtype=float) + ((t0 + t1) / 2.0) * np.array(direccion, dtype=float)
    for angulo in np.linspace(0.0, 2 * math.pi, WALL_SAMPLES, endpoint=False):
        p = centro + (radio + WALL_MARGIN_MM) * (math.cos(angulo) * u + math.sin(angulo) * v)
        if not b123d_kernel.inside_material(solido, tuple(float(c) for c in p)):
            return False
    return True


# ---------------------------------------------------------------- parallel faces (mover_cara)


def _stretch_once(forma: Any, eje: str, lo: float, hi: float, delta: float) -> tuple[Any, float]:
    """Gap of `delta` at mid-thickness: the lower half keeps its place, the upper
    half moves by `delta`, and the section between them is filled (delta > 0) or
    removed (delta < 0). Returns ``(shape, area)``, the area of the section."""
    altura = hi - lo
    if delta >= 0:
        corte = (lo + hi) / 2.0
        bajo = b123d_kernel.halfspace(forma, eje, corte, False)
        alto = b123d_kernel.halfspace(forma, eje, corte, True)
        caras = b123d_kernel.faces_on_plane(bajo, eje, corte)
        area = b123d_kernel.area_of(caras)
        relleno = b123d_kernel.union_extruded(bajo, caras, delta, eje)
        nueva = relleno.fuse(b123d_kernel.translate_along(alto, eje, delta)).clean()
    else:
        quitar = -delta
        corte = lo + (altura - quitar) / 2.0
        bajo = b123d_kernel.halfspace(forma, eje, corte, False)
        alto = b123d_kernel.halfspace(forma, eje, corte + quitar, True)
        caras = b123d_kernel.faces_on_plane(bajo, eje, corte)
        area = b123d_kernel.area_of(caras)
        nueva = bajo.fuse(b123d_kernel.translate_along(alto, eje, delta)).clean()
    return nueva, area


def _measured_distance(forma: Any, eje: str, lo: float, objetivo: float) -> float | None:
    """Distance of the pair whose lower face still sits at `lo` (closest to the target)."""
    candidatos = [
        p["hi"] - p["lo"]
        for p in b123d_kernel.parallel_planar_pairs(forma, eje, MAX_BREP_FACES)
        if abs(p["lo"] - lo) <= 1e-3
    ]
    return min(candidatos, key=lambda d: abs(d - objetivo), default=None)


def _probe(forma: Any, eje: str, lo: float, hi: float, delta: float, valor: float, presupuesto: Presupuesto) -> _Probe:
    presupuesto.gastar(6)
    nueva, area = _stretch_once(forma, eje, lo, hi, delta)
    medido = _measured_distance(nueva, eje, lo, valor)
    if medido is None:
        raise EditRejected("no se pudo volver a medir la distancia tras la edición")
    return _Probe(delta=delta, forma=nueva, area=area, medido=medido)


def _solve_by_bisection(
    primera: _Probe, valor: float, delta_minimo: float, sondear
) -> _Probe:
    """Bisection on the translation until the measured distance equals `valor`
    within half the tolerance. The first probe is the analytic guess, so the
    bracket is only searched when it misses."""
    medio_tol = TOLERANCE_MM / 2.0
    if abs(primera.residuo(valor)) <= medio_tol:
        return primera
    h = max(abs(primera.residuo(valor)), 0.01)
    a = max(primera.delta - h, delta_minimo)
    b = primera.delta + h
    fa, fb = sondear(a), sondear(b)
    for _ in range(8):
        if (fa.residuo(valor) < 0) != (fb.residuo(valor) < 0):
            break
        h *= 2.0
        a = max(primera.delta - h, delta_minimo)
        b = primera.delta + h
        fa, fb = sondear(a), sondear(b)
    else:
        raise EditRejected("no hay una separación que cumpla la cota dentro del rango")
    for _ in range(BISECT_STEPS):
        medio = (a + b) / 2.0
        fm = sondear(medio)
        if abs(fm.residuo(valor)) <= medio_tol:
            return fm
        if (fm.residuo(valor) < 0) == (fa.residuo(valor) < 0):
            a, fa = medio, fm
        else:
            b, fb = medio, fm
    raise EditRejected("la biseccion no converge dentro del presupuesto")


def _stretch(forma: Any, cota: dict[str, Any], valor: float, presupuesto: Presupuesto) -> Edit:
    _cap_brep(forma)
    if len(forma.solids()) != 1:
        raise EditRejected(REASON_SEVERAL_SOLIDS)
    eje = cota["eje"]
    idx = EJES.index(eje)
    pares = b123d_kernel.parallel_planar_pairs(forma, eje, MAX_BREP_FACES)
    par = next((p for p in pares if abs(round(p["lo"], 3) - cota["lo"]) <= 0.0005), None)
    if par is None:
        raise EditRejected("la cota ya no corresponde a dos caras paralelas; vuelve a leer las cotas")
    lo, hi = par["lo"], par["hi"]
    altura = hi - lo
    delta_minimo = -altura + 1e-3  # the stretched distance must stay positive

    def sondear(delta: float) -> _Probe:
        return _probe(forma, eje, lo, hi, delta, valor, presupuesto)

    elegido = _solve_by_bisection(sondear(valor - altura), valor, delta_minimo, sondear)
    _check_result(
        forma, elegido.forma, permitidos={idx}, solidos=1, delta_volumen=elegido.area * elegido.delta,
    )
    return Edit(forma=elegido.forma, editada=_informe(cota, valor, elegido.medido))


# ---------------------------------------------------------------- report


def _informe(cota: dict[str, Any], valor: float, medido: float) -> dict[str, Any]:
    return {
        "id": cota["id"],
        "operacion": cota.get("operacion"),
        "valor_antes": cota["valor_mm"],
        "pedido_mm": valor,
        "valor_despues": round(medido, 3),
        "error_mm": round(medido - valor, 3),
    }


# ---------------------------------------------------------------- anchors (viewer overlay)


def attach_anchors(forma: Any, listado: list[dict[str, Any]], malla: bool) -> None:
    """Adds `ancla` ([x, y, z], world mm) to each editable dimension of an imported
    document, so the 3D viewer can place its label: an overall size sits on the bbox
    edge along its axis, a hole group on its first hole at mid-depth, and a distance
    at the middle of the slab. Read-only dimensions get no anchor."""
    lo, hi = _extent(forma)
    medio = [(lo[i] + hi[i]) / 2.0 for i in range(3)]
    agujeros = [] if malla else b123d_kernel.global_axis_holes(forma)[0]
    for cota in listado:
        if cota.get("solo_lectura") or not cota.get("operacion"):
            continue
        eje = cota.get("eje")
        punto: list[float] | None = None
        if cota["tipo"] == "tamano_total" and eje in EJES:
            idx = EJES.index(eje)
            punto = [lo[i] for i in range(3)]
            punto[idx] = medio[idx]
        elif cota["tipo"] == "diametro":
            grupo = sorted(
                (h for h in agujeros if h["eje"] == eje and abs(h["d"] - cota["valor_mm"]) <= 0.0005),
                key=lambda h: h["c"],
            )
            if grupo:
                h = grupo[0]
                t = (h["t0"] + h["t1"]) / 2.0
                punto = [float(c) for c in b123d_kernel.point_on_axis(h["loc"], h["dir"], t)]
        elif cota["tipo"] == "distancia" and eje in EJES:
            punto = list(medio)
            punto[EJES.index(eje)] = (cota["lo"] + cota["hi"]) / 2.0
        if punto is not None:
            cota["ancla"] = [round(float(c), 3) for c in punto]
