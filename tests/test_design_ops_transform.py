"""Fase 24b: escalar, primitivas, booleanas con otra pieza, alinear y apoyar.

Pruebas de contrato (Operation) y de núcleo (edit_mesh / edit_shape) sobre
cuerpos aislados, más una pasada de candidato sobre un documento de una pieza.
"""

import math

import numpy as np
import pytest
import trimesh
from build123d import Box
from fastapi.testclient import TestClient
from pydantic import ValidationError

import auth
import design_contracts as dc
import design_operations as ops
from main import app

client = TestClient(app)
PI = math.pi


def op(**kw):
    return dc.Operation(piece="p", **kw)


def mesh_box(extents, center=(0, 0, 0)):
    m = trimesh.creation.box(extents=extents)
    m.apply_translation(center)
    return m


def shape_box(extents, center=(0, 0, 0)):
    return Box(*extents).translate(center)


def bounds_of_shape(s):
    bb = s.bounding_box(optimal=True)
    return np.array([[bb.min.X, bb.min.Y, bb.min.Z], [bb.max.X, bb.max.Y, bb.max.Z]])


# ---------------------------------------------------------------- escala


def test_escala_uniforme_multiplica_el_volumen_por_s_cubo():
    s = 1.5
    o = op(kind="scale", factor=(s, s, s))
    m = ops.edit_mesh(mesh_box((10, 12, 14)), o)
    assert m.is_volume
    assert m.volume == pytest.approx(10 * 12 * 14 * s**3, rel=1e-3)
    f = ops.edit_shape(shape_box((10, 12, 14)), o)
    assert f.volume == pytest.approx(10 * 12 * 14 * s**3, rel=1e-3)


def test_escala_por_eje_conserva_el_centro_de_la_caja():
    o = op(kind="scale", factor=(2, 1, 0.5))
    m = ops.edit_mesh(mesh_box((10, 10, 10), (40, 0, 0)), o)
    assert m.extents == pytest.approx([20, 10, 5], abs=1e-6)
    assert m.bounds.mean(axis=0) == pytest.approx([40, 0, 0], abs=1e-6)
    f = ops.edit_shape(shape_box((10, 10, 10), (40, 0, 0)), o)
    lo, hi = bounds_of_shape(f)
    assert hi - lo == pytest.approx([20, 10, 5], abs=1e-4)
    assert (lo + hi) / 2 == pytest.approx([40, 0, 0], abs=1e-4)


# ---------------------------------------------------------------- alinear


@pytest.mark.parametrize("modo", ["min", "max", "center"])
def test_alinear_caras_o_centros_en_los_ejes_pedidos(modo):
    piece = mesh_box((10, 10, 10), (0, 0, 0))
    other = mesh_box((4, 6, 8), (30, 5, 7))
    r = ops.edit_mesh(
        piece, op(kind="align", other="q", mode=modo, axes=["x", "z"]), auxiliar=other
    )
    ref = {"min": other.bounds[0], "max": other.bounds[1], "center": other.centroid}[modo]
    got = {"min": r.bounds[0], "max": r.bounds[1], "center": r.centroid}[modo]
    assert got[0] == pytest.approx(ref[0], abs=1e-6)
    assert got[2] == pytest.approx(ref[2], abs=1e-6)
    # y no pedido: no se mueve.
    assert r.bounds[0][1] == pytest.approx(-5, abs=1e-6)


def test_apilar_pone_la_base_sobre_la_cara_superior_de_la_otra():
    piece = mesh_box((10, 10, 10), (0, 0, 0))
    other = mesh_box((20, 20, 4), (0, 0, 52))  # z de 50 a 54
    r = ops.edit_mesh(piece, op(kind="align", other="q", mode="stack"), auxiliar=other)
    assert r.bounds[0][2] == pytest.approx(54, abs=1e-6)
    assert r.bounds[0][0] == pytest.approx(-5, abs=1e-6)


def test_alinear_forma_b_rep_coplanar():
    piece = shape_box((10, 10, 10), (0, 0, 0))
    other = shape_box((4, 6, 8), (30, 5, 7))
    f = ops.edit_shape(
        piece, op(kind="align", other="q", mode="min", axes=["z"]), auxiliar=other
    )
    lo, _ = bounds_of_shape(f)
    assert lo[2] == pytest.approx(3, abs=1e-6)


def test_alinear_stack_rechaza_ejes_distintos_de_z():
    with pytest.raises(ValidationError):
        op(kind="align", other="q", mode="stack", axes=["x"])


# ---------------------------------------------------------------- apoyar sobre cama


def test_apoyar_sobre_cama_deja_la_base_en_cero_sin_girar():
    m = mesh_box((10, 10, 10), (3, 4, 25))
    r = ops.edit_mesh(m, op(kind="drop_to_bed"))
    assert r.bounds[0][2] == pytest.approx(0, abs=1e-6)
    assert r.extents == pytest.approx([10, 10, 10], abs=1e-6)


def test_apoyar_cara_elegida_queda_mirando_a_la_cama():
    m = mesh_box((10, 20, 30), (0, 0, 50))
    r = ops.edit_mesh(m, op(kind="drop_to_bed", normal=(1, 0, 0)))
    assert r.bounds[0][2] == pytest.approx(0, abs=1e-6)
    down = r.face_normals[:, 2] < -1 + 1e-6
    assert down.any()
    assert np.allclose(r.vertices[r.faces[down]][:, :, 2], 0, atol=1e-6)


def test_apoyar_forma_b_rep_sobre_cama():
    f = ops.edit_shape(shape_box((10, 20, 30), (0, 0, 50)), op(kind="drop_to_bed", normal=(0, 1, 0)))
    lo, _ = bounds_of_shape(f)
    assert lo[2] == pytest.approx(0, abs=1e-6)


# ---------------------------------------------------------------- booleana con otra pieza


def test_booleanas_con_otra_pieza_volumen_analitico_malla():
    a = mesh_box((10, 10, 10))  # [-5,5]^3
    b = mesh_box((10, 10, 10), (5, 0, 0))  # solape [0,5]x[-5,5]x[-5,5] = 500
    assert ops.edit_mesh(a, op(kind="subtract", other="q"), auxiliar=b).volume == pytest.approx(500, rel=1e-4)
    assert ops.edit_mesh(a, op(kind="union", other="q"), auxiliar=b).volume == pytest.approx(1500, rel=1e-4)
    assert ops.edit_mesh(a, op(kind="intersect", other="q"), auxiliar=b).volume == pytest.approx(500, rel=1e-4)
    assert b.volume == pytest.approx(1000, rel=1e-6)  # la auxiliar no se modifica


def test_booleanas_con_otra_pieza_volumen_analitico_forma():
    a = shape_box((10, 10, 10))
    b = shape_box((10, 10, 10), (5, 0, 0))
    assert ops.edit_shape(a, op(kind="subtract", other="q"), auxiliar=b).volume == pytest.approx(500, rel=1e-6)
    assert ops.edit_shape(a, op(kind="union", other="q"), auxiliar=b).volume == pytest.approx(1500, rel=1e-6)
    assert ops.edit_shape(a, op(kind="intersect", other="q"), auxiliar=b).volume == pytest.approx(500, rel=1e-6)


def test_booleana_con_otra_pieza_abierta_se_rechaza():
    a = mesh_box((10, 10, 10))
    b = trimesh.Trimesh(vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0]], faces=[[0, 1, 2]])
    with pytest.raises(ValueError):
        ops.edit_mesh(a, op(kind="union", other="q"), auxiliar=b)


# ---------------------------------------------------------------- primitivas


@pytest.mark.parametrize(
    "primitive,size,volume,rel",
    [
        ("box", (10, 20, 30), 6000, 1e-6),
        ("cylinder", (10, 0, 20), PI * 25 * 20, 2e-2),
        ("sphere", (10, 0, 0), 4 / 3 * PI * 125, 2e-2),
        ("cone", (10, 0, 20), PI * 25 * 20 / 3, 2e-2),
        ("torus", (20, 4, 0), 2 * PI**2 * 8 * 4, 2e-2),
        ("wedge", (10, 20, 30), 10 * 20 * 30 / 2, 1e-6),
        ("tube", (20, 12, 10), PI * (100 - 36) * 10, 2e-2),
    ],
)
def test_primitivas_volumen_y_cuerpo_cerrado(primitive, size, volume, rel):
    o = op(kind="union", primitive=primitive, size=size)
    m = ops.primitiva_malla(o)
    assert m.is_volume
    assert m.volume == pytest.approx(volume, rel=rel)
    assert m.bounds.mean(axis=0) == pytest.approx([0, 0, 0], abs=1e-6)
    s = ops.primitiva_forma(o)
    assert s.is_valid
    assert s.volume == pytest.approx(volume, rel=1e-6)
    lo, hi = bounds_of_shape(s)
    assert (lo + hi) / 2 == pytest.approx([0, 0, 0], abs=1e-4)


def test_cono_y_cuna_tienen_centro_de_caja_como_el_resto():
    m = ops.primitiva_malla(op(kind="union", primitive="cone", size=(10, 0, 20)))
    assert m.bounds.mean(axis=0) == pytest.approx([0, 0, 0], abs=1e-6)
    w = ops.primitiva_malla(op(kind="union", primitive="wedge", size=(10, 20, 30)))
    assert w.bounds.mean(axis=0) == pytest.approx([0, 0, 0], abs=1e-6)


@pytest.mark.parametrize(
    "primitive,size",
    [
        ("torus", (4, 4, 0)),  # tubo no menor que el diámetro exterior
        ("tube", (12, 12, 10)),  # interior no menor que el exterior
        ("box", (0, 10, 10)),
        ("cylinder", (3000, 0, 10)),  # tope de 2000 mm
    ],
)
def test_dimensiones_de_primitivas_invalidas_se_rechazan(primitive, size):
    o = op(kind="union", primitive=primitive, size=size)
    with pytest.raises(ValueError):
        ops.primitiva_malla(o)
    with pytest.raises(ValueError):
        ops.primitiva_forma(o)


# ---------------------------------------------------------------- contrato


@pytest.mark.parametrize(
    "extra",
    [
        {"kind": "scale", "factor": (0, 1, 1)},
        {"kind": "scale", "factor": (float("nan"), 1, 1)},
        {"kind": "scale", "factor": (1e6, 1, 1)},
        {"kind": "scale", "factor": (1, 1, 1), "other": "q"},
        {"kind": "transform", "other": "q"},
        {"kind": "transform", "factor": (2, 2, 2)},
        {"kind": "union", "other": "p"},  # misma pieza
        {"kind": "union", "other": "q", "factor": (2, 2, 2)},
        {"kind": "align"},  # sin pieza de referencia
        {"kind": "align", "other": "q", "axes": []},
        {"kind": "align", "other": "q", "axes": ["x", "x"]},
        {"kind": "align", "other": "q", "mode": "diagonal"},
        {"kind": "drop_to_bed", "normal": (0, 0, 0)},
        {"kind": "drop_to_bed", "normal": (float("inf"), 0, 1)},
        {"kind": "drop_to_bed", "other": "q"},
        {"kind": "union", "primitive": "helix"},
        {"kind": "union", "primitive": "box", "other": None, "size": (1, 2)},
        {"kind": "scale", "factor": (1, 1, 1), "extra": 1},
    ],
)
def test_contrato_rechaza_entradas_hostiles(extra):
    with pytest.raises(ValidationError):
        dc.Operation(**{"piece": "p", **extra})


def test_contrato_acepta_las_formas_validas():
    dc.Operation(kind="scale", piece="p", factor=(2, 0.5, 1))
    dc.Operation(kind="union", piece="p", other="q")
    dc.Operation(kind="align", piece="p", other="q", mode="stack")
    dc.Operation(kind="drop_to_bed", piece="p", normal=(0, 1, 0))
    dc.Operation(kind="union", piece="p", primitive="torus", size=(20, 4, 0))
    assert "scale" in dc.Operation.model_fields["kind"].annotation.__args__
    assert {"align", "drop_to_bed"} <= set(dc.Operation.model_fields["kind"].annotation.__args__)


# ---------------------------------------------------------------- candidato (API)


@pytest.fixture(params=["stl", "step"])
def doc(request, tmp_path):
    from kernel import b123d_kernel

    if request.param == "stl":
        data = trimesh.creation.box(extents=[10, 12, 14]).export(file_type="stl")
    else:
        p = tmp_path / "shape.step"
        b123d_kernel.export_to_step(Box(10, 12, 14), p)
        data = p.read_bytes()
    r = client.post("/documentos", files={"file": ("part." + request.param, data)})
    assert r.status_code == 200, r.text
    d = r.json()["id"]
    yield d
    client.delete("/documentos/" + d, headers={"X-Forja-Token": auth.obtener_token()})


def _context(d):
    r = client.get(f"/documentos/{d}/design")
    assert r.status_code == 200, r.text
    return r.json()


def _prepare(d, c, operations, key):
    return client.post(
        f"/documentos/{d}/design/candidates",
        headers={"X-Forja-Token": auth.obtener_token()},
        json={
            "revision": c["revision"],
            "state_revision": c["state_revision"],
            "request_id": key,
            "operations": operations,
        },
    )


def test_candidato_escala_uniforme_y_contratos_por_api(doc):
    c = _context(doc)
    n = c["pieces"][0]["name"]
    r = _prepare(doc, c, [{"kind": "scale", "piece": n, "factor": [2, 2, 2]}], "scale2")
    assert r.status_code == 200, r.text
    assert r.json()["integrity"][n]["volume_mm3"] == pytest.approx(8 * 10 * 12 * 14, rel=1e-3)
    bad = [
        {"kind": "scale", "piece": n, "factor": [0, 1, 1]},
        {"kind": "union", "piece": n, "other": "no-existe"},
        {"kind": "union", "piece": n, "other": n},
        {"kind": "drop_to_bed", "piece": n, "normal": [0, 0, 0]},
    ]
    for i, o in enumerate(bad):
        assert _prepare(doc, c, [o], f"bad{i}").status_code == 422, o
