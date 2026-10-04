"""Mesh-side helpers: OCC tessellation -> trimesh, and raw STL analysis.

Kept separate from ``b123d_kernel.py`` so B-rep (STEP) and mesh-only (STL)
code paths stay easy to tell apart (ADR-0001).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import trimesh
from build123d import Shape


@dataclass
class MeshInfo:
    volumen: float
    bbox: tuple[float, float, float, float, float, float]
    solidos: int
    valido: bool


def tessellate_to_trimesh(shape: Shape, tolerance: float = 0.1) -> trimesh.Trimesh:
    """Tessellate a build123d/OCC shape into an independent trimesh mesh."""
    tri_mesh, _indices_cara = tessellate_to_trimesh_con_caras(shape, tolerance)
    return tri_mesh


def tessellate_to_trimesh_con_caras(
    shape: Shape, tolerance: float = 0.1
) -> tuple[trimesh.Trimesh, np.ndarray]:
    """Tessellate ``shape`` face-by-face (rather than as one opaque mesh),
    concatenating the per-face triangles into one mesh and returning a
    parallel ``uint32`` array (one entry per triangle) with the *index into
    ``shape.faces()``* that triangle belongs to.

    STL has no notion of shared vertices, so per-face tessellation
    (independent vertex buffers, concatenated) is a valid STL mesh; it also
    happens to be exactly what the web viewer needs to map a raycast
    triangle hit back to a stable face id (`app/naming.py`'s ``caras()``
    uses the same ``shape.faces()`` order, see `/documentos/{id}/caras` +
    `/documentos/{id}/caras_triangulos`). Volume/bbox are unaffected by the
    lack of vertex sharing (`Trimesh.volume` sums signed per-triangle
    contributions, it does not require watertight-by-shared-vertex mesh).
    """
    vertices_por_cara: list[np.ndarray] = []
    triangulos_por_cara: list[np.ndarray] = []
    indice_cara: list[int] = []
    offset = 0
    for i, face in enumerate(shape.faces()):
        vertices, triangulos = face.tessellate(tolerance)
        if not triangulos:
            continue
        verts = np.array([(v.X, v.Y, v.Z) for v in vertices], dtype=float)
        tris = np.array(triangulos, dtype=int) + offset
        vertices_por_cara.append(verts)
        triangulos_por_cara.append(tris)
        indice_cara.extend([i] * len(tris))
        offset += len(verts)

    verts_totales = np.concatenate(vertices_por_cara) if vertices_por_cara else np.zeros((0, 3))
    tris_totales = np.concatenate(triangulos_por_cara) if triangulos_por_cara else np.zeros((0, 3), dtype=int)
    tri_mesh = trimesh.Trimesh(vertices=verts_totales, faces=tris_totales, process=False)
    return tri_mesh, np.array(indice_cara, dtype=np.uint32)


def solid_indices_by_face(shape: Shape, solids: list[Shape]) -> np.ndarray | None:
    """Return the owning solid index for every face in ``shape.faces()``.

    Ownership is resolved from OpenCascade topology identity (``IsSame``),
    not from bounding boxes, face order, or geometric similarity. Hashes are
    used only to narrow candidate lookup; every candidate is confirmed with
    ``IsSame`` because hashes can collide. ``None`` means at least one face
    is unowned or shared by more than one solid, so callers must not assign
    component names to the mesh.

    This lives beside face tessellation so callers can map triangle face
    indices from :func:`tessellate_to_trimesh_con_caras` without tessellating
    geometry a second time.
    """
    if not solids:
        return None

    candidates: dict[int, list[tuple[int, object]]] = {}
    for solid_index, solid in enumerate(solids):
        for face in solid.faces():
            topology = face.wrapped
            candidates.setdefault(hash(topology), []).append((solid_index, topology))

    indices: list[int] = []
    for face in shape.faces():
        topology = face.wrapped
        owners = {
            index
            for index, candidate in candidates.get(hash(topology), [])
            if topology.IsSame(candidate)
        }
        if len(owners) != 1:
            return None
        indices.append(next(iter(owners)))

    if not indices:
        return None
    return np.asarray(indices, dtype=np.uint32)


def to_stl_bytes(mesh_: trimesh.Trimesh) -> bytes:
    """Binary STL bytes for a trimesh mesh (used by the future ``/malla`` endpoint)."""
    return mesh_.export(file_type="stl")


def _mesh_info(mesh_: trimesh.Trimesh) -> MeshInfo:
    xmin, ymin, zmin = mesh_.bounds[0]
    xmax, ymax, zmax = mesh_.bounds[1]
    solidos = mesh_.split(only_watertight=False)
    return MeshInfo(
        volumen=float(abs(mesh_.volume)),
        bbox=(float(xmin), float(xmax), float(ymin), float(ymax), float(zmin), float(zmax)),
        solidos=len(solidos) or 1,
        valido=bool(mesh_.is_watertight),
    )


def load_stl(path: str | Path) -> trimesh.Trimesh:
    """Load an STL file (ASCII or binary) into an independent trimesh mesh.

    Shared by ``analyze_stl_path`` and the ``/malla`` viewer endpoint, which
    re-exports the result through :func:`to_stl_bytes` to guarantee the
    binary STL transport contract regardless of the original file's flavor.
    """
    return trimesh.load(str(path), file_type="stl", force="mesh")


def analyze_stl_path(path: str | Path) -> MeshInfo:
    mesh_ = load_stl(path)
    return _mesh_info(mesh_)
