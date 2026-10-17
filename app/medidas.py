"""Live measures readout (Phase 21, `GET /documentos/{id}/medidas`).

The viewer's "Medidas" panel lists, for the open STEP/script document, the
closest pairs of named solids: contact, collision (choque) or clearance
(holgura), in mm. The numbers come from the SAME exact pairwise pass
`percibir` uses (`percepcion._pares_cercanos_con_tope`, same proximity
default, same `colisiones` tolerances), so a value in the panel equals what
`percibir` says for that pair. Nothing is approximated from bounding boxes
in the browser.

Contracts:

- One revision per reply: the payload is computed for ONE `revision`
  (the caller passes it; the route re-reads it after the computation and
  answers 409 if the document changed meanwhile). The cache keeps only the
  latest revision per document, so a stale result is never served.
- Bounded work: at most `TOPE_CALCULOS` exact OCC distance calls per
  computation (the walk stops and says so with `completo: false`), and at
  most `top` pairs in a reply (`TOP_MAX` at most), with the rest counted.
- Computed under the document lock by the route (never on the event loop),
  and cached per revision: a repeated read of an unchanged document costs no
  geometry work (`computos()` counts real computations).
- Read-only and token-free like `/percibir`; names are untrusted data and go
  to the browser as text only (the client renders with `textContent`).

Never imports build123d/OCP directly (ADR-0001): the route hands over the
already-imported shape through `cargar`.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import percepcion
from checks import colisiones

# Pairs listed in one reply (default) and the hard ceiling for the `top` query.
TOP_PARES = 20
TOP_MAX = 100
# Exact distance calls per computation (bbox prefilter already applied).
TOPE_CALCULOS = 500

_cache: dict[str, tuple[str, dict[str, Any]]] = {}
_cache_lock = threading.Lock()
_computos = 0


class RevisionCambiada(RuntimeError):
    """The document changed while its measures were being computed."""


def computos() -> int:
    """How many times the pair pass really ran in this process (tests: cache)."""
    return _computos


def _calcular_forma(shape: Any, entradas: list[dict[str, Any]]) -> dict[str, Any]:
    """Full pair pass for one shape. Returns every pair found within the
    proximity margin, sorted by gap; `presentar` cuts the list to `top`."""
    global _computos
    _computos += 1
    grupos, orden, nombres_inciertos = percepcion._cargar_grupos(shape, entradas, None)
    mejor, completo, calculos = percepcion._pares_cercanos_con_tope(
        grupos, percepcion.PROXIMIDAD_MM_POR_DEFECTO, TOPE_CALCULOS
    )
    choques, errores = percepcion._detectar_choques(mejor, colisiones.TOLERANCIA_MM3_POR_DEFECTO)
    volumen_por_par = {frozenset((c["a"], c["b"])): c["vol"] for c in choques}

    pares: list[dict[str, Any]] = []
    for (a, b), (distancia, *_resto) in mejor.items():
        fila: dict[str, Any] = {"a": a, "b": b, "distancia_mm": round(distancia, 3)}
        volumen = volumen_por_par.get(frozenset((a, b)))
        if volumen is not None:
            fila["estado"] = "choque"
            fila["volumen_mm3"] = volumen
        elif distancia <= colisiones.TOLERANCIA_TOQUE_MM:
            fila["estado"] = "contacto"
        else:
            fila["estado"] = "holgura"
        pares.append(fila)
    pares.sort(key=lambda f: (f["distancia_mm"], f["a"], f["b"]))

    return {
        "solidos": len(orden),
        "pares": pares,
        "completo": completo,
        "calculos": calculos,
        "tope_calculos": TOPE_CALCULOS,
        "choques": len(choques),
        "errores": len(errores),
        "nombres_inciertos": bool(nombres_inciertos),
    }


def obtener(
    doc_id: str,
    revision: str | None,
    cargar: Callable[[], tuple[Any, list[dict[str, Any]]]],
    revision_actual: Callable[[], str | None],
) -> tuple[dict[str, Any], bool]:
    """``(datos, desde_cache)`` for `doc_id` at `revision`.

    `cargar` returns ``(shape, entradas)`` and is only called on a cache miss.
    `revision_actual` is read again after the computation: if the document
    moved on meanwhile, `RevisionCambiada` is raised and nothing is cached.
    A missing revision is computed but never cached."""
    if revision:
        with _cache_lock:
            previo = _cache.get(doc_id)
        if previo is not None and previo[0] == revision:
            return previo[1], True
    shape, entradas = cargar()
    datos = _calcular_forma(shape, entradas)
    datos["revision"] = revision
    if revision_actual() != revision:
        raise RevisionCambiada("el documento cambió mientras se calculaban las medidas; vuelva a pedirlas")
    if revision:
        with _cache_lock:
            _cache[doc_id] = (revision, datos)
    return datos, False


def presentar(datos: dict[str, Any], top: int) -> dict[str, Any]:
    """Copy of `datos` with only the `top` closest pairs and the count of the rest."""
    mostrados = datos["pares"][:top]
    return {**datos, "pares": mostrados, "total_pares": len(datos["pares"]),
            "ocultos": len(datos["pares"]) - len(mostrados)}


def olvidar(doc_id: str) -> None:
    """Drop the cached measures of a deleted document."""
    with _cache_lock:
        _cache.pop(doc_id, None)
