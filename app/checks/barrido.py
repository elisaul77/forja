"""Continuous single-joint sweep by conservative advancement (Phase 18).

`pose_clearance` and `comprobar-recorrido` look at discrete poses; a thin
part can sit between two samples and never be seen. This engine instead
advances the joint by the largest step the bound allows: the moving group
cannot close a gap of `g` mm in less than `g / tasa` joint units, so no
contact is skipped. It stops at the first contact (gap <= 0.05 mm), then
bisects that step to the joint tolerance.

Guarantee scope: `garantia: "continua"` covers the pairs that START apart
(advanced to the end of the range or to the first contact). Pairs that
START in contact (a pin in its bore, a press fit) are sampled by volume
at each step and listed in `pares_en_contacto_inicial`; they never turn
the label into "continua" on their own, but they are never silently
ignored. Any exact-distance failure, or the evaluation cap, gives
`garantia: "muestreada"` and `completo: false`.

Pure: this module never reads or writes documents. Callers pass
`evaluar(valor) -> {nombre: shape}` (a fresh posed copy per call) and the
names of the moving group.
"""
from __future__ import annotations

import math
from typing import Any, Callable

from checks import colisiones, distancia, pose_clearance

CAP_EVALUACIONES = 400
TOLERANCIA_GRADOS = 0.05
TOLERANCIA_MM = 0.01
# Exact OCC distance for pairs whose bbox lower bound is within this radius;
# farther pairs advance on the bbox bound, which is conservative (never larger).
RADIO_EXACTO_MM = 10.0
TOPE_LISTA = 30
TOPE_MUESTRAS = 64


def tasa_giro(formas_previas: dict[str, Any], moviles: set[str], origen, eje) -> float:
    """Upper bound, in mm per degree, of the speed of the moving group.

    `formas_previas` are the moving solids BEFORE the joint's own rotation
    (descendant motions applied, the joint's rotation not yet). A point's
    speed under a rotation equals its distance to the axis times the
    angular rate, and the farthest point of a convex bbox is a corner, so
    the largest bbox-corner distance bounds every point of the group.
    """
    norma = math.sqrt(sum(c * c for c in eje))
    if not norma > 0:
        raise ValueError("eje de giro nulo")
    unitario = [c / norma for c in eje]
    maximo = 0.0
    for nombre in moviles:
        caja = formas_previas[nombre].bounding_box()
        for x in (caja.min.X, caja.max.X):
            for y in (caja.min.Y, caja.max.Y):
                for z in (caja.min.Z, caja.max.Z):
                    rel = [x - origen[0], y - origen[1], z - origen[2]]
                    proyeccion = sum(rel[i] * unitario[i] for i in range(3))
                    perpendicular = [rel[i] - proyeccion * unitario[i] for i in range(3)]
                    maximo = max(maximo, math.sqrt(sum(p * p for p in perpendicular)))
    return maximo * math.pi / 180


def tasa_deslizamiento(eje) -> float:
    """A slide translates each point by `valor * eje` (mm per mm)."""
    norma = math.sqrt(sum(c * c for c in eje))
    if not norma > 0:
        raise ValueError("eje de deslizamiento nulo")
    return norma


def _pares(named: dict[str, Any], moviles: set[str]) -> list[tuple[str, str, float]]:
    estacionarios = sorted(set(named) - moviles)
    cajas = {nombre: pose_clearance._bbox(forma) for nombre, forma in named.items()}
    return [(a, b, pose_clearance._lower_bound(cajas[a], cajas[b]))
            for a in sorted(moviles) for b in estacionarios]


def _biseccion(lo: float, hi: float, predicado: Callable[[float], bool], tolerancia: float):
    """`predicado(lo)` is False and `predicado(hi)` True; returns the bracket."""
    while abs(hi - lo) > tolerancia:
        medio = (lo + hi) / 2
        if predicado(medio):
            hi = medio
        else:
            lo = medio
    return lo, hi


def barrer(evaluar: Callable[[float], dict[str, Any]], moviles, inicio: float, fin: float,
           tasa: float, unidad: str = "grados", tolerancia: float | None = None,
           cap: int = CAP_EVALUACIONES, detalle: bool = False) -> dict[str, Any]:
    """Sweep one joint from `inicio` to `fin` (joint units) and report the first contact.

    `tasa` is the bound from `tasa_giro` / `tasa_deslizamiento` (mm per unit).
    The result never contains a fraction: values are in `unidad`.
    """
    moviles = set(moviles)
    if not moviles:
        raise ValueError("sin piezas móviles")
    if unidad not in ("grados", "mm"):
        raise ValueError("unidad desconocida")
    for nombre, valor in (("inicio", inicio), ("fin", fin), ("tasa", tasa)):
        if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not math.isfinite(valor):
            raise ValueError(f"{nombre} no finito")
    if tasa < 0 or cap < 1:
        raise ValueError("tasa o tope inválidos")
    tol = tolerancia if tolerancia is not None else (
        TOLERANCIA_GRADOS if unidad == "grados" else TOLERANCIA_MM)
    toque = colisiones.TOLERANCIA_TOQUE_MM
    umbral = colisiones.TOLERANCIA_MM3_POR_DEFECTO
    # Contact band: within one tolerance step of the touch distance. Without
    # it the conservative step (gap - touch) / rate shrinks geometrically and
    # the sweep never crosses the touch distance (Zeno), hitting the cap.
    banda = toque + tasa * tol
    span = abs(fin - inicio)
    sentido = 1.0 if fin >= inicio else -1.0

    errores: list[dict[str, Any]] = []
    muestras: list[dict[str, Any]] = []
    muestras_mas = 0
    pasos = 0
    completo = True
    volumen_max = 0.0
    holgura = None
    valor_holgura = None
    choque = None
    ultimo_seguro = None
    iniciales: list[tuple[str, str]] = []
    base: dict[tuple[str, str], float] = {}
    avanzan: set[tuple[str, str]] = set()
    valor = float(inicio)
    recorrido = 0.0
    seguro = float(inicio)
    par_actual: tuple[str, str] | None = None

    def gap(named, par):
        return named[par[0]].distance(named[par[1]])

    def volumen(named, par):
        return distancia.volumen_interseccion(named[par[0]], named[par[1]])

    try:
        while True:
            if pasos >= cap:
                completo = False
                break
            pasos += 1
            named = evaluar(valor)
            pares = _pares(named, moviles)
            if pasos == 1:
                for a, b, lb in pares:
                    par_actual = (a, b)
                    if lb > toque or gap(named, (a, b)) > toque:
                        avanzan.add((a, b))
                    else:
                        iniciales.append((a, b))
                        base[(a, b)] = volumen(named, (a, b))

            cambio = None
            for par in iniciales:
                par_actual = par
                vol = volumen(named, par)
                volumen_max = max(volumen_max, vol)
                if vol - base[par] > umbral:
                    cambio = par
                    break

            g_min = None
            contacto = None
            for a, b, lb in pares:
                if (a, b) not in avanzan or cambio is not None:
                    continue
                par_actual = (a, b)
                if lb > RADIO_EXACTO_MM:
                    distancia_par = lb
                else:
                    distancia_par = gap(named, (a, b))
                    if holgura is None or distancia_par < holgura:
                        holgura, valor_holgura = distancia_par, valor
                    if distancia_par <= banda:
                        contacto = (a, b)
                        break
                if g_min is None or distancia_par < g_min:
                    g_min = distancia_par

            if detalle:
                if len(muestras) < TOPE_MUESTRAS:
                    muestras.append({"valor": round(valor, 6),
                                     "holgura_mm": None if g_min is None else round(g_min, 6)})
                else:
                    muestras_mas += 1

            if cambio is not None:
                lo, hi = _biseccion(
                    seguro, valor,
                    lambda v, par=cambio: volumen(evaluar(v), par) - base[par] > umbral, tol)
                vol_hi = volumen(evaluar(hi), cambio)
                volumen_max = max(volumen_max, vol_hi)
                choque = {"valor": hi, "par": list(cambio),
                          "volumen_mm3": round(vol_hi, 3), "tipo": "choque"}
                ultimo_seguro = lo
                break

            if contacto is not None:
                lo, hi = _biseccion(
                    seguro, valor,
                    lambda v, par=contacto: gap(evaluar(v), par) <= banda, tol)
                vol_hi = volumen(evaluar(hi), contacto)
                volumen_max = max(volumen_max, vol_hi)
                tipo = "choque" if vol_hi > umbral else "contacto"
                choque = {"valor": hi, "par": list(contacto),
                          "volumen_mm3": round(vol_hi, 3), "tipo": tipo}
                ultimo_seguro = lo
                break

            if recorrido >= span - 1e-12:
                break
            restante = span - recorrido
            if g_min is None or tasa == 0:
                paso = restante
            else:
                paso = min(restante, (g_min - toque) / tasa)  # lands at >= touch, never past it
            seguro = valor
            recorrido += paso
            valor = fin if paso >= restante else inicio + sentido * recorrido
    except Exception as exc:  # noqa: BLE001 - an OCC or evaluation failure is reported, never swallowed
        par = par_actual or ("", "")
        errores.append({"a": par[0], "b": par[1], "mensaje": str(exc)})
        completo = False

    if ultimo_seguro is None:
        # Completed without contact: the last evaluated value is safe. Otherwise
        # only the previous fully checked value is.
        ultimo_seguro = valor if (completo and not errores) else seguro
    garantia = "continua" if completo and not errores else "muestreada"
    ok = choque is None and not errores and completo
    if garantia == "continua":
        frase = ("Cubierto de forma continua: los pares que empiezan separados se avanzan "
                 "por cota conservadora hasta el final del rango o hasta su primer contacto; "
                 "los que empiezan en contacto se muestrean por volumen.")
    elif errores:
        frase = "Solo muestreado: un error de OCC impide la garantía continua en un par."
    else:
        frase = "Solo muestreado: se alcanzó el tope de evaluaciones antes del final del rango."

    salida: dict[str, Any] = {
        "ok": ok,
        "garantia": garantia,
        "completo": completo,
        "unidad": unidad,
        "tolerancia": tol,
        "desde": round(float(inicio), 6),
        "hasta": round(float(fin), 6),
        "pasos": pasos,
        "ultimo_seguro": round(ultimo_seguro, 6),
        "primer_choque": None if choque is None else {
            **choque, "valor": round(choque["valor"], 6)},
        "volumen_maximo_mm3": round(volumen_max, 3),
        "holgura_minima_mm": None if holgura is None else round(holgura, 6),
        "valor_holgura_minima": None if valor_holgura is None else round(valor_holgura, 6),
        "pares_en_contacto_inicial": [list(par) for par in iniciales[:TOPE_LISTA]],
        "frase": frase,
    }
    if len(iniciales) > TOPE_LISTA:
        salida["pares_en_contacto_inicial_mas"] = len(iniciales) - TOPE_LISTA
    if errores:
        salida["errores"] = errores[:TOPE_LISTA]
    if detalle:
        salida["muestras"] = muestras
        if muestras_mas:
            salida["muestras_mas"] = muestras_mas
    return salida
