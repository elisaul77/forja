"""Phase 4 versioning tests (ADR-0003: snapshot before every accepted
mutation; a forced invalid edit must leave the document recoverable)."""
from __future__ import annotations

import uuid

import pytest

import versioning


@pytest.fixture()
def doc_id():
    return f"test-{uuid.uuid4().hex[:8]}"


def test_crear_snapshot_y_listar_historial_es_compacto(doc_id):
    id1 = versioning.crear_snapshot(doc_id, "documento creado", {"a.step": b"contenido-1"})
    id2 = versioning.crear_snapshot(doc_id, "editar", {"a.step": b"contenido-2"})

    historial = versioning.listar_historial(doc_id)
    assert [e["id"] for e in historial] == [id1, id2]
    for entrada in historial:
        assert set(entrada.keys()) == {"id", "fecha", "mensaje"}


def test_leer_snapshot_devuelve_los_bytes_originales(doc_id):
    snap_id = versioning.crear_snapshot(
        doc_id, "documento creado", {"a.step": b"contenido-1", "notas.json": b'{"notas": []}'}
    )
    archivos = versioning.leer_snapshot(doc_id, snap_id)
    assert archivos == {"a.step": b"contenido-1", "notas.json": b'{"notas": []}'}


def test_leer_snapshot_inexistente_lanza_filenotfound(doc_id):
    versioning.crear_snapshot(doc_id, "documento creado", {"a.step": b"x"})
    with pytest.raises(FileNotFoundError):
        versioning.leer_snapshot(doc_id, "no-existe")


def test_forced_invalid_edit_state_recoverable(doc_id, tmp_path):
    """Simulates the documents.py hook: snapshot before a mutation, apply
    it, and on a forced (kernel-like) failure, restore the file from the
    snapshot — the document must end up byte-identical to before the
    failed edit, never partially written."""
    archivo = tmp_path / "documento.step"
    archivo.write_bytes(b"contenido-valido-v1")

    contenido_antes = archivo.read_bytes()
    versioning.crear_snapshot(doc_id, "antes de editar", {archivo.name: contenido_antes})

    class EdicionInvalidaError(Exception):
        pass

    try:
        archivo.write_bytes(b"a medio escribir, geometria invalida")
        raise EdicionInvalidaError("el kernel rechazo la figura resultante")
    except EdicionInvalidaError:
        historial = versioning.listar_historial(doc_id)
        snapshot_previo = historial[-1]["id"]
        archivos_previos = versioning.leer_snapshot(doc_id, snapshot_previo)
        archivo.write_bytes(archivos_previos[archivo.name])

    assert archivo.read_bytes() == contenido_antes


def test_existe_snapshot(doc_id):
    assert versioning.existe_snapshot(doc_id, "cualquiera") is False
    snap_id = versioning.crear_snapshot(doc_id, "documento creado", {"a.step": b"x"})
    assert versioning.existe_snapshot(doc_id, snap_id) is True


def test_historial_vacio_para_documento_sin_snapshots(doc_id):
    assert versioning.listar_historial(doc_id) == []
