"""Phase 21 tests: live measures (`app/medidas.py`, `GET
/documentos/{id}/medidas`).

Same in-process `TestClient` + isolated data dir pattern as `tests/test_percibir.py`.
"""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

import auth
import documents
import medidas
from main import app

client = TestClient(app)

_A = "align=(Align.CENTER, Align.CENTER, Align.MIN)"

# base 40x40x10; caja_a y caja_b apoyadas en la base con 3 mm entre ellas.
_SCRIPT_DOS_CAJAS_3MM = (
    "from build123d import Box, Pos, Align\n"
    f"A = {_A}\n"
    "resultado = {\n"
    "    'base': Box(40, 40, 10, align=A),\n"
    "    'caja_a': Pos(-6.5, 0, 10) * Box(10, 10, 10, align=A),\n"
    "    'caja_b': Pos(6.5, 0, 10) * Box(10, 10, 10, align=A),\n"
    "}\n"
)

# x e y se solapan 8 mm en X: volumen de interseccion 800 mm3 (un choque).
_SCRIPT_CHOQUE = (
    "from build123d import Box, Pos, Align\n"
    f"A = {_A}\n"
    "resultado = {\n"
    "    'base': Box(40, 40, 10, align=A),\n"
    "    'x': Pos(0, 0, 10) * Box(10, 10, 10, align=A),\n"
    "    'y': Pos(2, 0, 10) * Box(10, 10, 10, align=A),\n"
    "}\n"
)

_SCRIPT_CON_PARAMETRO = (
    "from build123d import Box\n"
    "PARAMETROS = {'lado': {'valor': 10, 'min': 1, 'max': 50}}\n"
    "def construir(p):\n"
    "    return {'cubo': Box(p['lado'], 10, 10)}\n"
)


def _headers() -> dict[str, str]:
    return {"X-Forja-Token": auth.obtener_token()}


def _crear(script: str) -> str:
    resp = client.post("/documentos/script", json={"codigo": script}, headers=_headers())
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _medidas(doc_id: str, **params) -> dict:
    resp = client.get(f"/documentos/{doc_id}/medidas", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _par(datos: dict, a: str, b: str) -> dict:
    claves = {a, b}
    for fila in datos["pares"]:
        if {fila["a"], fila["b"]} == claves:
            return fila
    raise AssertionError(f"sin par {a}-{b} en {datos['pares']}")


def test_pares_contacto_holgura_y_orden_por_distancia():
    doc_id = _crear(_SCRIPT_DOS_CAJAS_3MM)
    datos = _medidas(doc_id)
    assert datos["solidos"] == 3
    assert datos["total_pares"] == 3
    assert datos["ocultos"] == 0
    assert datos["completo"] is True
    assert _par(datos, "base", "caja_a")["estado"] == "contacto"
    assert _par(datos, "base", "caja_a")["distancia_mm"] == 0.0
    holgura = _par(datos, "caja_a", "caja_b")
    assert holgura["estado"] == "holgura"
    assert holgura["distancia_mm"] == 3.0
    distancias = [fila["distancia_mm"] for fila in datos["pares"]]
    assert distancias == sorted(distancias)


def test_distancia_coincide_con_percibir_y_choque_con_colisiones():
    doc_id = _crear(_SCRIPT_CHOQUE)
    datos = _medidas(doc_id)
    choque = _par(datos, "x", "y")
    assert choque["estado"] == "choque"
    assert datos["choques"] == 1

    # Paridad: la misma pareja de `colisiones` (volumen) y el mismo número de
    # distancia que `percibir` imprime para la holgura de 3 mm.
    colisiones = client.get(f"/documentos/{doc_id}/colisiones").json()
    vol_colisiones = next(c["vol"] for c in colisiones["choques"] if {c["a"], c["b"]} == {"x", "y"})
    assert choque["volumen_mm3"] == pytest.approx(vol_colisiones, abs=0.1)

    doc_3mm = _crear(_SCRIPT_DOS_CAJAS_3MM)
    texto = client.get(f"/documentos/{doc_3mm}/percibir").json()["texto"]
    assert "caja_a <-> caja_b  3.0 mm" in texto
    assert _par(_medidas(doc_3mm), "caja_a", "caja_b")["distancia_mm"] == 3.0


def test_cache_por_revision_no_recalcula_en_la_segunda_peticion():
    doc_id = _crear(_SCRIPT_DOS_CAJAS_3MM)
    antes = medidas.computos()
    primera = client.get(f"/documentos/{doc_id}/medidas")
    segunda = client.get(f"/documentos/{doc_id}/medidas")
    assert primera.status_code == segunda.status_code == 200
    assert medidas.computos() - antes == 1
    assert primera.headers["X-Forja-Cache"] == "miss"
    assert segunda.headers["X-Forja-Cache"] == "hit"
    assert primera.headers["X-Forja-Revision"] == segunda.headers["X-Forja-Revision"]
    assert primera.json()["revision"] == primera.headers["X-Forja-Revision"]


def test_presupuesto_agotado_marca_completo_false(monkeypatch):
    monkeypatch.setattr(medidas, "TOPE_CALCULOS", 1)
    datos = _medidas(_crear(_SCRIPT_DOS_CAJAS_3MM))
    assert datos["completo"] is False
    assert datos["calculos"] == 1
    assert datos["tope_calculos"] == 1


def test_top_recorta_y_cuenta_los_ocultos():
    datos = _medidas(_crear(_SCRIPT_DOS_CAJAS_3MM), top=1)
    assert len(datos["pares"]) == 1
    assert datos["total_pares"] == 3
    assert datos["ocultos"] == 2


@pytest.mark.parametrize("top", [0, 101, -1])
def test_top_fuera_de_rango_422(top):
    resp = client.get(f"/documentos/{_crear(_SCRIPT_DOS_CAJAS_3MM)}/medidas", params={"top": top})
    assert resp.status_code == 422


def test_parametros_se_muestran_en_solo_lectura():
    datos = _medidas(_crear(_SCRIPT_CON_PARAMETRO))
    assert datos["parametros"] == {"lado": 10}


def test_documento_stl_400():
    from kernel import b123d_kernel, mesh

    tri_mesh = mesh.tessellate_to_trimesh(b123d_kernel.make_box(6, 6, 6), tolerance=0.05)
    resp = client.post(
        "/documentos",
        files={"file": ("cubo.stl", io.BytesIO(mesh.to_stl_bytes(tri_mesh)), "application/sla")},
    )
    resp = client.get(f"/documentos/{resp.json()['id']}/medidas")
    assert resp.status_code == 400
    assert "no malla STL" in resp.json()["detail"]


def test_id_desconocido_o_demasiado_largo_404():
    assert client.get("/documentos/no-existe/medidas").status_code == 404
    assert client.get("/documentos/" + "x" * 300 + "/medidas").status_code == 404


def test_obtener_recalcula_al_cambiar_la_revision(monkeypatch):
    llamadas = []

    def falsa(shape, entradas):
        llamadas.append(shape)
        return {"solidos": 0, "pares": [], "total_pares": 0, "ocultos": 0,
                "completo": True, "calculos": 0, "tope_calculos": 1, "choques": 0,
                "errores": 0, "nombres_inciertos": False}

    monkeypatch.setattr(medidas, "_calcular_forma", falsa)
    doc = "doc-revision-test"
    cargar = lambda: ("forma", [])  # noqa: E731 - tiny test double

    _, hit = medidas.obtener(doc, "r1", cargar, lambda: "r1")
    assert hit is False
    _, hit = medidas.obtener(doc, "r1", cargar, lambda: "r1")
    assert hit is True
    _, hit = medidas.obtener(doc, "r2", cargar, lambda: "r2")
    assert hit is False
    assert len(llamadas) == 2
    medidas.olvidar(doc)


def test_revision_que_cambia_durante_el_calculo_es_error(monkeypatch):
    monkeypatch.setattr(medidas, "_calcular_forma", lambda shape, entradas: {"pares": []})
    doc = "doc-carrera-test"
    with pytest.raises(medidas.RevisionCambiada):
        medidas.obtener(doc, "r1", lambda: ("forma", []), lambda: "r2")
    assert doc not in medidas._cache
    medidas.olvidar(doc)


def test_borrar_documento_olvida_la_cache():
    doc_id = _crear(_SCRIPT_DOS_CAJAS_3MM)
    _medidas(doc_id)
    assert doc_id in medidas._cache
    resp = client.delete(f"/documentos/{doc_id}", headers=_headers())
    assert resp.status_code == 200, resp.text
    assert doc_id not in medidas._cache
    assert documents._revisiones.get(doc_id) is None
