"""Fase 24a2 (ADR-0022): cursor de deshacer y rehacer calculado desde el
historial git. Sin estado mutable extra: `GET /deshacer` deriva todo de los
commits, y `POST /deshacer|/rehacer` exigen la cabeza que el cliente vio."""
from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

import auth
import deshacer
import versioning
from main import app

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
    r = client.post("/documentos/script", json={"codigo": SCRIPT, "nombre": "deshacer_test"}, headers=_h())
    assert r.status_code == 200, r.text
    doc_id = r.json()["id"]
    yield doc_id
    client.delete(f"/documentos/{doc_id}", headers=_h())


def _editar(doc_id, xa):
    r = client.post(f"/documentos/{doc_id}/parametros", json={"valores": {"xa": xa}, "confirmar_script": True},
                    headers=_h())
    assert r.status_code == 200, r.text


def _revision(doc_id):
    return client.get(f"/documentos/{doc_id}").json()["revision"]


def _estado(doc_id):
    r = client.get(f"/documentos/{doc_id}/deshacer")
    assert r.status_code == 200, r.text
    return r.json()


def _accion(doc_id, accion, esperado):
    return client.post(f"/documentos/{doc_id}/{accion}", json={"esperado": esperado}, headers=_h())


def _commits(doc_id):
    return versioning.listar_commits(doc_id)


# ------------------------------------------------------------ cursor puro

def _c(id_, accion=None, mensaje="edicion"):
    return {"id": id_, "accion": accion, "mensaje": mensaje}


def test_cursor_creacion_es_la_base_no_un_paso_deshacible():
    entradas = [_c("c0", mensaje="documento creado (script)")]
    estado = deshacer.estado(entradas, "sha0")
    assert estado == {"cabeza": "sha0", "puede_deshacer": False, "puede_rehacer": False,
                      "destino_deshacer": None, "destino_rehacer": None}


def test_cursor_deshacer_salta_lo_que_ya_se_revirtio():
    # e1, e2; deshacer (D1 revierte e2); deshacer (D2 revierte e1)
    entradas = [_c("c0", mensaje="documento creado (script)"), _c("e1"), _c("e2"),
                _c("d1", "deshacer"), _c("d2", "deshacer")]
    estado = deshacer.estado(entradas, "h")
    assert estado["puede_deshacer"] is False
    assert estado["puede_rehacer"] is True
    assert estado["destino_rehacer"] == "d2"


def test_cursor_undo_undo_redo_recorre_la_pila():
    base = [_c("c0", mensaje="documento creado (script)"), _c("e1"), _c("e2")]
    tras_u1 = base + [_c("d1", "deshacer")]
    assert deshacer.estado(tras_u1, "h")["destino_deshacer"] == "e1"
    tras_u2 = tras_u1 + [_c("d2", "deshacer")]
    estado = deshacer.estado(tras_u2, "h")
    assert (estado["puede_deshacer"], estado["destino_rehacer"]) == (False, "d2")
    tras_r1 = tras_u2 + [_c("r1", "rehacer")]
    estado = deshacer.estado(tras_r1, "h")
    assert estado["destino_deshacer"] == "r1"
    assert estado["destino_rehacer"] == "d1"


def test_cursor_edicion_tras_deshacer_borra_el_rehacer():
    entradas = [_c("e1"), _c("e2"), _c("d1", "deshacer"), _c("e3")]
    estado = deshacer.estado(entradas, "h")
    assert estado["puede_rehacer"] is False
    assert estado["destino_deshacer"] == "e3"


def test_cursor_restaurar_limpia_rehacer_y_no_es_rehacible():
    entradas = [_c("e1"), _c("e2"), _c("d1", "deshacer"), _c("x1", "restaurar")]
    estado = deshacer.estado(entradas, "h")
    assert estado["puede_rehacer"] is False
    assert estado["destino_deshacer"] == "x1"


# ------------------------------------------------------------ API

def test_estado_recien_creado_no_puede_deshacer(doc):
    estado = _estado(doc)
    assert estado["puede_deshacer"] is False
    assert estado["puede_rehacer"] is False
    assert estado["cabeza"] == _commits(doc)[-1]["sha"]


def test_deshacer_deshacer_rehacer(doc):
    rev0 = _revision(doc)
    _editar(doc, 10)
    rev1 = _revision(doc)
    _editar(doc, 12)
    rev2 = _revision(doc)
    assert len({rev0, rev1, rev2}) == 3

    estado = _estado(doc)
    assert estado["puede_deshacer"] and not estado["puede_rehacer"]

    r = _accion(doc, "deshacer", estado["cabeza"])
    assert r.status_code == 200, r.text
    assert _revision(doc) == rev1
    estado = _estado(doc)
    assert estado["puede_rehacer"] is True

    r = _accion(doc, "deshacer", estado["cabeza"])
    assert r.status_code == 200, r.text
    assert _revision(doc) == rev0
    estado = _estado(doc)
    assert estado["puede_deshacer"] is False and estado["puede_rehacer"] is True

    r = _accion(doc, "rehacer", estado["cabeza"])
    assert r.status_code == 200, r.text
    assert _revision(doc) == rev1
    assert r.json()["accion"] == "rehacer"


def test_historia_no_se_reescribe_y_cada_paso_es_un_commit(doc):
    _editar(doc, 10)
    antes = [c["sha"] for c in _commits(doc)]
    cabeza = _estado(doc)["cabeza"]
    assert _accion(doc, "deshacer", cabeza).status_code == 200
    despues = [c["sha"] for c in _commits(doc)]
    assert despues[:len(antes)] == antes
    assert len(despues) == len(antes) + 1


def test_commit_de_deshacer_lleva_trailers(doc):
    _editar(doc, 10)
    destino = _estado(doc)["destino_deshacer"]
    assert _accion(doc, "deshacer", _estado(doc)["cabeza"]).status_code == 200
    ultimo = _commits(doc)[-1]
    assert ultimo["accion"] == "deshacer"
    assert ultimo["destino"] == destino


def test_edicion_tras_deshacer_borra_el_rehacer(doc):
    _editar(doc, 10)
    assert _accion(doc, "deshacer", _estado(doc)["cabeza"]).status_code == 200
    _editar(doc, 12)
    estado = _estado(doc)
    assert estado["puede_rehacer"] is False
    r = _accion(doc, "rehacer", estado["cabeza"])
    assert r.status_code == 409, r.text


def test_esperado_obsoleto_da_409_y_no_escribe(doc):
    _editar(doc, 10)
    vista = _estado(doc)["cabeza"]
    _editar(doc, 12)
    antes = len(_commits(doc))
    r = _accion(doc, "deshacer", vista)
    assert r.status_code == 409
    assert "historial" in r.json()["detail"]
    assert len(_commits(doc)) == antes


def test_restaurar_no_se_puede_rehacer_pero_si_deshacer(doc):
    _editar(doc, 10)
    rev_antes_restaurar = _revision(doc)
    primero = _commits(doc)[0]["id"]
    r = client.post(f"/documentos/{doc}/restaurar", json={"snapshot": primero}, headers=_h())
    assert r.status_code == 200, r.text
    assert _revision(doc) != rev_antes_restaurar
    estado = _estado(doc)
    assert estado["puede_rehacer"] is False
    assert _accion(doc, "rehacer", estado["cabeza"]).status_code == 409
    assert _accion(doc, "deshacer", estado["cabeza"]).status_code == 200
    assert _revision(doc) == rev_antes_restaurar


def test_sin_nada_que_deshacer_o_rehacer_da_409(doc):
    r = _accion(doc, "deshacer", _estado(doc)["cabeza"])
    assert r.status_code == 409
    assert "No hay" in r.json()["detail"]
    r = _accion(doc, "rehacer", _estado(doc)["cabeza"])
    assert r.status_code == 409


def test_cuerpo_con_campos_extra_o_sin_esperado_da_422(doc):
    cabeza = _estado(doc)["cabeza"]
    r = client.post(f"/documentos/{doc}/deshacer", json={"esperado": cabeza, "otro": 1}, headers=_h())
    assert r.status_code == 422
    r = client.post(f"/documentos/{doc}/rehacer", json={}, headers=_h())
    assert r.status_code == 422


def test_deshacer_y_rehacer_exigen_token(doc):
    _editar(doc, 10)
    cabeza = _estado(doc)["cabeza"]
    assert client.post(f"/documentos/{doc}/deshacer", json={"esperado": cabeza}).status_code == 401
    assert client.post(f"/documentos/{doc}/rehacer", json={"esperado": cabeza}).status_code == 401


def test_documento_inexistente_da_404():
    assert client.get("/documentos/no-existe/deshacer").status_code == 404
    assert client.post("/documentos/no-existe/deshacer", json={"esperado": "x"}, headers=_h()).status_code == 404


def test_peticiones_concurrentes_con_la_misma_cabeza_solo_una_gana(doc):
    _editar(doc, 10)
    cabeza = _estado(doc)["cabeza"]
    antes = len(_commits(doc))
    resultados = []
    barrera = threading.Barrier(2)

    def lanzar():
        cliente = TestClient(app)
        barrera.wait()
        resultados.append(cliente.post(f"/documentos/{doc}/deshacer", json={"esperado": cabeza},
                                       headers=_h()).status_code)

    hilos = [threading.Thread(target=lanzar) for _ in range(2)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()
    assert sorted(resultados) == [200, 409]
    assert len(_commits(doc)) == antes + 1
