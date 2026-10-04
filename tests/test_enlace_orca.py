"""Phase 10.2: `exportar` composes `url_descarga` / `enlace_orca` at the MCP
boundary (`mcp_server/client.py`). The HTTP layer is stubbed in-process, so
nothing here touches the real data dir or needs the live backend."""
from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path

import pytest

import auth
from mcp_server import client
from mcp_server.server import crear_servidor

DEFECTO = "http://localhost:8710"
DESCARGA_3MF = "/documentos/abc123/descarga/mesa.3mf"
DESCARGA_STL = "/documentos/abc123/descarga/mesa.stl"
PREFIJO = "orcaslicer://open?file="


class _Resp:
    def __init__(self, cuerpo, status=200):
        self._cuerpo, self.status_code = cuerpo, status
        self.text = json.dumps(cuerpo)

    def json(self):
        return self._cuerpo


def _stub(monkeypatch, cuerpo, status=200):
    class _Cliente:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **k):
            return _Resp(dict(cuerpo) if isinstance(cuerpo, dict) else cuerpo, status)

    monkeypatch.setattr(client.httpx, "Client", _Cliente)


def _valor_file(enlace: str) -> str:
    assert enlace.startswith(PREFIJO)
    return urllib.parse.unquote(enlace[len(PREFIJO):])


def _exportar(monkeypatch, descarga=DESCARGA_3MF, formato="3mf", por_solido=False, **extra):
    cuerpo = {"ruta": "/data/x", "tamano_bytes": 10, **extra}
    if descarga is not None:
        cuerpo["descarga"] = descarga
    _stub(monkeypatch, cuerpo)
    return client.exportar("abc123", formato, por_solido=por_solido)


def test_base_por_defecto(monkeypatch):
    monkeypatch.delenv("FORJA_PUBLIC_URL", raising=False)
    r = _exportar(monkeypatch)
    assert r["url_descarga"] == DEFECTO + DESCARGA_3MF
    assert r["enlace_orca"].startswith(PREFIJO)
    assert _valor_file(r["enlace_orca"]) == r["url_descarga"]
    assert r["descarga"] == DESCARGA_3MF and r["ruta"] == "/data/x" and r["tamano_bytes"] == 10


def test_stl_tambien(monkeypatch):
    monkeypatch.delenv("FORJA_PUBLIC_URL", raising=False)
    r = _exportar(monkeypatch, DESCARGA_STL, "stl")
    assert _valor_file(r["enlace_orca"]) == DEFECTO + DESCARGA_STL


@pytest.mark.parametrize("base,esperada", [
    ("http://192.168.1.50:8710", "http://192.168.1.50:8710"),
    ("http://192.168.1.50:8710/", "http://192.168.1.50:8710"),
    ("https://forja.example:8443", "https://forja.example:8443"),
    ("https://forja.example", "https://forja.example"),
    ("http://[::1]:8710", "http://[::1]:8710"),
])
def test_bases_validas(monkeypatch, base, esperada):
    monkeypatch.setenv("FORJA_PUBLIC_URL", base)
    r = _exportar(monkeypatch)
    assert r["url_descarga"] == esperada + DESCARGA_3MF
    assert _valor_file(r["enlace_orca"]) == r["url_descarga"]


BASES_INVALIDAS = [
    "localhost:8710", "192.168.1.50:8710", "ftp://host:8710", "file:///etc/passwd",
    "javascript://host", "http://user:pw@host:8710", "http://user@host:8710",
    "http://host:8710/forja", "http://host:8710/forja/", "http://host:8710?x=1",
    "http://host:8710/?x=1", "http://host:8710#frag", "http://host:8710/#frag",
    "http://host:8710?", "http://host:8710#", "", " ", "http://host :8710",
    " http://host:8710", "http://host:8710 ", "http://host:8710\n", "http://host\t:8710",
    "http://host:8710\x7f", "http://host:99999", "http://host:abc",
    "http://", "http:///x", "http://host\\@evil:8710", "http://hóst:8710", "http://ho'st:8710",
]


@pytest.mark.parametrize("base", BASES_INVALIDAS)
def test_bases_invalidas_caen_al_defecto(monkeypatch, base):
    monkeypatch.setenv("FORJA_PUBLIC_URL", base)
    r = _exportar(monkeypatch)  # must not raise
    assert r["url_descarga"] == DEFECTO + DESCARGA_3MF


def test_nul_en_la_base_cae_al_defecto():
    # an env var cannot hold NUL, so exercise the validator directly
    assert client._validar_base("http://ho\x00st:8710") is None


@pytest.mark.parametrize("valor", [None, 8710, b"http://host:8710", ["http://h"]])
def test_base_no_string_cae_al_defecto(monkeypatch, valor):
    monkeypatch.setattr(client.os.environ, "get", lambda k, d=None: valor if k == "FORJA_PUBLIC_URL" else d)
    assert client._base_publica() == DEFECTO


def test_fallback_no_filtra_el_valor_en_el_log(monkeypatch, caplog):
    secreto = "http://usuario:clave-secreta@host:8710"
    monkeypatch.setenv("FORJA_PUBLIC_URL", secreto)
    with caplog.at_level("WARNING", logger=client.logger.name):
        _exportar(monkeypatch)
    assert caplog.records, "el fallback debe dejar una linea de log"
    assert "clave-secreta" not in caplog.text and "usuario" not in caplog.text


def test_base_se_lee_al_llamar_no_al_importar(monkeypatch):
    monkeypatch.setenv("FORJA_PUBLIC_URL", "http://a.example:1")
    assert _exportar(monkeypatch)["url_descarga"].startswith("http://a.example:1/")
    monkeypatch.setenv("FORJA_PUBLIC_URL", "http://b.example:2")
    assert _exportar(monkeypatch)["url_descarga"].startswith("http://b.example:2/")


@pytest.mark.parametrize("kwargs", [
    {"formato": "step", "descarga": None},
    {"formato": "step", "descarga": DESCARGA_3MF},   # even if a backend sent one
    {"formato": "3mf", "por_solido": True},
    {"formato": "stl", "por_solido": True, "descarga": DESCARGA_STL},
    {"formato": "3mf", "descarga": None},
    {"formato": "stl", "descarga": None},
])
def test_ausente_cuando_no_aplica(monkeypatch, kwargs):
    r = _exportar(monkeypatch, **kwargs)
    assert "url_descarga" not in r and "enlace_orca" not in r
    assert r["ruta"] == "/data/x"


def test_ausente_en_errores(monkeypatch):
    _stub(monkeypatch, {"detail": "no existe", "descarga": DESCARGA_3MF}, status=404)
    r = client.exportar("abc123", "3mf")
    assert r["error"] is True
    assert "url_descarga" not in r and "enlace_orca" not in r


@pytest.mark.parametrize("descarga", [
    DESCARGA_3MF + "?", DESCARGA_3MF + "?x=1", DESCARGA_3MF + "#f", DESCARGA_3MF + "#",
    DESCARGA_3MF + "x", DESCARGA_3MF + " ", DESCARGA_3MF + "\n", DESCARGA_3MF + "/",
    DESCARGA_3MF + ".stl", "/documentos/abc123/descarga/mesa.3mf?x=/a.3mf",
    "/documentos/abc123/descarga/mesa", "/documentos/abc123/descarga/mesa.obj",
    "/documentos/abc123/descarga/a b.3mf", "http://evil/documentos/a/descarga/m.3mf",
    "//evil/x.3mf", "", None, 5, ["x"],
])
def test_descarga_con_basura_no_emite_enlace(monkeypatch, descarga):
    cuerpo = {"ruta": "/data/x", "tamano_bytes": 10, "descarga": descarga}
    _stub(monkeypatch, cuerpo)
    r = client.exportar("abc123", "3mf")
    assert "url_descarga" not in r and "enlace_orca" not in r
    assert r["descarga"] == descarga  # passed through, untouched


@pytest.mark.parametrize("descarga,formato", [(DESCARGA_3MF, "3mf"), (DESCARGA_STL, "stl")])
def test_sin_interrogacion_y_termina_en_extension(monkeypatch, descarga, formato):
    r = _exportar(monkeypatch, descarga, formato)
    assert "?" not in r["descarga"] and "?" not in r["url_descarga"]
    assert "?" not in urllib.parse.unquote(r["enlace_orca"][len(PREFIJO):])
    assert re.search(r"\.(stl|3mf)$", _valor_file(r["enlace_orca"]))
    assert r["enlace_orca"].count(PREFIJO) == 1 and "%25" not in r["enlace_orca"]  # one encoding


def test_enlace_apto_para_shell_con_comillas_simples(monkeypatch):
    monkeypatch.setenv("FORJA_PUBLIC_URL", "http://192.168.1.50:8710")
    e = _exportar(monkeypatch)["enlace_orca"]
    assert "'" not in e and not re.search(r"\s", e)
    assert re.fullmatch(r"orcaslicer://open\?file=[A-Za-z0-9%_.~-]+", e)


def test_sin_token_en_la_respuesta(monkeypatch):
    token = auth.obtener_token()
    assert token
    monkeypatch.setenv("FORJA_PUBLIC_URL", "http://192.168.1.50:8710")
    r = _exportar(monkeypatch)
    assert token not in json.dumps(r) and urllib.parse.quote(token, safe="") not in json.dumps(r)


def test_claves_extra_del_backend_pasan(monkeypatch):
    extra = {"malla_mm3": 1.0, "esperado_mm3": 2.0, "diferencia_pct": 50.0}
    r = _exportar(monkeypatch, clave_futura=extra, objetos=["mesa"], otra="x")
    assert r["clave_futura"] == extra and r["objetos"] == ["mesa"] and r["otra"] == "x"
    assert set(r) == {"ruta", "tamano_bytes", "descarga", "clave_futura", "objetos", "otra",
                      "url_descarga", "enlace_orca"}


def test_sin_llamadas_de_red_extra(monkeypatch):
    llamadas = []

    class _C:
        def __init__(self, *a, **k):
            llamadas.append("client")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **k):
            llamadas.append("get")
            return _Resp({"ruta": "r", "tamano_bytes": 1, "descarga": DESCARGA_3MF})

    monkeypatch.setattr(client.httpx, "Client", _C)
    client.exportar("abc123", "3mf")
    assert llamadas == ["client", "get"]


NOMBRES_20 = {  # fdm-B: + cupon
    "estado", "listar_documentos", "abrir_archivo", "resumen_documento", "ejecutar_script",
    "exportar", "captura", "check_colisiones", "percibir", "parametros", "check_fdm",
    "leer_notas", "crear_nota", "borrar_nota", "leer_historial", "restaurar", "ensamble",
    "suspension", "puentes", "cupon", "rama",  # G2: + rama
}


def test_inventario_de_herramientas_no_cambia():
    registradas = {t.name for t in crear_servidor()._tool_manager.list_tools()}  # noqa: SLF001
    assert registradas == NOMBRES_20 and len(registradas) == 21


def test_docstring_documenta_los_campos():
    from mcp_server import tools
    doc = tools.exportar.__doc__
    for clave in ("url_descarga", "enlace_orca", "FORJA_PUBLIC_URL", "xdg-open", "http://localhost:8710"):
        assert clave in doc
