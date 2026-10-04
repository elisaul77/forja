"""Test coupons (fdm-B): print only the regions where named pieces fit
together before committing to a long print.

`detectar_zonas` finds pairs of differently-named solids that touch or sit
closer than ``holgura_max`` (bbox prefilter shared with
`checks/colisiones.py`, then one exact ``Shape.distance`` per candidate
pair) and returns a box of interest per pair; overlapping boxes are merged.
`recortar` intersects each implicated solid with its zone box and lays the
results out on the bed (z min = 0, side by side along X, original
orientation). The REST route creates a NEW document — the source document is
only read.

Crop box heuristic: the box is the overlap of the two bboxes (each grown by
``holgura_max``) plus ``margen``, but along an axis where the overlap is
small compared with the pair's full extent (less than half) the box is cut;
along the other axes it keeps the pair's full extent, so a bushing around a
shaft keeps its whole wall and the shaft is cut to the bushing's length.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import auth
import solids
from checks import distancia

router = APIRouter()

HOLGURA_MAX_MM = 1.0
MARGEN_MM = 4.0
SEPARACION_MM = 5.0
TOPE_ZONAS = 8
VOLUMEN_MIN_MM3 = 1e-3
PREFIJO = "cupon_"


def _caja_vacia(c: list[float]) -> bool:
    return c[0] >= c[1] or c[2] >= c[3] or c[4] >= c[5]


def _crecer(c: list[float], d: float) -> list[float]:
    return [c[0] - d, c[1] + d, c[2] - d, c[3] + d, c[4] - d, c[5] + d]


def _interseccion(a: list[float], b: list[float]) -> list[float]:
    return [max(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), min(a[3], b[3]), max(a[4], b[4]), min(a[5], b[5])]


def _union(a: list[float], b: list[float]) -> list[float]:
    return [min(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), max(a[3], b[3]), min(a[4], b[4]), max(a[5], b[5])]


def _se_tocan(a: list[float], b: list[float]) -> bool:
    return not _caja_vacia(_interseccion(a, b))


def caja_de_interes(bbox_a: list[float], bbox_b: list[float], holgura: float, margen: float) -> list[float]:
    solape = _interseccion(_crecer(bbox_a, holgura), _crecer(bbox_b, holgura))
    total = _union(bbox_a, bbox_b)
    caja = []
    for eje in range(3):
        lo, hi = solape[2 * eje] - margen, solape[2 * eje + 1] + margen
        t_lo, t_hi = total[2 * eje], total[2 * eje + 1]
        if (hi - lo) * 2 > (t_hi - t_lo):
            lo, hi = t_lo - margen, t_hi + margen
        caja += [lo, hi]
    return caja


def detectar_zonas(
    grupos: list[tuple], pieza: str | None, holgura_max: float, margen: float
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(zonas, errores)``; each zona is ``{pares: [{a, b, dist}], piezas:
    [nombre], caja: [xmin, xmax, ymin, ymax, zmin, zmax]}``."""
    zonas: list[dict[str, Any]] = []
    errores: list[dict[str, Any]] = []
    n = len(grupos)
    for i in range(n):
        nombre_a, _ia, solido_a, bbox_a, _va = grupos[i]
        for j in range(i + 1, n):
            nombre_b, _ib, solido_b, bbox_b, _vb = grupos[j]
            if nombre_a == nombre_b:
                continue
            if pieza is not None and pieza not in (nombre_a, nombre_b):
                continue
            if not distancia.bbox_se_acercan(bbox_a, bbox_b, holgura_max):
                continue
            try:
                d = solido_a.distance(solido_b)
            except Exception as exc:  # noqa: BLE001 - reported, never crash
                errores.append({"a": nombre_a, "b": nombre_b, "mensaje": str(exc)[:200]})
                continue
            if d > holgura_max:
                continue
            zonas.append({
                "pares": [{"a": nombre_a, "b": nombre_b, "dist": round(max(d, 0.0), 3)}],
                "piezas": [nombre_a, nombre_b],
                "caja": caja_de_interes(bbox_a, bbox_b, holgura_max, margen),
            })
    # Merge overlapping boxes until stable.
    fusionado = True
    while fusionado:
        fusionado = False
        for i in range(len(zonas)):
            for j in range(i + 1, len(zonas)):
                if _se_tocan(zonas[i]["caja"], zonas[j]["caja"]):
                    a, b = zonas[i], zonas.pop(j)
                    a["caja"] = _union(a["caja"], b["caja"])
                    a["pares"] += b["pares"]
                    a["piezas"] += [p for p in b["piezas"] if p not in a["piezas"]]
                    fusionado = True
                    break
            if fusionado:
                break
    return zonas, errores


def _nombre_cupon(pieza: str, sufijo: str, usados: set[str]) -> str:
    base_max = solids.NOMBRE_MAX_LEN - len(PREFIJO) - len(sufijo) - 3
    nombre = f"{PREFIJO}{pieza[:base_max]}{sufijo}"
    k = 2
    candidato = nombre
    while candidato in usados:
        candidato = f"{nombre}_{k}"
        k += 1
    usados.add(candidato)
    return candidato


def recortar(grupos: list[tuple], zonas: list[dict[str, Any]], todas: bool = False) -> list[tuple[str, str, Any]]:
    """Crop each zone's pieces (every solid when ``todas``) and lay them out
    on the bed. Returns ``[(nombre_cupon, pieza_origen, forma)]``."""
    from build123d import Box, Compound, Pos

    usados: set[str] = set()
    recortes: list[tuple[str, str, Any]] = []
    varias = len(zonas) > 1
    for k, zona in enumerate(zonas, start=1):
        c = zona["caja"]
        caja = Pos((c[0] + c[1]) / 2, (c[2] + c[3]) / 2, (c[4] + c[5]) / 2) * Box(c[1] - c[0], c[3] - c[2], c[5] - c[4])
        por_pieza: dict[str, list[Any]] = {}
        for nombre, _idx, solido, bbox, _vol in grupos:
            if not todas and nombre not in zona["piezas"]:
                continue
            if not _se_tocan(bbox, c):
                continue
            try:
                trozo = solido.intersect(caja)
            except Exception:  # noqa: BLE001 - a failed boolean just skips this solid
                continue
            if trozo is None:
                continue
            partes = [s for s in trozo.solids() if s.volume > VOLUMEN_MIN_MM3]
            if partes:
                por_pieza.setdefault(nombre, []).extend(partes)
        for nombre, partes in por_pieza.items():
            forma = partes[0] if len(partes) == 1 else Compound(children=partes)
            recortes.append((_nombre_cupon(nombre, f"_z{k}" if varias else "", usados), nombre, forma))

    colocados = []
    cursor = 0.0
    for nombre, origen, forma in recortes:
        bb = forma.bounding_box()
        forma = Pos(cursor - bb.min.X, -bb.min.Y, -bb.min.Z) * forma
        cursor += (bb.max.X - bb.min.X) + SEPARACION_MM
        colocados.append((nombre, origen, forma))
    return colocados


class _CuponBody(BaseModel):
    pieza: str | None = Field(default=None, max_length=solids.NOMBRE_MAX_LEN)
    holgura_max: float = Field(default=HOLGURA_MAX_MM, ge=0.0, le=5.0)
    margen: float = Field(default=MARGEN_MM, ge=0.0, le=20.0)
    caja: dict[str, list[float]] | None = None


def _caja_explicita(caja: dict[str, list[float]]) -> list[float]:
    try:
        mn, mx = [float(v) for v in caja["min"]], [float(v) for v in caja["max"]]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("caja debe ser {min: [x, y, z], max: [x, y, z]} en mm") from exc
    if len(mn) != 3 or len(mx) != 3:
        raise ValueError("caja debe ser {min: [x, y, z], max: [x, y, z]} en mm")
    c = [mn[0], mx[0], mn[1], mx[1], mn[2], mx[2]]
    if _caja_vacia(c):
        raise ValueError("caja vacia: cada max debe ser mayor que su min")
    return c


def _bbox(forma: Any) -> list[float]:
    bb = forma.bounding_box()
    return [bb.min.X, bb.max.X, bb.min.Y, bb.max.Y, bb.min.Z, bb.max.Z]


def _redondear(c: list[float]) -> list[float]:
    return [round(v, 2) for v in c]


@router.post("/documentos/{doc_id}/cupon", dependencies=[Depends(auth.requiere_token)])
def crear_cupon(doc_id: str, body: _CuponBody) -> dict[str, Any]:
    """New document «Cupón — <nombre>» with the fit zones of ``doc_id``
    cropped and laid on the bed. The source document is never modified."""
    import documents
    from kernel import b123d_kernel

    registro = documents._registry.get(doc_id)
    if registro is None:
        raise HTTPException(status_code=404, detail="documento no encontrado")
    ruta = documents._ruta_step_o_404(doc_id)
    entradas = solids.cargar(doc_id)
    if not entradas:
        raise HTTPException(status_code=400, detail="este documento no tiene desglose de solidos")
    shape = b123d_kernel.import_from_step(ruta)
    grupos, _inciertos = solids.solidos_por_indice(shape, entradas)
    nombres = {g[0] for g in grupos}
    if body.pieza is not None and body.pieza not in nombres:
        raise HTTPException(status_code=404, detail=f"pieza no encontrada: {body.pieza!r}")

    errores: list[dict[str, Any]] = []
    if body.caja is not None:
        try:
            c = _caja_explicita(body.caja)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        piezas = [body.pieza] if body.pieza else sorted(nombres)
        zonas = [{"pares": [], "piezas": piezas, "caja": c}]
    else:
        zonas, errores = detectar_zonas(grupos, body.pieza, body.holgura_max, body.margen)
        if not zonas:
            raise HTTPException(
                status_code=422,
                detail=f"sin zonas de encaje (ninguna pareja de piezas a menos de {body.holgura_max} mm)",
            )
    zonas_mas = max(0, len(zonas) - TOPE_ZONAS)
    zonas = zonas[:TOPE_ZONAS]

    colocados = recortar(grupos, zonas)
    if not colocados:
        raise HTTPException(status_code=422, detail="la caja no corta ningun solido")

    base = registro["nombre"].rsplit(".", 1)[0]
    nombre = f"Cupón — {base}"[:120]
    nuevo = documents.crear_documento_desde_formas({n: f for n, _o, f in colocados}, nombre)
    salida: dict[str, Any] = {
        "id_nuevo": nuevo["id"],
        "nombre": nuevo["nombre"],
        "descarga": f"/documentos/{nuevo['id']}/descarga/{documents._slug_descarga(nuevo['nombre'])}.3mf",
        "piezas": [
            {"nombre": n, "de": o, "bbox": _redondear(_bbox(f))} for n, o, f in colocados
        ],
        "zonas": [{"piezas": z["piezas"], "pares": z["pares"], "caja": _redondear(z["caja"])} for z in zonas],
    }
    if zonas_mas:
        salida["zonas_mas"] = zonas_mas
    if errores:
        salida["errores"] = errores[:10]
    return salida
