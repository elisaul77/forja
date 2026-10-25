"""Several rigid joints can move together and meet at sampled contact."""
from contextlib import nullcontext

from fastapi.testclient import TestClient

import assembly_routes
import assemblies
from checks import pose_clearance
from kernel import b123d_kernel as kernel
from main import app


def test_scene_detects_collision_between_two_driven_pieces():
    named = {
        "left": kernel.make_box(2, 2, 2),
        "right": kernel.make_box(2, 2, 2).translate((1, 0, 0)),
        "fixed": kernel.make_box(2, 2, 2).translate((20, 0, 0)),
    }
    checked = pose_clearance.check_scene(named, {"left", "right"})
    assert checked["completo"] is True
    assert checked["choques"] == [{"a": "left", "b": "right", "volumen_mm3": 4.0}]


def test_multi_joint_poses_reuse_rest_without_accumulating_motion():
    state = {"articulaciones": [
        {"id": "spin", "tipo": "giro", "padre": "base", "hijo": "arm",
         "origen": [0, 0, 0], "eje": [0, 0, 1], "limites": [0, 90]},
        {"id": "slide", "tipo": "deslizamiento", "padre": "arm", "hijo": "tip",
         "origen": [0, 0, 0], "eje": [1, 0, 0], "limites": [0, 10]},
    ], "valores": {"spin": 0, "slide": 0}}
    rest = {"base": kernel.make_box(1, 1, 1),
            "arm": kernel.make_box(1, 1, 1).translate((3, 0, 0)),
            "tip": kernel.make_box(1, 1, 1).translate((4, 0, 0))}
    moved = assemblies.pose_shapes_from_rest(state, rest, {"spin": 90, "slide": 2})
    assert moved["tip"].bounding_box().min.Y == 5.5
    returned = assemblies.pose_shapes_from_rest(state, rest, {"spin": 0, "slide": 0})
    assert returned["tip"].bounding_box().min.X == 3.5


def test_sampled_motion_stops_before_first_new_penetration(monkeypatch):
    state = {"articulaciones": [{"id": "slide", "tipo": "deslizamiento",
                                "padre": "base", "hijo": "arm", "limites": [-4, 0]}],
             "valores": {"slide": 0}}
    rest = {"base": kernel.make_box(2, 2, 2),
            "arm": kernel.make_box(2, 2, 2).translate((4, 0, 0))}
    monkeypatch.setattr(assembly_routes, "_path", lambda _doc_id: None)
    monkeypatch.setattr(assembly_routes, "_scene_context", lambda _doc_id, _revision: (state, rest))
    monkeypatch.setattr(assembly_routes.parametros, "bloqueo", lambda _doc_id: nullcontext())
    def pose(_state, _rest, values):
        return {"base": rest["base"], "arm": rest["arm"].translate((values["slide"], 0, 0))}
    monkeypatch.setattr(assemblies, "pose_shapes_from_rest", pose)
    result = TestClient(app).post("/documentos/demo/ensamble/comprobar-recorrido", json={
        "revision": "r1", "valores_desde": {"slide": 0},
        "valores_hasta": {"slide": -3}, "muestras": 4,
    })
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["completo"] is True
    assert data["primer_choque"]["fraccion"] == 0.75
    assert data["fraccion_segura_muestreada"] == 0.5
    assert data["muestras_comprobadas"] == 4
    assert result.headers["Cache-Control"] == "no-store"
