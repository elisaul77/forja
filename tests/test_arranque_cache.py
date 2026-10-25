"""Arranque: el analisis de cada documento (bbox, volumen, solidos, valido) se
reutiliza desde `{doc_id}.analisis.json` mientras el archivo de geometria no
cambie (clave: tamano + mtime_ns + version de esquema). Es dato derivado: no
entra en snapshots ni en el historial, y un cache ilegible se recalcula."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import auth
import documents
import git_store
import solids
import versioning
from main import app

FIXTURE_STEP = Path(__file__).parent / "fixtures" / "tuerca_p5.step"
client = TestClient(app)


@pytest.fixture
def aislado(tmp_path, monkeypatch):
    """Directorio de documentos temporal con una pieza STEP y registros vacios."""
    monkeypatch.setattr(documents, "DOCUMENTOS_DIR", tmp_path)
    monkeypatch.setattr(solids, "DOCUMENTOS_DIR", tmp_path)
    monkeypatch.setattr(documents, "_registry", {})
    monkeypatch.setattr(documents, "_files", {})
    shutil.copyfile(FIXTURE_STEP, tmp_path / "pieza.step")
    return tmp_path


def _contar_importaciones(monkeypatch) -> list[Path]:
    llamadas: list[Path] = []
    original = documents.b123d_kernel.import_from_step

    def contar(ruta):
        llamadas.append(Path(ruta))
        return original(ruta)

    monkeypatch.setattr(documents.b123d_kernel, "import_from_step", contar)
    return llamadas


def _reiniciar_registro() -> None:
    """Simula un reinicio: el registro en memoria se vacia, los archivos no."""
    documents._registry.clear()
    documents._files.clear()


def test_segunda_recarga_no_reanaliza_archivos_sin_cambios(aislado, monkeypatch):
    llamadas = _contar_importaciones(monkeypatch)
    documents.recargar_documentos()
    assert len(llamadas) >= 1  # arranque en frio: analiza y guarda el cache
    primera = dict(documents._registry["pieza"])
    assert (aislado / "pieza.analisis.json").exists()

    _reiniciar_registro()
    llamadas.clear()
    documents.recargar_documentos()

    assert llamadas == []
    assert documents._registry["pieza"] == primera


def test_cambiar_el_archivo_fuerza_nuevo_analisis(aislado, monkeypatch):
    llamadas = _contar_importaciones(monkeypatch)
    documents.recargar_documentos()
    _reiniciar_registro()
    llamadas.clear()

    ruta = aislado / "pieza.step"
    st = ruta.stat()
    os.utime(ruta, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    documents.recargar_documentos()

    assert llamadas == [ruta]
    assert "pieza" in documents._registry


@pytest.mark.parametrize(
    "contenido",
    [b"{no es json", b"[]", b'{"clave": null, "analisis": {}}', b""],
    ids=["json-roto", "lista", "forma-incorrecta", "vacio"],
)
def test_cache_corrupto_se_recalcula_sin_fallar(aislado, monkeypatch, contenido):
    llamadas = _contar_importaciones(monkeypatch)
    documents.recargar_documentos()
    referencia = dict(documents._registry["pieza"])
    _reiniciar_registro()
    llamadas.clear()
    (aislado / "pieza.analisis.json").write_bytes(contenido)

    documents.recargar_documentos()

    assert len(llamadas) == 1
    assert documents._registry["pieza"] == referencia
    datos = json.loads((aislado / "pieza.analisis.json").read_text())
    assert datos["analisis"]["solidos"] == referencia["solidos"]


def test_cache_no_entra_en_snapshots_ni_historial(aislado, monkeypatch):
    """Snapshots e historial se construyen con dicts explicitos de archivos;
    el cache de analisis nunca debe aparecer en ellos."""
    resp = client.post(
        "/documentos",
        files={"file": ("pieza.step", FIXTURE_STEP.read_bytes(), "application/step")},
        headers={"X-Forja-Token": auth.obtener_token()},
    )
    assert resp.status_code == 200, resp.text
    doc_id = resp.json()["id"]
    cache = documents._ruta_analisis(doc_id)

    _reiniciar_registro()
    documents._cargar_documento(documents.DOCUMENTOS_DIR / f"{doc_id}.step")
    assert cache.exists()

    assert not any("analisis" in n for n in documents._archivos_estado(doc_id, documents._files[doc_id]))
    for entrada in versioning.listar_historial(doc_id):
        archivos = versioning.leer_snapshot(doc_id, entrada["id"])
        assert not any("analisis" in n for n in archivos), entrada
    commit = git_store.cabeza(doc_id)
    assert commit is not None
    assert not any("analisis" in n for n in git_store.leer(doc_id, commit))
