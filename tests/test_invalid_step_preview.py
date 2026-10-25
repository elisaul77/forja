"""Invalid faces may be omitted only from a labeled preview, never CAD."""

import pytest

from kernel import mesh


class _Point:
    X = 0.0
    Y = 0.0
    Z = 0.0


class _GoodFace:
    def tessellate(self, _tolerance):
        return [_Point(), _Point(), _Point()], [(0, 1, 2)]


class _BadFace:
    def tessellate(self, _tolerance):
        raise AttributeError("missing triangulation")


class _Shape:
    def faces(self):
        return [_GoodFace(), _BadFace()]


def test_tolerant_preview_keeps_face_ids_and_reports_bad_face():
    geometry, owners, omitted = mesh.tessellate_to_trimesh_con_caras_tolerante(_Shape())
    assert len(geometry.faces) == 1
    assert owners.tolist() == [0]
    assert omitted == [1]


def test_normal_cad_tessellation_still_rejects_bad_face():
    with pytest.raises(AttributeError, match="missing triangulation"):
        mesh.tessellate_to_trimesh_con_caras(_Shape())
