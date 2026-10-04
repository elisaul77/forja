"""G2/G3 (Plan G): branches with complete post-change states, steps,
switching (materialised byte for byte + SSE), comparison by named piece,
safety of branch names/shas, and the MCP `rama` tool."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import auth
import documents
import eventos
import git_store
import notes
import ramas
import solids
from main import app
from mcp_server import client as mcp_client

client = TestClient(app)

SCRIPT = (
    "from build123d import Box, Pos\n"
    "PARAMETROS = {'alto': {'valor': 10, 'min': 1, 'max': 100},\n"
    "              'ancho': {'valor': 10, 'min': 1, 'max': 100},\n"
    "              'torre': {'valor': 0, 'min': 0, 'max': 1}}\n"
    "def construir(p):\n"
    "    piezas = {'base': Box(p['ancho'], 10, 4), 'brazo': Pos(20, 0, 0) * Box(4, 4, p['alto'])}\n"
    "    if p['torre'] >= 1:\n"
    "        piezas['torre'] = Pos(-20, 0, 0) * Box(3, 3, 8)\n"
    "    return piezas\n"
)


def _h() -> dict[str, str]:
    return {"X-Forja-Token": auth.obtener_token()}


@pytest.fixture()
def doc():
    r = client.post("/documentos/script", json={"codigo": SCRIPT, "nombre": "ramas_test"}, headers=_h())
    assert r.status_code == 200, r.text
    doc_id = r.json()["id"]
    yield doc_id
    client.delete(f"/documentos/{doc_id}", headers=_h())


def _param(doc_id, **valores):
    r = client.post(f"/documentos/{doc_id}/parametros", json={"valores": valores}, headers=_h())
    assert r.status_code == 200, r.text
    return r.json()


def _estado_disco(doc_id):
    archivos, _fuente, _rev = ramas.capturar(doc_id)
    return archivos


# ------------------------------------------------------------ G2

def test_creacion_deja_main_con_un_paso_completo(doc):
    r = client.get(f"/documentos/{doc}/ramas")
    assert r.status_code == 200
    datos = r.json()
    assert datos["activa"] == "main"
    assert [x["nombre"] for x in datos["ramas"]] == ["main"]
    sha = git_store.tip_rama(doc, "main")
    arbol = git_store.leer(doc, sha)
    assert {f"{doc}.step", "meta.json", "solidos.json"} <= set(arbol)
    assert git_store.leer_fuente(doc, sha)["script.py"] == SCRIPT.encode()
    assert datos["ramas"][0]["ultimo"]["revision"] == documents._revisiones[doc]


def test_cada_cambio_aceptado_agrega_un_paso_con_estado_posterior(doc):
    antes = git_store.contar(doc, git_store.tip_rama(doc, "main"))
    _param(doc, alto=20)
    p = client.get(f"/documentos/{doc}/ramas/main/pasos").json()
    assert p["total"] == antes + 1
    ultimo = p["pasos"][0]
    assert set(ultimo) == {"sha_corto", "fecha", "autor", "mensaje", "revision"}
    assert "alto=20" in ultimo["mensaje"] and not ultimo["mensaje"].startswith("antes de")
    assert ultimo["revision"] == documents._revisiones[doc]
    # the step holds exactly what is on disk now
    assert git_store.leer(doc, git_store.tip_rama(doc, "main")) == _estado_disco(doc)


def test_peticion_fallida_no_agrega_paso(doc):
    antes = git_store.tip_rama(doc, "main")
    r = client.post(f"/documentos/{doc}/parametros", json={"valores": {"alto": 999}}, headers=_h())
    assert r.status_code >= 400
    assert git_store.tip_rama(doc, "main") == antes


def test_crear_cambiar_y_materializar_byte_a_byte(doc):
    historial_antes = client.get(f"/documentos/{doc}/historial").json()
    r = client.post(f"/documentos/{doc}/ramas", json={"nombre": "alta"}, headers=_h())
    assert r.status_code == 200, r.text
    r = client.post(f"/documentos/{doc}/ramas/alta/activar", headers=_h())
    assert r.status_code == 200 and r.json()["rama"] == "alta"
    _param(doc, alto=40)
    client.post(f"/documentos/{doc}/notas", json={"comentario": "solo en alta",
                "referencia": {"tipo": "punto", "punto": [0, 0, 0]}})
    estado_alta = _estado_disco(doc)
    assert git_store.leer(doc, git_store.tip_rama(doc, "alta")) == estado_alta
    rev_alta = documents._revisiones[doc]

    r = client.post(f"/documentos/{doc}/ramas/main/activar", headers=_h())
    assert r.status_code == 200, r.text
    assert documents._revisiones[doc] != rev_alta
    estado_main = _estado_disco(doc)
    assert estado_main == git_store.leer(doc, git_store.tip_rama(doc, "main"))
    assert "solo en alta" not in json.dumps(notes.cargar(doc))
    assert client.get(f"/documentos/{doc}/parametros").json()["valores"]["alto"] == 10

    r = client.post(f"/documentos/{doc}/ramas/alta/activar", headers=_h())
    assert _estado_disco(doc) == estado_alta  # byte for byte
    assert documents._revisiones[doc] == rev_alta
    assert r.json()["volumen"] == pytest.approx(documents._registry[doc]["volumen"])
    assert solids.cargar(doc) == json.loads(estado_alta["solidos.json"])

    # G1 contracts intact: entries only grow, same shape
    historial = client.get(f"/documentos/{doc}/historial").json()
    assert historial[: len(historial_antes)] == historial_antes
    assert all(set(e) == {"id", "fecha", "mensaje"} for e in historial)
    assert any(e["mensaje"] == "antes de cambiar a la rama main" for e in historial)


def test_restaurar_sigue_funcionando_y_deja_paso(doc):
    _param(doc, alto=30)
    historial = client.get(f"/documentos/{doc}/historial").json()
    previo = [e for e in historial if e["mensaje"].startswith("antes de parametros")][-1]
    r = client.post(f"/documentos/{doc}/restaurar", json={"snapshot": previo["id"]}, headers=_h())
    assert r.status_code == 200, r.text
    assert client.get(f"/documentos/{doc}/parametros").json()["valores"]["alto"] == 10
    ultimo = client.get(f"/documentos/{doc}/ramas/main/pasos").json()["pasos"][0]
    assert ultimo["mensaje"].startswith("restaurar ")
    assert git_store.leer(doc, git_store.tip_rama(doc, "main")) == _estado_disco(doc)


def test_cambiar_rama_publica_evento(doc, monkeypatch):
    client.post(f"/documentos/{doc}/ramas", json={"nombre": "b"}, headers=_h())
    client.post(f"/documentos/{doc}/ramas/b/activar", headers=_h())
    _param(doc, alto=25)
    vistos = []
    monkeypatch.setattr(eventos, "publicar", lambda tipo, i, rev=None, cambios=None: vistos.append((tipo, i, rev)))
    client.post(f"/documentos/{doc}/ramas/main/activar", headers=_h())
    assert ("documento_actualizado", doc, documents._revisiones[doc]) in vistos


def test_cambio_sin_registrar_no_se_pierde_al_cambiar(doc):
    client.post(f"/documentos/{doc}/ramas", json={"nombre": "otra"}, headers=_h())
    # a change made outside any HTTP request (no post-change step recorded)
    notes.guardar(doc, {"notas": [{"id": "x", "comentario": "fuera"}], "trazos": []})
    client.post(f"/documentos/{doc}/ramas/otra/activar", headers=_h())
    tip_main = git_store.tip_rama(doc, "main")
    assert b"fuera" in git_store.leer(doc, tip_main)["notas.json"]
    client.post(f"/documentos/{doc}/ramas/main/activar", headers=_h())
    assert "fuera" in json.dumps(notes.cargar(doc))


def test_renombrar_y_borrar(doc):
    client.post(f"/documentos/{doc}/ramas", json={"nombre": "tmp"}, headers=_h())
    assert client.post(f"/documentos/{doc}/ramas/tmp/renombrar", json={"a": "tmp2"}, headers=_h()).status_code == 200
    nombres = [x["nombre"] for x in client.get(f"/documentos/{doc}/ramas").json()["ramas"]]
    assert nombres == ["main", "tmp2"]
    assert client.delete(f"/documentos/{doc}/ramas/main", headers=_h()).status_code == 409
    assert client.post(f"/documentos/{doc}/ramas/main/renombrar", json={"a": "x"}, headers=_h()).status_code == 409
    client.post(f"/documentos/{doc}/ramas/tmp2/activar", headers=_h())
    assert client.delete(f"/documentos/{doc}/ramas/tmp2", headers=_h()).status_code == 409  # active
    client.post(f"/documentos/{doc}/ramas/main/activar", headers=_h())
    r = client.delete(f"/documentos/{doc}/ramas/tmp2", headers=_h())
    assert r.status_code == 200 and len(r.json()["sha"]) == 40
    assert client.post(f"/documentos/{doc}/ramas", json={"nombre": "main"}, headers=_h()).status_code == 409


def test_crear_desde_un_paso(doc):
    primero = client.get(f"/documentos/{doc}/ramas/main/pasos").json()["pasos"][-1]["sha_corto"]
    _param(doc, alto=50)
    r = client.post(f"/documentos/{doc}/ramas", json={"nombre": "vieja", "desde": primero}, headers=_h())
    assert r.status_code == 200, r.text
    assert git_store.tip_rama(doc, "vieja").startswith(primero)


def test_mutaciones_exigen_token(doc):
    assert client.post(f"/documentos/{doc}/ramas", json={"nombre": "x"}).status_code == 401
    assert client.post(f"/documentos/{doc}/ramas/main/activar").status_code == 401
    assert client.delete(f"/documentos/{doc}/ramas/main").status_code == 401


# ------------------------------------------------------------ seguridad

@pytest.mark.parametrize("nombre", ["../x", "a/b", ".oculta", "a..b", "x.lock", "-x", "a b", "a@{1}",
                                    "", "x" * 49, "HEAD~1", "a\nb", "ñandú", "--upload-pack=x"])
def test_nombres_de_rama_inseguros_se_rechazan(doc, nombre):
    r = client.post(f"/documentos/{doc}/ramas", json={"nombre": nombre}, headers=_h())
    assert r.status_code in (400, 422), (nombre, r.status_code)
    with pytest.raises(ValueError):
        git_store.validar_rama(nombre)


@pytest.mark.parametrize("ref", ["HEAD", "main~1", "--all", "refs/heads/main", "main^{tree}", ":/x", "@{-1}"])
def test_comparar_y_desde_no_aceptan_expresiones(doc, ref):
    r = client.get(f"/documentos/{doc}/comparar", params={"a": ref, "b": "main"})
    assert r.status_code in (400, 404), (ref, r.status_code)
    r = client.post(f"/documentos/{doc}/ramas", json={"nombre": "z", "desde": ref}, headers=_h())
    assert r.status_code in (400, 404)


def test_shas_externos_validados():
    for malo in ("HEAD", "--output=/tmp/x", "a" * 39, "g" * 40):
        with pytest.raises(ValueError):
            git_store.validar_sha(malo)
    with pytest.raises(ValueError):
        git_store.log("doc-x", desde="--all")


# ------------------------------------------------------------ G3

def test_comparar_rebuild_identico_cero_falsos_cambios(doc):
    a = git_store.tip_rama(doc, "main")
    # same values again -> rebuild of the exact same geometry
    _param(doc, alto=10)
    b = git_store.tip_rama(doc, "main")
    assert a != b
    r = client.get(f"/documentos/{doc}/comparar", params={"a": a[:10], "b": b[:10]}).json()
    assert r["geometria_identica"] is True
    assert r["resumen"]["cambiada"] == r["resumen"]["añadida"] == r["resumen"]["quitada"] == 0
    assert r["volumen"]["delta"] == 0


def test_comparar_pieza_cambiada_anadida_y_parametros(doc):
    client.post(f"/documentos/{doc}/ramas", json={"nombre": "torre"}, headers=_h())
    client.post(f"/documentos/{doc}/ramas/torre/activar", headers=_h())
    _param(doc, alto=10.1, torre=1)
    r = client.get(f"/documentos/{doc}/comparar", params={"a": "main", "b": "torre"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["piezas"] == {"base": "igual", "brazo": "cambiada", "torre": "añadida"}
    assert d["volumen"]["delta"] == pytest.approx(4 * 4 * 0.1 + 3 * 3 * 8, rel=1e-6)
    assert {p["nombre"]: (p["antes"], p["despues"]) for p in d["parametros"]} == {
        "alto": (10, 10.1), "torre": (0, 1)}
    assert d["bbox"]["delta"][0] == pytest.approx(-(20 + 1.5 - 5), abs=1e-6)  # torre widens xmin
    inversa = client.get(f"/documentos/{doc}/comparar", params={"a": "torre", "b": "main"}).json()
    assert inversa["piezas"]["torre"] == "quitada"


def test_comparar_materiales(doc):
    client.post(f"/documentos/{doc}/ramas", json={"nombre": "color"}, headers=_h())
    client.post(f"/documentos/{doc}/ramas/color/activar", headers=_h())
    r = client.post(f"/documentos/{doc}/materiales", json={"materiales": {"base": {"color": "#ff0000"}}},
                    headers=_h())
    assert r.status_code == 200, r.text
    d = client.get(f"/documentos/{doc}/comparar", params={"a": "main", "b": "color"}).json()
    assert d["geometria_identica"] is True and d["sin_cambios"] is False
    assert [m["pieza"] for m in d["materiales"]] == ["base"]


def test_malla_de_paso_para_superponer(doc):
    r = client.get(f"/documentos/{doc}/comparar/malla", params={"ref": "main"})
    assert r.status_code == 200
    assert r.content[:4] == b"FJP1"
    assert r.headers["X-Forja-Revision"] == documents._revisiones[doc]


# ------------------------------------------------------------ MCP

def test_mcp_rama_sobre_el_cliente(doc, monkeypatch):
    """The MCP wrapper against the in-process app (same routes)."""
    monkeypatch.setattr(mcp_client, "_headers_con_token", _h)

    def _cliente(*a, base_url=None, headers=None, timeout=None):
        return _Envoltura(headers or {})

    class _Envoltura:
        def __init__(self, cab):
            self.cab = cab

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url, params=None):
            return client.get(url, params=params, headers=self.cab)

        def post(self, url, json=None, headers=None):
            return client.post(url, json=json, headers={**self.cab, **(headers or {})})

        def delete(self, url, headers=None):
            return client.delete(url, headers={**self.cab, **(headers or {})})

    monkeypatch.setattr(mcp_client.httpx, "Client", _cliente)
    from mcp_server import tools
    assert tools.rama(doc, "listar")["activa"] == "main"
    assert tools.rama(doc, "crear", nombre="mcp")["rama"] == "mcp"
    cambio = tools.rama(doc, "cambiar", nombre="mcp")
    assert cambio["rama"] == "mcp" and set(cambio) == {"rama", "sha_corto", "revision", "volumen", "solidos", "valido", "avisos"}
    assert cambio["avisos"] == []
    assert tools.rama(doc, "pasos")["rama"] == "mcp"
    assert tools.rama(doc, "comparar", desde="main")["geometria_identica"] is True
    assert tools.rama(doc, "renombrar", nombre="mcp", a="mcp2")["rama"] == "mcp2"
    tools.rama(doc, "cambiar", nombre="main")
    assert tools.rama(doc, "borrar", nombre="mcp2")["borrada"] == "mcp2"
    assert tools.rama(doc, "volar")["error"] is True
    assert tools.rama(doc, "crear", nombre="../x")["error"] is True


# ------------------------------------------------------------ fix-review G2/G3

_sin_propagar = TestClient(app, raise_server_exceptions=False)

def _foto(doc_id):
    """Everything a switch may touch, byte for byte."""
    import assemblies
    import materiales
    ruta = documents._files[doc_id]
    archivos = {ruta.name: ruta.read_bytes()}
    for camino in (documents._ruta_meta(doc_id), notes.ruta_notas(doc_id), solids.ruta_solidos(doc_id),
                   materiales.ruta(doc_id), assemblies._directory(doc_id) / "state.json",
                   assemblies._directory(doc_id) / "base.step"):
        archivos[str(camino)] = camino.read_bytes() if camino.exists() else None
    return (archivos, git_store.rama_activa(doc_id), dict(documents._registry[doc_id]),
            documents._revisiones.get(doc_id), git_store.leer_script_divergente(doc_id))


@pytest.mark.parametrize("donde", ["restore_files", "_analyze", "confirmar_revision"])
def test_cambiar_es_todo_o_nada(doc, monkeypatch, donde):
    import assemblies
    client.post(f"/documentos/{doc}/ramas", json={"nombre": "alta"}, headers=_h())
    client.post(f"/documentos/{doc}/ramas/alta/activar", headers=_h())
    _param(doc, alto=40)
    client.post(f"/documentos/{doc}/notas", json={"comentario": "solo en alta",
                "referencia": {"tipo": "punto", "punto": [0, 0, 0]}})
    antes = _foto(doc)
    tip_alta, tip_main = git_store.tip_rama(doc, "alta"), git_store.tip_rama(doc, "main")
    modulo = {"restore_files": assemblies, "_analyze": documents, "confirmar_revision": documents}[donde]
    original = getattr(modulo, donde)
    llamadas = []

    def _falla(*a, **k):
        llamadas.append(1)
        if len(llamadas) == 1:  # only the switch; the rollback must work
            if donde == "confirmar_revision":
                original(*a, **k)  # revision already moved, then it breaks
            raise OSError("disco lleno (inyectado)")
        return original(*a, **k)

    monkeypatch.setattr(modulo, donde, _falla)
    r = _sin_propagar.post(f"/documentos/{doc}/ramas/main/activar", headers=_h())
    assert r.status_code == 500
    assert llamadas, "el fallo no se inyecto"
    monkeypatch.setattr(modulo, donde, original)
    assert _foto(doc) == antes  # files, active branch, registry, revision, mark
    assert git_store.tip_rama(doc, "alta") == tip_alta and git_store.tip_rama(doc, "main") == tip_main
    directorio = documents._files[doc].parent
    assert not list(directorio.rglob("*.rama.tmp"))
    # and the document still switches fine afterwards
    r = client.post(f"/documentos/{doc}/ramas/main/activar", headers=_h())
    assert r.status_code == 200, r.text
    assert "solo en alta" not in json.dumps(notes.cargar(doc))


def test_fallo_preparando_temporales_no_toca_nada(doc, monkeypatch):
    client.post(f"/documentos/{doc}/ramas", json={"nombre": "alta"}, headers=_h())
    client.post(f"/documentos/{doc}/ramas/alta/activar", headers=_h())
    _param(doc, alto=40)
    antes = _foto(doc)
    from pathlib import Path
    escribir = Path.write_bytes
    cuenta = []

    def _write(self, datos):
        if self.name.endswith(".rama.tmp"):
            cuenta.append(self)
            if len(cuenta) == 3:
                raise OSError(28, "No space left on device")
        return escribir(self, datos)

    monkeypatch.setattr(Path, "write_bytes", _write)
    assert _sin_propagar.post(f"/documentos/{doc}/ramas/main/activar", headers=_h()).status_code == 500
    monkeypatch.setattr(Path, "write_bytes", escribir)
    assert len(cuenta) >= 3
    assert _foto(doc) == antes
    assert not list(documents._files[doc].parent.rglob("*.rama.tmp"))


SCRIPT_V2 = SCRIPT.replace("'alto': {'valor': 10", "'alto': {'valor': 25")


@pytest.fixture()
def doc_ruta(tmp_path, monkeypatch):
    monkeypatch.setattr(documents, "ALLOWED_READ_ROOTS", [*documents.ALLOWED_READ_ROOTS, tmp_path])
    fuente = tmp_path / "pieza.py"
    fuente.write_text(SCRIPT)
    r = client.post("/documentos/script", json={"ruta": str(fuente), "nombre": "ramas_ruta"}, headers=_h())
    assert r.status_code == 200, r.text
    doc_id = r.json()["id"]
    yield doc_id, fuente
    client.delete(f"/documentos/{doc_id}", headers=_h())


def test_script_por_ruta_divergente_avisa_y_bloquea_regenerar(doc_ruta):
    doc_id, fuente = doc_ruta
    client.post(f"/documentos/{doc_id}/ramas", json={"nombre": "v2"}, headers=_h())
    r = client.post(f"/documentos/{doc_id}/ramas/v2/activar", headers=_h())
    assert r.json()["avisos"] == []
    fuente.write_text(SCRIPT_V2)          # the user edits the file on v2
    _param(doc_id, ancho=12)              # regenerating on v2 is legit
    assert git_store.leer_fuente(doc_id, git_store.tip_rama(doc_id, "v2"))["script.py"] == SCRIPT_V2.encode()

    r = client.post(f"/documentos/{doc_id}/ramas/main/activar", headers=_h())
    assert r.status_code == 200, r.text
    avisos = r.json()["avisos"]
    assert len(avisos) == 1 and avisos[0].startswith("script_divergente: ")
    assert fuente.read_text() == SCRIPT_V2  # Forja never writes outside its data
    assert client.get(f"/documentos/{doc_id}/parametros").json()["avisos"][0].startswith("script_divergente: ")

    tip = git_store.tip_rama(doc_id, "main")
    r = client.post(f"/documentos/{doc_id}/parametros", json={"valores": {"ancho": 14}}, headers=_h())
    assert r.status_code == 409 and "no se regenera" in r.json()["detail"]
    assert git_store.tip_rama(doc_id, "main") == tip

    r = client.post(f"/documentos/{doc_id}/parametros",
                    json={"valores": {"ancho": 14}, "confirmar_script": True}, headers=_h())
    assert r.status_code == 200, r.text
    assert "avisos" not in client.get(f"/documentos/{doc_id}/parametros").json()
    assert git_store.leer_script_divergente(doc_id) is None


def test_script_divergente_se_resuelve_restaurando_el_archivo(doc_ruta):
    doc_id, fuente = doc_ruta
    client.post(f"/documentos/{doc_id}/ramas", json={"nombre": "v2"}, headers=_h())
    client.post(f"/documentos/{doc_id}/ramas/v2/activar", headers=_h())
    fuente.write_text(SCRIPT_V2)
    _param(doc_id, ancho=12)
    assert client.post(f"/documentos/{doc_id}/ramas/main/activar", headers=_h()).json()["avisos"]
    fuente.write_text(SCRIPT)               # the file is back to main's text
    _param(doc_id, ancho=14)                # no confirmation needed
    assert git_store.leer_script_divergente(doc_id) is None
    # switching to a branch whose text matches the file warns nothing
    fuente.write_text(SCRIPT_V2)
    assert client.post(f"/documentos/{doc_id}/ramas/v2/activar", headers=_h()).json()["avisos"] == []


def test_script_como_texto_nunca_diverge(doc):
    client.post(f"/documentos/{doc}/ramas", json={"nombre": "alta"}, headers=_h())
    assert client.post(f"/documentos/{doc}/ramas/alta/activar", headers=_h()).json()["avisos"] == []
    _param(doc, alto=30)
    assert client.post(f"/documentos/{doc}/ramas/main/activar", headers=_h()).json()["avisos"] == []
    _param(doc, alto=31)


def test_mcp_parametros_confirmar_script(doc_ruta, monkeypatch):
    from mcp_server import client as c
    vistos = []

    class _Resp:
        status_code = 200

        def json(self):
            return {}

    class _Cli:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *e):
            return False

        def post(self, url, json=None, headers=None):
            vistos.append(json)
            return _Resp()

    monkeypatch.setattr(c, "_headers_con_token", lambda: {})
    monkeypatch.setattr(c.httpx, "Client", _Cli)
    c.parametros("x", {"alto": 1})
    c.parametros("x", {"alto": 1}, confirmar_script=True)
    assert vistos == [{"valores": {"alto": 1}}, {"valores": {"alto": 1}, "confirmar_script": True}]


def test_rama_sin_meta_restablece_meta(doc):
    import parametros
    archivos, _f, _r = ramas.capturar(doc)
    assert "script" in json.loads(archivos["meta.json"])
    nombre = json.loads(archivos["meta.json"])["nombre"]
    sin_meta = {k: v for k, v in archivos.items() if k != "meta.json"}
    with parametros.bloqueo(doc):
        ramas._materializar(doc, documents._files[doc], sin_meta)
    meta = json.loads(documents._ruta_meta(doc).read_bytes())
    assert meta == {"nombre": nombre}
    assert client.get(f"/documentos/{doc}/parametros").json() == {"esquema": {}, "valores": {}}


def test_comparar_con_solidos_solo_en_un_lado(doc):
    client.get(f"/documentos/{doc}/ramas")
    tip = git_store.tip_rama(doc, "main")
    archivos = git_store.leer(doc, tip)
    sin = {k: v for k, v in archivos.items() if k != "solidos.json"}
    nuevo = git_store.escribir_commit(doc, sin, "sin solidos", "abc123abc123", "2026-10-04T00:00:00Z",
                                      "test", {}, padre=tip, extra={"Revision": "", "Rama": "sinsol"})
    git_store.mover_ref_rama(doc, "sinsol", nuevo, None)
    r = client.get(f"/documentos/{doc}/comparar", params={"a": "main", "b": "sinsol"}).json()
    assert set(r["piezas"]) == {"documento"}
    assert r["resumen"]["añadida"] == 0 and r["resumen"]["quitada"] == 0
    assert r["volumen"]["a"] > 0
