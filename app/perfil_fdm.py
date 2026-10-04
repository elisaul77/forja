"""FDM tolerance profile: pure math, no I/O, no FastAPI (fdm-A).

Imported by the server (`perfiles.py`: fitting measurements, REST) AND by
the sandbox (`sandbox/bootstrap.py` binds `agujero`/`eje`/`ranura`/`ajuste`
to the injected `PERFIL` global), so it must stay dependency-free.

Model: a printed feature comes out as ``nominal + offset(nominal)``. For
holes and pins the offset is linear in the diameter (``a + b*d``, a
least-squares fit over the user's measurements); slots use a constant
offset. Compensating means modelling ``m`` such that ``m + offset(m) == d``.
Fits (``holgura_*``) are the smallest diametral clearance that worked.
"""
from __future__ import annotations

from typing import Any

# Seed values for an Ender-3 V3 SE, 0.4 nozzle, PLA, 0.2 mm layers: holes
# come out ~0.15 mm small, pins ~0.05 mm fat, slots ~0.1 mm narrow.
SEMILLA: dict[str, Any] = {
    "nombre": "ender3v3se_pla",
    "semilla": True,
    "material": "PLA",
    "boquilla": 0.4,
    "altura_capa": 0.2,
    "agujero": {"a": -0.15, "b": 0.0},
    "agujero_horizontal": {"a": -0.2, "b": 0.0},
    "eje": {"a": 0.05, "b": 0.0},
    "ranura_offset": -0.1,
    "holgura_deslizante": 0.25,
    "holgura_presion": 0.1,
    "mediciones": {},
}

# Metric screws: nominal clearance hole and hole for self-tapping into plastic.
_TORNILLOS = {
    "M2": (2.4, 1.7),
    "M2.5": (2.9, 2.1),
    "M3": (3.4, 2.5),
    "M4": (4.5, 3.3),
    "M5": (5.5, 4.2),
}

NOMBRES_AJUSTE = sorted(
    [f"{m}_pasante" for m in _TORNILLOS] + [f"{m}_roscado" for m in _TORNILLOS]
    + ["eje_deslizante", "eje_presion"]
)


def _lineal(coef: Any) -> tuple[float, float]:
    if isinstance(coef, dict):
        return float(coef.get("a", 0.0)), float(coef.get("b", 0.0))
    return float(coef or 0.0), 0.0


def _compensar(d: float, coef: Any) -> float:
    a, b = _lineal(coef)
    return round((float(d) - a) / (1.0 + b), 4)


def agujero(d: float, perfil: dict[str, Any] | None = None, horizontal: bool = False) -> float:
    """Diameter to model so the printed hole measures ``d``."""
    perfil = perfil or SEMILLA
    clave = "agujero_horizontal" if horizontal and "agujero_horizontal" in perfil else "agujero"
    return _compensar(d, perfil.get(clave, SEMILLA[clave]))


def eje(d: float, perfil: dict[str, Any] | None = None) -> float:
    """Diameter to model so the printed pin measures ``d``."""
    perfil = perfil or SEMILLA
    return _compensar(d, perfil.get("eje", SEMILLA["eje"]))


def ranura(w: float, perfil: dict[str, Any] | None = None) -> float:
    """Width to model so the printed slot measures ``w``."""
    perfil = perfil or SEMILLA
    return round(float(w) - float(perfil.get("ranura_offset", SEMILLA["ranura_offset"])), 4)


def ajuste(nombre: str, d: float | None = None, perfil: dict[str, Any] | None = None) -> float:
    """Compensated diameter for a named fit.

    ``"M3_pasante"`` / ``"M3_roscado"`` (also M2, M2.5, M4, M5): hole to model.
    ``"eje_deslizante"`` / ``"eje_presion"``: with ``d`` (pin diameter), the
    hole to model for that pin; without ``d``, the bare diametral clearance.
    """
    perfil = perfil or SEMILLA
    if nombre in ("eje_deslizante", "eje_presion"):
        clave = "holgura_deslizante" if nombre == "eje_deslizante" else "holgura_presion"
        holgura = float(perfil.get(clave, SEMILLA[clave]))
        return holgura if d is None else agujero(float(d) + holgura, perfil)
    metrica, _, tipo = nombre.rpartition("_")
    if metrica in _TORNILLOS and tipo in ("pasante", "roscado"):
        pasante, roscado = _TORNILLOS[metrica]
        return agujero(pasante if tipo == "pasante" else roscado, perfil)
    raise ValueError(f"ajuste desconocido: {nombre!r} (validos: {', '.join(NOMBRES_AJUSTE)})")


def ajustar_lineal(puntos: list[tuple[float, float]]) -> dict[str, float]:
    """Least squares ``offset = a + b*nominal`` over (nominal, medido) pairs;
    constant (b = 0) with one distinct nominal."""
    if not puntos:
        raise ValueError("sin mediciones")
    xs = [float(n) for n, _ in puntos]
    ys = [float(m) - float(n) for n, m in puntos]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx < 1e-12:
        return {"a": round(my, 4), "b": 0.0}
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return {"a": round(my - b * mx, 4), "b": round(b, 4)}


def _pares(lista: Any, campo: str) -> list[tuple[float, float]]:
    if lista is None:
        return []
    if not isinstance(lista, list):
        raise ValueError(f"'{campo}' debe ser una lista de {{nominal, medido}}")
    pares = []
    for item in lista:
        try:
            nominal, medido = float(item["nominal"]), float(item["medido"])
        except (TypeError, KeyError, ValueError) as exc:
            raise ValueError(f"medicion invalida en '{campo}': {item!r}") from exc
        if not (0 < nominal <= 100 and 0 < medido <= 100):
            raise ValueError(f"medicion fuera de rango en '{campo}': {item!r}")
        pares.append((nominal, medido))
    return pares


def perfil_desde_mediciones(base: dict[str, Any], mediciones: dict[str, Any]) -> dict[str, Any]:
    """New profile = ``base`` with every measured group refitted. Groups:
    ``agujeros``, ``agujeros_horizontales``, ``ejes``, ``ranuras`` (lists of
    ``{nominal, medido}``) and scalars ``holgura_deslizante``/``holgura_presion``."""
    perfil = {**base, "mediciones": dict(mediciones)}
    medido = False
    for campo, clave in (("agujeros", "agujero"), ("agujeros_horizontales", "agujero_horizontal"), ("ejes", "eje")):
        pares = _pares(mediciones.get(campo), campo)
        if pares:
            perfil[clave] = ajustar_lineal(pares)
            medido = True
    pares = _pares(mediciones.get("ranuras"), "ranuras")
    if pares:
        perfil["ranura_offset"] = round(sum(m - n for n, m in pares) / len(pares), 4)
        medido = True
    for clave in ("holgura_deslizante", "holgura_presion"):
        if mediciones.get(clave) is not None:
            valor = float(mediciones[clave])
            if not 0 <= valor <= 2:
                raise ValueError(f"'{clave}' fuera de rango (0-2 mm)")
            perfil[clave] = valor
            medido = True
    if medido:
        perfil["semilla"] = False
    return perfil
