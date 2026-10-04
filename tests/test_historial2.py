"""Historial 2.0: per-step thumbnails (+ cache), commit graph with a
two-parent merge, per-piece history filter and «restore one piece»."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import auth
import git_store
import historial_grafo
import ramas
import solids
from main import app
from mcp_server import tools

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
    r = client.post("/documentos/script", json={"codigo": SCRIPT, "nombre": "historial2_test"}, headers=_h())
    assert r.status_code == 200, r.text
    doc_id = r.json()["id"]
    yield doc_id
    client.delete(f"/documentos/{doc_id}", headers=_h())


def _param(doc_id, **valores):
    r = client.post(f"/documentos/{doc_id}/parametros", json={"valores": valores, "confirmar_script": True},
                    headers=_h())
    assert r.status_code == 200, r.text


def _activar(doc_id, nombre):
    r = client.post(f"/documentos/{doc_id}/ramas/{nombre}/activar", headers=_h())
    assert r.status_code == 200, r.text


def _grafo(doc_id, **q):
    r = client.get(f"/documentos/{doc_id}/grafo", params=q)
    assert r.status_code == 200, r.text
    return r.json()


def _bbox(doc_id, pieza):
    return ramas._piezas(solids.cargar(doc_id))[pieza]["bbox"]


# ------------------------------------------------------------ miniaturas

def test_miniatura_png_cacheada_por_revision(doc):
    g = _grafo(doc)
    sha = g["nodos"][0]["sha_corto"]
    r = client.get(f"/documentos/{doc}/pasos/{sha}/miniatura.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
    from PIL import Image
    import io
    assert Image.open(io.BytesIO(r.content)).size == (historial_grafo.MINI_ANCHO, historial_grafo.MINI_ALTO)
    cache = list((historial_grafo._CACHE_MINI / doc).glob("*.png"))
    assert len(cache) == 1
    # Second call is served from the cache (same bytes, no new file).
    assert client.get(f"/documentos/{doc}/pasos/{sha}/miniatura.png").content == r.content
    pieza = client.get(f"/documentos/{doc}/pasos/{sha}/miniatura.png", params={"pieza": "torre"})
    assert pieza.status_code == 200 and pieza.content != r.content
    assert len(list((historial_grafo._CACHE_MINI / doc).glob("*.png"))) == 2


def test_miniatura_valida_sha_y_pieza(doc):
    assert client.get(f"/documentos/{doc}/pasos/main/miniatura.png").status_code == 400
    assert client.get(f"/documentos/{doc}/pasos/HEAD~1/miniatura.png").status_code in (400, 404)
    assert client.get(f"/documentos/{doc}/pasos/{'0' * 10}/miniatura.png").status_code == 404
    sha = _grafo(doc)["nodos"][0]["sha_corto"]
    assert client.get(f"/documentos/{doc}/pasos/{sha}/miniatura.png", params={"pieza": "nada"}).status_code == 404
    assert client.get(f"/documentos/noexiste/pasos/{sha}/miniatura.png").status_code == 404


def test_borrar_documento_borra_caches(doc):
    sha = _grafo(doc)["nodos"][0]["sha_corto"]
    client.get(f"/documentos/{doc}/pasos/{sha}/miniatura.png")
    assert (historial_grafo._CACHE_MINI / doc).is_dir() and (historial_grafo._CACHE_GRAFO / doc).is_dir()
    client.delete(f"/documentos/{doc}", headers=_h())
    assert not (historial_grafo._CACHE_MINI / doc).exists()
    assert not (historial_grafo._CACHE_GRAFO / doc).exists()


# ------------------------------------------------------------ grafo

def test_grafo_con_fusion_de_dos_padres_ramas_e_hitos(doc):
    _grafo(doc)  # creates main
    assert client.post(f"/documentos/{doc}/ramas", json={"nombre": "b"}, headers=_h()).status_code == 200
    _activar(doc, "b")
    _param(doc, xb=-30)
    _activar(doc, "main")
    _param(doc, xa=30)
    r = client.post(f"/documentos/{doc}/ramas/fusionar", json={"desde": "b"}, headers=_h())
    assert r.status_code == 200 and r.json()["resultado"] == "fusionada", r.text
    tip = git_store.tip_rama(doc, "main")
    assert client.post(f"/documentos/{doc}/hitos", json={"nombre": "v1", "paso": tip[:10]},
                       headers=_h()).status_code == 200

    g = _grafo(doc)
    assert g["activa"] == "main" and {x["nombre"] for x in g["ramas"]} == {"main", "b"}
    fusion = g["nodos"][0]
    assert fusion["fusion"] is True and len(fusion["padres"]) == 2
    assert fusion["hitos"] == ["v1"] and fusion["puntas"] == ["main"]
    shas = [n["sha_corto"] for n in g["nodos"]]
    assert all(p in shas for n in g["nodos"] for p in n["padres"])
    # Topological: children always before their parents.
    for i, n in enumerate(g["nodos"]):
        assert all(shas.index(p) > i for p in n["padres"])
    paso_b = next(n for n in g["nodos"] if n["puntas"] == ["b"])
    assert set(paso_b["ramas"]) == {"main", "b"}  # merged into main
    assert paso_b["cambios"]["cambiadas"] == ["torre"]
    assert paso_b["cambios"]["parametros"] == [{"nombre": "xb", "antes": -20, "despues": -30}]
    raiz = g["nodos"][-1]
    assert raiz["padres"] == [] and raiz["cambios"]["raiz"] is True
    assert set(raiz["cambios"]["añadidas"]) == {"base", "brazo", "torre"}
    # Summaries are cached per sha.
    assert (historial_grafo._CACHE_GRAFO / doc / f"{git_store.tip_rama(doc, 'b')}.v1.json").is_file()
    assert _grafo(doc, limite=2)["truncado"] is True and len(_grafo(doc, limite=2)["nodos"]) == 2


def test_grafo_filtrado_por_pieza(doc):
    _grafo(doc)
    _param(doc, xa=25)
    _param(doc, xb=-25)
    _param(doc, xa=10)
    g = _grafo(doc, pieza="brazo")
    msgs = [n["cambios"] for n in g["nodos"]]
    assert g["pieza"] == "brazo" and len(g["nodos"]) == 3  # 2 changes + creation
    assert all("brazo" in c["cambiadas"] or "brazo" in c["añadidas"] for c in msgs)
    # Parents are rewritten to the nearest kept ancestor.
    assert g["nodos"][0]["padres"] == [g["nodos"][1]["sha_corto"]]
    assert g["nodos"][-1]["padres"] == []
    assert len(_grafo(doc, pieza="torre")["nodos"]) == 2
    assert _grafo(doc, pieza="nada")["nodos"] == []


def test_simplificar_reescribe_padres_en_fusion():
    n = lambda s, p, k: {"sha_corto": s, "padres": p, "k": k}  # noqa: E731
    nodos = [n("m", ["a", "b"], True), n("a", ["o"], False), n("b", ["o"], True), n("o", [], True)]
    salida = historial_grafo._simplificar(nodos, lambda x: x["k"])
    assert [x["sha_corto"] for x in salida] == ["m", "b", "o"]
    assert salida[0]["padres"] == ["o", "b"]


# ------------------------------------------------------------ restaurar pieza

def test_restaurar_pieza_solo_cambia_esa_pieza(doc):
    _grafo(doc)
    viejo = git_store.tip_rama(doc, "main")
    bb_torre_0 = _bbox(doc, "torre")
    _param(doc, xa=30, xb=-30)
    bb_brazo = _bbox(doc, "brazo")
    sin_token = client.post(f"/documentos/{doc}/piezas/torre/restaurar", json={"desde": viejo[:10]})
    assert sin_token.status_code == 401
    r = client.post(f"/documentos/{doc}/piezas/torre/restaurar", json={"desde": viejo[:10]}, headers=_h())
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["resultado"] == "fusionada" and d["confirmada"] and d["pieza"] == "torre"
    assert _bbox(doc, "torre") == pytest.approx(bb_torre_0)
    assert _bbox(doc, "brazo") == pytest.approx(bb_brazo)
    paso = git_store.log_limitado(doc, git_store.tip_rama(doc, "main"), 1)[0]
    assert paso["mensaje"] == f"restaurar pieza torre a {viejo[:10]}"
    g = _grafo(doc)
    assert g["nodos"][0]["cambios"]["cambiadas"] == ["torre"] and g["nodos"][0]["desde"] == viejo[:10]
    # Again: already equal -> nothing written.
    tip = git_store.tip_rama(doc, "main")
    otra = client.post(f"/documentos/{doc}/piezas/torre/restaurar", json={"desde": viejo[:10]}, headers=_h())
    assert otra.json()["resultado"] == "sin_cambios" and git_store.tip_rama(doc, "main") == tip


def test_restaurar_pieza_todo_o_nada(doc, monkeypatch):
    _grafo(doc)
    viejo = git_store.tip_rama(doc, "main")
    _param(doc, xb=-30)
    tip = git_store.tip_rama(doc, "main")
    bb = _bbox(doc, "torre")

    import documents

    def roto(*_a, **_k):
        raise RuntimeError("fallo simulado")

    monkeypatch.setattr(documents, "confirmar_revision", roto)
    with pytest.raises(RuntimeError):
        client.post(f"/documentos/{doc}/piezas/torre/restaurar", json={"desde": viejo[:10]}, headers=_h())
    monkeypatch.undo()
    assert git_store.tip_rama(doc, "main") == tip
    assert _bbox(doc, "torre") == pytest.approx(bb)


def test_restaurar_pieza_valida_entrada(doc):
    _grafo(doc)
    viejo = git_store.tip_rama(doc, "main")[:10]
    assert client.post(f"/documentos/{doc}/piezas/nada/restaurar", json={"desde": viejo}, headers=_h()).status_code == 400
    assert client.post(f"/documentos/{doc}/piezas/torre/restaurar", json={"desde": "main~1"},
                       headers=_h()).status_code in (400, 422)
    assert client.post(f"/documentos/{doc}/piezas/torre/restaurar", json={"desde": "f" * 10},
                       headers=_h()).status_code == 404


def test_mcp_rama_restaurar_pieza(doc, monkeypatch):
    from mcp_server import client as mcp_client
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

    monkeypatch.setattr(mcp_client.httpx, "Client", lambda *a, headers=None, **k: _Envoltura(headers or {}))
    _grafo(doc)
    viejo = git_store.tip_rama(doc, "main")[:10]
    _param(doc, xb=-30)
    assert tools.rama(doc, "restaurar_pieza", desde=viejo)["error"] is True
    r = tools.rama(doc, "restaurar_pieza", desde=viejo, piezas=["torre"], simular=True)
    assert r.get("resultado") == "simulada" and r["pieza"] == "torre", r
    r = tools.rama(doc, "restaurar_pieza", desde=viejo, piezas=["torre"])
    assert r["resultado"] == "fusionada"


def test_restaurar_todo_el_documento_a_un_paso(doc):
    _grafo(doc)
    viejo = git_store.tip_rama(doc, "main")
    bb = {p: _bbox(doc, p) for p in ("brazo", "torre")}
    _param(doc, xa=30, xb=-30)
    assert client.post(f"/documentos/{doc}/pasos/{viejo[:10]}/restaurar").status_code == 401
    assert client.post(f"/documentos/{doc}/pasos/main/restaurar", headers=_h()).status_code == 400
    r = client.post(f"/documentos/{doc}/pasos/{viejo[:10]}/restaurar", headers=_h())
    assert r.status_code == 200, r.text
    assert r.json()["resultado"] == "restaurado"
    assert {p: _bbox(doc, p) for p in ("brazo", "torre")} == pytest.approx(bb)
    tip = git_store.tip_rama(doc, "main")
    assert git_store.arbol_de(doc, tip) == git_store.arbol_de(doc, viejo)
    assert git_store.contar(doc, tip) == 3  # nothing rewritten
    again = client.post(f"/documentos/{doc}/pasos/{viejo[:10]}/restaurar", headers=_h())
    assert again.json()["resultado"] == "sin_cambios"
