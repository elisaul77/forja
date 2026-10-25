"""F0.0: the suite is hermetic — it never touches the real documents dir.

- `FORJA_DATA_DIR` and the storage paths derived from it are a tmp dir,
  never (inside) the real `documentos_data` mount.
- The in-process app uses the throwaway test token, not the real one.
- Creating and deleting documents in-process leaves the real dir's
  fingerprint identical to the session-start one (the sentinel stays green).
- The sentinel's fingerprint does detect additions, edits and deletions.
- A registered live token is masked as `***` in failing-test output.
"""
from __future__ import annotations

import io
import os
import secrets
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import auth
import documents
import conftest
from conftest import (
    CENTINELA_DIR,
    TOKEN_PRUEBA,
    backend_de_prueba_motivo,
    diferencias,
    id_creado,
    instantanea,
    ruta_dentro_de,
)
from kernel import b123d_kernel, mesh
from main import app

pytest_plugins = ["pytester"]


def test_datos_en_directorio_temporal():
    datos = Path(os.environ["FORJA_DATA_DIR"])
    for ruta in (datos, Path(documents.DOCUMENTOS_DIR), auth.TOKEN_FILE.parent, auth.SECRET_FILE.parent):
        assert not ruta_dentro_de(ruta, CENTINELA_DIR), ruta
        assert ruta_dentro_de(ruta, datos), ruta


def test_token_de_prueba():
    assert TOKEN_PRUEBA.startswith("forja-test-")
    assert not auth.SECRET_FILE.exists()  # the real secret never wins in-process
    assert os.environ["FORJA_TOKEN"] == TOKEN_PRUEBA
    assert auth.obtener_token() == TOKEN_PRUEBA


def test_crear_y_borrar_no_dispara_centinela(centinela_documentos):
    stl = mesh.to_stl_bytes(mesh.tessellate_to_trimesh(b123d_kernel.make_box(5, 5, 5), tolerance=0.1))
    cliente = TestClient(app)
    doc_id = None
    try:
        resp = cliente.post("/documentos", files={"file": ("hermetico.stl", io.BytesIO(stl), "application/sla")})
        assert resp.status_code == 200, resp.text
        doc_id = resp.json()["id"]
        assert any(Path(documents.DOCUMENTOS_DIR).glob(f"{doc_id}*"))
    finally:
        if doc_id:
            borrado = cliente.delete(f"/documentos/{doc_id}", headers={"X-Forja-Token": TOKEN_PRUEBA})
            assert borrado.status_code in (200, 204), borrado.text
    assert not any(Path(documents.DOCUMENTOS_DIR).glob(f"{doc_id}*"))
    assert diferencias(centinela_documentos, instantanea(CENTINELA_DIR)) == []


def test_instantanea_detecta_cambios(tmp_path):
    (tmp_path / "a.txt").write_text("uno")
    (tmp_path / "b.txt").write_text("dos")
    (tmp_path / auth.TOKEN_FILE.name).write_text("no-se-lee")
    antes = instantanea(tmp_path)
    assert antes[auth.TOKEN_FILE.name] == "existe"
    (tmp_path / "a.txt").write_text("cambiado")
    (tmp_path / "b.txt").unlink()
    (tmp_path / "c.txt").write_text("nuevo")
    assert diferencias(antes, instantanea(tmp_path)) == ["+ c.txt", "- b.txt", "~ a.txt"]


class _PluginEnmascarado:
    """Runs conftest's real masking hookwrapper inside the inner pytester session."""

    pytest_runtest_makereport = staticmethod(conftest.pytest_runtest_makereport)


def test_token_vivo_enmascarado_en_fallos(pytester):
    falso = "forja-falso-" + secrets.token_hex(12)  # never the real token
    conftest.registrar_token_vivo(falso)
    pytester.makepyfile(
        f"""
        import pytest

        @pytest.fixture
        def cabeceras():
            print("teardown con {falso}")
            yield {{"X-Forja-Token": "{falso}"}}
            print("fin {falso}")

        def _llamar(headers):
            print("stdout con", headers["X-Forja-Token"])
            assert headers["X-Forja-Token"] == "otro"

        def test_falla(cabeceras):
            _llamar(cabeceras)
        """
    )
    resultado = pytester.runpytest("-p", "no:cacheprovider", "-rA", "--showlocals", plugins=[_PluginEnmascarado()])
    resultado.assert_outcomes(failed=1)
    salida = resultado.stdout.str() + resultado.stderr.str()
    assert "def _llamar(headers)" in salida or "_llamar" in salida
    assert "Captured stdout" in salida
    assert falso not in salida
    assert "***" in salida


# ------------------------------------------------ suite hermetica por defecto (1c)

def test_guarda_rechaza_suite_no_hermetica():
    """Sin el runner (`scripts/test.sh`) los tests en vivo no pueden tocar un backend."""
    motivo = backend_de_prueba_motivo({}, Path("/data/documentos"), "http://127.0.0.1:8001")
    assert motivo is not None and "scripts/test.sh" in motivo


def test_guarda_rechaza_backend_con_datos_reales():
    env = {"FORJA_TEST_BACKEND": "hermetico", "FORJA_TEST_BACKEND_DATOS": "/data/documentos/sub"}
    motivo = backend_de_prueba_motivo(env, Path("/data/documentos"), "http://127.0.0.1:8001")
    assert motivo is not None and "documentos reales" in motivo


def test_guarda_rechaza_backend_que_no_es_loopback():
    env = {"FORJA_TEST_BACKEND": "hermetico", "FORJA_TEST_BACKEND_DATOS": "/work/datos-backend"}
    assert backend_de_prueba_motivo(env, Path("/data/documentos"), "http://localhost:8000") is not None


def test_guarda_acepta_el_backend_de_prueba():
    env = {"FORJA_TEST_BACKEND": "hermetico", "FORJA_TEST_BACKEND_DATOS": "/work/datos-backend"}
    assert backend_de_prueba_motivo(env, Path("/data/documentos"), "http://127.0.0.1:8001") is None


def test_id_creado_solo_de_respuestas_de_creacion():
    """Solo cuenta como propio el id que devuelve una creacion exitosa."""
    assert id_creado("POST", "/documentos", 200, {"id": "a"}) == "a"
    assert id_creado("POST", "/documentos/desde_ruta", 200, {"id": "b"}) == "b"
    assert id_creado("POST", "/documentos/script", 200, {"id": "c", "nombre": "x"}) == "c"
    assert id_creado("GET", "/documentos", 200, [{"id": "d"}]) is None
    assert id_creado("POST", "/documentos/x/papelera", 200, {"id": "e"}) is None
    assert id_creado("POST", "/documentos", 400, {"id": "f"}) is None
    assert id_creado("POST", "/documentos", 200, {"sin": "id"}) is None


def test_id_creado_ignora_la_actualizacion_de_un_documento_existente():
    """Con `documento_id` en el pedido, `/documentos/script` actualiza un documento
    que ya existia (u se reutiliza uno): no es de este test y no se borra."""
    assert id_creado("POST", "/documentos/script", 200, {"id": "a"}, {"documento_id": "a"}) is None
    assert id_creado("POST", "/documentos/script", 200, {"id": "a"}, {"documento_id": None}) == "a"
    assert id_creado("POST", "/documentos/script", 200, {"id": "a"}, {"nombre": "x"}) == "a"
