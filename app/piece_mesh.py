"""Component manifest and transport for a STEP document's canonical STL.

The triangle stream is produced once by ``kernel.mesh``. Its face-to-solid
map is based on OpenCascade topology identity, so the ranges refer to exactly
the same triangle order returned by the ordinary ``/malla`` route.
"""
from __future__ import annotations

import json
import struct
from typing import Any

import solids
from kernel import mesh as kernel_mesh

_MAGIC = b"FJP1"


def _serialize(manifest: dict[str, Any], stl_bytes: bytes) -> bytes:
    header = json.dumps(
        manifest, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    if len(header) > 0xFFFFFFFF:
        raise ValueError("el manifiesto de piezas es demasiado grande")
    return _MAGIC + struct.pack("<I", len(header)) + header + stl_bytes


def _bbox_union(
    groups: list[tuple[str, int, Any, list[float], float]], name: str
) -> list[float]:
    entries = [group[3] for group in groups if group[0] == name]
    return [
        min(bbox[0] for bbox in entries),
        max(bbox[1] for bbox in entries),
        min(bbox[2] for bbox in entries),
        max(bbox[3] for bbox in entries),
        min(bbox[4] for bbox in entries),
        max(bbox[5] for bbox in entries),
    ]


def _whole_model(
    stl_bytes: bytes,
    triangle_count: int,
    bbox: list[float],
    volume: float,
    fragments: int,
    notice: str,
    names_uncertain: bool = True,
) -> bytes:
    ranges = [[0, triangle_count]] if triangle_count else []
    manifest = {
        "version": 1,
        "triangulos": triangle_count,
        "identificadas": False,
        "nombres_inciertos": names_uncertain,
        "aviso": notice,
        "piezas": [{
            "nombre": "Modelo completo",
            "bbox": bbox,
            "volumen": volume,
            "fragmentos": fragments,
            "rangos": ranges,
        }],
    }
    return _serialize(manifest, stl_bytes)


def from_step(
    shape: Any,
    entries: list[dict[str, Any]] | None,
    *,
    metadata_available: bool,
    bbox: list[float],
    volume: float,
) -> bytes:
    """Build the FJP1 envelope for a STEP shape, tessellating it once.

    If trusted names are missing, generic names may still be shown, but the
    manifest marks them uncertain. If topology cannot prove a unique owner
    for every face, the full model is returned as one safe component.
    """
    tri_mesh, triangle_faces = kernel_mesh.tessellate_to_trimesh_con_caras(shape)
    stl_bytes = kernel_mesh.to_stl_bytes(tri_mesh)
    triangle_count = len(tri_mesh.faces)
    fallback_fragments = max(1, len(shape.solids()))

    if not entries:
        entries = solids.construir_entradas(shape)
        metadata_available = False

    try:
        indexed_solids, names_uncertain = solids.solidos_por_indice(shape, entries)
    except (ValueError, KeyError, TypeError):
        return _whole_model(
            stl_bytes, triangle_count, bbox, volume, fallback_fragments,
            "No se pudo confirmar la correspondencia entre nombres y geometría; se muestra el modelo completo.",
        )

    solid_indices = [item[2] for item in indexed_solids]
    face_owners = kernel_mesh.solid_indices_by_face(shape, solid_indices)
    if (
        face_owners is None
        or len(face_owners) != len(shape.faces())
        or len(triangle_faces) != triangle_count
        or (triangle_count and int(triangle_faces.max()) >= len(face_owners))
    ):
        return _whole_model(
            stl_bytes, triangle_count, bbox, volume, fallback_fragments,
            "Hay caras compartidas o sin una pieza propietaria inequívoca; se muestra el modelo completo.",
        )

    if triangle_count == 0:
        return _whole_model(
            stl_bytes, 0, bbox, volume, fallback_fragments,
            "El modelo no produjo triángulos; se muestra como una sola pieza.",
        )

    owners_by_triangle = face_owners[triangle_faces]
    if len(owners_by_triangle) != triangle_count:
        return _whole_model(
            stl_bytes, triangle_count, bbox, volume, fallback_fragments,
            "No se pudo confirmar el orden de las caras; se muestra el modelo completo.",
        )

    names = [item[0] for item in indexed_solids]
    if not names or int(owners_by_triangle.max()) >= len(names):
        return _whole_model(
            stl_bytes, triangle_count, bbox, volume, fallback_fragments,
            "No se pudo confirmar la identidad de las piezas; se muestra el modelo completo.",
        )

    order: list[str] = []
    fragments_by_name: dict[str, int] = {}
    volume_by_name: dict[str, float] = {}
    for name, _index, _solid, _solid_bbox, solid_volume in indexed_solids:
        if name not in fragments_by_name:
            order.append(name)
            fragments_by_name[name] = 0
            volume_by_name[name] = 0.0
        fragments_by_name[name] += 1
        volume_by_name[name] += float(solid_volume)

    ranges_by_name: dict[str, list[list[int]]] = {name: [] for name in order}
    triangle_names = [names[int(owner)] for owner in owners_by_triangle]
    start = 0
    current_name = triangle_names[0]
    for position, name in enumerate(triangle_names[1:], start=1):
        if name != current_name:
            ranges_by_name[current_name].append([start, position - start])
            start, current_name = position, name
    ranges_by_name[current_name].append([start, triangle_count - start])

    # Fail closed if any validated solid has no triangles, or if generated
    # ranges do not form one exact, non-overlapping cover of the STL facets.
    flattened_ranges = sorted(
        (start, count)
        for piece_ranges in ranges_by_name.values()
        for start, count in piece_ranges
    )
    cursor = 0
    for range_start, count in flattened_ranges:
        if range_start != cursor or count <= 0:
            return _whole_model(
                stl_bytes, triangle_count, bbox, volume, fallback_fragments,
                "Los rangos de triángulos no se pudieron confirmar; se muestra el modelo completo.",
            )
        cursor += count
    if cursor != triangle_count or any(not ranges_by_name[name] for name in order):
        return _whole_model(
            stl_bytes, triangle_count, bbox, volume, fallback_fragments,
            "No todas las piezas tienen una región de malla confirmada; se muestra el modelo completo.",
        )

    pieces = [
        {
            "nombre": name,
            "bbox": _bbox_union(indexed_solids, name),
            "volumen": volume_by_name[name],
            "fragmentos": fragments_by_name[name],
            "rangos": ranges_by_name[name],
        }
        for name in order
    ]
    manifest: dict[str, Any] = {
        "version": 1,
        "triangulos": triangle_count,
        "identificadas": metadata_available and not names_uncertain,
        "nombres_inciertos": names_uncertain or not metadata_available,
        "piezas": pieces,
    }
    if names_uncertain or not metadata_available:
        manifest["aviso"] = (
            "Los nombres originales no se pudieron confirmar; las piezas se muestran con identificadores genéricos."
        )
    return _serialize(manifest, stl_bytes)


def from_stl(
    tri_mesh: Any, *, bbox: list[float], volume: float
) -> bytes:
    """Wrap an STL-only document as one explicitly unnamed mesh component."""
    stl_bytes = kernel_mesh.to_stl_bytes(tri_mesh)
    triangle_count = len(tri_mesh.faces)
    manifest = {
        "version": 1,
        "triangulos": triangle_count,
        "identificadas": False,
        "nombres_inciertos": False,
        "aviso": "El archivo STL no conserva nombres de piezas; se muestra la malla completa como una sola pieza.",
        "piezas": [{
            "nombre": "Malla completa",
            "bbox": bbox,
            "volumen": volume,
            "fragmentos": 1,
            "rangos": [[0, triangle_count]] if triangle_count else [],
        }],
    }
    return _serialize(manifest, stl_bytes)
