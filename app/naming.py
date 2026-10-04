"""Stable geometric fingerprints for faces/edges (ADR-0003).

Kernel face/edge indices are not stable across a rebuild (a parametric
change can silently reorder OCC's internal topology). This module computes
a **fingerprint** for every face/edge from geometric invariants (surface/
curve type, centroid, normal/direction, area/length — never the raw kernel
index) and re-resolves a previously-tagged reference against a *new* shape
by nearest-fingerprint match within tolerance.

Only `app/kernel/*` may import build123d/OCP directly (ADR-0001); this
module accepts already-built `build123d.Shape` objects and never imports
`build123d`/`OCP` itself beyond the type hints below, keeping it a plain,
file-I/O-free, dependency-light module `app/notes.py` and `app/documents.py`
can both use without risking a circular import between them.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any, Iterable

# Rounding/tolerance constants (mm for lengths, unitless for directions).
_ROUND_MM = 2
_ROUND_DIR = 3
_ROUND_MEDIDA = 2

TOL_CENTROIDE_MM = 0.5
TOL_DIRECCION = 0.05
TOL_MEDIDA_REL = 0.05

# Ambiguity policy (docs/decisions/0007-naming-ambiguity-policy.md): when two
# or more candidates pass every tolerance gate above, resolving to the
# nearest one by centroid distance alone is an *order-dependent guess*, not
# a match ADR-0003 allows. A clear winner is only accepted when it is both
# very close AND unambiguously closer than the runner-up.
TOL_GANADOR_CLARO_MM = 0.1
FACTOR_GANADOR_CLARO = 3.0

Punto = tuple[float, float, float]


@dataclass(frozen=True)
class Fingerprint:
    """A geometry-derived stable identity for one face or edge.

    ``tipo`` is ``"cara"`` or ``"arista"``; ``subtipo`` is the kernel's
    geometry type (``PLANE``, ``CYLINDER``, ``LINE``, ``CIRCLE``, ...);
    ``centroide`` is the face's center of mass / the edge's midpoint;
    ``direccion`` is the face's normal / the edge's tangent at its midpoint
    (both unit vectors); ``medida`` is area (cara) or length (arista).
    """

    tipo: str
    subtipo: str
    centroide: Punto
    direccion: Punto
    medida: float

    @property
    def id(self) -> str:
        crudo = f"{self.tipo}:{self.subtipo}:{self.centroide}:{self.direccion}:{round(self.medida, _ROUND_MEDIDA)}"
        return hashlib.sha1(crudo.encode("utf-8")).hexdigest()[:16]

    def a_dict(self) -> dict[str, Any]:
        return {
            "tipo": self.tipo,
            "subtipo": self.subtipo,
            "centroide": list(self.centroide),
            "direccion": list(self.direccion),
            "medida": self.medida,
        }


def _redondear(v: Any, decimales: int) -> Punto:
    return (round(float(v.X), decimales), round(float(v.Y), decimales), round(float(v.Z), decimales))


def _normalizar(v: Any) -> Any:
    largo = math.sqrt(v.X * v.X + v.Y * v.Y + v.Z * v.Z)
    if largo < 1e-9:
        return v
    return v * (1.0 / largo)


def fingerprint_cara(face: Any, indice: int | None = None) -> Fingerprint:
    """Fingerprint for one build123d/OCC ``Face``."""
    centro = face.center()
    normal = _normalizar(face.normal_at(centro))
    return Fingerprint(
        tipo="cara",
        subtipo=face.geom_type.name,
        centroide=_redondear(centro, _ROUND_MM),
        direccion=_redondear(normal, _ROUND_DIR),
        medida=round(float(face.area), _ROUND_MEDIDA),
    )


def fingerprint_arista(edge: Any, indice: int | None = None) -> Fingerprint:
    """Fingerprint for one build123d/OCC ``Edge``."""
    medio = edge.position_at(0.5)
    tangente = _normalizar(edge.tangent_at(0.5))
    return Fingerprint(
        tipo="arista",
        subtipo=edge.geom_type.name,
        centroide=_redondear(medio, _ROUND_MM),
        direccion=_redondear(tangente, _ROUND_DIR),
        medida=round(float(edge.length), _ROUND_MEDIDA),
    )


def caras(shape: Any) -> list[Fingerprint]:
    """Ordered list of face fingerprints, in ``shape.faces()`` order.

    The order here MUST match ``kernel.mesh.tessellate_to_trimesh_con_caras``'s
    per-face tessellation order, so `GET /documentos/{id}/caras`'s list
    positions line up with `GET /documentos/{id}/caras_triangulos`'s values.
    """
    return [fingerprint_cara(f, i) for i, f in enumerate(shape.faces())]


def aristas(shape: Any) -> list[Fingerprint]:
    """Ordered list of edge fingerprints, in ``shape.edges()`` order."""
    return [fingerprint_arista(e, i) for i, e in enumerate(shape.edges())]


def _distancia(a: Punto, b: Punto) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def resolver_detallado(
    guardada: Fingerprint, candidatas: Iterable[Fingerprint]
) -> tuple[Fingerprint | None, bool]:
    """Find the geometrically-nearest fingerprint to ``guardada`` among
    ``candidatas`` (a fresh rebuild's faces or edges), within tolerance —
    and report whether a failure to resolve was due to **ambiguity** (two or
    more candidates tied within tolerance, no clear winner) rather than
    simply finding nothing.

    Matching never relies on raw kernel order/index — only on the geometric
    invariants themselves — and never on exact hash equality either (two
    rebuilds of an *unrelated* feature should leave an untouched face's
    invariants bit-identical, but tolerance keeps this robust against tiny
    floating-point drift).

    Returns ``(None, False)`` if no candidate is within tolerance at all
    ("referencia perdida" territory — the caller decides how to surface
    that). If **more than one** candidate passes every gate, this is only
    resolved when the best match is a *clear winner* over the runner-up
    (docs/decisions/0007-naming-ambiguity-policy.md): the runner-up's
    centroid distance must be at least :data:`FACTOR_GANADOR_CLARO` times
    the best's, AND the best itself must be under
    :data:`TOL_GANADOR_CLARO_MM`. Otherwise this returns ``(None, True)`` —
    ambiguous, never a guess, and never dependent on ``candidatas``' order
    (the decision is made from the sorted distances, not first-seen order).
    """
    pasan: list[tuple[float, Fingerprint]] = []
    for candidata in candidatas:
        if candidata.tipo != guardada.tipo or candidata.subtipo != guardada.subtipo:
            continue
        d_centroide = _distancia(guardada.centroide, candidata.centroide)
        if d_centroide > TOL_CENTROIDE_MM:
            continue
        d_direccion = _distancia(guardada.direccion, candidata.direccion)
        if d_direccion > TOL_DIRECCION:
            continue
        base = max(abs(guardada.medida), 1e-9)
        if abs(candidata.medida - guardada.medida) / base > TOL_MEDIDA_REL:
            continue
        pasan.append((d_centroide, candidata))

    if not pasan:
        return None, False

    pasan.sort(key=lambda par: par[0])
    mejor_dist, mejor = pasan[0]
    if len(pasan) == 1:
        return mejor, False

    segundo_dist, _segunda = pasan[1]
    ganador_claro = (
        segundo_dist > mejor_dist
        and mejor_dist < TOL_GANADOR_CLARO_MM
        and segundo_dist >= FACTOR_GANADOR_CLARO * mejor_dist
    )
    if ganador_claro:
        return mejor, False
    return None, True


def resolver(guardada: Fingerprint, candidatas: Iterable[Fingerprint]) -> Fingerprint | None:
    """Thin wrapper over :func:`resolver_detallado` for callers that only
    need the match itself (``None`` covers both "nothing found" and
    "ambiguous" — use :func:`resolver_detallado` to tell them apart)."""
    encontrada, _ambigua = resolver_detallado(guardada, candidatas)
    return encontrada


def fingerprint_desde_dict(datos: dict[str, Any]) -> Fingerprint:
    return Fingerprint(
        tipo=datos["tipo"],
        subtipo=datos["subtipo"],
        centroide=tuple(datos["centroide"]),  # type: ignore[arg-type]
        direccion=tuple(datos["direccion"]),  # type: ignore[arg-type]
        medida=float(datos["medida"]),
    )


def resolver_referencia(shape: Any, referencia: dict[str, Any]) -> dict[str, Any]:
    """Re-resolve one note/stroke ``referencia`` dict against a rebuilt
    ``shape``.

    ``referencia`` is the schema shared with the FreeCAD add-on:
    ``{"tipo": "cara"|"arista"|"punto", "id": str|None, "punto": [x,y,z],
    "huella": {...} | None}``. A ``"punto"`` reference has no topological
    id to resolve (it is a free 3D point, exactly like `ClaudeNota.FCMacro`'s
    vertex/measurement pins) and always comes back unchanged. A ``"cara"``/
    ``"arista"`` reference is re-matched by :func:`resolver_detallado`; on
    success the returned dict carries the *new* id/huella (never the old,
    stale one) and ``referencia_perdida: False``; on failure the old id/
    huella/punto are kept as last-known-good and ``referencia_perdida:
    True`` — the reference is never silently dropped or rebound to an
    unrelated element. When the failure is specifically because two or more
    candidates were tied within tolerance (docs/decisions/0007), the dict
    also carries ``ambigua: True`` (omitted otherwise, so it stays cheap).
    """
    tipo = referencia.get("tipo")
    if tipo == "punto" or referencia.get("huella") is None:
        return {**referencia, "referencia_perdida": False}

    try:
        guardada = fingerprint_desde_dict(referencia["huella"])
    except (KeyError, TypeError, ValueError):
        # huella malformada/corrupta: nunca se re-enlaza a ciegas, se marca
        # perdida igual que un match no encontrado.
        return {**referencia, "referencia_perdida": True}
    candidatas = caras(shape) if tipo == "cara" else aristas(shape)
    encontrada, ambigua = resolver_detallado(guardada, candidatas)
    if encontrada is None:
        salida = {**referencia, "referencia_perdida": True}
        if ambigua:
            salida["ambigua"] = True
        return salida
    return {
        **referencia,
        "id": encontrada.id,
        "huella": encontrada.a_dict(),
        "referencia_perdida": False,
    }
