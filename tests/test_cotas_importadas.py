"""Cotas vivas de documentos importados (F23b, ADR-0025): una cota que nace de
una caracteristica reconocida (cilindro -> rellenar y retaladrar, caras planas
paralelas -> estirar por biseccion, medida total -> escalar el eje) se edita
sobre el B-rep o la malla. Cada edicion aceptada crea una revision; cualquier
fallo deja el documento, su historial y sus sidecars byte a byte iguales."""
from __future__ import annotations

import io
import math
import tempfile
import threading
from pathlib import Path

import pytest
import trimesh
from build123d import Box, Cylinder, Pos
from fastapi.testclient import TestClient

import auth
import cotas
import cotas_geometria
import documents
import git_store
import solids
from kernel import b123d_kernel
from main import app
from mcp_server import client as mcp_client
from mcp_server import tools

client = TestClient(app)


def _h() -> dict[str, str]:
    return {"X-Forja-Token": auth.obtener_token()}


def _step(figura) -> bytes:
    ruta = Path(tempfile.mkdtemp(prefix="cotas-b-")) / "pieza.step"
    b123d_kernel.export_to_step(figura, ruta)
    return ruta.read_bytes()


def _plancha(ancho=40, prof=30, alto=10, agujeros=(), ciegos=()):
    """Plate centred on the origin. `agujeros`: (x, y, d) through in Z.
    `ciegos`: (x, y, d, profundidad) drilled from the top face."""
    pieza = Box(ancho, prof, alto)
    for x, y, d in agujeros:
        pieza = pieza - Pos(x, y, 0) * Cylinder(d / 2, alto + 4)
    for x, y, d, h in ciegos:
        pieza = pieza - Pos(x, y, alto / 2 - h / 2 + 1) * Cylinder(d / 2, h + 2)
    return pieza


def _importar(contenido: bytes, nombre: str = "pieza.step") -> str:
    resp = client.post("/documentos", files={"file": (nombre, io.BytesIO(contenido), "application/step")}, headers=_h())
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _listado(doc_id: str) -> dict:
    resp = client.get(f"/documentos/{doc_id}/cotas")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _cota(listado: dict, tipo: str, eje: str | None = None, valor: float | None = None) -> dict:
    hallada = [
        c for c in listado["cotas"]
        if c["tipo"] == tipo and (eje is None or c["eje"] == eje)
        and (valor is None or abs(c["valor_mm"] - valor) <= 0.0005)
    ]
    assert len(hallada) == 1, (tipo, eje, valor, [(c["tipo"], c["eje"], c["valor_mm"]) for c in listado["cotas"]])
    return hallada[0]


def _aplicar(doc_id: str, revision, cambios: dict, **extra):
    return client.post(f"/documentos/{doc_id}/cotas/aplicar", json={"revision": revision, "cotas": cambios, **extra}, headers=_h())


def _volumen(doc_id: str) -> float:
    return documents._registry[doc_id]["volumen"]


def _bbox(doc_id: str) -> list[float]:
    return documents._registry[doc_id]["bbox"]


def _estado_en_disco(doc_id: str) -> dict[str, bytes]:
    """Bytes of the document's files and of its git repository: a refused edit leaves all of it identical."""
    estado = {
        f"doc:{ruta.name}": ruta.read_bytes()
        for ruta in sorted(documents.DOCUMENTOS_DIR.glob(f"{doc_id}*")) if ruta.is_file()
    }
    repo = git_store.ruta_repo(doc_id)
    estado.update({f"git:{ruta.relative_to(repo)}": ruta.read_bytes() for ruta in sorted(repo.rglob("*")) if ruta.is_file()})
    return estado


def _editar_ok(doc_id: str, cota_tipo: str, eje: str | None, valor_pedido: float, valor_actual: float | None = None):
    listado = _listado(doc_id)
    cota = _cota(listado, cota_tipo, eje, valor_actual)
    previa = listado["revision"]
    resp = _aplicar(doc_id, previa, {cota["id"]: valor_pedido})
    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["revision"] != previa
    return cuerpo


# ---------------------------------------------------------------- listing


def test_listado_importado_asigna_la_operacion_de_cada_cota():
    doc = _importar(_step(_plancha(agujeros=[(10, 0, 8)])))
    listado = _listado(doc)
    assert _cota(listado, "tamano_total", "x")["operacion"] == "escalar_eje"
    assert _cota(listado, "tamano_total", "z")["operacion"] == "escalar_eje"
    assert _cota(listado, "diametro")["operacion"] == "rellenar_y_taladrar"
    distancia = _cota(listado, "distancia", "z")
    assert distancia["operacion"] == "mover_cara" and not distancia.get("solo_lectura")
    posicion = _cota(listado, "posicion", "x")
    assert posicion["solo_lectura"] is True and posicion["razon"] == cotas.REASON_POSITION


def test_mesh_stl_solo_escala_el_eje_y_el_resto_es_solo_lectura():
    datos = trimesh.creation.box(extents=(40, 30, 20)).export(file_type="stl")
    doc = client.post("/documentos", files={"file": ("malla.stl", io.BytesIO(datos), "application/sla")}, headers=_h()).json()["id"]
    listado = _listado(doc)
    assert len(listado["cotas"]) == 3
    assert all(c["operacion"] == "escalar_eje" for c in listado["cotas"])


def test_cota_sin_caracteristica_reconocida_se_marca_con_razon():
    # Un agujero oblicuo no es un cilindro de eje global: F22 lo omite, asi que no hay cota de diametro.
    oblicuo = Box(40, 30, 10) - Pos(0, 0, 0) * Cylinder(3, 40).rotate(_eje_y(), 30)
    doc = _importar(_step(oblicuo))
    listado = _listado(doc)
    assert not [c for c in listado["cotas"] if c["tipo"] == "diametro"]
    assert all(c.get("operacion") for c in listado["cotas"] if not c.get("solo_lectura"))


def _eje_y():
    from build123d import Axis

    return Axis.Y


# ---------------------------------------------------------------- cylinder: re-drill


def test_taladro_pasante_cambia_de_diametro_y_crea_una_revision():
    doc = _importar(_step(_plancha(agujeros=[(10, 0, 8)])))
    v0, bbox0 = _volumen(doc), _bbox(doc)
    historial0 = len(_historial(doc))
    cuerpo = _editar_ok(doc, "diametro", None, 10.0)
    esperado = -math.pi * (5.0 ** 2 - 4.0 ** 2) * 10
    assert abs((_volumen(doc) - v0) - esperado) <= 0.01 * abs(esperado)
    assert _bbox(doc) == pytest.approx(bbox0, abs=1e-3)
    medido = _cota(_listado(doc), "diametro", None, 10.0)
    assert medido["valor_mm"] == 10.0
    assert cuerpo["editadas"][0]["operacion"] == "rellenar_y_taladrar"
    assert abs(cuerpo["editadas"][0]["error_mm"]) <= 0.01
    assert len(_historial(doc)) == historial0 + 1


def test_taladro_ciego_conserva_la_profundidad():
    doc = _importar(_step(_plancha(ciegos=[(10, 0, 8, 5)])))
    v0 = _volumen(doc)
    _editar_ok(doc, "diametro", None, 10.0)
    esperado = -math.pi * (5.0 ** 2 - 4.0 ** 2) * 5
    assert abs((_volumen(doc) - v0) - esperado) <= 0.01 * abs(esperado)
    assert _bbox(doc)[5] == pytest.approx(5.0, abs=1e-3)  # top face unchanged
    assert _bbox(doc)[4] == pytest.approx(-5.0, abs=1e-3)  # bottom face unchanged


def test_grupo_de_agujeros_edita_todos_y_lo_dice():
    doc = _importar(_step(_plancha(agujeros=[(-10, 0, 5), (0, 0, 5), (10, 0, 5)])))
    v0 = _volumen(doc)
    cuerpo = _editar_ok(doc, "diametro", None, 6.0)
    assert cuerpo["editadas"][0]["afectadas"] == 3
    esperado = -3 * math.pi * (3.0 ** 2 - 2.5 ** 2) * 10
    assert abs((_volumen(doc) - v0) - esperado) <= 0.01 * abs(esperado)
    assert _cota(_listado(doc), "diametro", None, 6.0)["cantidad"] == 3


def test_pared_rota_422_y_nada_escrito():
    doc = _importar(_step(_plancha(agujeros=[(-15, 0, 8)])))  # 1 mm of wall to the -X edge
    listado = _listado(doc)
    antes = _estado_en_disco(doc)
    resp = _aplicar(doc, listado["revision"], {_cota(listado, "diametro")["id"]: 12.0})
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] is True and resp.json()["razon"] == "el agujero rompe la pared"
    assert _estado_en_disco(doc) == antes


# ---------------------------------------------------------------- parallel faces: stretch


def test_caras_paralelas_se_estiran_por_biseccion():
    doc = _importar(_step(_plancha()))
    v0 = _volumen(doc)
    cuerpo = _editar_ok(doc, "distancia", "z", 14.0)
    assert abs(cuerpo["editadas"][0]["error_mm"]) <= 0.01
    assert cuerpo["editadas"][0]["operacion"] == "mover_cara"
    assert _bbox(doc)[5] - _bbox(doc)[4] == pytest.approx(14.0, abs=0.01)
    assert _bbox(doc)[1] - _bbox(doc)[0] == pytest.approx(40.0, abs=1e-3)
    assert abs((_volumen(doc) - v0) - 1200 * 4) <= 0.01 * 1200 * 4
    assert _cota(_listado(doc), "distancia", "z", 14.0)


def test_caras_paralelas_se_comprimen():
    doc = _importar(_step(_plancha()))
    v0 = _volumen(doc)
    _editar_ok(doc, "distancia", "z", 7.0)
    assert _bbox(doc)[5] - _bbox(doc)[4] == pytest.approx(7.0, abs=0.01)
    assert abs((_volumen(doc) - v0) + 1200 * 3) <= 0.01 * 1200 * 3


# ---------------------------------------------------------------- bbox: axis scale


def test_medida_total_escala_solo_su_eje():
    doc = _importar(_step(_plancha()))
    v0 = _volumen(doc)
    _editar_ok(doc, "tamano_total", "x", 60.0)
    assert _bbox(doc)[1] - _bbox(doc)[0] == pytest.approx(60.0, abs=1e-3)
    assert _bbox(doc)[3] - _bbox(doc)[2] == pytest.approx(30.0, abs=1e-3)
    assert _bbox(doc)[5] - _bbox(doc)[4] == pytest.approx(10.0, abs=1e-3)
    assert abs(_volumen(doc) - 60 * 30 * 10) < 1.0 and abs(v0 - 40 * 30 * 10) < 1.0


def test_malla_stl_escala_el_eje_pedido():
    datos = trimesh.creation.box(extents=(40, 30, 20)).export(file_type="stl")
    doc = client.post("/documentos", files={"file": ("malla.stl", io.BytesIO(datos), "application/sla")}, headers=_h()).json()["id"]
    _editar_ok(doc, "tamano_total", "y", 45.0)
    assert _bbox(doc)[3] - _bbox(doc)[2] == pytest.approx(45.0, abs=1e-3)
    assert _bbox(doc)[1] - _bbox(doc)[0] == pytest.approx(40.0, abs=1e-3)


# ---------------------------------------------------------------- refusals and guards


def test_posicion_422_con_razon_y_nada_escrito():
    doc = _importar(_step(_plancha(agujeros=[(10, 0, 8)])))
    listado = _listado(doc)
    antes = _estado_en_disco(doc)
    resp = _aplicar(doc, listado["revision"], {_cota(listado, "posicion", "x")["id"]: 12.0})
    assert resp.status_code == 422 and cotas.REASON_POSITION in resp.json()["mensaje"]
    assert _estado_en_disco(doc) == antes


@pytest.mark.parametrize("valor", [0.0, -3.0, 1e308, 0.05, 500.0])
def test_fuera_de_limites_geometricos_422_y_nada_escrito(valor):
    # 1e308 and 500 break the 10x cap of the 40 mm plate; 0.05 breaks the 0.1x floor.
    doc = _importar(_step(_plancha()))
    listado = _listado(doc)
    antes = _estado_en_disco(doc)
    resp = _aplicar(doc, listado["revision"], {_cota(listado, "tamano_total", "x")["id"]: valor})
    assert resp.status_code == 422, resp.text
    assert _estado_en_disco(doc) == antes


def test_valor_igual_al_actual_422_sin_revision_nueva():
    doc = _importar(_step(_plancha()))
    listado = _listado(doc)
    antes = _estado_en_disco(doc)
    resp = _aplicar(doc, listado["revision"], {_cota(listado, "tamano_total", "x")["id"]: 40.0})
    assert resp.status_code == 422 and "no hay cambio" in resp.json()["mensaje"]
    assert _estado_en_disco(doc) == antes


def test_revision_obsoleta_409_importado():
    doc = _importar(_step(_plancha(agujeros=[(10, 0, 8)])))
    listado = _listado(doc)
    _editar_ok(doc, "diametro", None, 10.0)
    antes = _estado_en_disco(doc)
    resp = _aplicar(doc, listado["revision"], {_cota(listado, "diametro")["id"]: 9.0})
    assert resp.status_code == 409
    assert _estado_en_disco(doc) == antes


def test_dos_cotas_geometricas_en_una_peticion_422():
    doc = _importar(_step(_plancha()))
    listado = _listado(doc)
    antes = _estado_en_disco(doc)
    resp = _aplicar(doc, listado["revision"], {
        _cota(listado, "tamano_total", "x")["id"]: 50.0, _cota(listado, "tamano_total", "y")["id"]: 25.0,
    })
    assert resp.status_code == 422 and "una a una" in resp.json()["mensaje"]
    assert _estado_en_disco(doc) == antes


def test_verificacion_fallida_revierte_sin_revision(monkeypatch):
    doc = _importar(_step(_plancha(agujeros=[(10, 0, 8)])))
    listado = _listado(doc)
    antes = _estado_en_disco(doc)

    def rechazar(*args, **kwargs):
        raise cotas_geometria.EditRejected("la geometria resultante no es valida")

    monkeypatch.setattr(cotas_geometria, "_check_result", rechazar)
    resp = _aplicar(doc, listado["revision"], {_cota(listado, "diametro")["id"]: 10.0})
    assert resp.status_code == 422 and resp.json()["error"] is True
    assert _estado_en_disco(doc) == antes


def test_presupuesto_agotado_422_y_nada_escrito(monkeypatch):
    doc = _importar(_step(_plancha(agujeros=[(10, 0, 8)])))
    listado = _listado(doc)
    antes = _estado_en_disco(doc)
    monkeypatch.setattr(cotas_geometria, "BUDGET_OPERATIONS", 1)
    resp = _aplicar(doc, listado["revision"], {_cota(listado, "diametro")["id"]: 10.0})
    assert resp.status_code == 422 and "presupuesto" in resp.json()["mensaje"]
    assert _estado_en_disco(doc) == antes


def test_pieza_mayor_que_el_tope_de_caras_422(monkeypatch):
    doc = _importar(_step(_plancha(agujeros=[(10, 0, 8)])))
    listado = _listado(doc)
    antes = _estado_en_disco(doc)
    monkeypatch.setattr(cotas_geometria, "MAX_BREP_FACES", 3)
    resp = _aplicar(doc, listado["revision"], {_cota(listado, "tamano_total", "x")["id"]: 50.0})
    assert resp.status_code == 422 and "caras" in resp.json()["mensaje"]
    assert _estado_en_disco(doc) == antes


def test_nombres_de_solidos_sobreviven_a_la_edicion():
    doc = _importar(_step(_plancha(agujeros=[(10, 0, 8)])))
    nombres = [e["nombre"] for e in solids.cargar(doc)]
    _editar_ok(doc, "diametro", None, 10.0)
    assert [e["nombre"] for e in solids.cargar(doc)] == nombres


def test_concurrencia_dos_ediciones_una_200_una_409():
    doc = _importar(_step(_plancha(agujeros=[(10, 0, 8)])))
    listado = _listado(doc)
    cota = _cota(listado, "diametro")["id"]
    resultados: list[int] = []
    barrera = threading.Barrier(2)

    def editar(valor):
        barrera.wait()
        resultados.append(_aplicar(doc, listado["revision"], {cota: valor}).status_code)

    hilos = [threading.Thread(target=editar, args=(v,)) for v in (9.0, 10.0)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert sorted(resultados) == [200, 409]


def test_mcp_cota_geometrica_aplica_por_el_mismo_endpoint(monkeypatch):
    fabrica_original = mcp_client.httpx.Client

    def fabrica(**kwargs):
        kwargs.pop("base_url", None)
        return TestClient(app, headers=kwargs.get("headers"))

    monkeypatch.setattr(mcp_client.httpx, "Client", fabrica)
    try:
        doc = _importar(_step(_plancha(agujeros=[(10, 0, 8)])))
        cota = _cota(_listado(doc), "diametro")["id"]
        resp = tools.parametros(doc, cotas={cota: 10.0})
    finally:
        monkeypatch.setattr(mcp_client.httpx, "Client", fabrica_original)
    assert resp.get("error") is not True, resp
    assert resp["editadas"][0]["operacion"] == "rellenar_y_taladrar"


def _historial(doc_id: str) -> list[dict]:
    return client.get(f"/documentos/{doc_id}/historial").json()


def test_cotas_editables_traen_ancla_para_el_visor():
    doc = _importar(_step(_plancha(agujeros=[(10, 0, 8)])))
    listado = _listado(doc)
    diametro = _cota(listado, "diametro")
    assert diametro["ancla"] == pytest.approx([10.0, 0.0, 0.0], abs=1e-3)  # mid-depth of the through hole
    assert len(_cota(listado, "tamano_total", "x")["ancla"]) == 3
    assert _cota(listado, "distancia", "z")["ancla"][2] == pytest.approx(0.0, abs=1e-3)
    assert "ancla" not in _cota(listado, "posicion", "x")


def test_agujero_en_varios_solidos_solo_cambia_el_sólido_que_lo_contiene():
    from build123d import Compound

    otra = Pos(60, 0, 0) * Box(20, 20, 10)
    doc = _importar(_step(Compound(children=[_plancha(agujeros=[(10, 0, 8)]), otra])))
    v_otra_antes = [e["volumen"] for e in solids.cargar(doc)]
    _editar_ok(doc, "diametro", None, 10.0)
    v_otra_despues = [e["volumen"] for e in solids.cargar(doc)]
    assert len(v_otra_despues) == 2
    assert v_otra_despues[1] == pytest.approx(v_otra_antes[1], abs=1e-3)  # the other solid is untouched
    assert v_otra_despues[0] < v_otra_antes[0]


def test_medida_de_varios_solidos_no_es_estirable_pero_si_escalable():
    from build123d import Compound

    doc = _importar(_step(Compound(children=[Box(10, 10, 10), Pos(40, 0, 0) * Box(10, 10, 10)])))
    listado = _listado(doc)
    assert not [c for c in listado["cotas"] if c["tipo"] == "distancia" and not c.get("solo_lectura")]
    assert _cota(listado, "tamano_total", "x")["operacion"] == "escalar_eje"


def test_listado_reconoce_las_caras_paralelas_de_una_placa():
    doc = _importar(_step(_plancha()))
    distancias = [c for c in _listado(doc)["cotas"] if c["tipo"] == "distancia"]
    assert sorted((c["eje"], c["valor_mm"]) for c in distancias) == [("x", 40.0), ("y", 30.0), ("z", 10.0)]


def test_malla_mayor_que_el_tope_de_triangulos_422(monkeypatch):
    datos = trimesh.creation.box(extents=(40, 30, 20)).export(file_type="stl")
    doc = client.post("/documentos", files={"file": ("malla.stl", io.BytesIO(datos), "application/sla")}, headers=_h()).json()["id"]
    listado = _listado(doc)
    antes = _estado_en_disco(doc)
    monkeypatch.setattr(cotas_geometria, "MAX_MESH_FACES", 10)
    resp = _aplicar(doc, listado["revision"], {_cota(listado, "tamano_total", "x")["id"]: 50.0})
    assert resp.status_code == 422 and "caras" in resp.json()["mensaje"]
    assert _estado_en_disco(doc) == antes


def test_resolver_geometrico_rechaza_dos_cotas_y_ids_desconocidos():
    listado = [{"id": "tamano_total:x:40.000", "tipo": "tamano_total", "eje": "x", "valor_mm": 40.0, "operacion": "escalar_eje"}]
    with pytest.raises(cotas.CotaInvalida):
        cotas.resolve_geometric(listado, {"a": 50, "b": 60})
    with pytest.raises(cotas.CotaNoEncontrada):
        cotas.resolve_geometric(listado, {"x": 50})
    cota, valor = cotas.resolve_geometric(listado, {"tamano_total:x:40.000": 50})
    assert cota["eje"] == "x" and valor == 50.0
