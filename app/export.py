"""Per-solid export with dedup for identical geometry (Phase 5B, feedback
item 6): one file per named piece, but geometrically-identical pieces
(placement-invariant, e.g. 4 chairs built from the same function at
different positions/rotations) collapse into ONE file, with the response
mapping that file to every name it represents.

3MF (Phase 5C) is the exception: `exportar_3mf_documento` writes ONE
package with one named object per piece (what a slicer wants), written by
hand (`escribir_3mf`) since trimesh's 3MF exporter needs `networkx`.

Filenames are built ONLY from solid names, which `solids.validar_nombres`
already restricts to a safe character set (no slashes/dots/spaces) — this
module additionally confines every candidate filename to the target
directory via a ``realpath`` check (defense in depth; the Phase 5A review
specifically flagged per-solid export filenames as a path-injection
surface).
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any
from xml.sax.saxutils import quoteattr

import trimesh

import solids
from kernel import b123d_kernel
from kernel import mesh as kernel_mesh

TOLERANCIA_TESSELLATION_MM = 0.1

# Rounding used to build the placement-invariant dedup signature: volume and
# surface area are exact geometric invariants; sorted bounding-box extents
# are invariant under axis-aligned rotations (any multiple of 90 degrees, the
# common case for furniture placed in a scene) but NOT under an arbitrary
# rotation, which would genuinely change the AABB — an acceptable limit for
# a cheap signature (see `docs/feedback-casa-munecas-2026-09-27.md`, item 6:
# every duplicated piece in the real dollhouse is placed at 0/90/180/270
# degrees around Z).
_DECIMALES_FIRMA = 2

FORMATOS_SOPORTADOS = ("stl", "step", "3mf")


_NS_3MF_CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
_TIPO_REL_3MF = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
_CONTENT_TYPES_3MF = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
    "</Types>"
)
_RELS_3MF = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    f'<Relationship Target="/3D/3dmodel.model" Id="rel0" Type="{_TIPO_REL_3MF}"/>'
    "</Relationships>"
)
_TOPE_OBJETOS_RESPUESTA = 30


def bytes_3mf(objetos: list[tuple[str, trimesh.Trimesh]]) -> bytes:
    """Build a 3MF package by hand, in memory (Phase 5C, no new dependency: trimesh's
    own 3MF exporter needs `networkx`, which is not in this image).

    One ``<object type="model" name="...">`` per entry -- the ``name``
    attribute is what OrcaSlicer/PrusaSlicer/Bambu show as the object name
    -- each with its own mesh (vertices merged, so slicers see a closed
    manifold), all placed by one ``<build><item>`` each in their original
    world coordinates (an assembly keeps its layout; the slicer can still
    arrange/split). Units: millimetre. Package parts: ``[Content_Types].xml``,
    ``_rels/.rels`` and ``3D/3dmodel.model``.
    """
    partes: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>\n',
        f'<model unit="millimeter" xml:lang="en-US" xmlns="{_NS_3MF_CORE}">',
        "<resources>",
    ]
    for i, (nombre, malla) in enumerate(objetos, start=1):
        malla = malla.copy()
        malla.merge_vertices()
        # Merging can collapse a sliver triangle onto a repeated vertex
        # index (v1 == v2); slicers reject those, so drop them (fix-review).
        malla.update_faces(malla.nondegenerate_faces())
        malla.remove_unreferenced_vertices()
        partes.append(f'<object id="{i}" name={quoteattr(nombre)} type="model"><mesh><vertices>')
        partes.extend(
            f'<vertex x="{x:.6f}" y="{y:.6f}" z="{z:.6f}"/>' for x, y, z in malla.vertices.tolist()
        )
        partes.append("</vertices><triangles>")
        partes.extend(
            f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in malla.faces.tolist()
        )
        partes.append("</triangles></mesh></object>")
    partes.append("</resources><build>")
    partes.extend(f'<item objectid="{i}"/>' for i in range(1, len(objetos) + 1))
    partes.append("</build></model>")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES_3MF)
        zf.writestr("_rels/.rels", _RELS_3MF)
        zf.writestr("3D/3dmodel.model", "".join(partes))
    return buffer.getvalue()


def escribir_3mf(objetos: list[tuple[str, trimesh.Trimesh]], destino: Path) -> None:
    """Write the package built by `bytes_3mf` to ``destino`` (Phase 5C)."""
    destino.write_bytes(bytes_3mf(objetos))


def objetos_3mf(
    shape: Any,
    entradas: list[dict[str, Any]] | None,
    nombre_por_defecto: str = "pieza",
    tolerancia: float = TOLERANCIA_TESSELLATION_MM,
) -> tuple[list[tuple[str, trimesh.Trimesh]], bool]:
    """The named objects of a document's 3MF: ``([(nombre, malla)], nombres_inciertos)``.
    Shared by the file export and the in-memory download so both produce
    the same object names and order."""
    nombres_inciertos = False
    if isinstance(shape, trimesh.Trimesh):
        objetos = [(nombre_por_defecto, shape)]
    elif not entradas:
        objetos = [(nombre_por_defecto, kernel_mesh.tessellate_to_trimesh(shape, tolerancia))]
    else:
        grupos, nombres_inciertos = solids.solidos_por_indice(shape, entradas)
        por_nombre: dict[str, list[trimesh.Trimesh]] = {}
        for nombre, _indice, solido, _bbox, _volumen in grupos:
            por_nombre.setdefault(nombre, []).append(
                kernel_mesh.tessellate_to_trimesh(solido, tolerancia)
            )
        objetos = [
            (nombre, mallas[0] if len(mallas) == 1 else trimesh.util.concatenate(mallas))
            for nombre, mallas in por_nombre.items()
        ]
    return objetos, nombres_inciertos


def exportar_3mf_documento(
    shape: Any,
    entradas: list[dict[str, Any]] | None,
    destino: Path,
    nombre_por_defecto: str = "pieza",
    tolerancia: float = TOLERANCIA_TESSELLATION_MM,
) -> dict[str, Any]:
    """ONE 3MF with one named object per named piece (a piece split into
    several disconnected solids stays one object, its meshes concatenated
    -- no boolean needed for a mesh container). This is the answer for both
    ``por_solido=false`` and ``por_solido=true``: a multi-object 3MF is
    exactly what a slicer wants (per-object settings/placement), unlike
    STL/STEP where "per solid" means one file each. ``shape`` may also be a
    ready ``trimesh.Trimesh`` (STL documents) with ``entradas=None``.

    Returns ``{ruta, tamano_bytes, objetos[, objetos_mas][, nombres_inciertos]}``.
    """
    objetos, nombres_inciertos = objetos_3mf(shape, entradas, nombre_por_defecto, tolerancia)
    escribir_3mf(objetos, destino)
    nombres = [nombre for nombre, _ in objetos]
    respuesta: dict[str, Any] = {
        "ruta": str(destino),
        "tamano_bytes": destino.stat().st_size,
        "objetos": nombres[:_TOPE_OBJETOS_RESPUESTA],
    }
    if len(nombres) > _TOPE_OBJETOS_RESPUESTA:
        respuesta["objetos_mas"] = len(nombres) - _TOPE_OBJETOS_RESPUESTA
    if nombres_inciertos:
        respuesta["nombres_inciertos"] = True
    return respuesta


def _firma(bbox: list[float], volumen: float, area: float) -> tuple:
    xmin, xmax, ymin, ymax, zmin, zmax = bbox
    dims = sorted((xmax - xmin, ymax - ymin, zmax - zmin))
    return (
        round(volumen, _DECIMALES_FIRMA),
        round(area, _DECIMALES_FIRMA),
        tuple(round(d, _DECIMALES_FIRMA) for d in dims),
    )


def _nombre_archivo_seguro(nombre: str, formato: str, directorio: Path) -> str:
    """``{nombre}.{formato}`` confined to ``directorio`` (realpath check) —
    ``nombre`` is already regex-validated by `solids.validar_nombres`
    (letters/digits/_/-/accents only, no slashes/dots/..), but this checks
    the actual resolved path anyway rather than trusting that alone."""
    archivo = f"{nombre}.{formato}"
    directorio_resuelto = directorio.resolve()
    candidato = (directorio / archivo).resolve()
    if candidato.parent != directorio_resuelto or candidato.name != archivo:
        raise ValueError(f"nombre de solido produce una ruta de archivo no segura: {nombre!r}")
    return archivo


def _escribir(solido: Any, formato: str, destino: Path, tolerancia: float) -> None:
    if formato == "step":
        b123d_kernel.export_to_step(solido, destino)
    elif formato == "stl":
        tri_mesh = kernel_mesh.tessellate_to_trimesh(solido, tolerancia)
        destino.write_bytes(kernel_mesh.to_stl_bytes(tri_mesh))
    elif formato == "3mf":
        escribir_3mf([(destino.stem, kernel_mesh.tessellate_to_trimesh(solido, tolerancia))], destino)
    else:
        raise ValueError(f"formato no soportado: {formato!r} (usar stl, step o 3mf)")


def exportar_por_solido(
    shape: Any,
    entradas: list[dict[str, Any]],
    formato: str,
    directorio: Path,
    tolerancia: float = TOLERANCIA_TESSELLATION_MM,
) -> tuple[dict[str, list[str]], bool]:
    """Write one file per named piece into ``directorio`` (created if
    missing), deduplicating geometrically-identical pieces. Returns
    ``({nombre_de_archivo: [nombres_de_solido, ...]}, nombres_inciertos)`` —
    a file mapped to more than one name means those names are the same
    geometry; ``nombres_inciertos`` (Phase 5B fix-review) is true when
    `solids.solidos_por_indice` couldn't confidently match the stored names
    to this reimport's geometry and fell back to flat ``solido_N`` naming —
    callers must surface that rather than silently export mislabeled files.

    A piece whose name maps to more than one disconnected solid (see
    `app/solids.py`'s "partidas"/astilla grouping) is exported as a single
    file: its fragments are unioned first, exactly like `resumen_documento`
    already treats them as one logical piece.
    """
    if formato not in FORMATOS_SOPORTADOS:
        raise ValueError(f"formato no soportado: {formato!r} (usar stl, step o 3mf)")

    grupos, nombres_inciertos = solids.solidos_por_indice(shape, entradas)
    por_nombre: dict[str, list[Any]] = {}
    orden: list[str] = []
    for nombre, _indice, solido, _bbox, _volumen in grupos:
        if nombre not in por_nombre:
            por_nombre[nombre] = []
            orden.append(nombre)
        por_nombre[nombre].append(solido)

    directorio.mkdir(parents=True, exist_ok=True)

    firma_a_archivo: dict[tuple, str] = {}
    archivo_a_nombres: dict[str, list[str]] = {}

    for nombre in orden:
        piezas = por_nombre[nombre]
        combinado = piezas[0]
        for extra in piezas[1:]:
            combinado = combinado + extra

        bbox_combinado = combinado.bounding_box()
        bbox = [
            bbox_combinado.min.X,
            bbox_combinado.max.X,
            bbox_combinado.min.Y,
            bbox_combinado.max.Y,
            bbox_combinado.min.Z,
            bbox_combinado.max.Z,
        ]
        firma = _firma(bbox, combinado.volume, combinado.area)

        if firma in firma_a_archivo:
            archivo = firma_a_archivo[firma]
        else:
            archivo = _nombre_archivo_seguro(nombre, formato, directorio)
            firma_a_archivo[firma] = archivo
            _escribir(combinado, formato, directorio / archivo, tolerancia)
        archivo_a_nombres.setdefault(archivo, []).append(nombre)

    return archivo_a_nombres, nombres_inciertos
