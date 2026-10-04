"""Focused tests for the FJP1 per-piece mesh transport."""
from __future__ import annotations

import io
import json
import struct
import tempfile
from pathlib import Path

from build123d import Box, Compound, Pos
from fastapi.testclient import TestClient

import solids
from kernel import b123d_kernel, mesh
from main import app
import piece_mesh

client = TestClient(app)


def _upload_step(shape, name="piezas.step") -> str:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / name
        b123d_kernel.export_to_step(shape, path)
        response = client.post(
            "/documentos",
            files={"file": (name, io.BytesIO(path.read_bytes()), "application/step")},
        )
    assert response.status_code == 200
    return response.json()["id"]


def _read_fjp1(data: bytes) -> tuple[dict, bytes]:
    assert data[:4] == b"FJP1"
    header_length = struct.unpack("<I", data[4:8])[0]
    header_end = 8 + header_length
    manifest = json.loads(data[8:header_end].decode("utf-8"))
    stl = data[header_end:]
    assert len(stl) >= 84
    assert struct.unpack("<I", stl[80:84])[0] == manifest["triangulos"]
    assert len(stl) == 84 + 50 * manifest["triangulos"]
    return manifest, stl


def _assert_ranges_cover_mesh(manifest: dict) -> None:
    ranges = sorted(
        (start, count)
        for piece in manifest["piezas"]
        for start, count in piece["rangos"]
    )
    cursor = 0
    for start, count in ranges:
        assert start == cursor
        assert count > 0
        cursor += count
    assert cursor == manifest["triangulos"]


def test_step_component_envelope_maps_faces_and_preserves_default_stl_order():
    shape = Compound(children=[Box(2, 2, 2), Pos(10, 0, 0) * Box(3, 3, 3)])
    doc_id = _upload_step(shape)

    entries = solids.cargar(doc_id)
    assert entries and len(entries) == 2
    entries[0]["nombre"] = "pieza_a"
    entries[1]["nombre"] = "pieza_b"
    solids.guardar(doc_id, entries)

    component_response = client.get(f"/documentos/{doc_id}/malla?componentes=true")
    default_response = client.get(f"/documentos/{doc_id}/malla")
    assert component_response.status_code == 200
    assert component_response.headers["content-type"].startswith("application/vnd.forja.piezas")
    assert default_response.headers["content-type"] == "model/stl"

    manifest, embedded_stl = _read_fjp1(component_response.content)
    assert embedded_stl == default_response.content
    assert manifest["version"] == 1
    assert manifest["identificadas"] is True
    assert manifest["nombres_inciertos"] is False
    assert [piece["nombre"] for piece in manifest["piezas"]] == ["pieza_a", "pieza_b"]
    pieces = {piece["nombre"]: piece for piece in manifest["piezas"]}
    assert all(piece["volumen"] > 0 for piece in pieces.values())
    expected_bounds = {
        "pieza_a": [-1, 1, -1, 1, -1, 1],
        "pieza_b": [8.5, 11.5, -1.5, 1.5, -1.5, 1.5],
    }
    for name, expected in expected_bounds.items():
        assert all(abs(actual - target) < 0.01 for actual, target in zip(pieces[name]["bbox"], expected))
    _assert_ranges_cover_mesh(manifest)


def test_step_repeated_name_groups_disconnected_solid_fragments():
    grouped = Compound(children=[Box(1, 1, 1), Pos(4, 0, 0) * Box(2, 2, 2)])
    shape = Compound(children=[grouped, Pos(10, 0, 0) * Box(3, 3, 3)])
    doc_id = _upload_step(shape)

    entries = solids.cargar(doc_id)
    assert entries and len(entries) == 3
    entries[0]["nombre"] = "soporte"
    entries[1]["nombre"] = "soporte"
    entries[2]["nombre"] = "carcasa"
    solids.guardar(doc_id, entries)

    response = client.get(f"/documentos/{doc_id}/malla?componentes=true")
    assert response.status_code == 200
    manifest, _stl = _read_fjp1(response.content)
    assert [piece["nombre"] for piece in manifest["piezas"]] == ["soporte", "carcasa"]
    assert manifest["piezas"][0]["fragmentos"] == 2
    assert abs(manifest["piezas"][0]["volumen"] - 9.0) < 0.01
    _assert_ranges_cover_mesh(manifest)


def test_stl_component_envelope_is_explicitly_one_unnamed_mesh():
    source = mesh.to_stl_bytes(mesh.tessellate_to_trimesh(Box(8, 8, 8)))
    response = client.post(
        "/documentos",
        files={"file": ("malla.stl", io.BytesIO(source), "application/sla")},
    )
    assert response.status_code == 200
    doc_id = response.json()["id"]

    component_response = client.get(f"/documentos/{doc_id}/malla?componentes=true")
    default_response = client.get(f"/documentos/{doc_id}/malla")
    manifest, embedded_stl = _read_fjp1(component_response.content)
    assert embedded_stl == default_response.content
    assert manifest["identificadas"] is False
    assert manifest["nombres_inciertos"] is False
    assert manifest["piezas"][0]["nombre"] == "Malla completa"
    assert "no conserva nombres" in manifest["aviso"]
    _assert_ranges_cover_mesh(manifest)


def test_ambiguous_shared_face_mapping_falls_back_to_the_whole_mesh(monkeypatch):
    class Topology:
        def __hash__(self):
            return 7

        def IsSame(self, other):
            return self is other

    shared_topology = Topology()

    class Face:
        wrapped = shared_topology

    class Solid:
        def faces(self):
            return [Face()]

    class ShapeWithSharedFace:
        def faces(self):
            return [Face()]

    assert mesh.solid_indices_by_face(
        ShapeWithSharedFace(), [Solid(), Solid()]
    ) is None

    shape = Box(4, 4, 4)
    entries = solids.construir_entradas(shape)
    monkeypatch.setattr(mesh, "solid_indices_by_face", lambda *_args: None)
    payload = piece_mesh.from_step(
        shape,
        entries,
        metadata_available=True,
        bbox=[-2, 2, -2, 2, -2, 2],
        volume=64,
    )
    manifest, _stl = _read_fjp1(payload)
    assert manifest["identificadas"] is False
    assert manifest["piezas"][0]["nombre"] == "Modelo completo"
    assert "caras compartidas" in manifest["aviso"]
    _assert_ranges_cover_mesh(manifest)


def test_uncertain_solid_names_are_explicitly_marked():
    shape = Box(5, 5, 5)
    invalid_entries = [{
        "nombre": "nombre_original",
        "indice": 0,
        "bbox": [100, 101, 100, 101, 100, 101],
        "volumen": 1,
    }]
    payload = piece_mesh.from_step(
        shape,
        invalid_entries,
        metadata_available=True,
        bbox=[-2.5, 2.5, -2.5, 2.5, -2.5, 2.5],
        volume=125,
    )
    manifest, _stl = _read_fjp1(payload)
    assert manifest["identificadas"] is False
    assert manifest["nombres_inciertos"] is True
    assert manifest["piezas"][0]["nombre"] == "solido_1"
    assert "identificadores genéricos" in manifest["aviso"]
    _assert_ranges_cover_mesh(manifest)
