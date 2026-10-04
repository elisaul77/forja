"""Rest-based assembly poses; callers own document locks and transactions.

Angles are degrees, translations millimetres. Joint origins and axes live
in the WORLD frame of the immutable rest STEP, before ancestor motions.

One vocabulary, the project's Spanish (Phase 6.3), for both the persisted
state and the wire: ``articulaciones``/``valores``; a joint is ``{id, tipo:
fijo|giro|deslizamiento, padre, hijo, origen, eje, limites, valor}`` plus
``referencia_padre``/``referencia_hijo`` when it was pinned to a face/edge.
A reference carries ``huella`` at definition time (proof it belongs to that
solid) and is projected to ``{tipo, id, punto}`` on the way out.

The persisted state carries the same vocabulary and a schema marker,
``version: FORMAT_VERSION`` — the rename bumped 1 -> 2. A state from an
older build is refused with `EstadoIncompatible` on every path that reads
it (never a bare `KeyError` 500) and `restore_files` refuses to plant one;
there is nothing to migrate, so `clear()` — which never reads the state —
is the recovery path.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Any

import naming
import solids
from kernel import b123d_kernel as kernel
from kernel.assembly_kernel import transform_rigid

DOCUMENTOS_DIR = Path(os.environ.get("FORJA_DATA_DIR", "/data/documentos"))
STATE_KEY = "ensamble.json"
BASE_KEY = "ensamble_base.step"

# Schema marker of the persisted `state.json`. 1 = the pre-6.3 English
# vocabulary (`joints`/`values`, `type`/`parent`/...), 2 = the Spanish one.
FORMAT_VERSION = 2

MENSAJE_FORMATO = "ensamble en formato anterior; quitar las articulaciones y redefinirlas"

# Exactly what a joint dict may carry: the wire keys plus the optional
# reference per side. Anything else is refused rather than persisted, so no
# stray (or English) key can end up in the state.
_CLAVES_PERMITIDAS = {"id", "tipo", "padre", "hijo", "origen", "eje", "limites",
                      "valor", "referencia_padre", "referencia_hijo"}


class EstadoIncompatible(ValueError):
    """A persisted `state.json` this build cannot read: written by an older
    Forja, before the vocabulary rename (`version: 1`). There is nothing to
    migrate — the caller removes the assembly (`DELETE …/ensamble`, whose
    path never reads the state) and defines it again. Subclasses
    `ValueError`, so callers that already handle bad assembly input keep
    working; the routes map it to a 409 with `MENSAJE_FORMATO`."""


def _estado_compatible(state: dict) -> dict:
    """Reject an unsupported `state.json` explicitly, instead of letting its
    missing Spanish keys blow up later as a `KeyError` (a bare 500 on every
    route of the affected document — Phase 6.3 fix round)."""
    if (state.get("version") != FORMAT_VERSION
            or not isinstance(state.get("articulaciones"), list)
            or not isinstance(state.get("valores"), dict)):
        raise EstadoIncompatible(MENSAJE_FORMATO)
    return state


def _directory(doc_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", doc_id):
        raise ValueError("identificador de documento inválido")
    return DOCUMENTOS_DIR / ".ensambles" / doc_id


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("se requiere un número finito")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("se requiere un número finito")
    return result


def _vector(value: Any) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError("se requiere un vector de tres números")
    return [_number(v) for v in value]


def _named(shape: Any, entries: list[dict]) -> dict[str, Any]:
    names = [entry["nombre"] for entry in entries]
    solids.validar_nombres(names)  # duplicate names mean multi-solid components
    rows, uncertain = solids.solidos_por_indice(shape, entries)
    if uncertain:
        raise ValueError("nombres de sólidos inciertos; no se puede definir el ensamble")
    return {name: solid for name, _, solid, _, _ in rows}


def _reference(shape: Any, reference: dict) -> dict:
    if not isinstance(reference, dict) or reference.get("tipo") not in ("cara", "arista"):
        raise ValueError("la referencia de articulación debe ser una cara o arista")
    if not reference.get("id") or not reference.get("huella"):
        raise ValueError("la referencia necesita id y huella de la Fase 4")
    # Reject fabricated/mismatched IDs, rather than silently interpreting a fingerprint.
    try:
        expected = naming.fingerprint_desde_dict(reference["huella"]).id
    except (TypeError, KeyError, ValueError):
        raise ValueError("huella de referencia inválida") from None
    if expected != reference["id"]:
        raise ValueError("id y huella de referencia no coinciden")
    resolved = naming.resolver_referencia(shape, reference)
    if resolved.get("referencia_perdida") or resolved.get("ambigua"):
        raise ValueError("referencia perdida o ambigua en la articulación")
    return resolved


def _validate_joints(joints: list[dict], named: dict[str, Any]) -> list[dict]:
    """Validate and normalize one definition; the wire vocabulary is Spanish
    (`tipo` `padre` `hijo` `origen` `eje` `limites` `valor`, references
    `referencia_padre`/`referencia_hijo`), same as every other payload in
    the project. A reference's `huella` is accepted and validated here — it
    is how a face/edge is proven to belong to the child/parent solid — but
    it is an internal fingerprint and read responses drop it (Phase 6.3)."""
    if not isinstance(joints, list) or not 1 <= len(joints) <= 60:
        raise ValueError("el ensamble requiere entre 1 y 60 articulaciones")
    ids, parents, result = set(), {}, []
    for raw in joints:
        if not isinstance(raw, dict):
            raise ValueError("articulación inválida")
        joint = dict(raw)
        sobran = set(joint) - _CLAVES_PERMITIDAS
        if sobran:
            raise ValueError(f"claves desconocidas en la articulación: {', '.join(sorted(sobran))}")
        jid = joint.get("id")
        solids.validar_nombres([jid])
        if jid in ids:
            raise ValueError("identificador de articulación repetido")
        ids.add(jid)
        kind = joint.get("tipo")
        if kind not in ("fijo", "giro", "deslizamiento"):
            raise ValueError("tipo de articulación desconocido")
        parent, child = joint.get("padre"), joint.get("hijo")
        if (not isinstance(parent, str) or not isinstance(child, str)
                or parent not in named or child not in named or parent == child):
            raise ValueError("padre e hijo deben ser sólidos distintos del documento")
        if child in parents:
            raise ValueError("un sólido no puede tener varios padres")
        parents[child] = parent
        joint["origen"] = _vector(joint.get("origen", [0, 0, 0]))
        axis = _vector(joint.get("eje", [0, 0, 1]))
        norm = math.hypot(*axis)
        if norm < 1e-12 or not math.isfinite(norm):
            raise ValueError("el eje de articulación no puede ser nulo")
        joint["eje"] = [v / norm for v in axis]
        limits = joint.get("limites", [0, 0] if kind == "fijo" else None)
        if not isinstance(limits, (list, tuple)) or len(limits) != 2:
            raise ValueError("la articulación necesita límites mínimo y máximo")
        low, high = map(_number, limits)
        if low > high or (kind == "fijo" and (low != 0 or high != 0)):
            raise ValueError("límites de articulación inválidos")
        joint["limites"] = [low, high]
        value = _number(joint.get("valor", 0))
        if not low <= value <= high:
            raise ValueError("valor de articulación fuera de los límites")
        joint["valor"] = value
        for side in ("padre", "hijo"):
            key = f"referencia_{side}"
            if key in joint and joint[key] is not None:
                joint[key] = _reference(named[joint[side]], joint[key])
        result.append(joint)
    for child in parents:
        seen, cursor = set(), child
        while cursor in parents:
            if cursor in seen:
                raise ValueError("el ensamble contiene un ciclo")
            seen.add(cursor)
            cursor = parents[cursor]
    return result


def _save(doc_id: str, state: dict) -> None:
    directory = _directory(doc_id)
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / "state.tmp"
    temporary.write_text(json.dumps(state, ensure_ascii=False, allow_nan=False))
    temporary.replace(directory / "state.json")


def load(doc_id: str) -> dict | None:
    """The document's assembly state, or `None` if it has none. Raises
    `EstadoIncompatible` for a state this build cannot read, so no caller
    reaches its keys unprepared."""
    path = _directory(doc_id) / "state.json"
    return _estado_compatible(json.loads(path.read_text())) if path.exists() else None


def is_stale(doc_id: str) -> bool:
    state = load(doc_id)
    if state is None:
        return False
    try:
        return _digest(Path(state["document_path"]).read_bytes()) != state["revision"]
    except OSError:
        return True


def define(doc_id: str, path: Path, entries: list[dict], joints: list[dict]) -> dict:
    """Define an assembly once; clear explicitly before adopting a new rest pose.

    Initial joint values must be zero: definition does not move document geometry.
    """
    if load(doc_id) is not None:
        raise ValueError("el ensamble ya existe; elimínelo antes de redefinir el reposo")
    path = Path(path).resolve()
    data = path.read_bytes()
    named = _named(kernel.import_from_step(path), entries)
    normalized = _validate_joints(joints, named)
    if any(j["valor"] != 0 for j in normalized):
        raise ValueError("la definición inicial requiere valores cero; aplique la pose después")
    state = {"version": FORMAT_VERSION, "document_path": str(path), "revision": _digest(data),
             "base_sha256": _digest(data), "entries": entries, "articulaciones": normalized,
             "valores": {j["id"]: j["valor"] for j in normalized}}
    directory = _directory(doc_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "base.step").write_bytes(data)
    _save(doc_id, state)
    return state


def _values(state: dict, values: dict) -> dict[str, float]:
    if not isinstance(values, dict):
        raise ValueError("los valores deben ser un objeto por identificador")
    joints = {j["id"]: j for j in state["articulaciones"]}
    if set(values) - set(joints):
        raise ValueError("articulación desconocida en los valores")
    merged = {**state["valores"], **values}
    for jid, value in merged.items():
        value = _number(value)
        if not joints[jid]["limites"][0] <= value <= joints[jid]["limites"][1]:
            raise ValueError(f"valor fuera de límites: {jid}")
        merged[jid] = value
    return merged


def pose_shapes(doc_id: str, values: dict[str, float]) -> dict[str, Any]:
    """Return a fresh absolute pose, without changing any persisted state."""
    state = load(doc_id)
    if state is None:
        raise ValueError("el documento no tiene ensamble")
    if _digest(Path(state["document_path"]).read_bytes()) != state["revision"]:
        raise ValueError("la geometría cambió; el ensamble está desactualizado")
    base = _directory(doc_id) / "base.step"
    if _digest(base.read_bytes()) != state["base_sha256"]:
        raise ValueError("la geometría de reposo del ensamble está dañada")
    named = _named(kernel.import_from_step(base), state["entries"])
    values = _values(state, values)
    by_child = {j["hijo"]: j for j in state["articulaciones"]}
    posed = {}
    for name, shape in named.items():
        cursor = name
        # Child motion in rest coordinates, then all ancestor motions.
        while cursor in by_child:
            joint = by_child[cursor]
            value = values[joint["id"]]
            shape = transform_rigid(
                shape, joint["origen"], joint["eje"],
                value if joint["tipo"] == "giro" else 0,
                value if joint["tipo"] == "deslizamiento" else 0,
            )
            cursor = joint["padre"]
        posed[name] = shape
    return posed


def set_revision(doc_id: str, current_step_bytes: bytes, values: dict | None = None) -> dict:
    """Call only after the posed document STEP was successfully committed."""
    state = load(doc_id)
    if state is None:
        raise ValueError("el documento no tiene ensamble")
    state["valores"] = _values(state, values or {})
    state["revision"] = _digest(current_step_bytes)
    _save(doc_id, state)
    return state


def snapshot_files(doc_id: str) -> dict[str, bytes]:
    directory = _directory(doc_id)
    if not (directory / "state.json").exists():
        return {}
    return {STATE_KEY: (directory / "state.json").read_bytes(),
            BASE_KEY: (directory / "base.step").read_bytes()}


def _validar_archivos_de_ensamble(files: dict[str, bytes]) -> dict:
    """Preconditions every restore owes: both files, a state this build can
    read, and a rest STEP matching that state. Returns the parsed state."""
    if STATE_KEY not in files or BASE_KEY not in files:
        raise ValueError("snapshot de ensamble incompleto")
    state = _estado_compatible(json.loads(files[STATE_KEY]))
    if _digest(files[BASE_KEY]) != state["base_sha256"]:
        raise ValueError("snapshot de reposo dañado")
    return state


def validate_snapshot(files: dict[str, bytes]) -> None:
    """Refuse, before anything is written, a snapshot whose assembly state
    this build cannot read. `restore_files` raises on its own too, but a
    caller that applies a snapshot in several steps — `documents.
    _restaurar_documento_bloqueado` writes the STEP first and the assembly
    afterwards — calls this up front, so a refusal is a no-op instead of a
    half-applied restore. A snapshot with neither assembly file is simply a
    document without an assembly, and passes."""
    if STATE_KEY in files or BASE_KEY in files:
        _validar_archivos_de_ensamble(files)


def restore_files(doc_id: str, files: dict[str, bytes]) -> None:
    if STATE_KEY not in files and BASE_KEY not in files:
        clear(doc_id)
        return
    state = _validar_archivos_de_ensamble(files)
    directory = _directory(doc_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "base.step").write_bytes(files[BASE_KEY])
    _save(doc_id, state)


def clear(doc_id: str) -> None:
    """Remove assembly metadata only; leave the current document pose untouched."""
    directory = _directory(doc_id)
    for name in ("state.json", "base.step", "state.tmp"):
        (directory / name).unlink(missing_ok=True)
    if directory.exists():
        directory.rmdir()
