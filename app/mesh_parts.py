"""Named STL regions, independent of connected shells and CAD topology.

Stored in the existing versioned solidos.json sidecar. A fingerprint binds
exhaustive triangle ranges to the canonical float32 stream. No mesh repair,
reordering, or inference from connected components is performed here.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import numpy as np
import trimesh

import solids

SCHEMA = "mesh-parts-v1"


def load(doc_id: str) -> Any:
    """Absent metadata is legacy; corrupt metadata must never mean absent."""
    path = solids.ruta_solidos(doc_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return data if data is not None else []
    except (OSError, ValueError):
        return []  # validation rejects, viewer exposes a safe fallback


def default_name(stem: str) -> str:
    name = re.sub(r"[^\w\-áéíóúÁÉÍÓÚñÑüÜ]", "_", stem, flags=re.UNICODE)[:64]
    try:
        solids.validar_nombres([name])
    except ValueError:
        return "Malla"
    return name


def fingerprint(mesh: trimesh.Trimesh) -> str:
    return hashlib.sha256(np.asarray(mesh.triangles, dtype="<f4").tobytes()).hexdigest()


def extract(mesh: trimesh.Trimesh, start: int, count: int) -> trimesh.Trimesh:
    part = trimesh.Trimesh(vertices=mesh.vertices.copy(),
                           faces=mesh.faces[start:start + count].copy(), process=False)
    part.remove_unreferenced_vertices()
    return part


def validate(mesh: trimesh.Trimesh, entries: Any) -> list[dict[str, Any]]:
    error = "Los nombres de piezas no corresponden a esta malla; restaura su versión antes de exportar por pieza."
    if not isinstance(entries, list) or not entries:
        raise ValueError(error)
    try:
        solids.validar_nombres([e["nombre"] for e in entries])
        digest = fingerprint(mesh)
        cursor = 0
        for entry in entries:
            if entry.get("tipo") != SCHEMA or entry.get("malla_sha256") != digest:
                raise ValueError(error)
            ranges = entry["rangos"]
            if not isinstance(ranges, list) or len(ranges) != 1 or len(ranges[0]) != 2:
                raise ValueError(error)
            start, count = ranges[0]
            if type(start) is not int or type(count) is not int or start != cursor or count <= 0:
                raise ValueError(error)
            cursor += count
            if cursor > len(mesh.faces):
                raise ValueError(error)
        if cursor != len(mesh.faces):
            raise ValueError(error)
    except (KeyError, TypeError, IndexError, AttributeError) as exc:
        raise ValueError(error) from exc
    return entries


def objects(mesh: trimesh.Trimesh, entries: Any, fallback: str) -> list[tuple[str, trimesh.Trimesh]]:
    if entries is None:
        return [(default_name(fallback), mesh)]
    return [(e["nombre"], extract(mesh, *e["rangos"][0])) for e in validate(mesh, entries)]


def combine(objects_: list[tuple[str, trimesh.Trimesh]]) -> tuple[trimesh.Trimesh, list[dict[str, Any]]]:
    solids.validar_nombres([name for name, _ in objects_])
    if not objects_ or any(not len(m.faces) for _, m in objects_):
        raise ValueError("No se pueden incorporar piezas de malla vacías.")
    combined = trimesh.util.concatenate([m for _, m in objects_])
    digest = fingerprint(combined)
    entries = []
    cursor = 0
    for index, (name, part) in enumerate(objects_):
        bounds = part.bounds
        entries.append({"tipo": SCHEMA, "nombre": name, "indice": index,
                        "bbox": bounds.T.reshape(-1).tolist(),
                        "volumen": float(abs(part.volume)),
                        "fragmentos": len(trimesh.graph.connected_components(
                            part.face_adjacency, nodes=np.arange(len(part.faces)), min_len=1, engine="scipy")),
                        "rangos": [[cursor, len(part.faces)]], "malla_sha256": digest})
        cursor += len(part.faces)
    return combined, entries
