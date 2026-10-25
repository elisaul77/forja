"""Clearance distinguishes contact from penetration; sections stay compact."""
import json

import trimesh

import design_geometry as geo


def box(x):
    m = trimesh.creation.box(extents=[10, 10, 10])
    m.apply_translation([x, 0, 0])
    return m


def test_touching_boxes_are_contact_not_interference():
    # 1 µm overlap: surfaces collide but shared volume is ~0.1 mm³.
    r = geo.clearance(box(0), box(9.999))
    assert r["surface_intersection"] is True
    assert r["contact"] is True
    assert r["interference"] is False


def test_overlapping_boxes_interfere():
    r = geo.clearance(box(0), box(5))
    assert r["interference"] is True
    assert r["overlap_mm3"] > 400


def test_separated_boxes():
    r = geo.clearance(box(0), box(12))
    assert r["interference"] is False and r["distance_mm"] > 1.9


def test_section_respects_point_budget():
    m = trimesh.creation.cylinder(radius=40, height=10, sections=6000)
    r = geo.section(m, [0, 0, 0], [0, 0, 1])
    pts = sum(len(line) for line in r["polylines"])
    assert r["points_total"] > geo.SECTION_POINT_BUDGET
    assert pts <= geo.SECTION_POINT_BUDGET + len(r["polylines"]) + 1
    assert len(json.dumps(r)) < 60000
