"""F20 captura enfocada y captura del primer choque del barrido (ADR-0028).

Tres niveles: funciones puras de `render.py` (encuadre, validación, caps),
REST (`GET /captura?foco=&radio=` y `GET /ensamble/captura-choque`) y las
herramientas MCP (`captura(foco, radio)`, `ensamble(captura_en_choque)`), que
necesitan el backend vivo como el resto de `test_mcp_tools.py`.

La línea base de `renderizar_png` sin foco se fijó antes de tocar el render:
el PNG de una escena fija no cambia ni un byte.
"""
from __future__ import annotations

import hashlib
import json

import httpx
import numpy as np
import pytest
import trimesh
from fastapi.testclient import TestClient

import auth
import documents
import render
from kernel import b123d_kernel as kernel
from main import app
from mcp_server import client, tools

# SHA-256 de `renderizar_png` (sin foco) sobre la escena de `_escena_base()`,
# medido en el código anterior a F20 dentro de la imagen forja-forja.
LINEA_BASE_SIN_FOCO = "693e5e5c15f06c6cc4a01874bc8875c736216ab26df125dd57ace4feff518cc8"

FONDO = np.array([0x10, 0x10, 0x14])


def _sha(datos: bytes) -> str:
    return hashlib.sha256(datos).hexdigest()


def _escena_base():
    caja_a = trimesh.creation.box(extents=(10, 10, 10))
    caja_b = trimesh.creation.box(extents=(6, 14, 4))
    caja_b.apply_translation((22, 3, 2))
    grupos = {"a": caja_a, "b": caja_b}
    colores = {"a": render._color_por_indice(0), "b": render._color_por_indice(1)}
    return grupos, colores


def _caja_en(centro, lado=10.0):
    caja = trimesh.creation.box(extents=(lado, lado, lado))
    caja.apply_translation(centro)
    return caja


def _mascara_pieza(png: bytes) -> np.ndarray:
    from PIL import Image
    import io

    rgb = np.asarray(Image.open(io.BytesIO(png)).convert("RGB")).astype(int)
    return np.abs(rgb - FONDO).sum(axis=2) > 12


def _centro_de_la_pieza(png: bytes) -> tuple[float, float]:
    """Centro de la caja de pixeles no-fondo. Medir con la leyenda puesta cuenta
    el panel de la esquina: los llamadores que miden centrado la quitan."""
    mascara = _mascara_pieza(png)
    filas, columnas = np.nonzero(mascara)
    assert filas.size > 0, "la imagen esta vacia (solo fondo)"
    return (columnas.min() + columnas.max()) / 2.0, (filas.min() + filas.max()) / 2.0


def _bloque_central_es_pieza(png: bytes, lado: int = 5) -> bool:
    """El bloque central de la imagen (donde cae el foco) es todo pieza."""
    mascara = _mascara_pieza(png)
    alto, ancho = mascara.shape
    fila, columna = alto // 2, ancho // 2
    bloque = mascara[fila - lado // 2: fila + lado // 2 + 1, columna - lado // 2: columna + lado // 2 + 1]
    return bool(bloque.all())


# --------------------------------------------------------- funciones puras

@pytest.mark.parametrize("texto, esperado", [
    ("1,2,3", (1.0, 2.0, 3.0)),
    (" -4.5 , 0 , 10e2 ", (-4.5, 0.0, 1000.0)),
])
def test_parsear_foco_acepta_tres_numeros_finitos(texto, esperado):
    assert render.parsear_foco(texto) == esperado


@pytest.mark.parametrize("texto", [
    "nan,0,0", "inf,0,0", "1e400,0,0", "1,2", "1,2,3,4", "a,b,c", "", "1,2,2000000",
])
def test_parsear_foco_rechaza_no_finitos_mal_formados_y_fuera_de_tope(texto):
    with pytest.raises(ValueError):
        render.parsear_foco(texto)


def test_validar_radio_respeta_minimo_y_cinco_diagonales():
    assert render.validar_radio(0.5, 10.0) == 0.5
    assert render.validar_radio(50.0, 10.0) == 50.0
    for malo in (0.49, 50.01, float("nan"), float("inf"), True):
        with pytest.raises(ValueError):
            render.validar_radio(malo, 10.0)


def test_render_sin_foco_es_byte_identico_a_la_linea_base():
    """Regresión F20: sin `foco` el PNG es el de antes de la fase (mismo hash)."""
    grupos, colores = _escena_base()
    png, caption = render.renderizar_png(grupos, colores, azimut=45.0, elevacion=25.0,
                                         ancho=300, alto=300)
    assert _sha(png) == LINEA_BASE_SIN_FOCO
    assert caption == "2 solido(s), 24 triangulos, vista azimut=45 elevacion=25"


def test_foco_centra_la_pieza_en_el_centro_de_la_imagen():
    """El punto de foco cae en el centro del PNG: la pieza centrada en él sale centrada."""
    grupos = {"caja": _caja_en((100.0, 0.0, 0.0))}
    colores = {"caja": (0.9, 0.5, 0.2)}
    png, caption = render.renderizar_png(grupos, colores, ancho=300, alto=300, leyenda_visible=False,
                                         foco=(100.0, 0.0, 0.0), radio=12.0)
    cx, cy = _centro_de_la_pieza(png)
    assert abs(cx - 149.5) <= 2.0 and abs(cy - 149.5) <= 2.0, (cx, cy)
    assert "encuadre en foco (100, 0, 0) radio 12 mm" in caption


def test_foco_con_radio_pequeno_recorta_sobre_la_pieza():
    """Un radio menor que la pieza llena el encuadre: las esquinas ya son pieza."""
    grupos = {"caja": _caja_en((0.0, 0.0, 0.0), lado=20.0)}
    colores = {"caja": (0.9, 0.5, 0.2)}
    png, _caption = render.renderizar_png(grupos, colores, ancho=200, alto=200,
                                          foco=(0.0, 0.0, 0.0), radio=5.0)
    mascara = _mascara_pieza(png)
    assert mascara[:10, :10].all() and mascara[-10:, -10:].all()


def test_foco_fuera_de_la_geometria_cae_al_encuadre_general_con_nota():
    """Un foco que no ve ninguna pieza no da imagen vacía: encuadre general y nota."""
    grupos = {"caja": _caja_en((100.0, 0.0, 0.0))}
    colores = {"caja": (0.9, 0.5, 0.2)}
    png, caption = render.renderizar_png(grupos, colores, ancho=200, alto=200,
                                         foco=(-500.0, 0.0, 0.0), radio=5.0)
    assert _mascara_pieza(png).sum() > 0
    assert "foco fuera de la geometria: encuadre general" in caption
    assert "encuadre en foco" not in caption


def test_foco_y_radio_van_juntos():
    grupos, colores = _escena_base()
    with pytest.raises(ValueError):
        render.renderizar_png(grupos, colores, foco=(0.0, 0.0, 0.0))
    with pytest.raises(ValueError):
        render.renderizar_png(grupos, colores, radio=5.0)


def test_foco_no_finito_o_radio_fuera_de_rango_es_error_en_el_render():
    grupos, colores = _escena_base()
    with pytest.raises(ValueError):
        render.renderizar_png(grupos, colores, foco=(float("nan"), 0.0, 0.0), radio=5.0)
    with pytest.raises(ValueError):
        render.renderizar_png(grupos, colores, foco=(0.0, 0.0, 0.0), radio=0.1)


def test_captura_enfocada_pesa_menos_que_el_tope_png():
    grupos = {"caja": _caja_en((0.0, 0.0, 0.0), lado=10.0)}
    colores = {"caja": (0.9, 0.5, 0.2)}
    png, _caption = render.renderizar_png(grupos, colores, ancho=2000, alto=2000,
                                          foco=(0.0, 0.0, 0.0), radio=6.0)
    assert len(png) <= render.LIMITE_PNG_BYTES


def test_grupos_desde_formas_filtra_y_colorea_con_el_orden_completo():
    """Colores por el orden de TODAS las piezas (como `captura`); solo el par se tesela."""
    formas = {
        "base": kernel.make_box(2, 2, 2),
        "arm": kernel.make_box(2, 2, 2).translate((5, 0, 0)),
        "tapa": kernel.make_box(2, 2, 2).translate((0, 9, 0)),
    }
    grupos, colores, inciertos = render.grupos_desde_formas(formas, ["arm", "base"])
    assert set(grupos) == {"base", "arm"}
    assert list(colores) == ["base", "arm", "tapa"]
    assert colores["arm"] == render._color_por_indice(1)
    assert inciertos is False


def test_grupos_desde_formas_con_nombre_desconocido_es_error():
    with pytest.raises(ValueError):
        render.grupos_desde_formas({"base": kernel.make_box(2, 2, 2)}, ["nope"])


def test_tope_de_triangulos_sin_teselacion_que_baje_es_refusal(monkeypatch):
    """Sobre el tope incluso en teselación gruesa: error en español, nunca degradación silenciosa."""
    monkeypatch.setattr(render, "LIMITE_TRIANGULOS", 1)
    formas = {"base": kernel.make_box(2, 2, 2), "arm": kernel.make_box(2, 2, 2).translate((5, 0, 0))}
    with pytest.raises(ValueError, match="demasiados triangulos"):
        render.grupos_desde_formas(formas, ["base", "arm"])


def test_radio_por_defecto_es_medio_diagonal_con_minimo():
    # Cubo de 10 mm: diagonal 10*sqrt(3); radio por defecto = medio diagonal.
    grupos = {"caja": _caja_en((0.0, 0.0, 0.0), lado=10.0)}
    assert render.radio_por_defecto(grupos) == pytest.approx(0.5 * 10.0 * np.sqrt(3.0), rel=1e-6)
    assert render.radio_por_defecto({"caja": _caja_en((0.0, 0.0, 0.0), lado=0.4)}) == render.RADIO_MINIMO_MM


# --------------------------------------------------------------- REST

def _headers():
    return {"X-Forja-Token": auth.obtener_token()}


def _hashes(doc_id):
    dato = {}
    for directorio in (documents.DOCUMENTOS_DIR / doc_id,
                       documents.DOCUMENTOS_DIR / ".ensambles" / doc_id):
        for ruta in sorted(directorio.rglob("*")) if directorio.exists() else []:
            if ruta.is_file():
                dato[str(ruta)] = hashlib.sha256(ruta.read_bytes()).hexdigest()
    return dato


CHOQUE_SCRIPT = """from build123d import Box, Pos
PARAMETROS = {'offset': 14}
def construir(p):
    return {'base': Box(2, 2, 2), 'arm': Pos(p['offset'], 0, 0) * Box(2, 2, 2)}
"""
CHOQUE_JOINT = dict(id="deslizador", tipo="deslizamiento", padre="base", hijo="arm",
                    origen=[0, 0, 0], eje=[1, 0, 0], limites=[-20, 0], valor=0)
# El brazo empieza a 14 mm y se desliza hacia la base: el primer contacto es hacia -12.


@pytest.fixture
def documento_choque():
    client_test = TestClient(app)
    record = client_test.post("/documentos/script", json={"codigo": CHOQUE_SCRIPT}, headers=_headers())
    assert record.status_code == 200, record.text
    doc_id = record.json()["id"]
    definido = client_test.post(f"/documentos/{doc_id}/ensamble",
                                json={"articulaciones": [CHOQUE_JOINT]}, headers=_headers())
    assert definido.status_code == 200, definido.text
    yield client_test, doc_id
    client_test.delete(f"/documentos/{doc_id}", headers=_headers())


@pytest.fixture
def documento_caja():
    client_test = TestClient(app)
    record = client_test.post("/documentos/script", json={"codigo": CHOQUE_SCRIPT}, headers=_headers())
    assert record.status_code == 200, record.text
    doc_id = record.json()["id"]
    yield client_test, doc_id
    client_test.delete(f"/documentos/{doc_id}", headers=_headers())


def test_rest_captura_con_foco_responde_png_y_no_muta_nada(documento_caja):
    client_test, doc_id = documento_caja
    antes = _hashes(doc_id)
    respuesta = client_test.get(f"/documentos/{doc_id}/captura?ancho=300&alto=300&foco=0,0,0&radio=3")
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.headers["content-type"] == "image/png"
    assert "encuadre en foco (0, 0, 0) radio 3 mm" in respuesta.headers["X-Forja-Captura"]
    assert _hashes(doc_id) == antes


@pytest.mark.parametrize("query", [
    "foco=nan,0,0&radio=3",
    "foco=0,0&radio=3",
    "foco=0,0,0",
    "radio=3",
    "foco=0,0,0&radio=0.1",
    "foco=0,0,0&radio=inf",
])
def test_rest_captura_foco_o_radio_invalidos_son_400(documento_caja, query):
    client_test, doc_id = documento_caja
    respuesta = client_test.get(f"/documentos/{doc_id}/captura?{query}")
    assert respuesta.status_code == 400, respuesta.text


def test_rest_captura_en_choque_muestra_el_par_centrado_en_el_contacto(documento_choque):
    client_test, doc_id = documento_choque
    antes = _hashes(doc_id)
    barrido = client_test.post(f"/documentos/{doc_id}/ensamble/barrido", json={}).json()
    choque = barrido["barrido"]["deslizador"]["primer_choque"]
    assert choque is not None and set(choque["par"]) == {"base", "arm"}

    respuesta = client_test.get(
        f"/documentos/{doc_id}/ensamble/captura-choque",
        params={"articulacion": "deslizador", "valor": choque["valor"],
                "a": choque["par"][0], "b": choque["par"][1], "ancho": 300, "alto": 300})

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.headers["content-type"] == "image/png"
    assert respuesta.headers["Cache-Control"] == "no-store"
    assert respuesta.headers["X-Forja-Captura"].startswith("2 solido(s)")
    assert _bloque_central_es_pieza(respuesta.content), "el contacto no cae en el centro del encuadre"
    assert _hashes(doc_id) == antes


@pytest.mark.parametrize("params", [
    {"articulacion": "nope", "valor": -12, "a": "base", "b": "arm"},
    {"articulacion": "deslizador", "valor": 999, "a": "base", "b": "arm"},
    {"articulacion": "deslizador", "valor": "nan", "a": "base", "b": "arm"},
    {"articulacion": "deslizador", "valor": -12, "a": "base", "b": "base"},
    {"articulacion": "deslizador", "valor": -12, "a": "base", "b": "fantasma"},
])
def test_rest_captura_en_choque_entrada_invalida_es_422(documento_choque, params):
    client_test, doc_id = documento_choque
    respuesta = client_test.get(f"/documentos/{doc_id}/ensamble/captura-choque", params=params)
    assert respuesta.status_code == 422, respuesta.text


# --------------------------------------------------------------- MCP

def _backend_disponible() -> bool:
    try:
        httpx.get(f"{client.BASE_URL}/salud", timeout=2.0)
        return True
    except httpx.HTTPError:
        return False


pytestmark_mcp = pytest.mark.skipif(not _backend_disponible(),
                                    reason="requiere el backend vivo de Forja (uvicorn en :8000)")

_SCRIPT_MCP_CHOQUE = """from build123d import Box, Pos
resultado = {'base': Box(2, 2, 2), 'arm': Pos(14, 0, 0) * Box(2, 2, 2)}
"""
_ARTICULACION_MCP = dict(id="deslizador", tipo="deslizamiento", padre="base", hijo="arm",
                         origen=[0, 0, 0], eje=[1, 0, 0], limites=[-20, 0], valor=0)
_ARTICULACION_MCP_LEJOS = dict(_ARTICULACION_MCP, limites=[0, 20])


@pytestmark_mcp
def test_mcp_captura_foco_devuelve_imagen_y_rechaza_entradas_malas(token_vivo):
    creado = tools.ejecutar_script(_SCRIPT_MCP_CHOQUE, nombre="captura_foco_mcp")
    assert "error" not in creado, creado
    doc_id = creado["id"]
    bien = tools.captura(doc_id, ancho=200, alto=200, foco=[0.0, 0.0, 0.0], radio=3.0)
    assert isinstance(bien, list) and len(bien) == 2
    assert "encuadre en foco" in bien[1]
    assert tools.captura(doc_id, foco=[0.0, 0.0, 0.0])["error"] is True
    assert tools.captura(doc_id, radio=3.0)["error"] is True
    assert tools.captura(doc_id, foco=[0.0, float("nan"), 0.0], radio=3.0)["error"] is True
    assert tools.captura(doc_id, foco=[0.0, 0.0], radio=3.0)["error"] is True


@pytestmark_mcp
def test_mcp_ensamble_captura_en_choque_devuelve_una_imagen_del_primer_choque(token_vivo):
    creado = tools.ejecutar_script(_SCRIPT_MCP_CHOQUE, nombre="captura_choque_mcp")
    assert "error" not in creado, creado
    doc_id = creado["id"]
    assert tools.ensamble(doc_id, articulaciones=[dict(_ARTICULACION_MCP)])["articulaciones"]

    resultado = tools.ensamble(doc_id, barrido="todas", captura_en_choque=True)

    assert isinstance(resultado, list) and len(resultado) == 2, resultado
    reply = json.loads(resultado[1])
    assert reply["imagenes"] == 1
    assert set(reply["par"]) == {"base", "arm"}
    assert reply["articulacion"] == "deslizador"
    assert "png" not in reply


@pytestmark_mcp
def test_mcp_ensamble_captura_en_choque_sin_choque_no_da_imagen(token_vivo):
    creado = tools.ejecutar_script(_SCRIPT_MCP_CHOQUE, nombre="captura_sin_choque_mcp")
    doc_id = creado["id"]
    assert tools.ensamble(doc_id, articulaciones=[dict(_ARTICULACION_MCP_LEJOS)])["articulaciones"]

    resultado = tools.ensamble(doc_id, barrido="todas", captura_en_choque=True)

    assert isinstance(resultado, dict) and "error" not in resultado, resultado
    assert resultado["imagenes"] == 0
    assert "sin choque" in resultado["motivo"]


@pytestmark_mcp
def test_mcp_captura_en_choque_exige_barrido():
    assert tools.ensamble("no-existe", captura_en_choque=True)["error"] is True


BISAGRA_SCRIPT = """from build123d import Box, Pos
resultado = {'wall': Pos(0, 11, 0) * Box(40, 2, 40), 'arm': Pos(8, 0, 0) * Box(16, 2, 2)}
"""
BISAGRA_JOINT = dict(id="bisagra", tipo="giro", padre="wall", hijo="arm",
                     origen=[0, 0, 0], eje=[0, 0, 1], limites=[0, 90], valor=0)


def test_rest_captura_en_choque_bisagra_muestra_las_dos_piezas():
    """Con la vista por defecto (45/25) el brazo queda tapado por la pared; la captura
    elige otra vista donde se ven las dos piezas y no lo dice como aviso."""
    client_test = TestClient(app)
    record = client_test.post("/documentos/script", json={"codigo": BISAGRA_SCRIPT}, headers=_headers())
    doc_id = record.json()["id"]
    try:
        definido = client_test.post(f"/documentos/{doc_id}/ensamble",
                                    json={"articulaciones": [BISAGRA_JOINT]}, headers=_headers())
        assert definido.status_code == 200, definido.text
        barrido = client_test.post(f"/documentos/{doc_id}/ensamble/barrido", json={}).json()
        choque = barrido["barrido"]["bisagra"]["primer_choque"]
        assert choque is not None and set(choque["par"]) == {"wall", "arm"}

        respuesta = client_test.get(
            f"/documentos/{doc_id}/ensamble/captura-choque",
            params={"articulacion": "bisagra", "valor": choque["valor"],
                    "a": choque["par"][0], "b": choque["par"][1]})
    finally:
        client_test.delete(f"/documentos/{doc_id}", headers=_headers())

    assert respuesta.status_code == 200, respuesta.text
    caption = respuesta.headers["X-Forja-Captura"]
    assert caption.startswith("2 solido(s)")
    assert "tapada" not in caption
