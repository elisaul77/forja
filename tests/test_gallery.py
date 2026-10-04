"""Gallery metadata: file-backed recency, stable ordering, safe disappearance."""
from datetime import datetime, timezone
import os

from fastapi.testclient import TestClient

import documents
from main import app


def test_gallery_recency_format_and_ties_survive_registry_reordering(tmp_path, monkeypatch):
    registry = {}
    files = {}
    for doc_id, extension, timestamp in [
        ("old", ".step", 1000), ("b", ".STP", 2000),
        ("a", ".step", 2000), ("mesh", ".stl", 3000),
    ]:
        path = tmp_path / f"{doc_id}{extension}"
        path.touch()
        os.utime(path, (timestamp, timestamp))
        files[doc_id] = path
        registry[doc_id] = {"id": doc_id, "nombre": "sin extension", "volumen": 1, "solidos": 1}
    monkeypatch.setattr(documents, "_registry", registry)
    monkeypatch.setattr(documents, "_files", files)
    client = TestClient(app)
    listed = client.get("/documentos").json()
    assert [entry["id"] for entry in listed] == ["mesh", "a", "b", "old"]
    assert [entry["formato"] for entry in listed] == ["stl", "step", "step", "step"]
    assert listed[0]["actualizado"] == datetime.fromtimestamp(3000, timezone.utc).isoformat()
    monkeypatch.setattr(documents, "_registry", dict(reversed(list(registry.items()))))
    assert client.get("/documentos").json() == listed
    # In-place rebuild/restore changes recency without reinserting a registry entry.
    os.utime(files["old"], (4000, 4000))
    assert client.get("/documentos").json()[0]["id"] == "old"


def test_gallery_skips_a_disappeared_file(tmp_path, monkeypatch):
    monkeypatch.setattr(documents, "_registry", {
        "missing": {"id": "missing", "nombre": "missing.step", "volumen": 1, "solidos": 1},
    })
    monkeypatch.setattr(documents, "_files", {"missing": tmp_path / "missing.step"})
    assert TestClient(app).get("/documentos").json() == []


def test_gallery_reload_preserves_geometry_timestamp(tmp_path, monkeypatch):
    path = tmp_path / "saved.step"
    path.touch()
    os.utime(path, (12345, 12345))
    monkeypatch.setattr(documents, "DOCUMENTOS_DIR", tmp_path)
    monkeypatch.setattr(documents, "_registry", {})
    monkeypatch.setattr(documents, "_files", {})
    monkeypatch.setattr(documents, "_analyze", lambda *_: {
        "bbox": [0, 1, 0, 1, 0, 1], "volumen": 1, "solidos": 1, "valido": True,
    })
    monkeypatch.setattr(documents.solids, "cargar", lambda *_: [])
    documents.recargar_documentos()
    assert documents.listar_documentos()[0]["actualizado"] == datetime.fromtimestamp(12345, timezone.utc).isoformat()
