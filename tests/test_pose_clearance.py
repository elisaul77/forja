"""Exact sampled-pose checks, including the read-only REST boundary."""
from __future__ import annotations

from contextlib import nullcontext

import pytest
from fastapi.testclient import TestClient

import assembly_routes
import documents
from checks import pose_clearance
from kernel import b123d_kernel as kernel
from main import app


def scene(position):
    return {"base": kernel.make_box(2, 2, 2),
            "arm": kernel.make_box(2, 2, 2).translate((position, 0, 0))}


def test_exact_gap_contact_and_intersection():
    gap = pose_clearance.check_pose(scene(2.5), {"arm"})
    assert gap["completo"] is True
    assert gap["holgura_minima_mm"] == pytest.approx(0.5)
    assert gap["choques"] == []
    contact = pose_clearance.check_pose(scene(2), {"arm"})
    assert contact["contactos"] == [{"a": "arm", "b": "base", "holgura_mm": 0.0}]
    assert contact["choques"] == []
    collision = pose_clearance.check_pose(scene(1), {"arm"})
    assert collision["choques"] == [{"a": "arm", "b": "base", "volumen_mm3": 4.0}]
    assert collision["holgura_minima_mm"] is None


def test_local_radius_and_honest_work_cap():
    far = pose_clearance.check_pose(scene(4), {"arm"})
    assert far["completo"] is True
    assert far["pares_en_radio"] == 0
    assert far["holgura_minima_mm"] is None
    limited = pose_clearance.check_pose(scene(1), {"arm"}, max_exact_pairs=0)
    assert limited["completo"] is False
    assert limited["pares_comprobados"] == 0
    assert limited["pares_en_radio"] == 1


def test_descendants_are_part_of_moving_rigid_group():
    joints = [
        {"id": "slide", "padre": "base", "hijo": "arm"},
        {"id": "fixed", "padre": "arm", "hijo": "tip"},
        {"id": "other", "padre": "base", "hijo": "shield"},
    ]
    assert pose_clearance.moving_names(joints, "slide") == {"arm", "tip"}
    with pytest.raises(ValueError, match="desconocida"):
        pose_clearance.moving_names(joints, "missing")


def test_api_checks_current_revision_without_write_or_token(monkeypatch):
    monkeypatch.setattr(assembly_routes, "_path", lambda doc_id: None)
    monkeypatch.setattr(assembly_routes.parametros, "bloqueo", lambda doc_id: nullcontext())
    monkeypatch.setattr(assembly_routes, "_estado_actual", lambda doc_id: {
        "articulaciones": [{"id": "slide", "padre": "base", "hijo": "arm"}],
    })
    monkeypatch.setattr(assembly_routes.assemblies, "is_stale", lambda doc_id: False)
    calls = []
    def posed(doc_id, values):
        calls.append(values)
        return scene(10 + values["slide"])
    monkeypatch.setattr(assembly_routes.assemblies, "pose_shapes", posed)
    monkeypatch.setitem(documents._revisiones, "unit-pose", "revision-1")
    client = TestClient(app)
    url = "/documentos/unit-pose/ensamble/comprobar-posicion"
    stale = client.get(url, params={"articulacion": "slide", "valor": -9,
                                    "revision": "old"})
    assert stale.status_code == 409
    assert calls == []
    checked = client.get(url, params={"articulacion": "slide", "valor": -9,
                                      "revision": "revision-1"})
    assert checked.status_code == 200, checked.text
    assert checked.json()["choques"][0]["volumen_mm3"] == pytest.approx(4)
    assert checked.headers["Cache-Control"] == "no-store"
    assert calls == [{"slide": -9}]
