"""G4/G5 (Plan G): verified merge (parameters, notes, scripts, geometric
conflict), per-piece cherry-pick with materials, `simular`, all-or-nothing,
two-parent commits, milestones/curation and the MCP `rama` actions."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import auth
import documents
import fusion
import git_store
import notes
import ramas
import solids
from main import app
from mcp_server import client as mcp_client

client = TestClient(app)

SCRIPT = (
    "from build123d import Box, Pos\n"
    "PARAMETROS = {'xa': {'valor': 20, 'min': -40, 'max': 40},\n"
    "              'xb': {'valor': -20, 'min': -40, 'max': 40}}\n"
    "def construir(p):\n"
    "    return {\n"
    "        'base': Pos(0, 30, 0) * Box(10, 10, 4),\n"
    "        'brazo': Pos(p['xa'], 0, 0) * Box(4, 4, 4),\n"
    "        'torre': Pos(p['xb'], 0, 0) * Box(4, 4, 4),\n"
    "    }\n"
)


def _h() -> dict[str, str]:
    return {"X-Forja-Token": auth.obtener_token()}


@pytest.fixture()
def doc():
    r = client.post("/documentos/script", json={"codigo": SCRIPT, "nombre": "fusion_test"}, headers=_h())
    assert r.status_code == 200, r.text
    doc_id = r.json()["id"]
    yield doc_id
    client.delete(f"/documentos/{doc_id}", headers=_h())


def _param(doc_id, **valores):
    r = client.post(f"/documentos/{doc_id}/parametros", json={"valores": valores}, headers=_h())
    assert r.status_code == 200, r.text


def _rama(doc_id, nombre):
    assert client.post(f"/documentos/{doc_id}/ramas", json={"nombre": nombre}, headers=_h()).status_code == 200


def _activar(doc_id, nombre):
    r = client.post(f"/documentos/{doc_id}/ramas/{nombre}/activar", headers=_h())
    assert r.status_code == 200, r.text


def _fusionar(doc_id, **cuerpo):
    r = client.post(f"/documentos/{doc_id}/ramas/fusionar", json=cuerpo, headers=_h())
    assert r.status_code == 200, r.text
    return r.json()


def _divergir(doc_id, en_b, en_a):
    """main = A (active at the end), rama `b` = B; both from the same step."""
    _rama(doc_id, "b")
    _activar(doc_id, "b")
    en_b()
    _activar(doc_id, "main")
    en_a()


def _bbox(doc_id, pieza):
    return ramas._piezas(solids.cargar(doc_id))[pieza]["bbox"]


def _padres(doc_id, sha):
    linea = git_store._git(git_store.ruta_repo(doc_id), "rev-list", "--parents", "-n", "1", sha).decode().split()
    return linea[1:]


# ------------------------------------------------------------ cherry-pick

def test_traer_pieza_sustituye_solo_esa_pieza_y_su_material(doc):
    def en_b():
        _param(doc, xb=-30)
        r = client.post(f"/documentos/{doc}/materiales", json={"materiales": {"torre": "PETG"}}, headers=_h())
        assert r.status_code == 200, r.text
    _divergir(doc, en_b, lambda: _param(doc, xa=25))
    tip_a = git_store.tip_rama(doc, "main")
    r = _fusionar(doc, desde="b", piezas=["torre"])
    assert r["resultado"] == "fusionada" and r["confirmada"] and r["modo"] == "piezas"
    assert _bbox(doc, "torre")[0] == pytest.approx(-32)      # B's torre
    assert _bbox(doc, "brazo")[0] == pytest.approx(23)       # A's brazo kept
    assert [e["nombre"] for e in solids.cargar(doc)] == ["base", "brazo", "torre"]
    mats = client.get(f"/documentos/{doc}/materiales").json()["materiales"]
    assert mats["torre"]["material"] == "PETG"
    # script document -> marked, regenerating asks for confirmation
    assert any(a.startswith("geometria_editada") for a in r["avisos"])
    avisos = client.get(f"/documentos/{doc}/parametros").json()["avisos"]
    assert any(a.startswith("geometria_editada") for a in avisos)
    r2 = client.post(f"/documentos/{doc}/parametros", json={"valores": {"xa": 26}}, headers=_h())
    assert r2.status_code == 409
    # cherry-pick = one parent (does not include the whole branch)
    tip = git_store.tip_rama(doc, "main")
    assert _padres(doc, tip) == [tip_a]
    assert git_store.leer(doc, tip) == ramas.capturar(doc)[0]
    # confirming regenerates from the script and clears the mark
    r3 = client.post(f"/documentos/{doc}/parametros", json={"valores": {"xa": 26}, "confirmar_script": True},
                     headers=_h())
    assert r3.status_code == 200, r3.text
    assert "avisos" not in client.get(f"/documentos/{doc}/parametros").json()


def test_traer_pieza_desconocida_es_400(doc):
    _rama(doc, "b")
    r = client.post(f"/documentos/{doc}/ramas/fusionar", json={"desde": "b", "piezas": ["nada"]}, headers=_h())
    assert r.status_code == 400


# ------------------------------------------------------------ datos

def test_fusion_de_parametros_sin_conflicto_reconstruye_y_tiene_dos_padres(doc):
    _divergir(doc, lambda: _param(doc, xb=-25), lambda: _param(doc, xa=25))
    tip_a, tip_b = git_store.tip_rama(doc, "main"), git_store.tip_rama(doc, "b")
    r = _fusionar(doc, desde="b")
    assert r["resultado"] == "fusionada", r
    assert r["verificacion"]["ok"] and r["verificacion"]["comprobada"]
    valores = client.get(f"/documentos/{doc}/parametros").json()["valores"]
    assert valores == {"xa": 25, "xb": -25}
    assert _bbox(doc, "brazo")[0] == pytest.approx(23) and _bbox(doc, "torre")[0] == pytest.approx(-27)
    tip = git_store.tip_rama(doc, "main")
    assert _padres(doc, tip) == [tip_a, tip_b]
    assert git_store.leer(doc, tip) == ramas.capturar(doc)[0]
    # merging again: already included
    assert _fusionar(doc, desde="b")["resultado"] == "ya_incluida"


def test_fusion_de_parametros_con_conflicto_no_escribe(doc):
    _divergir(doc, lambda: _param(doc, xa=30), lambda: _param(doc, xa=25))
    antes = ramas.capturar(doc)[0]
    tip = git_store.tip_rama(doc, "main")
    r = _fusionar(doc, desde="b")
    assert r["resultado"] == "conflicto" and not r["confirmada"]
    c = r["conflictos"][0]
    assert (c["tipo"], c["clave"], c["a"], c["b"], c["base"]) == ("parametro", "xa", 25, 30, 20)
    assert "cambio en las dos ramas" in c["mensaje"]
    assert ramas.capturar(doc)[0] == antes and git_store.tip_rama(doc, "main") == tip
    # estrategia suya resolves it
    r = _fusionar(doc, desde="b", estrategia="suya")
    assert r["resultado"] == "fusionada"
    assert client.get(f"/documentos/{doc}/parametros").json()["valores"]["xa"] == 30


def test_union_de_notas_por_id(doc):
    def nota(texto):
        r = client.post(f"/documentos/{doc}/notas", json={"comentario": texto,
                        "referencia": {"tipo": "punto", "punto": [0, 0, 0]}})
        assert r.status_code < 300, r.text
    _divergir(doc, lambda: nota("de b"), lambda: nota("de a"))
    rev = documents._revisiones[doc]
    r = _fusionar(doc, desde="b")
    assert r["resultado"] == "fusionada", r
    textos = sorted(n["comentario"] for n in notes.cargar(doc)["notas"])
    assert textos == ["de a", "de b"]
    assert documents._revisiones[doc] == rev  # geometry untouched


# ------------------------------------------------------------ scripts

def _script(doc_id, codigo):
    r = client.post("/documentos/script", json={"codigo": codigo, "documento_id": doc_id}, headers=_h())
    assert r.status_code == 200, r.text


def test_merge_de_scripts_limpio_reconstruye(doc):
    script_b = SCRIPT.replace("Box(10, 10, 4)", "Box(12, 10, 4)")
    script_a = SCRIPT.replace("'torre': Pos(p['xb'], 0, 0) * Box(4, 4, 4)", "'torre': Pos(p['xb'], 0, 0) * Box(4, 4, 6)")
    _divergir(doc, lambda: _script(doc, script_b), lambda: _script(doc, script_a))
    r = _fusionar(doc, desde="b")
    assert r["resultado"] == "fusionada", r
    codigo = json.loads(documents._ruta_meta(doc).read_text())["script"]["codigo"]
    assert "Box(12, 10, 4)" in codigo and "Box(4, 4, 6)" in codigo
    assert _bbox(doc, "base")[1] - _bbox(doc, "base")[0] == pytest.approx(12)
    assert _bbox(doc, "torre")[5] - _bbox(doc, "torre")[4] == pytest.approx(6)
    assert git_store.leer_fuente(doc, git_store.tip_rama(doc, "main"))["script.py"].decode() == codigo


def test_merge_de_scripts_con_conflicto_devuelve_lineas(doc):
    _divergir(doc, lambda: _script(doc, SCRIPT.replace("Box(10, 10, 4)", "Box(12, 10, 4)")),
              lambda: _script(doc, SCRIPT.replace("Box(10, 10, 4)", "Box(14, 10, 4)")))
    antes = ramas.capturar(doc)[0]
    r = _fusionar(doc, desde="b")
    assert r["resultado"] == "conflicto"
    c = next(x for x in r["conflictos"] if x["tipo"] == "script")
    assert c["conflictos"] == 1
    assert any("<<<<<<<" in linea for linea in c["lineas"]) and any("Box(12" in linea for linea in c["lineas"])
    assert ramas.capturar(doc)[0] == antes


# ------------------------------------------------------------ verificación

def test_conflicto_geometrico_detectado_y_forzar(doc):
    _divergir(doc, lambda: _param(doc, xb=5), lambda: _param(doc, xa=5))
    antes = ramas.capturar(doc)[0]
    tip = git_store.tip_rama(doc, "main")
    r = _fusionar(doc, desde="b")
    assert r["resultado"] == "conflicto_geometrico" and not r["confirmada"]
    assert r["verificacion"]["choques_nuevos"] == [["brazo", "torre"]]
    assert r["verificacion"]["padres"] == {"a": 0, "b": 0}
    assert ramas.capturar(doc)[0] == antes and git_store.tip_rama(doc, "main") == tip
    r = _fusionar(doc, desde="b", forzar=True)
    assert r["resultado"] == "fusionada" and r["forzada"] is True


def test_simular_no_escribe(doc):
    _divergir(doc, lambda: _param(doc, xb=-25), lambda: _param(doc, xa=25))
    antes = ramas.capturar(doc)[0]
    tip = git_store.tip_rama(doc, "main")
    historial = client.get(f"/documentos/{doc}/historial").json()
    r = _fusionar(doc, desde="b", simular=True)
    assert r["resultado"] == "simulada" and r["simulada"] and r["verificacion"]["ok"]
    assert any("xb" in c for c in r["cambios"])
    assert ramas.capturar(doc)[0] == antes and git_store.tip_rama(doc, "main") == tip
    assert client.get(f"/documentos/{doc}/historial").json() == historial


def test_todo_o_nada_ante_fallo(doc, monkeypatch):
    _divergir(doc, lambda: _param(doc, xb=-25), lambda: _param(doc, xa=25))
    antes = ramas.capturar(doc)[0]
    tip = git_store.tip_rama(doc, "main")
    rev = documents._revisiones[doc]

    def _rompe(*a, **k):
        raise RuntimeError("fallo simulado")
    monkeypatch.setattr(documents, "confirmar_revision", _rompe)
    import parametros
    with pytest.raises(RuntimeError):
        with parametros.bloqueo(doc):
            fusion.fusionar(doc, "b")
    assert ramas.capturar(doc)[0] == antes
    assert git_store.tip_rama(doc, "main") == tip
    assert documents._revisiones[doc] == rev


# ------------------------------------------------------------ G5 hitos

def test_hitos_y_vista_curada(doc):
    for i in range(6):
        _param(doc, xa=10 + i)
        if i in (1, 4):
            r = client.post(f"/documentos/{doc}/hitos", json={"nombre": f"h{i}", "descripcion": f"hito {i}"},
                            headers=_h())
            assert r.status_code == 200, r.text
    assert client.post(f"/documentos/{doc}/hitos", json={"nombre": "h1"}, headers=_h()).status_code == 409
    hitos = client.get(f"/documentos/{doc}/hitos").json()["hitos"]
    assert [h["nombre"] for h in hitos] == ["h4", "h1"] and hitos[0]["descripcion"] == "hito 4"
    v = client.get(f"/documentos/{doc}/ramas/main/pasos_curados", params={"vista": "hitos"}).json()
    total = v["total"]
    assert len(v["pasos"]) == 3 and sum(p["agrupa"] for p in v["pasos"]) == total
    assert [p["hito"] for p in v["pasos"]] == [None, "h4", "h1"]
    # hide one step: visible view leaves it out; history intact
    ultimo = v["pasos"][0]["sha_corto"]
    assert client.post(f"/documentos/{doc}/pasos/ocultar", json={"pasos": [ultimo]}, headers=_h()).status_code == 200
    vis = client.get(f"/documentos/{doc}/ramas/main/pasos_curados", params={"vista": "visibles"}).json()
    assert ultimo not in [p["sha_corto"] for p in vis["pasos"]]
    assert client.get(f"/documentos/{doc}/ramas/main/pasos").json()["total"] == total
    assert client.delete(f"/documentos/{doc}/hitos/h1", headers=_h()).status_code == 200
    assert [h["nombre"] for h in client.get(f"/documentos/{doc}/hitos").json()["hitos"]] == ["h4"]
    assert client.post(f"/documentos/{doc}/hitos", json={"nombre": "../x"}, headers=_h()).status_code == 400


# ------------------------------------------------------------ MCP

def test_mcp_rama_fusionar_traer_e_hitos(doc, monkeypatch):
    monkeypatch.setattr(mcp_client, "_headers_con_token", _h)

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

    monkeypatch.setattr(mcp_client.httpx, "Client", lambda *a, headers=None, **k: _Envoltura(headers or {}))
    from mcp_server import tools
    _divergir(doc, lambda: _param(doc, xb=-25), lambda: _param(doc, xa=25))
    sim = tools.rama(doc, "fusionar", desde="b", simular=True)
    assert sim["resultado"] == "simulada"
    traida = tools.rama(doc, "traer_pieza", desde="b", piezas=["torre"])
    assert traida["resultado"] == "fusionada" and traida["modo"] == "piezas"
    assert tools.rama(doc, "traer_pieza", desde="b")["error"] is True
    assert tools.rama(doc, "hito", nombre="v1", a="primera")["hito"] == "v1"
    assert tools.rama(doc, "hitos")["hitos"][0]["nombre"] == "v1"
    assert tools.rama(doc, "fusionar")["error"] is True
