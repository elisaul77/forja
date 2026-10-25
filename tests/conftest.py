"""Session-scoped test isolation (Phase 4 fix-review, hardened in F0.0).

Running the suite must never write into the user's real, bind-mounted
`documentos_data/` volume. `app/documents.py`, `app/notes.py`,
`app/versioning.py`, and `app/auth.py` all derive their storage paths
(`DOCUMENTOS_DIR`, `HISTORIAL_DIR`, `TOKEN_FILE`) from env `FORJA_DATA_DIR`
at *import time* — so this module sets it, to a throwaway tmp directory,
before any of them get imported by a test file. Pytest always imports every
`conftest.py` in scope before collecting test modules, so doing this at
plain module level (not inside a fixture) is what makes it land in time.

Token (F0.0): every in-process test authenticates with a throwaway **test
token** pinned via env `FORJA_TOKEN` (which `auth.obtener_token()` always
prefers over the file). Only the tests that talk to the **live** `uvicorn`
process (a separate OS process this env override can never reach, ADR-0005)
need the real token; they request the `token_vivo` fixture, which swaps
`FORJA_TOKEN` for the duration of that one test only. The real token is
taken from env `FORJA_TOKEN_VIVO` if set, otherwise read lazily — only when
a live test actually runs, never printed — from the real token file path
captured below (F0.2: the server secret `/run/secrets/forja_token`, falling
back to the legacy `documentos_data/.forja_token`). It is never put in
`os.environ` for the whole session, so subprocesses spawned by in-process
tests never inherit it. `FORJA_SECRET_FILE` is pointed at a path that does
not exist, so the in-process app never prefers the real secret over the
test token.

Sentinel (F0.0): a session fixture fingerprints the real documents dir
(`FORJA_CENTINELA_DIR`, default `/data/documentos`) — relative path +
sha256 of every file, except the token file, which is only `stat`-ed for
existence and never read — before the first test and fails the session if
anything changed by the end. It also refuses to run if `FORJA_DATA_DIR`
points at (or inside) that real dir.
Do not run the suite while Forja is being used live: real edits made
meanwhile would change that dir and the sentinel would raise a false alarm.

Masking (F0.0): every live token read by `_leer_token_vivo()` is recorded,
and the `pytest_runtest_makereport` hookwrapper replaces it with `***` in the
report of every phase (setup/call/teardown) — the traceback (`longrepr`,
where pytest shows function arguments such as `headers`) and the captured
stdout/stderr/log sections — so a failing live test never prints it.
`-s`, `--pdb` and `log_cli` do not mask the token (explicit debug modes).
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import secrets
import tempfile
import urllib.parse
from pathlib import Path

import httpx
import pytest

import auth as _auth  # imported before FORJA_DATA_DIR is overridden

# Paths only; contents not read here. The secret wins when it exists (F0.2).
_REAL_SECRET_FILE: Path = _auth.SECRET_FILE
_REAL_LEGACY_TOKEN_FILE: Path = _auth.TOKEN_FILE
_TOKEN_NAME = _REAL_LEGACY_TOKEN_FILE.name

CENTINELA_DIR = Path(os.environ.get("FORJA_CENTINELA_DIR", "/data/documentos"))

# Always the throwaway token, even if an external FORJA_TOKEN is set.
TOKEN_PRUEBA = "forja-test-" + secrets.token_urlsafe(16)
os.environ["FORJA_TOKEN"] = TOKEN_PRUEBA

os.environ["FORJA_DATA_DIR"] = tempfile.mkdtemp(prefix="forja-tests-")
os.environ["FORJA_SECRET_FILE"] = str(Path(os.environ["FORJA_DATA_DIR"]) / "sin-secreto" / "forja_token")
importlib.reload(_auth)  # TOKEN_FILE/SECRET_FILE now under the tmp FORJA_DATA_DIR


def ruta_dentro_de(ruta: Path, raiz: Path) -> bool:
    ruta, raiz = ruta.resolve(), raiz.resolve()
    return ruta == raiz or raiz in ruta.parents


def instantanea(raiz: Path) -> dict[str, str]:
    """Relative path -> sha256 for every file under `raiz`. The token file
    is never opened: it is recorded only as present/absent via `stat`."""
    resultado: dict[str, str] = {}
    if not raiz.is_dir():
        return resultado
    for ruta in sorted(raiz.rglob("*")):
        rel = ruta.relative_to(raiz).as_posix()
        if rel == ".cache" or rel.startswith(".cache/"):
            continue  # derived data (mesh cache), regenerated on demand; not user data
        if ruta.name == _TOKEN_NAME:
            resultado[rel] = "existe" if ruta.exists() else "ausente"
            continue
        if not ruta.is_file() or ruta.is_symlink():
            continue
        h = hashlib.sha256()
        with ruta.open("rb") as f:
            for bloque in iter(lambda: f.read(1 << 20), b""):
                h.update(bloque)
        resultado[rel] = h.hexdigest()
    return resultado


def diferencias(antes: dict[str, str], despues: dict[str, str]) -> list[str]:
    cambios = [f"+ {k}" for k in despues.keys() - antes.keys()]
    cambios += [f"- {k}" for k in antes.keys() - despues.keys()]
    cambios += [f"~ {k}" for k in antes.keys() & despues.keys() if antes[k] != despues[k]]
    return sorted(cambios)


# Only these POST routes create a document; their successful response carries its id.
RUTAS_CREACION = frozenset({"/documentos", "/documentos/desde_ruta", "/documentos/script"})
HOST_BACKEND_PRUEBA = "127.0.0.1"


def backend_de_prueba_motivo(env, datos_reales: Path, base_url: str) -> str | None:
    """Why the live backend must NOT be used, or None when it is the hermetic one.

    `FORJA_TEST_BACKEND=hermetico` and `FORJA_TEST_BACKEND_DATOS` are exported only
    by `tests/hermetico.sh` (run through `scripts/test.sh`) for its own uvicorn.
    Anything else (the default `docker exec forja pytest`) is refused."""
    if env.get("FORJA_TEST_BACKEND") != "hermetico":
        return (
            "la suite no es hermetica: ejecuta los tests con scripts/test.sh "
            "(FORJA_TEST_BACKEND=hermetico lo fija el runner, nunca a mano)"
        )
    datos = env.get("FORJA_TEST_BACKEND_DATOS", "")
    if not datos:
        return "falta FORJA_TEST_BACKEND_DATOS: el runner declara el directorio de su backend"
    ruta = Path(datos)
    if ruta_dentro_de(ruta, datos_reales) or ruta_dentro_de(datos_reales, ruta):
        return f"el backend de prueba usa los documentos reales ({datos_reales})"
    if urllib.parse.urlsplit(base_url).hostname != HOST_BACKEND_PRUEBA:
        return f"FORJA_HTTP_BASE_URL ({base_url}) no es el backend local de prueba"
    return None


def id_creado(metodo: str, ruta: str, estado: int, cuerpo: object, pedido: object = None) -> str | None:
    """Id of the document that a successful creation response just returned, else None.

    Anything that is not one of `RUTAS_CREACION` is not counted as created by the test.
    A request that names an existing document (`documento_id`, also set by the client
    when it reuses one) updates that document: its id is not ours to delete."""
    if metodo.upper() != "POST" or ruta not in RUTAS_CREACION or not 200 <= estado < 300:
        return None
    if isinstance(pedido, dict) and pedido.get("documento_id") is not None:
        return None
    if isinstance(cuerpo, dict) and isinstance(cuerpo.get("id"), str):
        return cuerpo["id"]
    return None


def pytest_report_header(config):
    if os.environ.get("FORJA_TEST_BACKEND") != "hermetico":
        return "forja: suite NO hermetica; los tests en vivo se negaran. Usa scripts/test.sh"
    return "forja: suite hermetica (backend de prueba propio, datos temporales)"


@pytest.fixture(scope="session", autouse=True)
def centinela_documentos():
    datos = Path(os.environ["FORJA_DATA_DIR"])
    if ruta_dentro_de(datos, CENTINELA_DIR) or ruta_dentro_de(CENTINELA_DIR, datos):
        pytest.exit(f"FORJA_DATA_DIR ({datos}) apunta a los documentos reales ({CENTINELA_DIR})", returncode=3)
    antes = instantanea(CENTINELA_DIR)
    try:
        yield antes
    finally:
        cambios = diferencias(antes, instantanea(CENTINELA_DIR))
        if cambios:
            pytest.fail(
                f"centinela: la suite modificó {CENTINELA_DIR} ({len(cambios)} cambios): {cambios[:20]}",
                pytrace=False,
            )


_TOKENS_VIVOS: set[str] = set()
_MASCARA = "***"


def registrar_token_vivo(token: str) -> str:
    """Record a secret so every test report masks it; returns it unchanged."""
    if token:
        _TOKENS_VIVOS.add(token)
    return token


def enmascarar(texto: str) -> str:
    for token in sorted(_TOKENS_VIVOS, key=len, reverse=True):
        texto = texto.replace(token, _MASCARA)
    return texto


def _enmascarar_informe(rep: pytest.TestReport) -> None:
    if not _TOKENS_VIVOS:
        return
    longrepr = rep.longrepr
    if isinstance(longrepr, tuple):  # skip: (path, lineno, message)
        rep.longrepr = tuple(enmascarar(x) if isinstance(x, str) else x for x in longrepr)
    elif longrepr is not None:
        texto = str(longrepr)
        enmascarado = enmascarar(texto)
        if enmascarado != texto:
            rep.longrepr = enmascarado  # plain text; pytest prints str longreprs as-is
    rep.sections = [(titulo, enmascarar(contenido)) for titulo, contenido in rep.sections]


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    resultado = yield
    _enmascarar_informe(resultado.get_result())


def _leer_token_vivo() -> str:
    env = os.environ.get("FORJA_TOKEN_VIVO")
    if env:
        return registrar_token_vivo(env)
    for ruta in (_REAL_SECRET_FILE, _REAL_LEGACY_TOKEN_FILE):
        if ruta.exists():
            return registrar_token_vivo(ruta.read_text().strip())
    pytest.skip("sin token del backend en vivo")


def exigir_backend_de_prueba() -> None:
    """Refuse the live tests unless the backend under test is the hermetic one."""
    from mcp_server import client  # lazy: only the live tests need the MCP client

    motivo = backend_de_prueba_motivo(os.environ, CENTINELA_DIR, client.BASE_URL)
    if motivo is not None:
        pytest.fail(f"test en vivo bloqueado: {motivo}", pytrace=False)


@pytest.fixture
def token_vivo():
    """Real token for tests that hit the live backend; restored afterwards.
    Plain env swap (not `monkeypatch`) so requesting fixtures don't change
    the teardown order of a test's own `monkeypatch` mocks. Every live test
    goes through here, so the guard covers all of them."""
    exigir_backend_de_prueba()
    token = _leer_token_vivo()
    os.environ["FORJA_TOKEN"] = token
    try:
        yield token
    finally:
        os.environ["FORJA_TOKEN"] = TOKEN_PRUEBA


@pytest.fixture
def docs_propios(token_vivo, monkeypatch):
    """Ids of the documents THIS test creates, read from the creation responses
    (`id_creado`). Never from a listing: a document that merely appears during
    the test is not ours and is never returned here."""
    ids: list[str] = []
    original = httpx.Client

    def _registrar(respuesta: httpx.Response) -> None:
        peticion = respuesta.request
        if peticion.method != "POST" or peticion.url.path not in RUTAS_CREACION:
            return
        respuesta.read()
        try:
            cuerpo = respuesta.json()
        except ValueError:
            return  # not JSON: cannot be a creation answer, so it is not ours
        # Only JSON bodies are read: a multipart upload is a stream httpx has not read
        # (reading it would raise inside the hook), and it never names a documento_id.
        pedido = None
        if peticion.headers.get("content-type", "").startswith("application/json"):
            pedido = json.loads(peticion.content)  # a malformed request fails the test loudly
        doc_id = id_creado(peticion.method, peticion.url.path, respuesta.status_code, cuerpo, pedido)
        if doc_id is not None and doc_id not in ids:
            ids.append(doc_id)

    class _ClienteRegistrado(original):
        def __init__(self, *args, **kwargs):
            hooks = dict(kwargs.pop("event_hooks", None) or {})
            hooks["response"] = [*hooks.get("response", []), _registrar]
            super().__init__(*args, event_hooks=hooks, **kwargs)

    monkeypatch.setattr(httpx, "Client", _ClienteRegistrado)
    yield ids
