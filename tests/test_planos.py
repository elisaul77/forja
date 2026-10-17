"""Planos tecnicos (F22, ADR-0021): hoja con vistas, cotas automaticas y
cajetin en SVG, PDF y DXF. Unit tests sobre `app/planos.py` (formas B-rep y
mallas) y pruebas HTTP sobre `GET /documentos/{id}/exportar` y `descarga`.
"""
from __future__ import annotations

import hashlib
import io
import math
import re
import threading
import xml.etree.ElementTree as ET
from pathlib import Path

import ezdxf
import numpy as np
import pytest
import trimesh
from build123d import Align, Axis, Box, Cylinder, Pos, Rot, fillet
from fastapi.testclient import TestClient

import auth
import parametros
import planos
from kernel import b123d_kernel
from main import app

client = TestClient(app)
NS_SVG = "{http://www.w3.org/2000/svg}"


def _placa_con_agujero(x: float = 5.0):
    """40x30x20 centred at the origin, 8 mm through hole at (x, 0)."""
    return Box(40, 30, 20) - Pos(x, 0, 0) * Cylinder(4, 40)


def _opts(**kw):
    return planos.validar(kw.pop("formato", "svg"), **kw)


def _cotas(plano, tipo=None):
    return [c for c in plano.cotas if tipo is None or c["tipo"] == tipo]


# ---------------------------------------------------------------- validation


@pytest.mark.parametrize(
    "kwargs",
    [
        {"formato": "png"},
        {"formato": "svg", "vistas": "frontal,lateral"},
        {"formato": "svg", "escala": "3:1"},
        {"formato": "svg", "papel": "A5"},
        {"formato": "svg", "proyeccion": "ortogonal"},
        {"formato": "svg", "vistas": ""},
    ],
)
def test_validar_rechaza_opciones_fuera_de_enum(kwargs):
    kwargs = dict(kwargs)
    formato = kwargs.pop("formato")
    if kwargs.get("vistas") == "":
        kwargs["vistas"] = ","
    with pytest.raises(ValueError):
        planos.validar(formato, **kwargs)


def test_validar_normaliza_vistas_y_defaults():
    opts = planos.validar("pdf", vistas="isometrica, frontal,frontal")
    assert opts["vistas"] == ("frontal", "isometrica")
    assert opts["proyeccion"] == "tercer_angulo"
    assert opts["papel"] == "A4"
    assert opts["escala"] is None


# ---------------------------------------------------------------- B-rep: plate (a)


def test_placa_con_agujero_svg_capas_y_cotas():
    plano = planos.construir(_placa_con_agujero(), nombre="Placa", opciones=_opts())
    svg = planos.escribir_svg(plano)
    raiz = ET.fromstring(svg)
    ids = {g.get("id") for g in raiz.iter(f"{NS_SVG}g")}
    assert {"VISIBLES", "OCULTAS", "COTAS", "TEXTO"} <= ids
    valores = {(c["tipo"], c["eje"], c["valor_mm"]) for c in plano.cotas}
    assert ("tamano_total", "x", 40.0) in valores
    assert ("tamano_total", "y", 30.0) in valores
    assert ("tamano_total", "z", 20.0) in valores
    assert ("diametro", "z", 8.0) in valores
    # Position of the hole centre measured from the min corner (x=5 -> 25).
    assert ("posicion", "x", 25.0) in valores
    assert ("posicion", "y", 15.0) in valores


def test_placa_agujero_tiene_dimension_de_diametro_con_cantidad():
    plano = planos.construir(_placa_con_agujero(), nombre="Placa", opciones=_opts())
    diametros = _cotas(plano, "diametro")
    assert len(diametros) == 1
    assert diametros[0]["cantidad"] == 1
    assert diametros[0]["vista"] == "superior"
    assert plano.omitidas == 0


def test_cotas_tienen_forma_de_descriptor_y_valores_limpios():
    plano = planos.construir(_placa_con_agujero(), nombre="Placa", opciones=_opts())
    for c in plano.cotas:
        assert set(c) >= {"id", "tipo", "valor_mm", "eje", "vista", "origen", "parametro", "operacion"}
        assert c["tipo"] in {"tamano_total", "diametro", "posicion", "distancia"}
        assert isinstance(c["valor_mm"], float)
        assert c["operacion"] is None
        assert c["origen"] in {"parametro", "reconocido"}


# ---------------------------------------------------------------- hidden lines (b)


def _caja_con_bolsillo_inferior():
    return Box(40, 30, 20) - Pos(0, 0, -10) * Cylinder(3, 10)


def test_bolsillo_inferior_da_lineas_ocultas_en_planta():
    plano = planos.construir(_caja_con_bolsillo_inferior(), nombre="Bolsillo", opciones=_opts())
    ocultas = [p for p in plano.primitivas if p["tipo"] == "trazo" and p["capa"] == "OCULTAS"]
    assert ocultas, "un bolsillo desde abajo debe dar aristas ocultas en la vista superior"
    assert all(p["discontinua"] for p in ocultas)


def test_caja_solida_no_tiene_lineas_ocultas():
    plano = planos.construir(Box(40, 30, 20), nombre="Caja", opciones=_opts(vistas="superior"))
    ocultas = [p for p in plano.primitivas if p["tipo"] == "trazo" and p["capa"] == "OCULTAS"]
    assert ocultas == []


# ---------------------------------------------------------------- determinism (f)


def test_ids_de_cotas_deterministas_y_sin_azar():
    a = planos.construir(_placa_con_agujero(), nombre="Placa", opciones=_opts())
    b = planos.construir(_placa_con_agujero(), nombre="Placa", opciones=_opts())
    assert [c["id"] for c in a.cotas] == [c["id"] for c in b.cotas]
    assert len({c["id"] for c in a.cotas}) == len(a.cotas)


# ---------------------------------------------------------------- PARAMETROS (e)


def test_parametro_liga_la_cota_por_valor_unico():
    plano = planos.construir(
        Box(40, 30, 20), nombre="Script", opciones=_opts(),
        parametros={"ancho": 40, "alto": 20, "prof": 30},
    )
    ancho = next(c for c in plano.cotas if c["tipo"] == "tamano_total" and c["eje"] == "x")
    assert ancho["origen"] == "parametro"
    assert ancho["parametro"] == "ancho"
    assert plano.resumen(False)["parametros_ligados"] == 3


def test_valor_que_coincide_con_dos_claves_no_se_liga():
    plano = planos.construir(
        Box(40, 30, 20), nombre="Script", opciones=_opts(),
        parametros={"a": 40, "b": 40},
    )
    ancho = next(c for c in plano.cotas if c["tipo"] == "tamano_total" and c["eje"] == "x")
    assert ancho["parametro"] is None
    assert ancho["origen"] == "reconocido"


def test_ids_estables_cuando_cambia_solo_una_dimension():
    antes = planos.construir(_placa_con_agujero(), nombre="P", opciones=_opts())
    despues = planos.construir(
        Box(50, 30, 20) - Pos(5, 0, 0) * Cylinder(4, 40), nombre="P", opciones=_opts()
    )
    ids_antes = {c["id"] for c in antes.cotas}
    ids_despues = {c["id"] for c in despues.cotas}
    # Height and depth unchanged; their ids must survive the width change.
    assert "tamano_total:z:20.000" in ids_antes & ids_despues
    assert "tamano_total:y:30.000" in ids_antes & ids_despues
    assert "tamano_total:x:40.000" in ids_antes - ids_despues


# ---------------------------------------------------------------- escaping (g)

_HOSTIL = '<script>&"x'


def test_nombre_hostil_escapado_en_svg_y_dxf():
    plano = planos.construir(
        _placa_con_agujero(), nombre=_HOSTIL, piezas=[_HOSTIL], opciones=_opts()
    )
    svg = planos.escribir_svg(plano).decode("utf-8")
    ET.fromstring(svg)  # still well-formed XML
    assert "<script>" not in svg
    assert "&lt;script&gt;&amp;" in svg
    dxf = planos.escribir_dxf(plano)
    doc = ezdxf.read(io.StringIO(dxf.decode("utf-8")))
    textos = [e.dxf.text for e in doc.modelspace().query("TEXT")]
    assert any(_HOSTIL in t for t in textos)


def test_nombre_con_parentesis_y_barra_no_rompe_el_pdf():
    plano = planos.construir(
        _placa_con_agujero(), nombre="a)b(c\\d", opciones=_opts(formato="pdf")
    )
    pdf = planos.escribir_pdf(plano)
    assert pdf.startswith(b"%PDF-")
    assert b"a\\)b\\(c\\\\d" in pdf


# ---------------------------------------------------------------- DXF (c)


def test_dxf_reabre_con_capas_y_entidades():
    plano = planos.construir(_placa_con_agujero(), nombre="Placa", opciones=_opts(formato="dxf"))
    dxf = planos.escribir_dxf(plano)
    doc = ezdxf.read(io.StringIO(dxf.decode("utf-8")))
    capas = {layer.dxf.name for layer in doc.layers}
    assert {"VISIBLES", "OCULTAS", "COTAS", "TEXTO"} <= capas
    assert doc.header["$INSUNITS"] == 4
    entidades = list(doc.modelspace())
    assert len(entidades) > 0
    assert {e.dxf.layer for e in entidades} >= {"VISIBLES", "TEXTO"}


# ---------------------------------------------------------------- PDF (d)


def _leer_xref(pdf: bytes) -> list[int]:
    """Offsets of the xref table entries (entry 0 is the free head)."""
    inicio = int(re.search(rb"startxref\n(\d+)\n%%EOF", pdf).group(1))
    lineas = pdf[inicio:].split(b"\n")
    assert lineas[0] == b"xref"
    total = int(lineas[1].split()[1])
    entradas = lineas[2 : 2 + total]
    assert all(len(linea) == 19 for linea in entradas), "each xref entry is 20 bytes with its newline"
    return [int(linea[:10]) for linea in entradas]


def test_pdf_xref_valido_y_titulo_presente():
    plano = planos.construir(
        _placa_con_agujero(), nombre="Placa Eli", revision="r7", opciones=_opts(formato="pdf")
    )
    pdf = planos.escribir_pdf(plano)
    assert pdf.startswith(b"%PDF-")
    desplazamientos = _leer_xref(pdf)
    for numero, pos in enumerate(desplazamientos[1:], start=1):
        assert pdf[pos:].startswith(f"{numero} 0 obj".encode()), f"xref entry {numero} is off"
    assert b"Documento: Placa Eli" in pdf
    assert b"/Helvetica" in pdf


# ---------------------------------------------------------------- scale and paper


def test_escala_automatica_entra_en_el_papel():
    plano = planos.construir(_placa_con_agujero(), nombre="P", opciones=_opts())
    assert plano.escala in planos.ESCALAS
    ancho_p, alto_p = planos.PAPELES[plano.papel]
    assert plano.papel == "A4" and (ancho_p, alto_p) == (297.0, 210.0)


def test_escala_explicita_que_no_cabe_da_error():
    enorme = Box(600, 300, 200)
    with pytest.raises(ValueError, match="no cabe"):
        planos.construir(enorme, nombre="E", opciones=_opts(escala="1:1"))


def test_escala_explicita_se_respeta_y_se_imprime():
    plano = planos.construir(_placa_con_agujero(), nombre="P", opciones=_opts(escala="1:2"))
    assert plano.escala == "1:2"
    texto = " ".join(p["texto"] for p in plano.primitivas if p["tipo"] == "texto")
    assert "Escala: 1:2" in texto


def test_proyeccion_primer_angulo_cambia_la_disposicion():
    tercer = planos.construir(_placa_con_agujero(), nombre="P", opciones=_opts())
    primer = planos.construir(_placa_con_agujero(), nombre="P", opciones=_opts(proyeccion="primer_angulo"))
    texto_primer = " ".join(p["texto"] for p in primer.primitivas if p["tipo"] == "texto")
    assert "primer ángulo" in texto_primer
    assert tercer.proyeccion == "tercer_angulo" and primer.proyeccion == "primer_angulo"


# ---------------------------------------------------------------- non-axis holes


def test_agujero_oblicuo_se_omite_y_se_cuenta():
    oblicuo = Box(40, 30, 20) - Rot(0, 45, 0) * Cylinder(3, 60)
    plano = planos.construir(oblicuo, nombre="Oblicuo", opciones=_opts())
    assert plano.omitidas >= 1
    assert _cotas(plano, "diametro") == []
    assert plano.aviso and "sin cota" in plano.aviso


# ---------------------------------------------------------------- mesh (h)


def test_malla_stl_se_dibuja_sin_rechazo_y_sin_ocultas():
    malla = trimesh.creation.box(extents=(40, 30, 20))
    plano = planos.construir(malla, nombre="Caja STL", opciones=_opts())
    assert plano.es_malla
    medidas = {(c["eje"], c["valor_mm"]) for c in _cotas(plano, "tamano_total")}
    assert medidas == {("x", 40.0), ("y", 30.0), ("z", 20.0)}
    assert [p for p in plano.primitivas if p["capa"] == "OCULTAS"] == []
    assert "STL" in plano.aviso
    trazos = [p for p in plano.primitivas if p["tipo"] == "trazo" and p["capa"] == "VISIBLES"]
    assert len(trazos) >= 12  # silhouette + crease edges of a box


# ---------------------------------------------------------------- edge cap (j)


def test_tope_de_aristas_produce_completo_false(monkeypatch):
    monkeypatch.setattr(planos, "CAP_ARISTAS", 5)
    plano = planos.construir(_placa_con_agujero(), nombre="P", opciones=_opts())
    assert plano.completo is False
    visibles = [p for p in plano.primitivas if p["capa"] == "VISIBLES" and p["tipo"] == "trazo"]
    assert 0 < len(visibles) <= 5 * len(planos.VISTAS)


# ---------------------------------------------------------------- limits


def test_pieza_sin_volumen_da_error_claro():
    malla = trimesh.Trimesh(vertices=[[0, 0, 0], [1, 0, 0], [2, 0, 0]], faces=[[0, 1, 2]], process=False)
    with pytest.raises(ValueError, match="volumen"):
        planos.construir(malla, nombre="Plana", opciones=_opts())


def test_sin_parametros_no_hay_tabla():
    plano = planos.construir(_placa_con_agujero(), nombre="P", opciones=_opts())
    assert all(p["texto"] != "Parámetros" for p in plano.primitivas if p["tipo"] == "texto")


def test_tabla_de_parametros_con_unidad():
    plano = planos.construir(
        _placa_con_agujero(), nombre="P", opciones=_opts(),
        parametros={"ancho": 40, "alto": (20.5, "mm")},
    )
    textos = [p["texto"] for p in plano.primitivas if p["tipo"] == "texto"]
    assert "Parámetros" in textos
    assert "alto = 20.5 mm" in textos


# ================================================================ HTTP (route wiring)

_SCRIPT_PARAMETRICO = (
    "from build123d import Box, Cylinder, Pos\n"
    'PARAMETROS = {"ancho": 40, "alto": 20, "prof": 30}\n'
    'resultado = Box(PARAMS["ancho"], PARAMS["prof"], PARAMS["alto"]) - Pos(5, 0, 0) * Cylinder(4, 40)\n'
)


def _h() -> dict[str, str]:
    return {"X-Forja-Token": auth.obtener_token()}


def _documento_parametrico(ancho: float = 40.0, nombre: str = "placa_param.step") -> dict:
    """STEP document + the PARAMETROS sidecar a script document would have.
    Built without the sandbox (untrusted scripts run only in forja-sandbox),
    so these tests also run in a throwaway container; the route reads the
    same files either way."""
    import tempfile

    from kernel import b123d_kernel

    ruta = Path(tempfile.mkdtemp(prefix="planos-")) / "modelo_plano.step"  # the upload name may be hostile
    b123d_kernel.export_to_step(Box(ancho, 30, 20) - Pos(5, 0, 0) * Cylinder(4, 40), ruta)
    resp = client.post("/documentos", files={"file": (nombre, ruta.read_bytes(), "application/step")}, headers=_h())
    assert resp.status_code == 200, resp.text
    doc = resp.json()
    parametros.guardar(
        doc["id"],
        codigo=_SCRIPT_PARAMETRICO,
        ruta=None,
        variables=None,
        timeout=None,
        esquema=parametros.esquema_desde_codigo(_SCRIPT_PARAMETRICO),
        valores={"ancho": ancho, "alto": 20.0, "prof": 30.0},
    )
    return doc


def _exportar(doc_id: str, **params):
    return client.get(f"/documentos/{doc_id}/exportar", params={"formato": "svg", **params})


def _hashes_de_datos() -> dict[str, str]:
    import documents

    raiz = documents.DOCUMENTOS_DIR
    resultado = {}
    for ruta in sorted(raiz.rglob("*")):
        rel = ruta.relative_to(raiz).as_posix()
        if ruta.is_file() and not rel.startswith("exportados") and ruta.name != "forja_token":
            resultado[rel] = hashlib.sha256(ruta.read_bytes()).hexdigest()
    return resultado


@pytest.mark.parametrize("formato,marca", [("svg", b"<svg"), ("pdf", b"%PDF-"), ("dxf", b"SECTION")])
def test_exportar_plano_formatos_escriben_archivo_bajo_exportados(formato, marca):
    doc = _documento_parametrico()
    resp = _exportar(doc["id"], formato=formato)
    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["vistas"] == ["frontal", "superior", "derecha", "isometrica"]
    assert cuerpo["papel"] == "A4" and cuerpo["proyeccion"] == "tercer_angulo"
    assert cuerpo["cotas"] >= 4
    assert cuerpo["parametros_ligados"] == 3
    assert cuerpo["omitidas"] == 0
    assert "cotas_detalle" not in cuerpo
    ruta = Path(cuerpo["ruta"])
    assert ruta.name == f"{doc['id']}.{formato}"
    assert ruta.parent.name == "exportados"
    assert ruta.read_bytes().find(marca) != -1


def test_exportar_plano_formato_desconocido_da_400():
    doc = _documento_parametrico()
    resp = client.get(f"/documentos/{doc['id']}/exportar", params={"formato": "png"}, headers=_h())
    assert resp.status_code == 400


@pytest.mark.parametrize(
    "params",
    [{"vistas": "frontal,lateral"}, {"escala": "3:1"}, {"papel": "A5"}, {"proyeccion": "ortogonal"}],
)
def test_exportar_plano_opciones_fuera_de_enum_dan_400(params):
    doc = _documento_parametrico()
    assert _exportar(doc["id"], **params).status_code == 400


def test_detalle_lista_cotas_y_parametro_ligado():
    doc = _documento_parametrico()
    cuerpo = _exportar(doc["id"], detalle="true").json()
    detalle = cuerpo["cotas_detalle"]
    ancho = next(c for c in detalle if c["tipo"] == "tamano_total" and c["eje"] == "x")
    assert ancho["valor_mm"] == 40.0 and ancho["parametro"] == "ancho"
    assert ancho["origen"] == "parametro"


def test_ids_estables_entre_revisiones_con_otro_valor():
    """Same PARAMETROS, one width changed: unchanged dimensions keep their ids."""
    antes = {c["id"]: c for c in _exportar(_documento_parametrico(40.0)["id"], detalle="true").json()["cotas_detalle"]}
    despues = _exportar(_documento_parametrico(50.0)["id"], detalle="true").json()["cotas_detalle"]
    ids_despues = {c["id"]: c for c in despues}
    ancho_nuevo = next(c for c in despues if c["tipo"] == "tamano_total" and c["eje"] == "x")
    assert ancho_nuevo["valor_mm"] == 50.0 and ancho_nuevo["parametro"] == "ancho"
    assert "tamano_total:z:20.000" in antes and "tamano_total:z:20.000" in ids_despues
    assert "tamano_total:x:40.000" in antes and "tamano_total:x:40.000" not in ids_despues


def test_stl_se_dibuja_con_aviso_en_vez_de_rechazo():
    caja = trimesh.creation.box(extents=(40, 30, 20))
    data = caja.export(file_type="stl")
    resp = client.post("/documentos", files={"file": ("caja_malla.stl", io.BytesIO(data), "application/sla")}, headers=_h())
    assert resp.status_code == 200, resp.text
    doc_id = resp.json()["id"]
    cuerpo = _exportar(doc_id).json()
    assert cuerpo["aviso"].startswith("malla STL")
    assert cuerpo["cotas"] == 3
    assert "cotas_detalle" not in cuerpo


def test_exportar_plano_es_solo_lectura():
    doc = _documento_parametrico()
    antes_hist = client.get(f"/documentos/{doc['id']}/historial").json()
    antes = _hashes_de_datos()
    assert _exportar(doc["id"], formato="pdf").status_code == 200
    assert _exportar(doc["id"], formato="dxf").status_code == 200
    assert client.get(f"/documentos/{doc['id']}/historial").json() == antes_hist
    assert _hashes_de_datos() == antes


def test_nombre_hostil_del_documento_escapado_en_svg():
    doc = _documento_parametrico(nombre='<b>&"x.step')
    cuerpo = _exportar(doc["id"]).json()
    svg = Path(cuerpo["ruta"]).read_text(encoding="utf-8")
    ET.fromstring(svg)
    assert "<b>" not in svg
    assert "&lt;b&gt;&amp;" in svg


def test_descarga_plano_pdf_en_memoria():
    doc = _documento_parametrico()
    resp = client.get(f"/documentos/{doc['id']}/descarga/Placa.pdf")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF-")
    assert 'filename="placa_param.pdf"' in resp.headers["content-disposition"]  # slug of the document name, as STL/3MF


def test_descarga_plano_svg_y_dxf():
    doc = _documento_parametrico()
    svg = client.get(f"/documentos/{doc['id']}/descarga/Placa.svg")
    dxf = client.get(f"/documentos/{doc['id']}/descarga/Placa.dxf")
    assert svg.status_code == 200 and svg.content.startswith(b"<?xml")
    assert dxf.status_code == 200 and b"SECTION" in dxf.content
    assert dxf.headers["content-type"].startswith("image/vnd.dxf")


def test_descarga_plano_de_una_pieza_da_400():
    doc = _documento_parametrico()
    resp = client.get(f"/documentos/{doc['id']}/descarga/Pieza/Placa.pdf")
    assert resp.status_code == 400


# ---------------------------------------------------------------- review fixes (F22 review)


def _escalonada_fuera_de_origen():
    """Stepped part far from the origin: X 80..180, Y 60..100, Z 35..59, with
    a Ø6 through hole at (X=100, Y=80) = 20 mm from the min corner in X and Y."""
    base = Pos(130, 80, 40) * Box(100, 40, 10)
    escalon = Pos(160, 80, 52) * Box(40, 20, 14)
    return (base + escalon) - Pos(100, 80, 40) * Cylinder(3, 60)


def _extremos_visibles(plano):
    pts = [pt for p in plano.primitivas if p["tipo"] == "trazo" and p["capa"] == "VISIBLES" for pt in p["puntos"]]
    xs = [x for x, _ in pts]
    ys = [y for _, y in pts]
    return min(xs), max(xs), min(ys), max(ys)


def _segmentos_cota(plano):
    return [p["puntos"] for p in plano.primitivas if p["tipo"] == "trazo" and p["capa"] == "COTAS"]


def _textos_capa(plano, capa):
    return [p["texto"] for p in plano.primitivas if p["tipo"] == "texto" and p["capa"] == capa]


def test_pieza_fuera_del_origen_cotas_alineadas_con_el_contorno():
    plano = planos.construir(
        _escalonada_fuera_de_origen(), nombre="Escalonada", opciones=_opts(vistas="frontal")
    )
    izq, der, _arriba, _abajo = _extremos_visibles(plano)
    verticales = [pts[0][0] for pts in _segmentos_cota(plano) if abs(pts[0][0] - pts[1][0]) < 1e-9]
    assert any(abs(x - izq) < 0.01 for x in verticales), "la cota de ancho debe empezar en el borde izquierdo del contorno"
    assert any(abs(x - der) < 0.01 for x in verticales), "la cota de ancho debe acabar en el borde derecho del contorno"


def test_agujero_fuera_del_origen_marca_el_agujero_real():
    plano = planos.construir(
        _escalonada_fuera_de_origen(), nombre="Escalonada", opciones=_opts(vistas="superior")
    )
    izq, _der, arriba, _abajo = _extremos_visibles(plano)
    f = planos.ESCALAS[plano.escala]
    ex, ey = izq + 20.0 * f, arriba + 20.0 * f  # hole centre at X=100, Y=80 in a view from +Z
    segmentos = _segmentos_cota(plano)
    horizontales = [pts for pts in segmentos if abs(pts[0][1] - pts[1][1]) < 1e-9 and abs(pts[0][1] - ey) < 0.01]
    verticales = [pts for pts in segmentos if abs(pts[0][0] - pts[1][0]) < 1e-9 and abs(pts[0][0] - ex) < 0.01]
    assert any(abs((pts[0][0] + pts[1][0]) / 2 - ex) < 0.01 for pts in horizontales), "marca horizontal fuera del agujero"
    assert any(abs((pts[0][1] + pts[1][1]) / 2 - ey) < 0.01 for pts in verticales), "marca vertical fuera del agujero"
    assert "Ø6" in _textos_capa(plano, "COTAS")


@pytest.mark.parametrize("vista", list(planos.VISTAS))
def test_proyeccion_brep_usa_el_marco_absoluto_de_cada_vista(vista):
    """Off-origin solid: the projected outline must sit on the bbox extents in
    the absolute frame of the view (build123d reports it relative to look_at)."""
    pieza = Pos(100, 50, 20) * Box(10, 6, 4)
    bb = pieza.bounding_box()
    lo = np.array([bb.min.X, bb.min.Y, bb.min.Z], dtype=float)
    hi = np.array([bb.max.X, bb.max.Y, bb.max.Z], dtype=float)
    v, u, r = planos._base(vista)
    visibles, _ocultas, _n = planos._proyectar_brep(pieza, v, u, r, (lo + hi) / 2.0, float(np.linalg.norm(hi - lo)))
    pts = np.array([p for pl in visibles for p in pl])
    umin, umax, vmin, vmax = planos._marco(lo, hi, u, r)
    assert pts[:, 0].min() == pytest.approx(umin, abs=1e-3)
    assert pts[:, 0].max() == pytest.approx(umax, abs=1e-3)
    assert pts[:, 1].min() == pytest.approx(vmin, abs=1e-3)
    assert pts[:, 1].max() == pytest.approx(vmax, abs=1e-3)


def _pieza_con_radio_interior_r4():
    cuerpo = Box(50, 50, 10, align=Align.MIN) + Box(50, 10, 40, align=Align.MIN)
    arista = (
        cuerpo.edges()
        .filter_by(Axis.X)
        .filter_by_position(Axis.Z, 9.99, 10.01)
        .filter_by_position(Axis.Y, 9.99, 10.01)
    )
    return fillet(arista, 4)


def test_radio_interior_r4_no_es_agujero():
    plano = planos.construir(_pieza_con_radio_interior_r4(), nombre="Radio", opciones=_opts())
    assert _cotas(plano, "diametro") == []
    assert _cotas(plano, "posicion") == []
    assert not any(t.startswith("Ø") for t in _textos_capa(plano, "COTAS"))


def test_cotas_dibujadas_coinciden_con_el_cajetin():
    plano = planos.construir(_placa_con_agujero(), nombre="Placa", opciones=_opts())
    textos = _textos_capa(plano, "COTAS")
    assert len(textos) == len(plano.cotas)
    assert any(t.endswith(f"Cotas: {len(plano.cotas)}") for t in _textos_capa(plano, "TEXTO"))
    assert "Ø8" in textos
    # Hole at X=5 is 25 mm from the min corner (20 + 5); at Y=0 it is 15 mm.
    assert "25" in textos and "15" in textos


def test_cajetin_cuenta_solo_las_cotas_de_las_vistas_pedidas():
    plano = planos.construir(_placa_con_agujero(), nombre="Placa", opciones=_opts(vistas="frontal"))
    assert len(plano.cotas) == 2  # overall X and Z only; Ø and Y live in the superior view
    assert len(_textos_capa(plano, "COTAS")) == 2
    assert any(t.endswith(f"Cotas: {len(plano.cotas)}") for t in _textos_capa(plano, "TEXTO"))


def test_malla_demasiado_grande_se_rechaza_con_mensaje_claro(monkeypatch):
    monkeypatch.setattr(planos, "MAX_TRIANGULOS_PLANO", 10)
    malla = trimesh.creation.box(extents=(40, 30, 20))  # 12 triangles
    with pytest.raises(ValueError, match="malla demasiado grande para un plano"):
        planos.construir(malla, nombre="Caja STL", opciones=_opts())


@pytest.mark.parametrize("formato", ["svg", "pdf", "dxf"])
def test_descarga_plano_cabeceras_de_seguridad(formato):
    doc = _documento_parametrico()
    resp = client.get(f"/documentos/{doc['id']}/descarga/Placa.{formato}")
    assert resp.status_code == 200, resp.text
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in resp.headers["content-security-policy"]


def test_descarga_plano_proyecta_sin_retener_el_bloqueo(monkeypatch):
    doc = _documento_parametrico()
    real = planos.construir
    resultado: dict[str, bool] = {}

    def sondeo(*args, **kwargs):
        # Another thread must be able to take the document lock while the
        # sheet is projected; if the download still holds it, the probe times out.
        adquirido = threading.Event()

        def probar():
            with parametros.bloqueo(doc["id"]):
                adquirido.set()

        threading.Thread(target=probar, daemon=True).start()
        resultado["ok"] = adquirido.wait(5)
        return real(*args, **kwargs)

    monkeypatch.setattr(planos, "construir", sondeo)
    resp = client.get(f"/documentos/{doc['id']}/descarga/Placa.pdf")
    assert resp.status_code == 200, resp.text
    assert resultado.get("ok") is True, "la proyeccion no debe ejecutarse con el bloqueo del documento retenido"


def test_descarga_nombre_hostil_no_rompe_cabeceras_ni_rotulo():
    doc = _documento_parametrico(nombre='../../etc/"<script>x</script>.step')
    resp = client.get(f"/documentos/{doc['id']}/descarga/Placa.svg")
    assert resp.status_code == 200, resp.text
    assert re.fullmatch(r'attachment; filename="[A-Za-z0-9_-]{1,64}\.svg"', resp.headers["content-disposition"])
    svg = resp.content.decode("utf-8")
    ET.fromstring(svg)
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg
