"""Phase 1 kernel smoke tests: build123d/OCP + manifold3d + trimesh/fcl wiring."""
from __future__ import annotations

import pytest

from kernel import b123d_kernel, mesh


def test_dependencies_importable():
    import build123d  # noqa: F401
    import OCP  # noqa: F401
    import manifold3d  # noqa: F401
    import trimesh  # noqa: F401
    import fcl  # noqa: F401


def test_box_volume_matches_trimesh_tessellation():
    box = b123d_kernel.make_box(10, 20, 30)
    info = b123d_kernel.analyze(box)
    analytic_volume = 10 * 20 * 30
    assert info.volumen == pytest.approx(analytic_volume, rel=1e-6)
    assert info.solidos == 1
    assert info.valido is True

    tri_mesh = mesh.tessellate_to_trimesh(box, tolerance=0.05)
    rel_error = abs(tri_mesh.volume - analytic_volume) / analytic_volume
    assert rel_error < 1e-3


def test_step_roundtrip_preserves_volume(tmp_path):
    box = b123d_kernel.make_box(15, 5, 7)
    original_volume = b123d_kernel.analyze(box).volumen

    step_path = tmp_path / "box.step"
    b123d_kernel.export_to_step(box, step_path)
    reimported = b123d_kernel.import_from_step(step_path)
    reimported_volume = b123d_kernel.analyze(reimported).volumen

    rel_error = abs(reimported_volume - original_volume) / original_volume
    assert rel_error < 1e-3
