"""G1 (ADR-0014): git as the history backend — commits, authors, migration
from the ADR-0006 `.historial/` store, and the safety rails."""
from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import git_store
import migrar_historial
import versioning
from main import app

client = TestClient(app)


@pytest.fixture()
def doc_id():
    return f"test-{uuid.uuid4().hex[:8]}"


@pytest.fixture()
def aislado(tmp_path, monkeypatch):
    """Own `.historial/` and `.repos/` under tmp_path."""
    monkeypatch.setattr(versioning, "HISTORIAL_DIR", tmp_path / ".historial")
    monkeypatch.setattr(git_store, "REPOS_DIR", tmp_path / ".repos")
    return tmp_path


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "--git-dir", str(repo), *args], capture_output=True,
                          text=True, check=True, env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent",
                                                       "GIT_CONFIG_NOSYSTEM": "1"}).stdout


# ------------------------------------------------------------ commits

def test_cada_snapshot_es_un_commit_con_autor_y_mensaje(doc_id):
    id1 = versioning.crear_snapshot(doc_id, "documento creado", {"a.step": b"v1"})
    id2 = versioning.crear_snapshot(doc_id, "antes de parametros: alto=3", {"a.step": b"v2"}, autor="agente")
    commits = versioning.listar_commits(doc_id)
    assert [c["id"] for c in commits] == [id1, id2]
    assert [c["autor"] for c in commits] == ["forja", "agente"]
    assert commits[1]["mensaje"] == "antes de parametros: alto=3"
    assert commits[1]["archivos"] == ["a.step"]
    repo = git_store.ruta_repo(doc_id)
    assert _git(repo, "log", "-1", "--format=%an <%ae>", "main").strip() == "agente <agente@forja.local>"
    assert int(_git(repo, "rev-list", "--count", "main")) == 2


def test_autor_desconocido_cae_en_forja(doc_id):
    versioning.crear_snapshot(doc_id, "x", {"a.step": b"1"}, autor="Eli Florez <eli@x>")
    assert versioning.listar_commits(doc_id)[0]["autor"] == "forja"


def test_instantaneas_identicas_siguen_siendo_entradas_distintas(doc_id):
    ids = [versioning.crear_snapshot(doc_id, "igual", {"a.step": b"x"}) for _ in range(3)]
    assert [e["id"] for e in versioning.listar_historial(doc_id)] == ids


def test_mensaje_multilinea_y_unicode_se_conserva(doc_id):
    mensaje = "antes de parametros: caída=12°\nsegunda línea  "
    sid = versioning.crear_snapshot(doc_id, mensaje, {"a.step": b"x"})
    assert versioning.listar_historial(doc_id)[0]["mensaje"] == mensaje
    assert versioning.existe_snapshot(doc_id, sid)


def test_fuente_no_vuelve_por_leer_snapshot(doc_id):
    sid = versioning.crear_snapshot(doc_id, "x", {"a.step": b"x"}, fuente={"script.py": b"print(1)"})
    assert versioning.leer_snapshot(doc_id, sid) == {"a.step": b"x"}
    sha = versioning.listar_commits(doc_id)[0]["sha"]
    assert git_store.leer_fuente(doc_id, sha) == {"script.py": b"print(1)"}


def test_borrar_historial_quita_el_repo(doc_id):
    versioning.crear_snapshot(doc_id, "x", {"a.step": b"x"})
    assert git_store.existe(doc_id)
    versioning.borrar_historial(doc_id)
    assert not git_store.ruta_repo(doc_id).exists()
    assert versioning.listar_historial(doc_id) == []


def test_middleware_marca_humano_en_navegador_y_agente_por_cabecera():
    from main import _OrigenDelCambio

    vistos = []

    async def interior(scope, receive, send):
        vistos.append(versioning.autor_actual.get())

    import asyncio
    mw = _OrigenDelCambio(interior)
    for cabeceras in ([(b"x-forja-origen", b"agente")], [(b"sec-fetch-site", b"same-origin")], [],
                      [(b"x-forja-origen", b"root")]):
        asyncio.run(mw({"type": "http", "headers": cabeceras}, None, None))
    assert vistos == ["agente", "humano", "forja", "forja"]
    assert versioning.autor_actual.get() == "forja"


def test_restaurar_por_rest_crea_commit_de_humano():
    creado = client.post("/documentos/script", json={"codigo": "from build123d import Box\nresultado = Box(1, 1, 1)\n"},
                         headers={"X-Forja-Token": __import__("auth").obtener_token()}).json()
    doc = creado["id"]
    try:
        primero = client.get(f"/documentos/{doc}/historial").json()[0]["id"]
        r = client.post(f"/documentos/{doc}/restaurar", json={"snapshot": primero},
                        headers={"X-Forja-Token": __import__("auth").obtener_token(),
                                 "Sec-Fetch-Site": "same-origin"})
        assert r.status_code == 200
        ultimo = versioning.listar_commits(doc)[-1]
        assert ultimo["mensaje"] == f"antes de restaurar {primero}"
        assert ultimo["autor"] == "humano"
    finally:
        client.delete(f"/documentos/{doc}", headers={"X-Forja-Token": __import__("auth").obtener_token()})


# ------------------------------------------------------------ security

@pytest.mark.parametrize("nombre", ["../x", "a/b", ".git", "..", ".", "", "_fuente", "a\\b", "x\ny"])
def test_rutas_fuera_del_repo_se_rechazan(doc_id, nombre):
    with pytest.raises(ValueError):
        versioning.crear_snapshot(doc_id, "x", {nombre: b"1"})
    assert versioning.listar_historial(doc_id) == []


@pytest.mark.parametrize("malo", ["../etc", "a/b", ".repos", "", "x" * 80])
def test_doc_id_invalido_se_rechaza(malo):
    with pytest.raises(ValueError):
        git_store.ruta_repo(malo)


def test_hooks_no_se_ejecutan(doc_id, tmp_path):
    versioning.crear_snapshot(doc_id, "x", {"a.step": b"1"})
    repo = git_store.ruta_repo(doc_id)
    testigo = tmp_path / "hook-corrio"
    for hook in ("reference-transaction", "post-commit", "pre-auto-gc"):
        h = repo / "hooks" / hook
        h.parent.mkdir(exist_ok=True)
        h.write_text(f"#!/bin/sh\ntouch {testigo}\n")
        h.chmod(0o755)
    versioning.crear_snapshot(doc_id, "y", {"a.step": b"2"})
    assert not testigo.exists()


def test_sin_red_y_sin_config_global():
    cmd_env = git_store._entorno()
    assert cmd_env["GIT_CONFIG_GLOBAL"] == "/dev/null" and cmd_env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert cmd_env["GIT_TERMINAL_PROMPT"] == "0"
    # Every transport is forbidden: even a local "clone" of a URL fails.
    with pytest.raises(git_store.GitError):
        git_store._git(Path("/nonexistent.git"), "ls-remote", "https://example.com/x.git", timeout=20)


def test_sha_invalido_en_leer(doc_id):
    with pytest.raises(ValueError):
        git_store.leer(doc_id, "HEAD")


# ------------------------------------------------------------ migration

def _legado(base: Path, doc: str, entradas: list[tuple[str, str, str, dict[str, bytes]]]) -> None:
    d = base / ".historial" / doc
    d.mkdir(parents=True)
    manifest = []
    for sid, fecha, mensaje, archivos in entradas:
        (d / sid).mkdir()
        for n, c in archivos.items():
            (d / sid / n).write_bytes(c)
        manifest.append({"id": sid, "fecha": fecha, "mensaje": mensaje, "archivos": sorted(archivos)})
    (d / "manifest.json").write_text(json.dumps(manifest))


def _sintetico(base: Path) -> dict[str, list]:
    datos = {
        # scripted STEP document with params, solids, notes; one duplicate state
        "aaaa1111": [
            ("000000000001", "2026-09-01T10:00:00Z", "documento creado (script)",
             {"aaaa1111.step": b"ISO-10303-21;v1", "parametros.json": b'{"script":{"codigo":"x"}}',
              "solidos.json": b"[]"}),
            ("000000000002", "2026-09-01T10:05:00Z", "antes de parametros: alto=30",
             {"aaaa1111.step": b"ISO-10303-21;v1", "parametros.json": b'{"script":{"codigo":"x"}}',
              "solidos.json": b"[]"}),
            ("000000000003", "2026-09-01T10:06:00Z", "anotar: nota",
             {"notas.json": b'{"notas": [], "trazos": []}'}),
        ],
        # imported STL, no script
        "bbbb2222": [
            ("000000000010", "2026-09-02T08:00:00Z", "documento creado (subida)",
             {"bbbb2222.stl": bytes(range(256)) * 40}),
        ],
    }
    for doc, entradas in datos.items():
        _legado(base, doc, entradas)
    return datos


def test_historial_legado_visible_antes_de_migrar(aislado):
    datos = _sintetico(aislado)
    h = versioning.listar_historial("aaaa1111")
    assert [e["id"] for e in h] == [e[0] for e in datos["aaaa1111"]]
    assert versioning.leer_snapshot("bbbb2222", "000000000010") == datos["bbbb2222"][0][3]


def test_dry_run_no_escribe_nada(aislado):
    _sintetico(aislado)
    antes = sorted(p.relative_to(aislado) for p in aislado.rglob("*"))
    resumen = migrar_historial.migrar(aplicar=False)
    assert resumen["modo"] == "dry-run"
    assert [d["accion"] for d in resumen["documentos"]] == ["migrar", "migrar"]
    assert sorted(p.relative_to(aislado) for p in aislado.rglob("*")) == antes


def test_migracion_reproduce_byte_a_byte_y_mueve_el_legado(aislado):
    datos = _sintetico(aislado)
    esperado = {doc: versioning.listar_historial(doc) for doc in datos}
    # a commit made by the new code before the migration ran
    tardio = versioning.crear_snapshot("aaaa1111", "posterior al despliegue", {"notas.json": b"{}"}, autor="humano")
    resumen = migrar_historial.migrar(aplicar=True)
    assert not (aislado / ".historial").exists()
    assert (aislado / ".historial.migrado" / "aaaa1111" / "manifest.json").exists()
    marcador = json.loads((aislado / ".repos" / ".migracion.json").read_text())
    assert marcador["modo"] == "aplicar"
    assert resumen["documentos"][0]["accion"].startswith("migrar (+1")
    for doc, entradas in datos.items():
        h = versioning.listar_historial(doc)
        extra = [{"id": tardio, "fecha": h[-1]["fecha"], "mensaje": "posterior al despliegue"}] if doc == "aaaa1111" else []
        assert h == esperado[doc] + extra
        for sid, _f, _m, archivos in entradas:
            assert versioning.leer_snapshot(doc, sid) == archivos
    commits = versioning.listar_commits("aaaa1111")
    assert [c["autor"] for c in commits] == ["forja", "forja", "forja", "humano"]
    assert _git(git_store.ruta_repo("aaaa1111"), "log", "-1", "--format=%aI", commits[0]["sha"]).startswith("2026-09-01T10:00:00")


def test_migracion_idempotente(aislado):
    _sintetico(aislado)
    migrar_historial.migrar(aplicar=True)
    # put the legacy dir back as if someone restored it: nothing duplicated
    (aislado / ".historial.migrado").rename(aislado / ".historial")
    (aislado / ".repos" / ".migracion.json").unlink()
    resumen = migrar_historial.migrar(aplicar=False)
    assert {d["accion"] for d in resumen["documentos"]} == {"ya migrado"}
    assert len(versioning.listar_commits("aaaa1111")) == 3


def test_migracion_aborta_sin_tocar_nada_si_falta_un_archivo(aislado):
    _sintetico(aislado)
    (aislado / ".historial" / "bbbb2222" / "000000000010").rename(aislado / "perdido")
    with pytest.raises(FileNotFoundError):
        migrar_historial.migrar(aplicar=True)
    assert (aislado / ".historial" / "aaaa1111" / "manifest.json").exists()
    assert not (aislado / ".historial.migrado").exists()
    assert not (aislado / ".repos" / ".migracion.json").exists()


def test_migracion_aborta_si_la_verificacion_falla(aislado, monkeypatch):
    _sintetico(aislado)
    real = git_store.leer
    monkeypatch.setattr(git_store, "leer", lambda d, s: {**real(d, s), "x": b"corrupto"})
    with pytest.raises(RuntimeError, match="bytes distintos"):
        migrar_historial.migrar(aplicar=True)
    monkeypatch.setattr(git_store, "leer", real)
    assert (aislado / ".historial").exists() and not (aislado / ".historial.migrado").exists()
    assert git_store.cabeza("aaaa1111") is None  # branch never moved
