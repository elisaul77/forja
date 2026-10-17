"""Fase 24d: rasgos de modificación (filete, chaflán, vaciado, desfase de cara,
matriz, simetría, dividir por plano y reparación de malla) sobre candidatos.

Las aristas y caras se direccionan por huellas geométricas (`naming.py`) del
estado actual; una huella que ya no existe es 422 y no se crea ningún paso."""

import math

import numpy as np
import pytest
import trimesh
from build123d import Axis, Box, Pos, Solid
from fastapi.testclient import TestClient

import auth, design_contracts as dc, design_features as feat, documents, naming
from kernel import b123d_kernel
from main import app

client = TestClient(app)
ABS = pytest.approx


def headers():
    return {"X-Forja-Token": auth.obtener_token()}


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def vertical_edges(shape, x=10, y=10):
    return [
        e
        for e in shape.edges()
        if abs(e.center().X - x) < 1e-6 and abs(e.center().Y - y) < 1e-6
    ]


def huellas(shape, tipo, filtro):
    fuentes = naming.aristas(shape) if tipo == "arista" else naming.caras(shape)
    elementos = shape.edges() if tipo == "arista" else shape.faces()
    return [fp.a_dict() for fp, e in zip(fuentes, elementos) if filtro(e)]


def volumen(shape):
    return float(sum(s.volume for s in shape.solids()))


# ---------------- Validación del contrato ----------------


def op(**kw):
    base = {"kind": "fillet", "piece": "p", "huellas": [], "valor_mm": 1}
    base.update(kw)
    return dc.Operation(**base)


def test_contrato_rechaza_campos_extra_y_numeros_no_finitos():
    with pytest.raises(ValueError):
        dc.Operation(kind="mirror", piece="p", inventado=1)
    with pytest.raises(ValueError):
        op(valor_mm=float("nan"))
    with pytest.raises(ValueError):
        op(valor_mm=0)  # filete con radio cero


def test_contrato_exige_referencias_del_tipo_correcto():
    arista = {
        "tipo": "arista", "subtipo": "LINE", "centroide": [0, 0, 0],
        "direccion": [0, 0, 1], "medida": 10,
    }
    cara = {**arista, "tipo": "cara", "subtipo": "PLANE"}
    with pytest.raises(ValueError):
        op(kind="fillet", huellas=[])
    with pytest.raises(ValueError):
        op(kind="fillet", huellas=[cara])
    with pytest.raises(ValueError):
        op(kind="shell", huellas=[arista])
    with pytest.raises(ValueError):
        op(kind="push_pull", huellas=[cara, cara])
    with pytest.raises(ValueError):
        op(kind="mirror", huellas=[arista])
    assert op(kind="fillet", huellas=[arista]).huellas[0].tipo == "arista"


def test_contrato_acota_patron_y_desfase():
    with pytest.raises(ValueError):
        dc.Operation(kind="pattern", piece="p", cantidad=33)
    with pytest.raises(ValueError):
        dc.Operation(kind="pattern", piece="p", cantidad=1)
    with pytest.raises(ValueError):
        dc.Operation(kind="pattern", piece="p", angulo_deg=400)
    with pytest.raises(ValueError):
        dc.Operation(kind="pattern", piece="p", eje="w")
    cara = {
        "tipo": "cara", "subtipo": "PLANE", "centroide": [0, 0, 5],
        "direccion": [0, 0, 1], "medida": 400,
    }
    with pytest.raises(ValueError):
        op(kind="push_pull", huellas=[cara], valor_mm=0)
    with pytest.raises(ValueError):
        op(kind="push_pull", huellas=[cara], valor_mm=5000)
    assert op(kind="push_pull", huellas=[cara], valor_mm=-3).valor_mm == -3


# ---------------- Geometría B-rep (kernel + feat) ----------------


def test_filete_reduce_el_volumen_en_el_area_analitica():
    caja = Box(20, 20, 10)
    hs = huellas(caja, "arista", lambda e: e in vertical_edges(caja))
    r = feat.aplicar_brep(caja, dc.Operation(kind="fillet", piece="p", huellas=hs, valor_mm=2))
    esperado = 4000 - (1 - math.pi / 4) * 4 * 10
    assert volumen(r) == ABS(esperado, abs=1e-3)


def test_chaflan_quita_un_triangulo_de_lado_dos():
    caja = Box(20, 20, 10)
    hs = huellas(caja, "arista", lambda e: e in vertical_edges(caja))
    r = feat.aplicar_brep(caja, dc.Operation(kind="chamfer", piece="p", huellas=hs, valor_mm=2))
    assert volumen(r) == ABS(4000 - 2 * 10, abs=1e-3)


def test_vaciado_con_cara_abierta_y_espesor_dos():
    caja = Box(20, 20, 10)
    hs = huellas(caja, "cara", lambda f: f.center().Z > 4)
    r = feat.aplicar_brep(caja, dc.Operation(kind="shell", piece="p", huellas=hs, valor_mm=2))
    assert volumen(r) == ABS(4000 - 16 * 16 * 8, abs=1e-2)


def test_desfase_de_cara_empuja_y_tira_en_su_normal():
    caja = Box(20, 20, 10)
    hs = huellas(caja, "cara", lambda f: f.center().Z > 4)
    subida = feat.aplicar_brep(caja, dc.Operation(kind="push_pull", piece="p", huellas=hs, valor_mm=5))
    bajada = feat.aplicar_brep(caja, dc.Operation(kind="push_pull", piece="p", huellas=hs, valor_mm=-3))
    assert volumen(subida) == ABS(4000 + 20 * 20 * 5, abs=1e-2)
    assert volumen(bajada) == ABS(4000 - 20 * 20 * 3, abs=1e-2)


def test_matriz_lineal_repite_la_pieza_sin_solapes():
    caja = Box(20, 20, 10)
    r = feat.aplicar_brep(
        caja,
        dc.Operation(kind="pattern", piece="p", patron="lineal", eje="x", cantidad=3, valor_mm=30),
    )
    assert len(r.solids()) == 3
    assert volumen(r) == ABS(3 * 4000, abs=1e-2)


def test_matriz_circular_reparte_copias_alrededor_del_eje():
    pieza = Pos(50, 0, 0) * Box(20, 20, 10)
    r = feat.aplicar_brep(
        pieza,
        dc.Operation(kind="pattern", piece="p", patron="circular", eje="z", cantidad=4, angulo_deg=360),
    )
    assert len(r.solids()) == 4
    centros = sorted(
        (round(s.center().X), round(s.center().Y)) for s in r.solids()
    )
    assert centros == sorted([(50, 0), (0, 50), (-50, 0), (0, -50)])


def test_simetria_une_la_pieza_con_su_reflejo():
    pieza = Pos(10, 0, 0) * Box(20, 20, 10)  # x en [0, 20]
    r = feat.aplicar_brep(pieza, dc.Operation(kind="mirror", piece="p", eje="x", plano_mm=0))
    assert len(r.solids()) == 1
    assert volumen(r) == ABS(8000, abs=1e-2)


def test_dividir_por_plano_deja_dos_cuerpos():
    caja = Box(20, 20, 10)
    r = feat.aplicar_brep(caja, dc.Operation(kind="split", piece="p", eje="z", plano_mm=0))
    assert sorted(round(s.volume) for s in r.solids()) == [2000, 2000]


def test_filete_sobre_arista_inexistente_falla_sin_geometria():
    caja = Box(20, 20, 10)
    fantasma = {
        "tipo": "arista", "subtipo": "LINE", "centroide": [300, 300, 0],
        "direccion": [0, 0, 1], "medida": 10,
    }
    with pytest.raises(ValueError, match="no encontrada"):
        feat.aplicar_brep(caja, dc.Operation(kind="fillet", piece="p", huellas=[fantasma], valor_mm=1))


def test_filete_demasiado_grande_se_rechaza():
    caja = Box(20, 20, 10)
    hs = huellas(caja, "arista", lambda e: e in vertical_edges(caja))
    with pytest.raises(ValueError):
        feat.aplicar_brep(caja, dc.Operation(kind="fillet", piece="p", huellas=hs, valor_mm=60))


# ---------------- Malla (STL) ----------------


def test_reparacion_de_malla_une_vertices_duplicados():
    # Caso típico de STL exportado: las caras no comparten vértices.
    caja = trimesh.creation.box(extents=[10, 10, 10])
    malla = trimesh.Trimesh(
        vertices=caja.triangles.reshape(-1, 3),
        faces=np.arange(len(caja.triangles) * 3).reshape(-1, 3),
        process=False,
    )
    assert not malla.is_volume
    r = feat.aplicar_malla(malla, dc.Operation(kind="mesh_repair", piece="p"))
    assert r.is_volume
    assert r.volume == ABS(1000, rel=1e-6)


def test_reparacion_no_inventa_volumen_en_malla_ya_cerrada():
    caja = trimesh.creation.box(extents=[10, 10, 10])
    r = feat.aplicar_malla(caja, dc.Operation(kind="mesh_repair", piece="p"))
    assert r.is_volume and r.volume == ABS(1000, rel=1e-6)


def test_simetria_de_malla_duplica_el_volumen():
    malla = trimesh.creation.box(extents=[10, 10, 10])
    malla.apply_translation([10, 0, 0])  # x en [5, 15]
    r = feat.aplicar_malla(malla, dc.Operation(kind="mirror", piece="p", eje="x", plano_mm=0))
    assert r.is_volume
    assert abs(r.volume) == ABS(2000, rel=1e-3)


def test_operaciones_b_rep_no_se_aplican_a_mallas():
    malla = trimesh.creation.box(extents=[10, 10, 10])
    arista = {"tipo": "arista", "subtipo": "LINE", "centroide": [0, 0, 0],
              "direccion": [0, 0, 1], "medida": 10}
    cara = {**arista, "tipo": "cara", "subtipo": "PLANE"}
    casos = [("fillet", [arista]), ("chamfer", [arista]), ("shell", [cara]),
             ("push_pull", [cara]), ("split", [])]
    for kind, huellas in casos:
        with pytest.raises(ValueError, match="solo para sólidos B-rep"):
            feat.aplicar_malla(malla, dc.Operation(kind=kind, piece="p", huellas=huellas))


# ---------------- Flujo HTTP: candidatos ----------------


def step_doc(tmp_path, forma, nombre="rasgo.step"):
    p = tmp_path / nombre
    b123d_kernel.export_to_step(forma, p)
    return ok(client.post("/documentos", files={"file": (nombre, p.read_bytes())}))["id"]


def stl_doc(tmp_path):
    data = trimesh.creation.box(extents=[10, 10, 10]).export(file_type="stl")
    return ok(client.post("/documentos", files={"file": ("malla.stl", data)}))["id"]


@pytest.fixture
def caja_doc(tmp_path):
    d = step_doc(tmp_path, Box(20, 20, 10))
    yield d
    client.delete("/documentos/" + d, headers=headers())


def contexto(d):
    return ok(client.get(f"/documentos/{d}/design"))


def referencias(d, pieza, tipo):
    return ok(client.get(f"/documentos/{d}/design/referencias", params={"pieza": pieza, "tipo": tipo}))


def candidatos(d):
    return ok(client.get(f"/documentos/{d}/design/candidates"))["candidates"]


def preparar(d, operaciones, key):
    c = contexto(d)
    return client.post(
        f"/documentos/{d}/design/candidates",
        headers=headers(),
        json={
            "revision": c["revision"],
            "state_revision": c["state_revision"],
            "request_id": key,
            "operations": operaciones,
        },
    )


def test_referencias_listan_aristas_y_caras_del_documento(caja_doc):
    pieza = contexto(caja_doc)["pieces"][0]["name"]
    aristas = referencias(caja_doc, pieza, "arista")
    caras = referencias(caja_doc, pieza, "cara")
    assert aristas["total"] == 12 and len(aristas["elementos"]) == 12
    assert caras["total"] == 6
    assert all(e["huella"]["tipo"] == "arista" for e in aristas["elementos"])
    assert all("id" in e for e in caras["elementos"])


def test_referencias_rechaza_mallas_y_tipos_desconocidos(tmp_path, caja_doc):
    malla = stl_doc(tmp_path)
    try:
        pieza = contexto(malla)["pieces"][0]["name"]
        assert client.get(f"/documentos/{malla}/design/referencias", params={"pieza": pieza, "tipo": "arista"}).status_code == 422
        pieza_b = contexto(caja_doc)["pieces"][0]["name"]
        assert client.get(f"/documentos/{caja_doc}/design/referencias", params={"pieza": pieza_b, "tipo": "vertice"}).status_code == 422
    finally:
        client.delete("/documentos/" + malla, headers=headers())


def test_filete_por_huella_crea_candidato_y_no_guarda_nada_hasta_aplicar(caja_doc):
    pieza = contexto(caja_doc)["pieces"][0]["name"]
    aristas = referencias(caja_doc, pieza, "arista")["elementos"]
    vertical = next(e for e in aristas if abs(e["huella"]["direccion"][2]) > 0.99)
    antes = len(candidatos(caja_doc))
    r = preparar(
        caja_doc,
        [{"kind": "fillet", "piece": pieza, "huellas": [vertical["huella"]], "valor_mm": 2}],
        "filete-1",
    )
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "prepared"
    assert r.json()["checks"]["failed"] == 0
    assert len(candidatos(caja_doc)) == antes + 1


def test_huella_que_ya_no_existe_es_422_y_no_crea_candidato(caja_doc):
    pieza = contexto(caja_doc)["pieces"][0]["name"]
    fantasma = {
        "tipo": "arista", "subtipo": "LINE", "centroide": [400, 0, 0],
        "direccion": [0, 0, 1], "medida": 10,
    }
    antes = len(candidatos(caja_doc))
    r = preparar(caja_doc, [{"kind": "chamfer", "piece": pieza, "huellas": [fantasma], "valor_mm": 1}], "fantasma")
    assert r.status_code == 422
    assert len(candidatos(caja_doc)) == antes


def test_fallo_del_nucleo_revierte_sin_paso(caja_doc):
    pieza = contexto(caja_doc)["pieces"][0]["name"]
    aristas = referencias(caja_doc, pieza, "arista")["elementos"]
    vertical = next(e for e in aristas if abs(e["huella"]["direccion"][2]) > 0.99)
    antes = len(candidatos(caja_doc))
    r = preparar(
        caja_doc,
        [{"kind": "fillet", "piece": pieza, "huellas": [vertical["huella"]], "valor_mm": 60}],
        "demasiado",
    )
    assert r.status_code == 422
    assert len(candidatos(caja_doc)) == antes


def test_operacion_con_campo_desconocido_es_422(caja_doc):
    pieza = contexto(caja_doc)["pieces"][0]["name"]
    r = preparar(caja_doc, [{"kind": "mirror", "piece": pieza, "eje": "x", "rotura": 1}], "extra")
    assert r.status_code == 422


def test_rasgo_b_rep_sobre_malla_es_422(tmp_path):
    malla = stl_doc(tmp_path)
    try:
        pieza = contexto(malla)["pieces"][0]["name"]
        r = preparar(malla, [{"kind": "split", "piece": pieza, "eje": "z", "plano_mm": 0}], "malla-split")
        assert r.status_code == 422
        assert "sólidos B-rep" in r.text
        r = preparar(malla, [{"kind": "mesh_repair", "piece": pieza}], "malla-repara")
        assert r.status_code == 200, r.text
    finally:
        client.delete("/documentos/" + malla, headers=headers())


def test_desfase_aplicado_cambia_la_altura_de_la_pieza(caja_doc):
    pieza = contexto(caja_doc)["pieces"][0]["name"]
    caras = referencias(caja_doc, pieza, "cara")["elementos"]
    arriba = next(e for e in caras if e["huella"]["direccion"][2] > 0.99)
    antes = contexto(caja_doc)["pieces"][0]["bbox"]
    r = preparar(
        caja_doc,
        [{"kind": "push_pull", "piece": pieza, "huellas": [arriba["huella"]], "valor_mm": 5}],
        "desfase-aplicar",
    )
    assert r.status_code == 200, r.text
    aplicado = client.post(
        f"/documentos/{caja_doc}/design/candidates/{r.json()['id']}/apply", headers=headers()
    )
    assert aplicado.status_code == 200, aplicado.text
    despues = contexto(caja_doc)["pieces"][0]["bbox"]
    assert (despues[5] - despues[2]) == ABS((antes[5] - antes[2]) + 5, abs=1e-3)


# ---------------- Revisión 1: fallos del núcleo y vaciado sin pared ----------------


def test_vaciado_fino_con_todas_las_caras_abiertas_es_422_sin_candidato(caja_doc):
    pieza = contexto(caja_doc)["pieces"][0]["name"]
    caras = [e["huella"] for e in referencias(caja_doc, pieza, "cara")["elementos"]]
    antes = len(candidatos(caja_doc))
    r = preparar(
        caja_doc,
        [{"kind": "shell", "piece": pieza, "huellas": caras, "valor_mm": 0.0001}],
        "vaciado-fino-todas",
    )
    assert r.status_code == 422, r.text
    assert "vaciado falló en el núcleo" in r.text
    assert len(candidatos(caja_doc)) == antes


def test_vaciado_con_todas_las_caras_abiertas_no_deja_pared_y_es_422(caja_doc):
    pieza = contexto(caja_doc)["pieces"][0]["name"]
    caras = [e["huella"] for e in referencias(caja_doc, pieza, "cara")["elementos"]]
    antes = len(candidatos(caja_doc))
    r = preparar(
        caja_doc,
        [{"kind": "shell", "piece": pieza, "huellas": caras, "valor_mm": 2}],
        "vaciado-sin-pared",
    )
    assert r.status_code == 422, r.text
    assert "no deja pared" in r.text
    assert len(candidatos(caja_doc)) == antes


def test_vaciado_con_todas_las_caras_abiertas_no_deja_pared_en_el_nucleo():
    caja = Box(20, 20, 10)
    todas = huellas(caja, "cara", lambda e: True)
    with pytest.raises(ValueError, match="no deja pared"):
        feat.aplicar_brep(caja, dc.Operation(kind="shell", piece="p", huellas=todas, valor_mm=2))


@pytest.mark.parametrize("tipo", b123d_kernel.FALLOS_NUCLEO, ids=lambda t: t.__name__)
def test_fallo_del_nucleo_se_traduce_a_valueerror_con_causa(monkeypatch, tipo):
    caja = Box(20, 20, 10)
    tapa = huellas(caja, "cara", lambda e: abs(e.center().Z - 5) < 1e-6)

    def roto(*_a, **_k):
        raise tipo("boom")

    monkeypatch.setattr(b123d_kernel, "vaciar_caras", roto)
    with pytest.raises(ValueError, match="vaciado falló en el núcleo: boom") as exc:
        feat.aplicar_brep(caja, dc.Operation(kind="shell", piece="p", huellas=tapa, valor_mm=2))
    assert isinstance(exc.value.__cause__, tipo)


def test_todos_los_rasgos_brep_traducen_el_fallo_del_nucleo(monkeypatch):
    caja = Box(20, 20, 10)
    aristas = huellas(caja, "arista", lambda e: e in vertical_edges(caja))
    tapa = huellas(caja, "cara", lambda e: abs(e.center().Z - 5) < 1e-6)
    casos = [
        ("fillet", "filete_aristas", "filete", dict(huellas=aristas, valor_mm=1)),
        ("chamfer", "chaflan_aristas", "chaflán", dict(huellas=aristas, valor_mm=1)),
        ("push_pull", "desfasar_cara", "desfase de cara", dict(huellas=tapa, valor_mm=5)),
        ("split", "dividir_por_plano", "división", dict(eje="z", plano_mm=0)),
    ]
    for kind, fn, rasgo, extra in casos:

        def roto(*_a, **_k):
            raise RuntimeError("boom")

        monkeypatch.setattr(b123d_kernel, fn, roto)
        with pytest.raises(ValueError, match=f"{rasgo} falló en el núcleo: boom") as exc:
            feat.aplicar_brep(caja, dc.Operation(kind=kind, piece="p", **extra))
        assert isinstance(exc.value.__cause__, RuntimeError), kind
        monkeypatch.undo()
