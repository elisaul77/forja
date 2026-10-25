"""Galeria ordenada, fase 3a: cuerpos de malla dentro de un documento STEP.

`abrir_archivo(ruta, documento_id, nombre_solido)` y
`POST /documentos/{id}/incorporar_archivo` anaden un STL como cuerpo de malla
(`{id}.mallas/`) sin tocar el STEP ni `solidos.json` y sin crear documento
nuevo. Hermetico: los STL viven en `tmp_path` y las raices permitidas se
amplian solo para el test.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import trimesh
from build123d import Box
from fastapi.testclient import TestClient

import auth
import documents
import solids
from kernel import b123d_kernel
from main import app
import revision

client = TestClient(app)

SCRIPT_BASE = (
    "from build123d import Box, Pos\n"
    "resultado = {'soporte': Box(20, 20, 2), 'pata': Pos(0, 0, -20) * Box(2, 2, 2)}\n"
)
STL_ASCII_NAN = (
    b"solid nan\n facet normal 0 0 1\n  outer loop\n   vertex nan 0 0\n"
    b"   vertex 1 0 0\n   vertex 0 1 0\n  endloop\n endfacet\nendsolid nan\n"
)


def _h() -> dict[str, str]:
    return {"X-Forja-Token": auth.obtener_token()}


def _script(codigo: str, nombre: str) -> str:
    r = client.post("/documentos/script", json={"codigo": codigo, "nombre": nombre}, headers=_h())
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _caja_stl(extents=(30, 30, 30)) -> bytes:
    return trimesh.creation.box(extents=extents).export(file_type="stl")


def _escribir(directorio: Path, nombre: str, datos: bytes) -> Path:
    ruta = directorio / nombre
    ruta.write_bytes(datos)
    return ruta


def _huella(doc_id: str) -> tuple[str, str]:
    ruta = documents._files[doc_id]
    lado = solids.ruta_solidos(doc_id)
    return (hashlib.sha256(ruta.read_bytes()).hexdigest(),
            hashlib.sha256(lado.read_bytes()).hexdigest() if lado.exists() else "")


def _almacen(doc_id: str) -> Path:
    return documents.DOCUMENTOS_DIR / f"{doc_id}.mallas"


def _manifiesto(doc_id: str) -> dict:
    return json.loads((_almacen(doc_id) / "manifest.json").read_text())


@pytest.fixture()
def fuente(tmp_path, monkeypatch):
    """Raiz de lectura ampliada solo para este test: el directorio temporal."""
    monkeypatch.setattr(documents, "ALLOWED_READ_ROOTS", (tmp_path, documents.DOCUMENTOS_DIR))
    return tmp_path


@pytest.fixture()
def docs():
    creados: list[str] = []
    yield creados
    for d in creados:
        client.delete(f"/documentos/{d}", headers=_h())


def _incorporar_archivo(doc_id: str, ruta: Path, nombre_solido: str | None = None):
    cuerpo: dict = {"ruta": str(ruta)}
    if nombre_solido is not None:
        cuerpo["nombre_solido"] = nombre_solido
    return client.post(f"/documentos/{doc_id}/incorporar_archivo", json=cuerpo, headers=_h())


def test_stl_en_step_anade_cuerpo_de_malla_sin_tocar_el_step(fuente, docs):
    base = _script(SCRIPT_BASE, "base")
    docs.append(base)
    ruta = _escribir(fuente, "carcasa.stl", _caja_stl())
    antes_ids, huella, revision_previa = set(documents._registry), _huella(base), documents._revisiones[base]

    r = _incorporar_archivo(base, ruta, "carcasa")

    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["id"] == base
    assert cuerpo["anadidos"][0]["nombre"] == "carcasa" and cuerpo["anadidos"][0]["kind"] == "malla"
    assert cuerpo["renombradas"] == {} and cuerpo["aproximado"] is False
    assert set(documents._registry) == antes_ids, "no debe aparecer un documento nuevo"
    assert _huella(base) == huella, "STEP y solidos.json intactos"
    manifiesto = _manifiesto(base)
    assert manifiesto["schema"] == "mallas-v1"
    assert [c["nombre"] for c in manifiesto["cuerpos"]] == ["carcasa"]
    assert (_almacen(base) / "carcasa.stl").is_file()
    assert documents._revisiones[base] != revision_previa
    assert ruta.read_bytes() == _caja_stl(), "el STL de origen no se modifica"


def test_nombre_repetido_o_de_solido_recibe_sufijo(fuente, docs):
    base = _script(SCRIPT_BASE, "base")
    docs.append(base)
    ruta = _escribir(fuente, "carcasa.stl", _caja_stl())

    assert _incorporar_archivo(base, ruta, "carcasa").json()["renombradas"] == {}
    segunda = _incorporar_archivo(base, ruta, "carcasa").json()
    assert segunda["renombradas"] == {"carcasa": "carcasa_2"}
    choque = _incorporar_archivo(base, ruta, "soporte").json()
    assert choque["renombradas"] == {"soporte": "soporte_2"}
    assert [c["nombre"] for c in _manifiesto(base)["cuerpos"]] == ["carcasa", "carcasa_2", "soporte_2"]


def test_malla_abierta_se_acepta_con_aviso_y_no_estanca(fuente, docs):
    base = _script(SCRIPT_BASE, "base")
    docs.append(base)
    abierta = trimesh.creation.box(extents=(30, 30, 30))
    abierta.update_faces(np.arange(len(abierta.faces))[:-2])
    ruta = _escribir(fuente, "abierta.stl", abierta.export(file_type="stl"))

    r = _incorporar_archivo(base, ruta, "abierta")

    assert r.status_code == 200, r.text
    assert r.json()["anadidos"][0]["estanca"] is False
    assert any("abierta" in aviso or "estanca" in aviso for aviso in r.json()["avisos"])


@pytest.mark.parametrize("datos", [b"solid vacio\nendsolid vacio\n", STL_ASCII_NAN],
                         ids=["vacia", "nan"])
def test_malla_vacia_o_no_finita_se_rechaza_sin_escribir(fuente, docs, datos):
    base = _script(SCRIPT_BASE, "base")
    docs.append(base)
    ruta = _escribir(fuente, "mala.stl", datos)
    huella = _huella(base)

    r = _incorporar_archivo(base, ruta, "mala")

    assert r.status_code == 400, r.text
    assert not _almacen(base).exists()
    assert _huella(base) == huella


def test_ruta_fuera_de_raices_permitidas_es_400(docs, tmp_path):
    base = _script(SCRIPT_BASE, "base")
    docs.append(base)
    r = client.post(f"/documentos/{base}/incorporar_archivo", json={"ruta": "/etc/passwd"}, headers=_h())
    assert r.status_code == 400
    assert not _almacen(base).exists()


def test_archivo_mayor_que_el_tope_de_subida_es_413(fuente, docs, monkeypatch):
    base = _script(SCRIPT_BASE, "base")
    docs.append(base)
    ruta = _escribir(fuente, "carcasa.stl", _caja_stl())
    monkeypatch.setenv("FORJA_MAX_UPLOAD_MB", "0.00001")  # ~10 bytes: cualquier STL real lo supera
    assert _incorporar_archivo(base, ruta, "carcasa").status_code == 413
    assert not _almacen(base).exists()


def test_documento_inexistente_es_404_y_sin_token_401(fuente, docs):
    base = _script(SCRIPT_BASE, "base")
    docs.append(base)
    ruta = _escribir(fuente, "carcasa.stl", _caja_stl())
    assert _incorporar_archivo("0" * 32, ruta, "carcasa").status_code == 404
    sin_token = client.post(f"/documentos/{base}/incorporar_archivo", json={"ruta": str(ruta)})
    assert sin_token.status_code == 401


def test_restaurar_al_snapshot_previo_quita_el_almacen(fuente, docs):
    base = _script(SCRIPT_BASE, "base")
    docs.append(base)
    ruta = _escribir(fuente, "carcasa.stl", _caja_stl())
    revision_previa = documents._revisiones[base]

    cuerpo = _incorporar_archivo(base, ruta, "carcasa").json()
    assert _almacen(base).is_dir()

    r = client.post(f"/documentos/{base}/restaurar", json={"snapshot": cuerpo["snapshot_previo"]}, headers=_h())

    assert r.status_code == 200, r.text
    assert not _almacen(base).exists()
    assert documents._revisiones[base] == revision_previa


def test_abrir_archivo_stl_con_documento_id_no_crea_documento(fuente, docs):
    base = _script(SCRIPT_BASE, "base")
    docs.append(base)
    ruta = _escribir(fuente, "tapa.stl", _caja_stl())
    antes_ids = set(documents._registry)

    r = client.post("/documentos/desde_ruta",
                    json={"ruta": str(ruta), "documento_id": base, "nombre_solido": "tapa"}, headers=_h())

    assert r.status_code == 200, r.text
    assert r.json()["id"] == base
    assert r.json()["anadidos"][0]["nombre"] == "tapa" and r.json()["anadidos"][0]["kind"] == "malla"
    assert set(documents._registry) == antes_ids
    assert (_almacen(base) / "tapa.stl").is_file()


def test_abrir_archivo_step_con_documento_id_anade_solido_con_nombre(fuente, docs):
    base = _script(SCRIPT_BASE, "base")
    docs.append(base)
    ruta = fuente / "pieza.step"
    b123d_kernel.export_to_step(Box(5, 5, 5), ruta)

    r = client.post("/documentos/desde_ruta",
                    json={"ruta": str(ruta), "documento_id": base, "nombre_solido": "pieza"}, headers=_h())

    assert r.status_code == 200, r.text
    assert r.json()["anadidos"] == [{"nombre": "pieza", "kind": "solido"}]
    assert "pieza" in [e["nombre"] for e in solids.cargar(base)]
    assert not _almacen(base).exists()


def test_nombre_solido_sin_documento_id_es_400(fuente):
    ruta = _escribir(fuente, "tapa.stl", _caja_stl())
    antes_ids = set(documents._registry)
    r = client.post("/documentos/desde_ruta", json={"ruta": str(ruta), "nombre_solido": "tapa"}, headers=_h())
    assert r.status_code == 400
    assert set(documents._registry) == antes_ids, "un 400 no crea documento"


def test_desde_ruta_con_documento_inexistente_es_404(fuente):
    ruta = _escribir(fuente, "tapa.stl", _caja_stl())
    r = client.post("/documentos/desde_ruta",
                    json={"ruta": str(ruta), "documento_id": "0" * 32, "nombre_solido": "tapa"}, headers=_h())
    assert r.status_code == 404


def test_doc_step_sin_almacen_conserva_la_revision_de_siempre(docs):
    base = _script(SCRIPT_BASE, "base")
    docs.append(base)
    sidecar = solids.ruta_solidos(base).read_bytes()
    esperada = revision.calcular(documents._files[base].read_bytes(), True, [("solidos.json", sidecar)])
    assert documents.calcular_revision(base) == esperada


def test_stl_en_stl_sigue_fusionando_regiones_sin_almacen(docs):
    primero = client.post("/documentos", files={"file": ("uno.stl", _caja_stl(), "application/sla")}, headers=_h())
    segundo = client.post("/documentos", files={"file": ("dos.stl", _caja_stl((10, 10, 10)), "application/sla")},
                          headers=_h())
    assert primero.status_code == 200 and segundo.status_code == 200
    a, b = primero.json()["id"], segundo.json()["id"]
    docs += [a, b]

    r = client.post(f"/documentos/{a}/incorporar", json={"desde": b}, headers=_h())

    assert r.status_code == 200, r.text
    assert not _almacen(a).exists(), "STL en STL usa la fusion por regiones, no el almacen de mallas"
    entradas = json.loads(solids.ruta_solidos(a).read_text())
    assert len(entradas) == 2
    assert [e["nombre"] for e in entradas] == ["uno", "dos"]


def test_papelera_conserva_el_almacen_y_la_purga_lo_borra(fuente, docs):
    base = _script(SCRIPT_BASE, "base")
    docs.append(base)
    ruta = _escribir(fuente, "carcasa.stl", _caja_stl())
    assert _incorporar_archivo(base, ruta, "carcasa").status_code == 200

    enpapelera = client.post(f"/documentos/{base}/papelera", headers=_h())
    assert enpapelera.status_code == 200, enpapelera.text
    assert _almacen(base).is_dir(), "mandar a la papelera no borra archivos"

    purga = client.delete(f"/papelera/{base}", headers=_h())
    assert purga.status_code == 200, purga.text
    assert not _almacen(base).exists(), "la purga borra el almacen de mallas"


def test_tope_de_triangulos_coincide_con_design_geometry():
    # Import dentro del test: design_geometry importa incorporar, que importa mallas.
    import design_geometry
    import mallas

    assert mallas.MAX_TRIANGULOS == design_geometry.MAX_FACES
