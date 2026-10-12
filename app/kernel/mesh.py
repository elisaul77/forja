"""Mesh-side helpers: OCC tessellation -> trimesh, and raw STL analysis.

Kept separate from ``b123d_kernel.py`` so B-rep (STEP) and mesh-only (STL)
code paths stay easy to tell apart (ADR-0001).
"""
from __future__ import annotations

import math
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import trimesh
from build123d import Shape, export_stl


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
    tri_mesh, indices, _omitted = _tessellate_faces(shape, tolerance, skip_invalid=False)
    return tri_mesh, indices


def tessellate_to_trimesh_con_caras_tolerante(
    shape: Shape, tolerance: float = 0.1
) -> tuple[trimesh.Trimesh, np.ndarray, list[int]]:
    """Preview invalid STEP geometry, reporting every face that could not mesh.

    The caller must show the omitted-face count. This never changes the B-rep.
    """
    return _tessellate_faces(shape, tolerance, skip_invalid=True)


def _tessellate_faces(
    shape: Shape, tolerance: float, *, skip_invalid: bool
) -> tuple[trimesh.Trimesh, np.ndarray, list[int]]:
    vertices_por_cara: list[np.ndarray] = []
    triangulos_por_cara: list[np.ndarray] = []
    indice_cara: list[int] = []
    omitted: list[int] = []
    offset = 0
    for i, face in enumerate(shape.faces()):
        try:
            vertices, triangulos = face.tessellate(tolerance)
        except (AttributeError, RuntimeError, ValueError):
            if not skip_invalid:
                raise
            omitted.append(i)
            continue
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
    return tri_mesh, np.array(indice_cara, dtype=np.uint32), omitted


# Export-only tessellation (ADR-0016). Linear deflection = bbox diagonal times
# TOLERANCIA_EXPORT_FRACCION_DIAGONAL, clamped to [MIN, MAX] mm. Angular in rad.
TOLERANCIA_EXPORT_MIN_MM = 0.005
TOLERANCIA_EXPORT_MAX_MM = 0.05
TOLERANCIA_EXPORT_FRACCION_DIAGONAL = 5e-5
TOLERANCIA_ANGULAR_EXPORT = 0.1


def tolerancia_exportacion(shape: Shape) -> float:
    """Linear deflection used for export of ``shape`` (ADR-0016)."""
    caja = shape.bounding_box()
    diagonal = math.dist(
        (caja.min.X, caja.min.Y, caja.min.Z), (caja.max.X, caja.max.Y, caja.max.Z)
    )
    return min(
        max(diagonal * TOLERANCIA_EXPORT_FRACCION_DIAGONAL, TOLERANCIA_EXPORT_MIN_MM),
        TOLERANCIA_EXPORT_MAX_MM,
    )


def tessellate_para_exportar(
    shape: Shape,
    tolerance: float | None = None,
    angular: float = TOLERANCIA_ANGULAR_EXPORT,
) -> trimesh.Trimesh:
    """Watertight mesh of a whole B-rep shape, for STL/3MF export (ADR-0016).

    The shape is tessellated once as a whole, so faces that share an edge
    share its nodes (face-by-face tessellation leaves T-junction cracks, see
    :func:`tessellate_to_trimesh_con_caras`). The STL round trip merges the
    coincident vertices (``process=True``; no networkx/shapely needed).

    Works on a copy: ``BRepTools.Clean_s`` drops the triangulation cached on
    the shape (imported STEPs may carry a coarser or finer one), and that cache
    belongs to the viewer.
    """
    solidos = shape.solids()
    if len(solidos) <= 1:
        return _tesela_solido_exportar(shape, _tolerancia_lineal(shape, tolerance), angular)
    # Several solids (a compound): each one is tessellated on its own, with its
    # own tolerance, and the meshes are concatenated without merging. Touching
    # solids then keep their own closed shells instead of sharing nodes (which
    # trimesh reads as non-manifold edges), and a tolerance that suits a big
    # neighbour cannot open a small solid.
    return trimesh.util.concatenate(
        [_tesela_solido_exportar(s, _tolerancia_lineal(s, tolerance), angular) for s in solidos]
    )


def _tolerancia_lineal(shape: Shape, tolerance: float | None) -> float:
    return tolerancia_exportacion(shape) if tolerance is None else tolerance


def _tesela_solido_exportar(shape: Shape, tolerance: float, angular: float) -> trimesh.Trimesh:
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
    from OCP.BRepTools import BRepTools

    copia_occ = BRepBuilderAPI_Copy(shape.wrapped, True, False).Shape()
    BRepTools.Clean_s(copia_occ)
    copia = Shape.cast(copia_occ)
    with tempfile.TemporaryDirectory(prefix="forja-export-") as directorio:
        ruta = Path(directorio) / "malla.stl"
        export_stl(copia, ruta, tolerance=tolerance, angular_tolerance=angular)
        malla = trimesh.load(str(ruta), file_type="stl", process=True, force="mesh")
    # OCC emits zero-area triangles at sphere poles; their doubled edges would
    # make an otherwise closed shell read as open.
    malla.update_faces(malla.nondegenerate_faces())
    malla.remove_unreferenced_vertices()
    return malla


def malla_abierta(malla: trimesh.Trimesh) -> bool:
    """True when ``malla`` has faces and is not watertight. Empty meshes are not open."""
    return len(malla.faces) > 0 and not malla.is_watertight


def mallas_abiertas(objetos: list[tuple[str, trimesh.Trimesh]]) -> list[str]:
    """Names of the meshes in ``objetos`` that are open (ADR-0016)."""
    return [nombre for nombre, malla in objetos if malla_abierta(malla)]


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
    # Count connected bodies with scipy (networkx is not in the image), without
    # materialising one Trimesh per body.
    componentes = trimesh.graph.connected_components(
        mesh_.face_adjacency, nodes=np.arange(len(mesh_.faces)), min_len=1, engine="scipy"
    )
    solidos = len(componentes)
    return MeshInfo(
        volumen=float(abs(mesh_.volume)),
        bbox=(float(xmin), float(xmax), float(ymin), float(ymax), float(zmin), float(zmax)),
        solidos=solidos or 1,
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
