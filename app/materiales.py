"""Material/filament per named piece (fdm-D).

A script may declare, at top level, a LITERAL dict::

    MATERIALES = {
        "tapa": "PETG negro",                                   # short form
        "junta": {"material": "TPU", "color": "#202020", "extrusor": 2},
    }

Like ``PARAMETROS`` it is read with ``ast.literal_eval`` from the source
text, never by executing the script in the web process. Keys must be piece
names of ``resultado``; names the run did not produce are dropped and
reported (``materiales_ignorados``) instead of failing a build that already
succeeded.

Persistence: ``{doc_id}.materiales.json`` (atomic write) holding
``{"materiales": {...}, "declarado": {...} | null}``. ``declarado`` is the
script's last declaration: a re-run with the SAME declaration keeps manual
edits made from the viewer/REST/MCP; a CHANGED declaration replaces them.
Every geometry snapshot carries it as ``materiales.json`` and ``restaurar``
writes it back (or removes it when the snapshot predates it), exactly like
the parametric state.

Route wiring lives in `app/documents.py`; logic is here.
"""
from __future__ import annotations

import ast
import json
import os
import re
from pathlib import Path
from typing import Any

DOCUMENTOS_DIR = Path(os.environ.get("FORJA_DATA_DIR", "/data/documentos"))

NOMBRE_SNAPSHOT = "materiales.json"
MAX_MATERIALES = 200
MATERIAL_MAX = 64
EXTRUSOR_MIN, EXTRUSOR_MAX = 1, 16
_CLAVES = {"material", "color", "extrusor"}
_RE_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")
_RE_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class MaterialesInvalidos(ValueError):
    """Bad ``MATERIALES`` declaration or bad edit request."""


def normalizar_entrada(nombre: Any, crudo: Any) -> dict[str, Any]:
    """``{material?, color?, extrusor?}`` (at least one) from the short
    (text) or long (dict) form. Color is upper-cased ``#RRGGBB``."""
    if not isinstance(nombre, str) or not nombre or len(nombre) > 64:
        raise MaterialesInvalidos(f"nombre de pieza invalido: {nombre!r}")
    entrada = {"material": crudo} if isinstance(crudo, str) else crudo
    if not isinstance(entrada, dict):
        raise MaterialesInvalidos(f"material de '{nombre}': usar un texto o un dict")
    extra = set(entrada) - _CLAVES
    if extra:
        raise MaterialesInvalidos(f"material de '{nombre}': claves no admitidas {sorted(extra)}")
    salida: dict[str, Any] = {}
    if "material" in entrada and entrada["material"] is not None:
        texto = entrada["material"]
        if not isinstance(texto, str):
            raise MaterialesInvalidos(f"material de '{nombre}': 'material' debe ser texto")
        texto = " ".join(texto.split())
        if not texto or len(texto) > MATERIAL_MAX or _RE_CONTROL.search(texto):
            raise MaterialesInvalidos(
                f"material de '{nombre}': texto de 1 a {MATERIAL_MAX} caracteres"
            )
        salida["material"] = texto
    if "color" in entrada and entrada["color"] is not None:
        color = entrada["color"]
        if not isinstance(color, str) or not _RE_COLOR.fullmatch(color):
            raise MaterialesInvalidos(f"material de '{nombre}': 'color' debe ser #RRGGBB")
        salida["color"] = color.upper()
    if "extrusor" in entrada and entrada["extrusor"] is not None:
        extrusor = entrada["extrusor"]
        if (
            not isinstance(extrusor, int)
            or isinstance(extrusor, bool)
            or not EXTRUSOR_MIN <= extrusor <= EXTRUSOR_MAX
        ):
            raise MaterialesInvalidos(
                f"material de '{nombre}': 'extrusor' debe ser un entero {EXTRUSOR_MIN}..{EXTRUSOR_MAX}"
            )
        salida["extrusor"] = extrusor
    if not salida:
        raise MaterialesInvalidos(f"material de '{nombre}': indicar material, color o extrusor")
    return salida


def normalizar(crudo: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(crudo, dict):
        raise MaterialesInvalidos("MATERIALES debe ser un dict {pieza: material}")
    if len(crudo) > MAX_MATERIALES:
        raise MaterialesInvalidos(f"MATERIALES admite como maximo {MAX_MATERIALES} piezas")
    return {nombre: normalizar_entrada(nombre, valor) for nombre, valor in crudo.items()}


def desde_codigo(codigo: str) -> dict[str, dict[str, Any]] | None:
    """Normalized top-level ``MATERIALES`` literal, or ``None`` when the
    script declares none (or doesn't parse: the sandbox reports that)."""
    try:
        arbol = ast.parse(codigo)
    except SyntaxError:
        return None
    nodo_valor = None
    for nodo in arbol.body:
        if isinstance(nodo, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "MATERIALES" for t in nodo.targets
        ):
            nodo_valor = nodo.value
        elif (
            isinstance(nodo, ast.AnnAssign)
            and isinstance(nodo.target, ast.Name)
            and nodo.target.id == "MATERIALES"
        ):
            nodo_valor = nodo.value
    if nodo_valor is None:
        return None
    try:
        crudo = ast.literal_eval(nodo_valor)
    except (ValueError, SyntaxError, TypeError, RecursionError, MemoryError) as exc:
        raise MaterialesInvalidos("MATERIALES debe ser un dict literal (sin calculos)") from exc
    return normalizar(crudo)


def ruta(doc_id: str) -> Path:
    return DOCUMENTOS_DIR / f"{doc_id}.materiales.json"


def _leer(doc_id: str) -> dict[str, Any]:
    archivo = ruta(doc_id)
    if not archivo.exists():
        return {"materiales": {}, "declarado": None}
    try:
        datos = json.loads(archivo.read_text())
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {"materiales": {}, "declarado": None}
    if not isinstance(datos, dict) or not isinstance(datos.get("materiales"), dict):
        return {"materiales": {}, "declarado": None}
    declarado = datos.get("declarado")
    return {"materiales": datos["materiales"], "declarado": declarado if isinstance(declarado, dict) else None}


def _escribir(doc_id: str, datos: dict[str, Any]) -> None:
    archivo = ruta(doc_id)
    temporal = archivo.with_name(archivo.name + ".tmp")
    temporal.write_text(json.dumps(datos, ensure_ascii=False))
    os.replace(temporal, archivo)


def cargar(doc_id: str, nombres: list[str] | None = None) -> dict[str, dict[str, Any]]:
    """Stored materials; restricted to ``nombres`` (current pieces, in
    that order) when given — entries of pieces that no longer exist stay
    on disk (a later re-run may bring the piece back) but are not shown."""
    materiales = _leer(doc_id)["materiales"]
    if nombres is None:
        return dict(materiales)
    return {n: materiales[n] for n in nombres if n in materiales}


def aplicar_declaracion(
    doc_id: str, declarado: dict[str, dict[str, Any]] | None, nombres: list[str]
) -> list[str]:
    """Persist a script's declaration after a successful run. Returns the
    declared names that the run did not produce (dropped)."""
    if declarado is None:
        return []
    ignorados = [n for n in declarado if n not in nombres]
    validos = {n: v for n, v in declarado.items() if n in nombres}
    actual = _leer(doc_id)
    if actual["declarado"] == declarado and ruta(doc_id).exists():
        return ignorados  # same declaration: keep manual edits
    _escribir(doc_id, {"materiales": validos, "declarado": declarado})
    return ignorados


def actualizar(
    doc_id: str, cambios: dict[str, Any], nombres: list[str], reemplazar: bool = False
) -> dict[str, dict[str, Any]]:
    """Manual edit: ``{pieza: material | null}`` merged (``null`` removes);
    ``reemplazar`` drops every entry not mentioned. Unknown pieces -> error."""
    if not isinstance(cambios, dict):
        raise MaterialesInvalidos("'materiales' debe ser un dict {pieza: material}")
    if len(cambios) > MAX_MATERIALES:
        raise MaterialesInvalidos(f"como maximo {MAX_MATERIALES} piezas por cambio")
    desconocidos = [n for n in cambios if n not in nombres]
    if desconocidos:
        raise MaterialesInvalidos(f"piezas inexistentes en el documento: {desconocidos[:10]}")
    normalizados = {
        n: (None if v is None else normalizar_entrada(n, v)) for n, v in cambios.items()
    }
    actual = _leer(doc_id)
    materiales = {} if reemplazar else dict(actual["materiales"])
    for nombre, valor in normalizados.items():
        if valor is None:
            materiales.pop(nombre, None)
        else:
            materiales[nombre] = valor
    _escribir(doc_id, {"materiales": materiales, "declarado": actual["declarado"]})
    return {n: materiales[n] for n in nombres if n in materiales}


def estado_para_snapshot(doc_id: str) -> bytes | None:
    archivo = ruta(doc_id)
    try:
        return archivo.read_bytes() if archivo.exists() else None
    except OSError:
        return None


def restaurar_estado(doc_id: str, contenido: bytes | None) -> None:
    """Write a snapshot's ``materiales.json`` back; ``None`` (snapshot
    without it) removes the sidecar."""
    if contenido is None:
        borrar(doc_id)
        return
    archivo = ruta(doc_id)
    temporal = archivo.with_name(archivo.name + ".tmp")
    temporal.write_bytes(contenido)
    os.replace(temporal, archivo)


def borrar(doc_id: str) -> None:
    ruta(doc_id).unlink(missing_ok=True)
