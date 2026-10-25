"""Regression checks for STEP import work shared by upload and abrir_archivo."""
from __future__ import annotations

import io

import pytest
from build123d import Compound, Pos
from fastapi.testclient import TestClient

import auth
import documents
import solids
from kernel import b123d_kernel
from main import app


@pytest.mark.parametrize("route", ["upload", "desde_ruta"])
def test_step_creation_imports_once_and_keeps_per_solid_metadata(
    route, tmp_path, monkeypatch
):
    shape = Compound(children=[
        b123d_kernel.make_box(10, 10, 10),
        Pos(20, 0, 0) * b123d_kernel.make_box(5, 5, 5),
    ])
    source = tmp_path / "dos.step"
    b123d_kernel.export_to_step(shape, source)
    monkeypatch.setattr(documents, "ALLOWED_READ_ROOTS", (tmp_path,))

    original_import = b123d_kernel.import_from_step
    calls = []

    def counted_import(path):
        calls.append(path)
        assert path.parent.name.startswith(".importando-")
        assert not (documents.DOCUMENTOS_DIR / path.name).exists()
        return original_import(path)

    monkeypatch.setattr(b123d_kernel, "import_from_step", counted_import)
    client = TestClient(app)
    if route == "upload":
        response = client.post(
            "/documentos", files={"file": ("dos.step", io.BytesIO(source.read_bytes()))}
        )
    else:
        response = client.post(
            "/documentos/desde_ruta", json={"ruta": str(source)},
            headers={"X-Forja-Token": auth.obtener_token()},
        )
    assert response.status_code == 200, response.text
    assert len(calls) == 1

    data = response.json()
    assert data["solidos"] == 2
    assert data["valido"] is True
    assert data["volumen"] == pytest.approx(1125.0)
    entries = solids.cargar(data["id"])
    assert entries is not None
    assert [entry["nombre"] for entry in entries] == ["solido_1", "solido_2"]
    assert sorted(entry["volumen"] for entry in entries) == pytest.approx([125.0, 1000.0])
    assert sum(entry["volumen"] for entry in entries) == pytest.approx(data["volumen"])


def test_failed_from_path_import_leaves_no_document(tmp_path, monkeypatch):
    source = tmp_path / "roto.step"
    source.write_text("not STEP geometry")
    monkeypatch.setattr(documents, "ALLOWED_READ_ROOTS", (tmp_path,))
    before = set(documents.DOCUMENTOS_DIR.glob("*"))
    response = TestClient(app).post(
        "/documentos/desde_ruta", json={"ruta": str(source)},
        headers={"X-Forja-Token": auth.obtener_token()},
    )
    assert response.status_code == 422
    assert set(documents.DOCUMENTOS_DIR.glob("*")) == before
