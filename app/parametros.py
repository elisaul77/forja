"""Parametric scripts (Phase 5C): iterate a design by changing numbers, not
by re-sending code.

A script may declare, at top level, a LITERAL dict::

    PARAMETROS = {
        "alto": {"valor": 30, "min": 10, "max": 80, "paso": 1, "unidad": "mm", "desc": "..."},
        "ancho": 20,            # short form == {"valor": 20}
    }

and read the current values either through a ``construir(params)``
function (preferred: called by the sandbox wrapper when the script leaves
``resultado`` unset, see `scripts_runner`) or through the ``PARAMS`` dict
Forja injects as a global before the script runs.

The schema is read with ``ast.literal_eval`` from the source text — never by
executing the script in this (the web) process: only the sandboxed
subprocess ever runs user code. A non-literal ``PARAMETROS`` (computed,
comprehension, ...) is rejected with a clear message.

Persistence: the script source (``codigo``, or the ``ruta`` it came from —
re-read on every apply so editing the file on disk takes effect), the
effective ``variables`` and the CURRENT parameter values live in the
document's existing ``{doc_id}.meta.json`` sidecar (read-modify-write, the
``nombre`` key written by `documents._guardar_meta` is preserved). Deleting
a document already removes that file.

Route wiring lives in `app/documents.py` (god node: wiring only); every bit
of logic is here.
"""
from __future__ import annotations

import ast
import json
import math
import os
import threading
from pathlib import Path
from typing import Any

DOCUMENTOS_DIR = Path(os.environ.get("FORJA_DATA_DIR", "/data/documentos"))

NOMBRE_VARIABLE_INYECTADA = "PARAMS"
MAX_PARAMETROS = 40
_CLAVES_PERMITIDAS = {"valor", "min", "max", "paso", "unidad", "desc"}
_DESC_MAX = 200
_UNIDAD_MAX = 16

_bloqueos: dict[str, threading.Lock] = {}
_bloqueo_global = threading.Lock()


class ParametrosInvalidos(ValueError):
    """Bad ``PARAMETROS`` declaration or bad values in an apply request."""


def bloqueo(doc_id: str) -> threading.Lock:
    """Per-document lock: two param changes on the same document (e.g. a
    slider fired twice by the web UI) are applied one after the other,
    never interleaved on the same STEP/meta files."""
    with _bloqueo_global:
        return _bloqueos.setdefault(doc_id, threading.Lock())


def _es_numero(valor: Any) -> bool:
    if not isinstance(valor, (int, float)) or isinstance(valor, bool):
        return False
    try:
        return math.isfinite(valor)
    except OverflowError:  # int too large to convert to float (e.g. 10**400)
        return False


def _normalizar_entrada(nombre: str, crudo: Any) -> dict[str, Any]:
    if not isinstance(nombre, str) or not nombre.isidentifier():
        raise ParametrosInvalidos(f"nombre de parametro invalido: {nombre!r} (usar un identificador)")
    entrada = dict(crudo) if isinstance(crudo, dict) else {"valor": crudo}
    extra = set(entrada) - _CLAVES_PERMITIDAS
    if extra:
        raise ParametrosInvalidos(f"parametro '{nombre}': claves no admitidas {sorted(extra)}")
    if "valor" not in entrada or not _es_numero(entrada["valor"]):
        raise ParametrosInvalidos(f"parametro '{nombre}': 'valor' debe ser un numero")
    for clave in ("min", "max", "paso"):
        if clave in entrada and not _es_numero(entrada[clave]):
            raise ParametrosInvalidos(f"parametro '{nombre}': '{clave}' debe ser un numero")
    if "paso" in entrada and entrada["paso"] <= 0:
        raise ParametrosInvalidos(f"parametro '{nombre}': 'paso' debe ser > 0")
    if "min" in entrada and "max" in entrada and entrada["min"] > entrada["max"]:
        raise ParametrosInvalidos(f"parametro '{nombre}': 'min' mayor que 'max'")
    for clave, tope in (("unidad", _UNIDAD_MAX), ("desc", _DESC_MAX)):
        if clave in entrada:
            entrada[clave] = str(entrada[clave])[:tope]
    entrada.setdefault("unidad", "mm")
    _validar_rango(nombre, entrada["valor"], entrada)
    return entrada


def _validar_rango(nombre: str, valor: float, entrada: dict[str, Any]) -> None:
    if "min" in entrada and valor < entrada["min"]:
        raise ParametrosInvalidos(f"'{nombre}'={valor} por debajo del minimo {entrada['min']}")
    if "max" in entrada and valor > entrada["max"]:
        raise ParametrosInvalidos(f"'{nombre}'={valor} por encima del maximo {entrada['max']}")


def esquema_desde_codigo(codigo: str) -> dict[str, dict[str, Any]]:
    """Normalized schema ``{nombre: {valor, min?, max?, paso?, unidad, desc?}}``
    from a top-level ``PARAMETROS = {...}`` literal; ``{}`` when the script
    declares none (or can't even be parsed — the sandbox run will report
    that syntax error with its line number, better than we could here)."""
    try:
        arbol = ast.parse(codigo)
    except SyntaxError:
        return {}
    nodo_valor = None
    for nodo in arbol.body:
        if isinstance(nodo, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "PARAMETROS" for t in nodo.targets
        ):
            nodo_valor = nodo.value
        elif isinstance(nodo, ast.AnnAssign) and isinstance(nodo.target, ast.Name) and nodo.target.id == "PARAMETROS":
            nodo_valor = nodo.value
    if nodo_valor is None:
        return {}
    try:
        crudo = ast.literal_eval(nodo_valor)
    except (ValueError, SyntaxError, TypeError, RecursionError, MemoryError) as exc:
        raise ParametrosInvalidos("PARAMETROS debe ser un dict literal (sin calculos)") from exc
    if not isinstance(crudo, dict):
        raise ParametrosInvalidos("PARAMETROS debe ser un dict")
    if len(crudo) > MAX_PARAMETROS:
        raise ParametrosInvalidos(f"PARAMETROS admite como maximo {MAX_PARAMETROS} entradas")
    try:
        return {nombre: _normalizar_entrada(nombre, valor) for nombre, valor in crudo.items()}
    except OverflowError as exc:
        raise ParametrosInvalidos("PARAMETROS contiene un numero fuera de rango") from exc


def valores_por_defecto(esquema: dict[str, dict[str, Any]]) -> dict[str, float]:
    return {nombre: entrada["valor"] for nombre, entrada in esquema.items()}


def aplicar_valores(
    esquema: dict[str, dict[str, Any]], actuales: dict[str, Any], nuevos: dict[str, Any]
) -> dict[str, float]:
    """Merge ``nuevos`` over ``actuales`` (entries no longer in the schema,
    or out of their current range, fall back to the default), validating
    every new value: unknown name, non-number or outside [min, max] ->
    `ParametrosInvalidos`. An int-declared parameter stays an int when the
    new value is integral (so ``range(n)`` in a script keeps working)."""
    if not esquema:
        raise ParametrosInvalidos("este documento no declara PARAMETROS")
    desconocidos = sorted(set(nuevos) - set(esquema))
    if desconocidos:
        raise ParametrosInvalidos(f"parametros desconocidos: {desconocidos}")
    resultado: dict[str, float] = {}
    for nombre, entrada in esquema.items():
        previo = actuales.get(nombre)
        valor = previo if _es_numero(previo) else entrada["valor"]
        try:
            _validar_rango(nombre, valor, entrada)
        except ParametrosInvalidos:
            valor = entrada["valor"]
        resultado[nombre] = valor
    for nombre, valor in nuevos.items():
        if not _es_numero(valor):
            raise ParametrosInvalidos(f"'{nombre}' debe ser un numero")
        entrada = esquema[nombre]
        _validar_rango(nombre, valor, entrada)
        if isinstance(entrada["valor"], int) and float(valor).is_integer():
            valor = int(valor)
        resultado[nombre] = valor
    return resultado


def variables_efectivas(
    variables: dict[str, Any] | None, valores: dict[str, float] | None
) -> dict[str, Any] | None:
    """The ``variables`` actually handed to the sandbox: the caller's own
    plus ``PARAMS`` (only when there is a schema, so a plain script never
    sees an extra global)."""
    if not valores:
        return variables
    return {**(variables or {}), NOMBRE_VARIABLE_INYECTADA: dict(valores)}


# ---------------------------------------------------------------- persistence


def _ruta_meta(doc_id: str) -> Path:
    return DOCUMENTOS_DIR / f"{doc_id}.meta.json"


def _leer_meta(doc_id: str) -> dict[str, Any]:
    ruta = _ruta_meta(doc_id)
    if not ruta.exists():
        return {}
    try:
        datos = json.loads(ruta.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return datos if isinstance(datos, dict) else {}


def guardar(
    doc_id: str,
    *,
    codigo: str | None,
    ruta: str | None,
    variables: dict[str, Any] | None,
    timeout: float | None,
    esquema: dict[str, dict[str, Any]],
    valores: dict[str, float],
) -> None:
    """Record the script behind ``doc_id`` + its current parameter values.
    ``ruta`` wins over ``codigo`` (only one is stored)."""
    meta = _leer_meta(doc_id)
    meta["script"] = {"ruta": ruta} if ruta is not None else {"codigo": codigo}
    meta["variables"] = variables or {}
    if timeout:
        meta["timeout"] = timeout
    else:
        meta.pop("timeout", None)
    meta["parametros"] = esquema
    meta["valores"] = valores
    _ruta_meta(doc_id).write_text(json.dumps(meta))


def guardar_valores(doc_id: str, esquema: dict[str, dict[str, Any]], valores: dict[str, float]) -> None:
    meta = _leer_meta(doc_id)
    meta["parametros"] = esquema
    meta["valores"] = valores
    _ruta_meta(doc_id).write_text(json.dumps(meta))


def cargar(doc_id: str) -> dict[str, Any]:
    """``{script?, variables, timeout?, parametros, valores}`` (always the
    last two, ``{}`` when the document has no parametric script)."""
    meta = _leer_meta(doc_id)
    return {
        "script": meta.get("script"),
        "variables": meta.get("variables") or {},
        "timeout": meta.get("timeout"),
        "parametros": meta.get("parametros") or {},
        "valores": meta.get("valores") or {},
    }


def resumen_valores(valores: dict[str, float]) -> str:
    """Short label of the snapshot taken right BEFORE applying ``valores``
    (the snapshot holds the previous state), e.g.
    ``antes de parametros: alto=40, ancho=20``."""
    partes = [f"{k}={v:g}" for k, v in list(valores.items())[:6]]
    if len(valores) > 6:
        partes.append(f"+{len(valores) - 6}")
    return "antes de parametros: " + ", ".join(partes)


# ---------------------------------------------------------------- history
# Phase 5C fix-review: the parametric state (script + values) is part of a
# document's state, so every history snapshot carries it as
# `parametros.json` (the params-relevant subset of meta.json -- `nombre`
# is never touched), and `restaurar` writes it back, exactly like
# `notas.json`/`solidos.json`.

NOMBRE_SNAPSHOT = "parametros.json"
_CLAVES_ESTADO = ("script", "variables", "timeout", "parametros", "valores")


def estado_para_snapshot(doc_id: str) -> bytes | None:
    """``parametros.json`` bytes for a snapshot, or ``None`` when the
    document has no stored script (uploads, abrir_archivo)."""
    meta = _leer_meta(doc_id)
    if not meta.get("script"):
        return None
    return json.dumps({k: meta[k] for k in _CLAVES_ESTADO if k in meta}).encode()


def restaurar_estado(doc_id: str, contenido: bytes | None) -> None:
    """Write a snapshot's ``parametros.json`` back into meta.json; ``None``
    (snapshot without it) clears the script/params keys, so the document
    becomes non-parametric -- never keeps today's script over an older
    geometry."""
    meta = _leer_meta(doc_id)
    for clave in _CLAVES_ESTADO:
        meta.pop(clave, None)
    if contenido is not None:
        try:
            datos = json.loads(contenido)
        except (json.JSONDecodeError, UnicodeDecodeError):
            datos = {}
        if isinstance(datos, dict):
            meta.update({k: datos[k] for k in _CLAVES_ESTADO if k in datos})
    _ruta_meta(doc_id).write_text(json.dumps(meta))
