"""Phase 1 API tests: /salud and POST /documentos (FastAPI TestClient)."""
from __future__ import annotations

import io

from fastapi.testclient import TestClient

from documents import DOCUMENTOS_DIR
from kernel import b123d_kernel, mesh
from main import app

client = TestClient(app)


def test_salud_reports_versions():
    resp = client.get("/salud")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    for key in ("build123d", "ocp", "manifold3d", "trimesh", "fcl"):
        assert data[key], f"missing version for {key}"


def test_post_documento_step():
    box = b123d_kernel.make_box(10, 10, 10)
    step_bytes = io.BytesIO()
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        step_path = Path(tmp) / "cubo.step"
        b123d_kernel.export_to_step(box, step_path)
        step_bytes = io.BytesIO(step_path.read_bytes())

    resp = client.post(
        "/documentos",
        files={"file": ("cubo.step", step_bytes, "application/step")},
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["nombre"] == "cubo.step"
    assert data["valido"] is True
    assert data["solidos"] == 1
    assert len(data["bbox"]) == 6
    assert abs(data["volumen"] - 1000.0) / 1000.0 < 1e-3
    assert set(data.keys()) == {"id", "nombre", "bbox", "volumen", "solidos", "valido", "revision"}
    assert len(data["revision"]) == 32


def test_post_documento_stl():
    box = b123d_kernel.make_box(10, 10, 10)
    tri_mesh = mesh.tessellate_to_trimesh(box, tolerance=0.05)
    stl_bytes = mesh.to_stl_bytes(tri_mesh)

    resp = client.post(
        "/documentos",
        files={"file": ("cubo.stl", io.BytesIO(stl_bytes), "application/sla")},
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["nombre"] == "cubo.stl"
    assert abs(data["volumen"] - 1000.0) / 1000.0 < 1e-2
    assert set(data.keys()) == {"id", "nombre", "bbox", "volumen", "solidos", "valido", "revision"}
    assert len(data["revision"]) == 32


def test_post_documento_formato_no_soportado():
    resp = client.post(
        "/documentos",
        files={"file": ("modelo.obj", io.BytesIO(b"not a real obj"), "text/plain")},
    )
    assert resp.status_code == 400


def test_post_documento_step_invalido_422():
    """A `.step` file with garbage content (no parseable solids) must be
    rejected the same way an unparsable STL is: 422, not a fake 200 with
    solidos=0/valido=True (regression for the Phase 1 review finding)."""
    resp = client.post(
        "/documentos",
        files={
            "file": (
                "no_es_step.step",
                io.BytesIO(b"esto no es un STEP valido, solo texto"),
                "application/step",
            )
        },
    )
    assert resp.status_code == 422
    assert "archivo invalido" in resp.json()["detail"]


def test_post_documento_step_invalido_no_deja_archivo_huerfano():
    ruta_antes = set(DOCUMENTOS_DIR.glob("*"))
    resp = client.post(
        "/documentos",
        files={
            "file": (
                "otro_no_step.step",
                io.BytesIO(b"tampoco es un STEP valido"),
                "application/step",
            )
        },
    )
    assert resp.status_code == 422
    ruta_despues = set(DOCUMENTOS_DIR.glob("*"))
    assert ruta_despues == ruta_antes, "no debe quedar un archivo huerfano tras un 422"


def test_post_documento_supera_limite_413(monkeypatch):
    monkeypatch.setenv("FORJA_MAX_UPLOAD_MB", "0.0001")  # ~104 bytes

    box = b123d_kernel.make_box(10, 10, 10)
    tri_mesh = mesh.tessellate_to_trimesh(box, tolerance=0.05)
    stl_bytes = mesh.to_stl_bytes(tri_mesh)
    assert len(stl_bytes) > 104, "el fixture debe superar el limite diminuto del test"

    ruta_antes = set(DOCUMENTOS_DIR.glob("*"))
    resp = client.post(
        "/documentos",
        files={"file": ("grande.stl", io.BytesIO(stl_bytes), "application/sla")},
    )
    assert resp.status_code == 413
    ruta_despues = set(DOCUMENTOS_DIR.glob("*"))
    assert ruta_despues == ruta_antes, "no debe quedar un archivo huerfano tras un 413"


def test_listar_documentos_incluye_lo_subido():
    box = b123d_kernel.make_box(5, 5, 5)
    tri_mesh = mesh.tessellate_to_trimesh(box, tolerance=0.05)
    stl_bytes = mesh.to_stl_bytes(tri_mesh)

    resp_post = client.post(
        "/documentos",
        files={"file": ("cubito.stl", io.BytesIO(stl_bytes), "application/sla")},
    )
    assert resp_post.status_code == 200
    creado = resp_post.json()

    resp = client.get("/documentos")
    assert resp.status_code == 200
    listado = resp.json()
    assert isinstance(listado, list)
    entradas = [d for d in listado if d["id"] == creado["id"]]
    assert len(entradas) == 1
    assert set(entradas[0].keys()) == {"id", "nombre", "volumen", "solidos", "formato", "actualizado", "revision"}
    assert entradas[0]["revision"] == creado["revision"]
    assert entradas[0]["nombre"] == "cubito.stl"
    assert entradas[0]["formato"] == "stl"


def test_malla_de_documento_step_es_binaria_y_no_vacia():
    box = b123d_kernel.make_box(10, 10, 10)
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        step_path = Path(tmp) / "cubo_malla.step"
        b123d_kernel.export_to_step(box, step_path)
        step_bytes = step_path.read_bytes()

    resp_post = client.post(
        "/documentos",
        files={"file": ("cubo_malla.step", io.BytesIO(step_bytes), "application/step")},
    )
    assert resp_post.status_code == 200
    doc_id = resp_post.json()["id"]

    resp = client.get(f"/documentos/{doc_id}/malla")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "model/stl"
    assert len(resp.content) > 0
    # cabecera binaria STL: 80 bytes de encabezado + 4 de conteo de triangulos
    assert len(resp.content) >= 84


def test_malla_de_documento_stl_es_binaria_y_no_vacia():
    box = b123d_kernel.make_box(8, 8, 8)
    tri_mesh = mesh.tessellate_to_trimesh(box, tolerance=0.05)
    stl_bytes = mesh.to_stl_bytes(tri_mesh)

    resp_post = client.post(
        "/documentos",
        files={"file": ("cubo_malla.stl", io.BytesIO(stl_bytes), "application/sla")},
    )
    assert resp_post.status_code == 200
    doc_id = resp_post.json()["id"]

    resp = client.get(f"/documentos/{doc_id}/malla")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "model/stl"
    assert len(resp.content) > 0


def test_malla_de_documento_inexistente_404():
    resp = client.get("/documentos/no-existe/malla")
    assert resp.status_code == 404


def test_raiz_sirve_index_html():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Forja" in resp.text
