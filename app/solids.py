"""Named-solid metadata for multi-solid STEP documents (Phase 5A).

A script's ``resultado`` may be a ``dict[str, Shape]`` instead of a single
Shape/Compound (``kernel.b123d_kernel.combinar_nombrados`` tags each value's
``.label`` with its dict key before merging them into one Compound).
build123d's STEP exporter DOES write ``.label`` as the real STEP PRODUCT
name for those explicitly-named children — the exported file genuinely
carries "silla_1"/"mesa" as product names, valuable for anyone opening it in
another CAD tool. **But** ``export_step`` also always turns on OCCT's
``ShapeTool.SetAutoNaming`` (hardcoded ``auto_naming=True`` in build123d
0.13's ``exporters3d.py``, not something Forja controls), which silently
assigns a generic placeholder label (e.g. ``"COMPOUND"``) to EVERY child
that has no explicit name — verified against a real multi-part script with
no dict naming: every single child came back labelled ``"COMPOUND"`` after
a round trip. That makes re-reading ``.label`` after import fundamentally
unable to tell "the user really named this" from "OCCT filled in a generic
placeholder" — so this module never infers naming from re-imported labels.

Instead, the trusted subprocess (``scripts_runner``'s sandboxed wrapper)
passes the ordered list of dict keys back to ``app/documents.py`` through an
explicit side channel (a small sidecar next to the exported STEP, read by
``scripts_runner.ejecutar_script`` and forwarded to
:func:`construir_entradas` as ``nombres_ordenados``) whenever ``resultado``
was a dict — zero ambiguity, no dependency on OCCT's XCAF naming quirks.
Everything else (plain upload, `abrir_archivo`, a script whose ``resultado``
was a plain Shape) falls back to auto-generated ``solido_N`` names.

What this module persists, in a small sidecar ``{doc_id}.solidos.json`` next
to the document (mirrors ``app/notes.py``'s ``{doc_id}.notas.json``
pattern), is the derived per-solid breakdown (name, bbox, volume, index)
computed once at creation/update time — a container restart reuses that
same sidecar as-is (`app/documents.py`'s ``recargar_documentos``) rather
than recomputing it from the STEP file's unreliable labels, so a restart
never regresses "silla_1" back to a generic "solido_N".

Like ``app/naming.py``, this module accepts already-built build123d
``Shape`` objects and never imports ``build123d``/``OCP`` itself
(ADR-0001): it only duck-types ``.children``, ``.solids()``,
``.bounding_box()``, ``.volume`` on whatever object the caller hands it.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterable

# See `app/documents.py`'s `DOCUMENTOS_DIR` for the `FORJA_DATA_DIR`
# override contract; every module deriving a path from the documents
# storage root must agree on the same base directory.
DOCUMENTOS_DIR = Path(os.environ.get("FORJA_DATA_DIR", "/data/documentos"))

# Safe chars for a solid name: letters (incl. Spanish accents/enye), digits,
# underscore, hyphen. No slashes/dots/spaces/control chars (used as a plain
# label, never as a filesystem path segment, but kept conservative anyway).
_NOMBRE_RE = re.compile(r"^[A-Za-z0-9_\-áéíóúñÁÉÍÓÚÑ]+$")
NOMBRE_MAX_LEN = 64

TOPE_FILAS_POR_DEFECTO = 30
UMBRAL_ASTILLA_MM3_POR_DEFECTO = 1.0

# "astilla" (sliver) rule (Phase 5A fix-review): comparing every solid's
# volume against the LARGEST solid in the whole document (the original
# Phase 5A rule) flagged 14 of 21 legitimate furniture pieces in a real
# dollhouse scene — a chair is tiny next to a house, but it is not a defect.
# Volume alone, relative to an unrelated solid elsewhere in the document,
# says nothing about whether THIS solid is a mistake. Three independent,
# per-solid (or per-name-group) checks replace it, ORed together:
#
# 1. Group-fragment check (see `resumen()`): a NAMED piece that produced
#    more than one disconnected solid keeps its largest fragment as "the
#    piece" and flags every smaller fragment — this is the actual "piece
#    split into N solids" case the astilla flag exists for. Names in flat
#    `solido_N` documents (no dict, `construir_entradas`'s fallback) are
#    all unique by construction, so this check never fires for them —
#    they only ever hit checks 2/3 below, as intended.
# 2. Absolute floor: `volumen < UMBRAL_ASTILLA_MM3_POR_DEFECTO` mm3
#    (configurable per call) — a solid this small is negligible in any
#    reasonably-scaled document regardless of anything else in it.
# 3. Thinness (both conditions required, not either alone): the solid
#    fills less than `UMBRAL_RELLENO_ASTILLA` of its OWN bounding box
#    volume, AND its smallest bbox dimension is under
#    `UMBRAL_DIM_MIN_ASTILLA_MM` mm. A legitimate thin plate (a 2 mm
#    shelf, a rug modelled as a hollowed picture-frame) is thin in one
#    axis but still mostly SOLID within that thin box — a 44x28x0.8 mm
#    rug frame with a 38x22 mm cutout still fills ~32% of its bounding
#    box (well above 2%), so it is safe; only a near-degenerate
#    boolean-tolerance residue (razor-thin AND nearly hollow within its
#    own tiny footprint) trips both conditions at once. See
#    `tests/test_solidos.py` for the rug-shaped regression case.
UMBRAL_RELLENO_ASTILLA = 0.02  # 2% de su propia caja delimitadora
UMBRAL_DIM_MIN_ASTILLA_MM = 1.0


def validar_nombres(nombres: Iterable[str]) -> None:
    """Validate a batch of solid names (a script's ``resultado`` dict keys).

    Raises ``ValueError`` with a Spanish message on the first problem found:
    empty name, disallowed characters, too long, or a repeat (defensive —
    dict keys are already unique, but this also guards a caller that hands
    us a plain list). Never silently truncates or sanitizes a bad name.
    """
    vistos: set[str] = set()
    for nombre in nombres:
        if not isinstance(nombre, str) or not nombre:
            raise ValueError("nombre de solido invalido: no puede estar vacio")
        if len(nombre) > NOMBRE_MAX_LEN:
            raise ValueError(
                f"nombre de solido demasiado largo (maximo {NOMBRE_MAX_LEN} caracteres): {nombre!r}"
            )
        if not _NOMBRE_RE.match(nombre):
            raise ValueError(
                f"nombre de solido invalido {nombre!r}: solo se permiten letras, numeros, "
                "guion (-), guion bajo (_) y tildes/enye"
            )
        if nombre in vistos:
            raise ValueError(f"nombre de solido repetido: {nombre!r}")
        vistos.add(nombre)


def ruta_solidos(doc_id: str) -> Path:
    return DOCUMENTOS_DIR / f"{doc_id}.solidos.json"


def guardar(doc_id: str, entradas: list[dict[str, Any]]) -> None:
    ruta_solidos(doc_id).write_text(json.dumps(entradas))


def cargar(doc_id: str) -> list[dict[str, Any]] | None:
    """Read back the per-solid breakdown for ``doc_id``, or ``None`` if it
    was never computed (STL document, or a STEP predating this phase that
    hasn't been re-created/re-loaded yet) or the sidecar is corrupt."""
    ruta = ruta_solidos(doc_id)
    if not ruta.exists():
        return None
    try:
        return json.loads(ruta.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def borrar(doc_id: str) -> None:
    ruta_solidos(doc_id).unlink(missing_ok=True)


def _entrada(nombre: str, indice: int, solido: Any) -> dict[str, Any]:
    bb = solido.bounding_box()
    return {
        "nombre": nombre,
        "indice": indice,
        "bbox": [bb.min.X, bb.max.X, bb.min.Y, bb.max.Y, bb.min.Z, bb.max.Z],
        "volumen": solido.volume,
    }


def construir_entradas(
    shape: Any, nombres_ordenados: list[str] | None = None
) -> list[dict[str, Any]]:
    """Per-solid breakdown of ``shape`` (an already-imported build123d
    Shape/Compound): ``[{nombre, indice, bbox, volumen}, ...]``.

    ``nombres_ordenados``, when given, is the dict-key order captured by
    ``scripts_runner`` straight from the script's ``resultado`` (never
    re-derived from STEP labels, see this module's docstring) — it must
    line up 1:1 with ``shape.children`` (the same order build123d's
    ``Compound(children=[...])`` preserves through export/import). Every
    solid inside a child is grouped under that child's name, so a name
    whose value produced several disconnected solids keeps them all under
    the same ``nombre`` with different ``indice`` (needed later for "piece
    split into N solids" checks).

    Whenever ``nombres_ordenados`` is ``None``, or its length doesn't match
    ``shape.children`` (defensive — should not happen when the caller wired
    it through correctly), every solid gets an auto-generated ``solido_N``
    (1-based) in ``shape.solids()`` order instead.
    """
    hijos = list(getattr(shape, "children", None) or [])

    entradas: list[dict[str, Any]] = []
    if nombres_ordenados is not None and len(nombres_ordenados) == len(hijos) and hijos:
        indice = 0
        for nombre, hijo in zip(nombres_ordenados, hijos):
            for solido in hijo.solids():
                entradas.append(_entrada(nombre, indice, solido))
                indice += 1
    else:
        for indice, solido in enumerate(shape.solids()):
            entradas.append(_entrada(f"solido_{indice + 1}", indice, solido))
    return entradas


# Phase 5B fix-review: `shape.solids()` returning entries in the same order
# across STEP re-imports was already an assumption (see the docstring
# below); rather than trust it blindly forever, every call now cross-checks
# each entry's stored volume/bbox centre against the solid its `indice`
# actually points at, within these tolerances.
_TOLERANCIA_VOLUMEN_REL = 0.01  # 1% relative
_TOLERANCIA_BBOX_CENTRO_MM = 0.5


def _centro_de_bbox(bbox: list[float]) -> tuple[float, float, float]:
    xmin, xmax, ymin, ymax, zmin, zmax = bbox
    return ((xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2)


def _bbox_de_solido(solido: Any) -> list[float]:
    bb = solido.bounding_box()
    return [bb.min.X, bb.max.X, bb.min.Y, bb.max.Y, bb.min.Z, bb.max.Z]


def _distancia(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def _coincide(entrada: dict[str, Any], solido: Any) -> tuple[bool, float, float]:
    """``(coincide, error_relativo_volumen, distancia_centros_mm)`` between
    a stored entry and a candidate solid."""
    volumen_entrada = entrada["volumen"]
    error_vol = abs(solido.volume - volumen_entrada) / max(abs(volumen_entrada), 1e-9)
    dist_centro = _distancia(_centro_de_bbox(entrada["bbox"]), _centro_de_bbox(_bbox_de_solido(solido)))
    coincide = error_vol <= _TOLERANCIA_VOLUMEN_REL and dist_centro <= _TOLERANCIA_BBOX_CENTRO_MM
    return coincide, error_vol, dist_centro


def _reasignar_por_similitud(
    entradas_ordenadas: list[dict[str, Any]], todos: list[Any]
) -> list[Any] | None:
    """Best-effort recovery when the positional ``indice`` mapping disagrees
    with the reimported geometry (e.g. a permuted `shape.solids()` order):
    match every entry to the geometrically closest candidate solid (volume +
    bbox-centre distance), but ONLY trust the result if every match is an
    unambiguous winner — no other candidate solid also falls within
    tolerance for that same entry. Returns ``None`` (never guesses) if any
    entry has no acceptable match, two entries would need the same solid, or
    any entry has more than one plausible candidate."""
    n = len(entradas_ordenadas)
    centros_entrada = [_centro_de_bbox(e["bbox"]) for e in entradas_ordenadas]
    centros_solido = [_centro_de_bbox(_bbox_de_solido(s)) for s in todos]
    volumenes_solido = [s.volume for s in todos]

    def costo(i: int, j: int) -> tuple[float, float]:
        error_vol = abs(volumenes_solido[j] - entradas_ordenadas[i]["volumen"]) / max(
            abs(entradas_ordenadas[i]["volumen"]), 1e-9
        )
        dist_centro = _distancia(centros_entrada[i], centros_solido[j])
        return error_vol, dist_centro

    def dentro_de_tolerancia(i: int, j: int) -> bool:
        error_vol, dist_centro = costo(i, j)
        return error_vol <= _TOLERANCIA_VOLUMEN_REL and dist_centro <= _TOLERANCIA_BBOX_CENTRO_MM

    pares = sorted(
        ((costo(i, j), i, j) for i in range(n) for j in range(n)),
        key=lambda t: t[0],
    )

    asignado_entrada: dict[int, int] = {}
    usado_solido: set[int] = set()
    for _costo, i, j in pares:
        if i in asignado_entrada or j in usado_solido:
            continue
        if not dentro_de_tolerancia(i, j):
            continue
        asignado_entrada[i] = j
        usado_solido.add(j)

    if len(asignado_entrada) != n:
        return None  # at least one entry has no acceptable match at all

    for i, j_elegido in asignado_entrada.items():
        for j in range(n):
            if j != j_elegido and dentro_de_tolerancia(i, j):
                return None  # ambiguous: more than one plausible match

    return [todos[asignado_entrada[i]] for i in range(n)]


def solidos_por_indice(
    shape: Any, entradas: list[dict[str, Any]]
) -> tuple[list[tuple[str, int, Any, list[float], float]], bool]:
    """Zip persisted per-solid metadata (``nombre``/``indice``/``bbox``/
    ``volumen``, straight from `cargar`/`construir_entradas`) with the
    actual build123d ``Solid`` objects from a freshly-(re)imported
    ``shape`` — the sidecar only ever stores numbers, never geometry, so
    `check_colisiones`/`captura`/per-solid `exportar` (Phase 5B) all need
    this to get back an actual solid to tessellate/boolean-check/export.

    Returns ``(entradas_con_solido, nombres_inciertos)``. The happy path
    relies on ``shape.solids()`` returning solids in the SAME order every
    time the same STEP bytes are (re)imported (verified against a real
    multi-name STEP round-trip for the named-dict branch, and true by
    construction for the flat fallback) — but Phase 5B fix-review no longer
    trusts that blindly: every entry's stored volume/bbox is cross-checked
    against the solid its ``indice`` actually points at. A mismatch (e.g. a
    permuted ``shape.solids()`` order) triggers `_reasignar_por_similitud`;
    if THAT can't find an unambiguous match either, this falls all the way
    back to flat, unnamed ``solido_N`` (`shape.solids()`'s own order) with
    ``nombres_inciertos=True`` — never a silent mislabel of one piece's
    geometry as another's. Callers (routes/MCP tools) must surface that flag
    rather than swallow it.

    Raises ``ValueError`` if the counts disagree outright — a different
    number of solids is a structurally different document, not a naming
    ambiguity `_reasignar_por_similitud` could ever resolve."""
    todos = shape.solids()
    if len(todos) != len(entradas):
        raise ValueError(
            f"desacuerdo entre solidos.json ({len(entradas)} entradas) y la "
            f"geometria reimportada ({len(todos)} solidos); reconstruir el documento"
        )

    entradas_ordenadas = sorted(entradas, key=lambda e: e["indice"])

    if all(_coincide(e, todos[e["indice"]])[0] for e in entradas_ordenadas):
        return [
            (e["nombre"], e["indice"], todos[e["indice"]], e["bbox"], e["volumen"])
            for e in entradas_ordenadas
        ], False

    reasignados = _reasignar_por_similitud(entradas_ordenadas, todos)
    if reasignados is not None:
        return [
            (e["nombre"], e["indice"], solido, e["bbox"], e["volumen"])
            for e, solido in zip(entradas_ordenadas, reasignados)
        ], False

    return [
        (f"solido_{i + 1}", i, solido, _bbox_de_solido(solido), solido.volume)
        for i, solido in enumerate(todos)
    ], True


def _bbox_volumen_y_dim_min(bbox: list[float]) -> tuple[float, float]:
    """``(bbox volume, smallest bbox dimension)`` from a
    ``[xmin, xmax, ymin, ymax, zmin, zmax]`` list."""
    xmin, xmax, ymin, ymax, zmin, zmax = bbox
    dx, dy, dz = xmax - xmin, ymax - ymin, zmax - zmin
    return dx * dy * dz, min(dx, dy, dz)


def _es_astilla(
    entrada: dict[str, Any],
    conteo_por_nombre: dict[str, int],
    volumen_max_por_nombre: dict[str, float],
    umbral_astilla_mm3: float,
) -> bool:
    """One entry's sliver verdict — see the module-level comment above
    ``UMBRAL_RELLENO_ASTILLA`` for the full rationale of each check."""
    nombre, vol, bbox = entrada["nombre"], entrada["volumen"], entrada["bbox"]

    # 1. Fragment of a named piece that split into several solids: the
    # largest fragment is "the piece", every smaller sibling is a shard.
    if conteo_por_nombre[nombre] > 1 and vol < volumen_max_por_nombre[nombre]:
        return True

    # 2. Absolute floor.
    if vol < umbral_astilla_mm3:
        return True

    # 3. Thin AND nearly hollow within its own (already thin) bounding box.
    bbox_vol, dim_min = _bbox_volumen_y_dim_min(bbox)
    relleno = (vol / bbox_vol) if bbox_vol > 0 else 0.0
    return relleno < UMBRAL_RELLENO_ASTILLA and dim_min < UMBRAL_DIM_MIN_ASTILLA_MM


def resumen(
    entradas: list[dict[str, Any]],
    tope: int = TOPE_FILAS_POR_DEFECTO,
    umbral_astilla_mm3: float = UMBRAL_ASTILLA_MM3_POR_DEFECTO,
) -> dict[str, Any]:
    """Compact ``{lista, mas, astillas}`` block for `resumen_documento`
    (backs `GET /documentos/{id}`'s ``solidos_detalle`` when solidos > 1,
    feedback item 4). ``lista`` is capped at ``tope`` compact rows
    ``{nombre, bbox, vol}``; ``mas`` (the "+K mas" tail) is only present
    when truncated. ``astillas`` (sliver solid names, deduplicated) is
    scanned over EVERY entry, never just the capped ``lista``, so a shard
    past row 30 is never hidden again — see ``_es_astilla`` for the rule."""
    conteo_por_nombre: dict[str, int] = {}
    volumen_max_por_nombre: dict[str, float] = {}
    for e in entradas:
        conteo_por_nombre[e["nombre"]] = conteo_por_nombre.get(e["nombre"], 0) + 1
        if e["volumen"] > volumen_max_por_nombre.get(e["nombre"], -1.0):
            volumen_max_por_nombre[e["nombre"]] = e["volumen"]

    astillas_con_repetidos = [
        e["nombre"]
        for e in entradas
        if _es_astilla(e, conteo_por_nombre, volumen_max_por_nombre, umbral_astilla_mm3)
    ]
    # A name shared by several fragments can appear more than once above
    # (e.g. two shards of the same split piece) — dedupe, keep first-seen
    # order, since `astillas` is a flat list of names, not per-solid rows.
    astillas = list(dict.fromkeys(astillas_con_repetidos))

    lista = [{"nombre": e["nombre"], "bbox": e["bbox"], "vol": e["volumen"]} for e in entradas[:tope]]
    salida: dict[str, Any] = {"lista": lista, "astillas": astillas}
    if len(entradas) > tope:
        salida["mas"] = len(entradas) - tope
    return salida
