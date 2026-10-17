"""Cotas vivas (F23a, ADR-0023): dimensions of the drawing that trace to a
PARAMETROS entry of a script document are edited through the same path as
`parametros`. Unit tests over `app/cotas.py`, HTTP over `/documentos/{id}/cotas`
(real sandbox: script documents run in forja-sandbox), and the MCP client
branch `parametros(cotas=...)` run in-process."""
from __future__ import annotations

import io
import threading

import pytest
import trimesh
from fastapi.testclient import TestClient

import auth
import cotas
from main import app
from mcp_server import client as mcp_client
from mcp_server import tools

client = TestClient(app)

_SCRIPT = (
    "from build123d import Box, Cylinder, Pos\n"
    'PARAMETROS = {"ancho": {"valor": 40, "min": 10, "max": 80, "unidad": "mm"}, "alto": 20, "prof": 30}\n'
    'resultado = Box(PARAMS["ancho"], PARAMS["prof"], PARAMS["alto"]) - Pos(5, 0, 0) * Cylinder(4, 40)\n'
)
# `prof` is declared and linked by value (30 mm) but the script ignores it.
_SCRIPT_SIN_USO = (
    "from build123d import Box, Cylinder, Pos\n"
    'PARAMETROS = {"ancho": 40, "alto": 20, "prof": 30}\n'
    'resultado = Box(PARAMS["ancho"], 30, PARAMS["alto"]) - Pos(5, 0, 0) * Cylinder(4, 40)\n'
)


def _h() -> dict[str, str]:
    return {"X-Forja-Token": auth.obtener_token()}


def _crear(codigo: str = _SCRIPT) -> dict:
    resp = client.post("/documentos/script", json={"codigo": codigo}, headers=_h())
    assert resp.status_code == 200, resp.text
    return resp.json()


def _listado(doc_id: str) -> dict:
    resp = client.get(f"/documentos/{doc_id}/cotas")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _cota(listado: dict, tipo: str, eje: str) -> dict:
    return next(c for c in listado["cotas"] if c["tipo"] == tipo and c["eje"] == eje)


def _aplicar(doc_id: str, revision: str | None, cambios: dict, **extra):
    cuerpo = {"revision": revision, "cotas": cambios, **extra}
    return client.post(f"/documentos/{doc_id}/cotas/aplicar", json=cuerpo, headers=_h())


def _historial(doc_id: str) -> list[dict]:
    return client.get(f"/documentos/{doc_id}/historial").json()


# ---------------------------------------------------------------- pure logic


def _descriptor(**kw):
    base = {"id": "x", "tipo": "tamano_total", "valor_mm": 40.0, "eje": "x", "vista": "frontal",
            "origen": "parametro", "parametro": "ancho", "operacion": None}
    base.update(kw)
    return base


_ESQUEMA = {"ancho": {"valor": 40, "min": 10, "max": 80, "unidad": "mm"}, "alto": {"valor": 20, "unidad": "mm"}}


def test_clasificar_liga_solo_las_claves_del_esquema():
    listado = cotas.clasificar(
        [_descriptor(id="a"), _descriptor(id="b", parametro="ajeno"), _descriptor(id="c", origen="reconocido", parametro=None)],
        _ESQUEMA,
    )
    editable, ajena, reconocida = listado
    assert editable["operacion"] == "parametro" and "solo_lectura" not in editable
    assert editable["min"] == 10 and editable["max"] == 80
    for cota in (ajena, reconocida):
        assert cota["solo_lectura"] is True and cota["operacion"] is None
        assert cota["razon"] == cotas.RAZON_SIN_PARAMETRO


def test_clasificar_malla_es_solo_lectura_aunque_tenga_clave():
    listado = cotas.clasificar([_descriptor()], _ESQUEMA, malla=True)
    assert listado[0]["solo_lectura"] is True and listado[0]["razon"] == cotas.RAZON_MALLA


def test_resolver_edicion_devuelve_claves():
    listado = cotas.clasificar([_descriptor(id="a")], _ESQUEMA)
    assert cotas.resolver_edicion(listado, {"a": 50}) == {"ancho": 50.0}


@pytest.mark.parametrize("valor", [0, -5, float("nan"), float("inf"), "50", True, None])
def test_resolver_edicion_rechaza_valores_no_finitos_o_no_positivos(valor):
    listado = cotas.clasificar([_descriptor(id="a")], _ESQUEMA)
    with pytest.raises(cotas.CotaInvalida):
        cotas.resolver_edicion(listado, {"a": valor})


def test_resolver_edicion_id_desconocido_y_solo_lectura():
    listado = cotas.clasificar([_descriptor(id="a"), _descriptor(id="b", parametro=None, origen="reconocido")], _ESQUEMA)
    with pytest.raises(cotas.CotaNoEncontrada):
        cotas.resolver_edicion(listado, {"zz": 5})
    with pytest.raises(cotas.CotaInvalida, match="no ligada"):
        cotas.resolver_edicion(listado, {"b": 5})


def test_resolver_edicion_tope_sin_max_y_dos_cotas_en_una_clave():
    sin_max = cotas.clasificar([_descriptor(id="a", valor_mm=40.0, parametro="alto")], _ESQUEMA)
    with pytest.raises(cotas.CotaInvalida, match="veces"):
        cotas.resolver_edicion(sin_max, {"a": 401})
    dos = cotas.clasificar(
        [_descriptor(id="a"), _descriptor(id="b", eje="y")], _ESQUEMA
    )
    with pytest.raises(cotas.CotaInvalida, match="distintos"):
        cotas.resolver_edicion(dos, {"a": 50, "b": 60})
    assert cotas.resolver_edicion(dos, {"a": 50, "b": 50}) == {"ancho": 50.0}


def test_tras_aplicar_marca_parametro_que_no_mueve_la_cota():
    antes = cotas.clasificar([_descriptor(id="a", valor_mm=30.0, parametro="prof")], {"prof": {"valor": 30}})
    despues = [_descriptor(id="b", valor_mm=30.0, parametro=None, origen="reconocido")]
    informe = cotas.tras_aplicar(antes, despues, {"a": 35})
    assert informe[0]["no_sigue_al_parametro"] is True
    assert informe[0]["valor_despues"] is None and informe[0]["valor_antes"] == 30.0
    ok = cotas.tras_aplicar(antes, [_descriptor(id="c", valor_mm=35.0, parametro="prof")], {"a": 35})
    assert "no_sigue_al_parametro" not in ok[0] and ok[0]["error_mm"] == 0.0


# ---------------------------------------------------------------- listing


def test_listado_marca_editables_y_solo_lectura():
    creado = _crear()
    listado = _listado(creado["id"])
    assert listado["revision"] == creado["revision"]
    ancho = _cota(listado, "tamano_total", "x")
    assert ancho["operacion"] == "parametro" and ancho["parametro"] == "ancho"
    assert ancho["min"] == 10 and ancho["max"] == 80
    assert _cota(listado, "tamano_total", "y")["parametro"] == "prof"
    agujero = next(c for c in listado["cotas"] if c["tipo"] == "diametro")
    assert agujero["solo_lectura"] is True and agujero["razon"] == cotas.RAZON_SIN_PARAMETRO


def test_documento_sin_script_es_todo_solo_lectura():
    from build123d import Box

    from kernel import b123d_kernel

    import tempfile
    from pathlib import Path

    ruta = Path(tempfile.mkdtemp(prefix="cotas-")) / "caja.step"
    b123d_kernel.export_to_step(Box(20, 20, 10), ruta)
    creado = client.post("/documentos", files={"file": ("caja.step", ruta.read_bytes(), "application/step")}, headers=_h()).json()
    listado = _listado(creado["id"])
    assert listado["cotas"] and all(c["solo_lectura"] for c in listado["cotas"])
    resp = _aplicar(creado["id"], listado["revision"], {listado["cotas"][0]["id"]: 30})
    assert resp.status_code == 422 and cotas.RAZON_SIN_PARAMETRO in resp.json()["detail"]


def test_stl_es_malla_solo_lectura():
    caja = trimesh.creation.box(extents=(40, 30, 20))
    data = caja.export(file_type="stl")
    creado = client.post("/documentos", files={"file": ("malla.stl", io.BytesIO(data), "application/sla")}, headers=_h()).json()
    listado = _listado(creado["id"])
    assert len(listado["cotas"]) == 3
    assert all(c["razon"] == cotas.RAZON_MALLA for c in listado["cotas"])


def test_exportar_detalle_trae_operacion_y_revision():
    creado = _crear()
    cuerpo = client.get(f"/documentos/{creado['id']}/exportar", params={"formato": "svg", "detalle": "true"}, headers=_h()).json()
    assert cuerpo["revision"] == creado["revision"]
    ancho = next(c for c in cuerpo["cotas_detalle"] if c["tipo"] == "tamano_total" and c["eje"] == "x")
    assert ancho["operacion"] == "parametro"
    agujero = next(c for c in cuerpo["cotas_detalle"] if c["tipo"] == "diametro")
    assert agujero["solo_lectura"] is True


# ---------------------------------------------------------------- apply (HTTP)


def test_editar_cota_equivale_a_parametros_y_crea_una_revision():
    doc_a, doc_b = _crear(), _crear()
    listado = _listado(doc_a["id"])
    ancho = _cota(listado, "tamano_total", "x")
    n_antes = len(_historial(doc_a["id"]))

    resp = _aplicar(doc_a["id"], listado["revision"], {ancho["id"]: 50})
    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    editada = cuerpo["editadas"][0]
    assert editada["valor_antes"] == 40.0 and editada["valor_despues"] == 50.0
    assert editada["error_mm"] == 0.0 and "no_sigue_al_parametro" not in editada
    assert cuerpo["valores"]["ancho"] == 50
    assert cuerpo["revision"] != listado["revision"]
    assert len(_historial(doc_a["id"])) == n_antes + 1

    par = client.post(f"/documentos/{doc_b['id']}/parametros", json={"valores": {"ancho": 50}}, headers=_h())
    assert par.status_code == 200, par.text
    assert cuerpo["bbox"] == pytest.approx(par.json()["bbox"], abs=1e-6)
    assert cuerpo["volumen"] == pytest.approx(par.json()["volumen"], rel=1e-9)
    despues = _cota(_listado(doc_a["id"]), "tamano_total", "x")
    assert despues["valor_mm"] == 50.0 and despues["parametro"] == "ancho"


def test_cota_solo_lectura_422_y_nada_escrito():
    creado = _crear()
    listado = _listado(creado["id"])
    agujero = next(c for c in listado["cotas"] if c["tipo"] == "diametro")
    n_antes = len(_historial(creado["id"]))
    resp = _aplicar(creado["id"], listado["revision"], {agujero["id"]: 6})
    assert resp.status_code == 422
    assert cotas.RAZON_SIN_PARAMETRO in resp.json()["detail"]
    assert len(_historial(creado["id"])) == n_antes
    assert _listado(creado["id"])["revision"] == listado["revision"]


def test_fuera_de_rango_422_y_nada_escrito():
    creado = _crear()
    listado = _listado(creado["id"])
    ancho = _cota(listado, "tamano_total", "x")
    n_antes = len(_historial(creado["id"]))
    resp = _aplicar(creado["id"], listado["revision"], {ancho["id"]: 90})
    assert resp.status_code == 422 and "maximo" in resp.json()["detail"]
    assert len(_historial(creado["id"])) == n_antes
    assert _listado(creado["id"])["revision"] == listado["revision"]


@pytest.mark.parametrize("literal", ["0", "-3", "NaN", "Infinity"])
def test_valor_no_finito_o_no_positivo_422(literal):
    """Raw JSON literal: the test client's encoder refuses NaN/Infinity, the
    server must still answer 422 (Python's json accepts them)."""
    creado = _crear()
    listado = _listado(creado["id"])
    ancho = _cota(listado, "tamano_total", "x")
    cuerpo = '{"revision": "%s", "cotas": {"%s": %s}}' % (listado["revision"], ancho["id"], literal)
    resp = client.post(
        f"/documentos/{creado['id']}/cotas/aplicar", content=cuerpo,
        headers={**_h(), "Content-Type": "application/json"},
    )
    assert resp.status_code == 422, resp.text


def test_revision_obsoleta_409_sin_cambios():
    creado = _crear()
    listado = _listado(creado["id"])
    ancho = _cota(listado, "tamano_total", "x")
    assert _aplicar(creado["id"], "revision-vieja", {ancho["id"]: 50}).status_code == 409
    assert _aplicar(creado["id"], listado["revision"], {ancho["id"]: 50}).status_code == 200
    # the same read is now stale: the second edit must not apply on top of it
    n_antes = len(_historial(creado["id"]))
    assert _aplicar(creado["id"], listado["revision"], {ancho["id"]: 60}).status_code == 409
    assert len(_historial(creado["id"])) == n_antes


def test_id_desconocido_404_y_campos_extra_422():
    creado = _crear()
    listado = _listado(creado["id"])
    assert _aplicar(creado["id"], listado["revision"], {"tamano_total:x:999.000": 5}).status_code == 404
    resp = client.post(
        f"/documentos/{creado['id']}/cotas/aplicar",
        json={"revision": listado["revision"], "cotas": {}, "extra": 1}, headers=_h(),
    )
    assert resp.status_code == 422


def test_restaurar_snapshot_previo_vuelve_a_40():
    creado = _crear()
    listado = _listado(creado["id"])
    ancho = _cota(listado, "tamano_total", "x")
    assert _aplicar(creado["id"], listado["revision"], {ancho["id"]: 50}).status_code == 200
    previo = next(h for h in _historial(creado["id"]) if h["mensaje"].startswith("antes de parametros"))
    resp = client.post(f"/documentos/{creado['id']}/restaurar", json={"snapshot": previo["id"]}, headers=_h())
    assert resp.status_code == 200, resp.text
    assert _cota(_listado(creado["id"]), "tamano_total", "x")["valor_mm"] == 40.0


def test_cota_que_no_sigue_al_parametro_se_informa():
    creado = _crear(_SCRIPT_SIN_USO)
    listado = _listado(creado["id"])
    prof = _cota(listado, "tamano_total", "y")
    assert prof["parametro"] == "prof"
    cuerpo = _aplicar(creado["id"], listado["revision"], {prof["id"]: 35}).json()
    assert cuerpo["editadas"][0]["no_sigue_al_parametro"] is True


def test_dos_ediciones_simultaneas_una_200_y_una_409():
    creado = _crear()
    listado = _listado(creado["id"])
    ancho = _cota(listado, "tamano_total", "x")
    resultados: list[int] = []

    def editar(valor: float) -> None:
        cliente = TestClient(app)
        cuerpo = {"revision": listado["revision"], "cotas": {ancho["id"]: valor}}
        resultados.append(cliente.post(f"/documentos/{creado['id']}/cotas/aplicar", json=cuerpo, headers=_h()).status_code)

    hilos = [threading.Thread(target=editar, args=(v,)) for v in (50.0, 60.0)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()
    assert sorted(resultados) == [200, 409]


# ---------------------------------------------------------------- MCP (in-process)


@pytest.fixture
def mcp_en_proceso(monkeypatch):
    """`mcp_server.client` talks to the live server over HTTP; here its httpx
    client is the in-process TestClient, so the branch under test runs for real."""
    fabrica_original = mcp_client.httpx.Client

    def fabrica(**kwargs):
        kwargs.pop("base_url", None)
        return TestClient(app, headers=kwargs.get("headers"))

    monkeypatch.setattr(mcp_client.httpx, "Client", fabrica)
    yield fabrica_original


def test_mcp_parametros_cotas_aplica_y_valida(mcp_en_proceso):
    creado = _crear()
    x_id = _cota(_listado(creado["id"]), "tamano_total", "x")["id"]
    ok = tools.parametros(creado["id"], cotas={x_id: 50})
    assert "error" not in ok, ok
    assert ok["valores"]["ancho"] == 50 and ok["editadas"][0]["valor_despues"] == 50.0
    mezcla = tools.parametros(creado["id"], valores={"ancho": 45}, cotas={x_id: 50})
    assert mezcla["error"] is True
    viejo = tools.parametros(creado["id"], cotas={x_id: 55}, revision="revision-vieja")
    assert viejo["error"] is True
