"""Vista explosionada (F19, ADR-0027): desplazamientos de DISPLAY de las
piezas con nombre. Función pura, sin geometría ni estado: `desplazamientos`
es el oráculo, y `web/static/js/explosion.js` debe dar lo mismo sobre la
tabla compartida `tests/fixtures/explosion_offsets.json`.

Reglas (las mismas en JS):
- Dirección. Una pieza hija de una articulación `giro`/`deslizamiento` se
  aleja de su padre por el signo del eje (eje * signo, signo = -1 si el eje
  apunta hacia el padre; con producto punto 0 gana +). Las demás se alejan
  del centro del conjunto; una pieza con el centro en ese punto va a +Z.
  «Centro» = centro de la caja delimitadora (las entradas de `solidos` no
  traen centroide).
- Distancia. Cada pieza tiene una semiextensión h a lo largo de su dirección.
  Se ordenan por su proyección sobre la dirección (empate por nombre) y el
  acumulado G_i = G_{i-1} + h_{i-1} + h_i separa cada par consecutivo por al
  menos la suma de sus semiextensiones. Desplazamiento = factor * 0.5 * D *
  G_i / G_max, con D la diagonal de la caja del conjunto. Así la pieza más
  alejada se mueve exactamente factor * D / 2. La separación mínima es una
  meta de display, no una garantía de que no haya interpenetración.
"""
from __future__ import annotations

import math
from typing import Any

FACTOR_MIN = 0.0
FACTOR_MAX = 1.0
MAX_PIEZAS = 500
_EPS = 1e-9
_TIPOS_CON_EJE = ("giro", "deslizamiento")


def validar_factor(factor: Any) -> float:
    """Factor de explosión: número finito entre 0 y 1 (no booleano)."""
    if (isinstance(factor, bool) or not isinstance(factor, (int, float))
            or not math.isfinite(factor) or not FACTOR_MIN <= factor <= FACTOR_MAX):
        raise ValueError("el factor de explosión debe ser un número entre 0 y 1")
    return float(factor)


def cajas_desde_entradas(entradas: list[dict[str, Any]]) -> dict[str, list[float]]:
    """Caja por nombre de pieza: une las cajas de los fragmentos que comparten
    nombre (una pieza partida en varios sólidos)."""
    cajas: dict[str, list[float]] = {}
    for entrada in entradas:
        nombre = entrada["nombre"]
        xmin, xmax, ymin, ymax, zmin, zmax = (float(v) for v in entrada["bbox"])
        previa = cajas.get(nombre)
        if previa is None:
            cajas[nombre] = [xmin, xmax, ymin, ymax, zmin, zmax]
        else:
            cajas[nombre] = [min(previa[0], xmin), max(previa[1], xmax), min(previa[2], ymin),
                             max(previa[3], ymax), min(previa[4], zmin), max(previa[5], zmax)]
    return cajas


def _validar_cajas(cajas: dict[str, list[float]]) -> dict[str, list[float]]:
    if len(cajas) > MAX_PIEZAS:
        raise ValueError(f"demasiadas piezas para explosionar ({len(cajas)} > {MAX_PIEZAS})")
    validadas: dict[str, list[float]] = {}
    for nombre, caja in cajas.items():
        if not isinstance(caja, (list, tuple)) or len(caja) != 6:
            raise ValueError(f"caja inválida para la pieza {nombre}")
        valores = []
        for v in caja:
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                raise ValueError(f"caja inválida para la pieza {nombre}: se requiere un número finito")
            valores.append(float(v))
        for k in range(3):
            if valores[2 * k] > valores[2 * k + 1]:
                raise ValueError(f"caja invertida para la pieza {nombre}")
        validadas[nombre] = valores
    return validadas


def _centro(caja: list[float]) -> tuple[float, float, float]:
    return ((caja[0] + caja[1]) / 2, (caja[2] + caja[3]) / 2, (caja[4] + caja[5]) / 2)


def _resta(a, b) -> tuple[float, float, float]:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _punto(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _unitario(eje: Any) -> tuple[float, float, float]:
    if not isinstance(eje, (list, tuple)) or len(eje) != 3:
        raise ValueError("el eje de articulación debe tener tres números")
    vector = tuple(float(v) for v in eje)
    if not all(math.isfinite(v) for v in vector):
        raise ValueError("el eje de articulación debe ser finito")
    norma = math.sqrt(_punto(vector, vector))
    if norma < _EPS:
        raise ValueError("el eje de articulación no puede ser nulo")
    return (vector[0] / norma, vector[1] / norma, vector[2] / norma)


def desplazamientos(cajas: dict[str, list[float]], articulaciones: list[dict[str, Any]] | None,
                    factor: Any) -> dict[str, list[float]]:
    """Desplazamiento `[dx, dy, dz]` en mm por pieza (ver el docstring del
    módulo). `articulaciones`: dicts con `tipo`, `padre`, `hijo`, `eje`; las
    `fijo` no dirigen nada. Determinista: no depende del orden de `cajas`."""
    factor = validar_factor(factor)
    cajas = _validar_cajas(cajas)
    if not cajas:
        return {}

    xmin = min(c[0] for c in cajas.values())
    xmax = max(c[1] for c in cajas.values())
    ymin = min(c[2] for c in cajas.values())
    ymax = max(c[3] for c in cajas.values())
    zmin = min(c[4] for c in cajas.values())
    zmax = max(c[5] for c in cajas.values())
    centro_conjunto = ((xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2)
    diagonal = math.sqrt((xmax - xmin) ** 2 + (ymax - ymin) ** 2 + (zmax - zmin) ** 2)

    centros = {n: _centro(c) for n, c in cajas.items()}
    eje_de_hijo: dict[str, tuple[str, tuple[float, float, float]]] = {}
    for art in articulaciones or []:
        if art.get("tipo") in _TIPOS_CON_EJE and art.get("padre") in cajas and art.get("hijo") in cajas:
            eje_de_hijo[art["hijo"]] = (art["padre"], _unitario(art.get("eje")))

    direcciones: dict[str, tuple[float, float, float]] = {}
    for nombre in cajas:
        if nombre in eje_de_hijo:
            padre, eje = eje_de_hijo[nombre]
            signo = -1.0 if _punto(eje, _resta(centros[nombre], centros[padre])) < 0 else 1.0
            direcciones[nombre] = (eje[0] * signo, eje[1] * signo, eje[2] * signo)
        else:
            relativo = _resta(centros[nombre], centro_conjunto)
            norma = math.sqrt(_punto(relativo, relativo))
            direcciones[nombre] = (0.0, 0.0, 1.0) if norma < _EPS else (
                relativo[0] / norma, relativo[1] / norma, relativo[2] / norma)

    semiextension: dict[str, float] = {}
    proyeccion: dict[str, float] = {}
    for nombre, d in direcciones.items():
        c = cajas[nombre]
        semiextension[nombre] = sum(abs(d[k]) * (c[2 * k + 1] - c[2 * k]) / 2 for k in range(3))
        proyeccion[nombre] = _punto(d, _resta(centros[nombre], centro_conjunto))

    orden = sorted(cajas, key=lambda n: (proyeccion[n], n))
    acumulado: list[float] = []
    for i, nombre in enumerate(orden):
        if i == 0:
            acumulado.append(semiextension[nombre])
        else:
            acumulado.append(acumulado[-1] + semiextension[orden[i - 1]] + semiextension[nombre])

    maximo = acumulado[-1]
    base = factor * 0.5 * diagonal
    resultado: dict[str, list[float]] = {}
    for i, nombre in enumerate(orden):
        if maximo > _EPS:
            t = base * acumulado[i] / maximo
        else:
            t = base * (i + 1) / len(orden)
        d = direcciones[nombre]
        # `+ 0.0` convierte -0.0 en 0.0 para que la salida sea limpia.
        resultado[nombre] = [d[k] * t + 0.0 for k in range(3)]
    return {n: resultado[n] for n in sorted(resultado)}
