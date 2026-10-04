"""Rigid assembly transforms in the rest coordinate frame."""
from build123d import Axis


def transform_rigid(shape, origin, axis, angle_deg, displacement_mm):
    if angle_deg:
        shape = shape.rotate(Axis(origin, axis), angle_deg)
    if displacement_mm:
        shape = shape.translate(tuple(v * displacement_mm for v in axis))
    return shape
