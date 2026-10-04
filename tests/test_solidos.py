"""Phase 5A tests: named solids (`app/solids.py`), `ejecutar_script`'s
`ruta`/`variables`/`documento_id`, and structured error line-numbers
(`app/scripts_runner.py`, `documents.py`'s `POST /documentos/script`).

Uses the in-process `TestClient` against the isolated `FORJA_DATA_DIR`
(`tests/conftest.py`), same pattern as `tests/test_update_in_place.py`.
`/data/fuentes` itself is the container's real (read-only) mount and is
unaffected by that override, so the `ruta` tests below can safely point at
it without touching `FORJA_DATA_DIR`.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

import auth
import documents
import solids
from documents import DOCUMENTOS_DIR
from main import app

client = TestClient(app)


def _headers() -> dict[str, str]:
    return {"X-Forja-Token": auth.obtener_token()}


_SCRIPT_DICT_SIMPLE = (
    "from build123d import Box\n"
    "resultado = {'silla_1': Box(12, 12, 20), 'mesa': Box(30, 20, 22)}\n"
)

_RUTA_CASA_V1 = "/data/fuentes/forja/casa-munecas/casa_v1.py"


def test_dict_script_nombra_solidos_en_resumen():
    resp = client.post("/documentos/script", json={"codigo": _SCRIPT_DICT_SIMPLE}, headers=_headers())
    assert resp.status_code == 200, resp.text
    creado = resp.json()
    assert creado["solidos"] == 2

    resumen = client.get(f"/documentos/{creado['id']}").json()
    detalle = resumen["solidos_detalle"]
    nombres = {fila["nombre"] for fila in detalle["lista"]}
    assert nombres == {"silla_1", "mesa"}
    assert detalle["astillas"] == []
    assert "mas" not in detalle


def test_dict_script_nombre_invalido_422():
    script = "from build123d import Box\nresultado = {'silla malA!': Box(10, 10, 10)}\n"
    resp = client.post("/documentos/script", json={"codigo": script}, headers=_headers())
    assert resp.status_code == 422
    assert "nombre" in resp.json()["detail"].lower()


def test_dict_script_vacio_rechazado():
    resp = client.post("/documentos/script", json={"codigo": "resultado = {}\n"}, headers=_headers())
    assert resp.status_code == 422


def test_astilla_no_flaguea_por_tamano_relativo_a_otro_solido():
    """Fix-review: the original rule ("volume negligible next to the
    biggest solid in the document") flagged 14/21 legitimate furniture
    pieces in a real dollhouse scene — a chair is tiny next to a house, but
    that alone is not a defect. A fully-solid small/thin box must NOT be
    flagged just for being small; only a genuinely near-hollow sliver is
    (see the next test)."""
    script = (
        "from build123d import Box\n"
        "resultado = {'grande': Box(100, 100, 100), 'chip_solido': Box(50, 50, 0.02)}\n"
    )
    resp = client.post("/documentos/script", json={"codigo": script}, headers=_headers())
    assert resp.status_code == 200, resp.text
    doc_id = resp.json()["id"]
    detalle = client.get(f"/documentos/{doc_id}").json()["solidos_detalle"]
    assert detalle["astillas"] == []


def test_astilla_detectada_por_delgadez_y_hueco_no_por_regla_absoluta():
    """A genuinely near-hollow sliver (a thin frame that fills well under
    2% of its own bounding box, smallest bbox dimension well under 1 mm)
    IS flagged — with a volume deliberately kept above
    `UMBRAL_ASTILLA_MM3_POR_DEFECTO` (1.0 mm3) so this specifically
    exercises the thinness rule, not the absolute-floor one."""
    script = (
        "from build123d import Box\n"
        "resultado = {\n"
        "    'grande': Box(100, 100, 100),\n"
        "    'chip_solido': Box(50, 50, 0.02),\n"
        "    'residuo': Box(100, 100, 0.05) - Box(99.8, 99.8, 0.05),\n"
        "}\n"
    )
    resp = client.post("/documentos/script", json={"codigo": script}, headers=_headers())
    assert resp.status_code == 200, resp.text
    doc_id = resp.json()["id"]
    detalle = client.get(f"/documentos/{doc_id}").json()["solidos_detalle"]
    assert detalle["astillas"] == ["residuo"]


def test_astillas_no_flaguean_piezas_legitimas_solo_fragmento_partido():
    """Regression requested by the fix-review: several small legit named
    solids (none flagged) plus one named piece that splits into 2
    disconnected fragments — only the smaller fragment is flagged, the
    larger one (still called `pieza_partida`, same name) is not."""
    script = (
        "from build123d import Box, Pos, Compound\n"
        "resultado = {\n"
        "    'silla_1': Box(12, 12, 20),\n"
        "    'mesa': Box(30, 20, 22),\n"
        "    'estante': Box(40, 10, 2),\n"
        "    'pieza_partida': Compound(children=[Box(5, 5, 5), Pos(50, 0, 0) * Box(2, 2, 2)]),\n"
        "}\n"
    )
    resp = client.post("/documentos/script", json={"codigo": script}, headers=_headers())
    assert resp.status_code == 200, resp.text
    doc_id = resp.json()["id"]
    assert resp.json()["solidos"] == 5  # 1 + 1 + 1 + 2

    detalle = client.get(f"/documentos/{doc_id}").json()["solidos_detalle"]
    assert detalle["astillas"] == ["pieza_partida"]
    nombres_en_lista = [fila["nombre"] for fila in detalle["lista"]]
    assert nombres_en_lista.count("pieza_partida") == 2  # ambos fragmentos siguen listados


def test_solids_resumen_placa_delgada_tipo_alfombra_no_es_astilla():
    """Unit-level check straight against `solids.resumen` (no build123d
    needed): a rug modelled as a hollowed picture-frame (44x28x0.8 mm bbox,
    38x22 mm cutout -> ~32% fill ratio) must not be flagged just for being
    thin (0.8 mm) — its fill ratio is nowhere near the 2% "nearly hollow"
    threshold."""
    entradas = [
        {"nombre": "alfombra", "indice": 0, "bbox": [-22.0, 22.0, -14.0, 14.0, 0.0, 0.8], "volumen": 316.8},
        {"nombre": "otro", "indice": 1, "bbox": [0.0, 30.0, 0.0, 20.0, 0.0, 22.0], "volumen": 13200.0},
    ]
    detalle = solids.resumen(entradas)
    assert detalle["astillas"] == []


def test_solids_resumen_residuo_delgado_y_hueco_se_flaguea_sin_regla_absoluta():
    entradas = [
        # bbox 100x100x0.05 mm (dim minima 0.05mm < 1mm), volumen 5 mm3
        # (por encima del umbral absoluto 1.0) pero solo ~1% de su propia
        # caja -> residuo booleano casi hueco, no una lamina solida legitima.
        {"nombre": "residuo", "indice": 0, "bbox": [0.0, 100.0, 0.0, 100.0, 0.0, 0.05], "volumen": 5.0},
        {"nombre": "silla_1", "indice": 1, "bbox": [0.0, 12.0, 0.0, 12.0, 0.0, 20.0], "volumen": 2880.0},
    ]
    detalle = solids.resumen(entradas)
    assert detalle["astillas"] == ["residuo"]


def test_solido_unico_mantiene_resumen_compacto():
    resp = client.post(
        "/documentos/script", json={"codigo": "from build123d import Box\nresultado = Box(5, 5, 5)\n"},
        headers=_headers(),
    )
    doc_id = resp.json()["id"]
    registro = client.get(f"/documentos/{doc_id}").json()
    assert set(registro.keys()) == {"id", "nombre", "bbox", "volumen", "solidos", "valido", "revision"}


def test_solidos_lista_capada_con_cola_mas():
    piezas = ", ".join(f"'p{i}': Box(2, 2, 2)" for i in range(35))
    script = f"from build123d import Box\nresultado = {{{piezas}}}\n"
    resp = client.post("/documentos/script", json={"codigo": script}, headers=_headers())
    assert resp.status_code == 200, resp.text
    doc_id = resp.json()["id"]
    detalle = client.get(f"/documentos/{doc_id}").json()["solidos_detalle"]
    assert len(detalle["lista"]) == 30
    assert detalle["mas"] == 5


def test_nombres_sobreviven_a_reload_simulando_reinicio():
    """A container restart wipes the in-memory registry (`_registry`/
    `_files`) but never the files on `DOCUMENTOS_DIR` — including the
    `{doc_id}.solidos.json` sidecar `crear_documento_desde_script` just
    wrote. `recargar_documentos` must leave that sidecar exactly as-is
    (never recompute it from scratch: the STEP file's own labels can't be
    trusted post-hoc, see `app/solids.py`'s docstring) so "silla_1"/"mesa"
    survive the restart."""
    resp = client.post("/documentos/script", json={"codigo": _SCRIPT_DICT_SIMPLE}, headers=_headers())
    doc_id = resp.json()["id"]

    documents._registry.pop(doc_id, None)
    documents._files.pop(doc_id, None)

    documents.recargar_documentos()

    detalle = client.get(f"/documentos/{doc_id}").json()["solidos_detalle"]
    nombres = {fila["nombre"] for fila in detalle["lista"]}
    assert nombres == {"silla_1", "mesa"}


def test_solido_sin_sidecar_previo_recibe_desglose_plano_al_recargar():
    """A STEP predating this phase (no `.solidos.json` sidecar at all) gets
    bootstrapped with a flat `solido_N` breakdown on reload, instead of
    staying with no `solidos_detalle` forever."""
    resp = client.post(
        "/documentos/script",
        json={"codigo": "from build123d import Box, Cylinder\nresultado = Box(20, 20, 5) - Cylinder(1, 10)\n"},
        headers=_headers(),
    )
    doc_id = resp.json()["id"]
    assert client.get(f"/documentos/{doc_id}").json()["solidos"] == 1

    documents._registry.pop(doc_id, None)
    documents._files.pop(doc_id, None)
    solids.borrar(doc_id)  # simulates a document that predates this phase

    documents.recargar_documentos()

    registro = client.get(f"/documentos/{doc_id}").json()
    assert "solidos_detalle" not in registro  # solo 1 solido -> se mantiene compacto


def test_restaurar_recupera_los_nombres_de_ese_snapshot():
    """`solidos.json` is snapshotted/restored alongside the STEP file and
    `notas.json` — restoring to a point where the document had named solids
    must bring those names back too, not leave the (different-geometry)
    sidecar of whatever was live moments before restoring."""
    resp = client.post("/documentos/script", json={"codigo": _SCRIPT_DICT_SIMPLE}, headers=_headers())
    doc_id = resp.json()["id"]
    historial = client.get(f"/documentos/{doc_id}/historial").json()
    snapshot_con_nombres = historial[-1]["id"]

    resp2 = client.post(
        "/documentos/script",
        json={"documento_id": doc_id, "codigo": "from build123d import Box\nresultado = Box(9, 9, 9)\n"},
        headers=_headers(),
    )
    assert resp2.status_code == 200, resp2.text
    assert "solidos_detalle" not in client.get(f"/documentos/{doc_id}").json()

    resp3 = client.post(
        f"/documentos/{doc_id}/restaurar", json={"snapshot": snapshot_con_nombres}, headers=_headers()
    )
    assert resp3.status_code == 200, resp3.text

    detalle = client.get(f"/documentos/{doc_id}").json()["solidos_detalle"]
    nombres = {fila["nombre"] for fila in detalle["lista"]}
    assert nombres == {"silla_1", "mesa"}


def test_documento_id_actualiza_solidos_en_su_sitio():
    resp = client.post("/documentos/script", json={"codigo": _SCRIPT_DICT_SIMPLE}, headers=_headers())
    doc_id = resp.json()["id"]

    script2 = "from build123d import Box\nresultado = {'silla_1': Box(12, 12, 20)}\n"
    resp2 = client.post(
        "/documentos/script",
        json={"documento_id": doc_id, "codigo": script2},
        headers=_headers(),
    )
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["solidos"] == 1
    registro = client.get(f"/documentos/{doc_id}").json()
    assert "solidos_detalle" not in registro  # un solo solido -> compacto


def test_variables_llegan_al_script():
    script = "from build123d import Box\nresultado = Box(LADO, LADO, LADO)\n"
    resp = client.post(
        "/documentos/script",
        json={"codigo": script, "variables": {"LADO": 7}},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    assert abs(resp.json()["volumen"] - 7**3) < 1e-6


def test_nombres_json_plantado_por_script_malicioso_no_se_confia():
    """Fix-review security finding: the sandboxed script runs in the same
    process/filesystem as the trusted wrapper, so nothing stops it from
    reading `sys.argv` itself and planting a crafted `.nombres.json`
    sidecar next to the STEP output BEFORE the wrapper's own (conditional,
    dict-only) write ever runs. `resultado` here is a plain `Compound`
    (never a dict), so the wrapper never touches that path itself -- the
    planted file, containing a path-injection name, must still never reach
    `solidos.json`."""
    script = (
        "import sys, json\n"
        "from pathlib import Path\n"
        "salida = Path(sys.argv[2])\n"
        "ruta_nombres = salida.parent / (salida.stem + '.nombres.json')\n"
        "ruta_nombres.write_text(json.dumps(['../evil']))\n"
        "from build123d import Box, Compound\n"
        "resultado = Compound(children=[Box(10, 10, 10)])\n"
    )
    resp = client.post("/documentos/script", json={"codigo": script}, headers=_headers())
    assert resp.status_code == 200, resp.text
    doc_id = resp.json()["id"]
    assert resp.json()["solidos"] == 1

    registro = client.get(f"/documentos/{doc_id}").json()
    assert "solidos_detalle" not in registro  # 1 solo solido -> compacto de todas formas

    entradas = solids.cargar(doc_id)
    nombres = {e["nombre"] for e in entradas} if entradas else set()
    assert "../evil" not in nombres
    assert nombres == {"solido_1"}  # rechazado por completo -> vuelve al fallback plano


def test_leer_nombres_confiables_rechaza_tipos_y_nombres_invalidos(tmp_path):
    """Unit-level coverage of `scripts_runner._leer_nombres_confiables`
    beyond the end-to-end attack scenario above: wrong JSON type, non-string
    entries, and a syntactically-valid-JSON-but-unsafe name must all fall
    back to `None`, never partially trusted."""
    import scripts_runner

    casos_invalidos = [
        '{"no": "es una lista"}',
        "[1, 2, 3]",
        '["ok", 2]',
        '["../evil"]',
        '["nombre valido", "otro/invalido"]',
        "no es json",
    ]
    for i, contenido in enumerate(casos_invalidos):
        ruta = tmp_path / f"caso_{i}.json"
        ruta.write_text(contenido)
        assert scripts_runner._leer_nombres_confiables(ruta) is None, contenido

    ruta_valida = tmp_path / "valido.json"
    ruta_valida.write_text('["silla_1", "mesa"]')
    assert scripts_runner._leer_nombres_confiables(ruta_valida) == ["silla_1", "mesa"]


def test_error_reporta_linea_y_codigo_linea():
    script = "from build123d import Box\nx = 1\nresultado = 1 / 0\n"
    resp = client.post("/documentos/script", json={"codigo": script}, headers=_headers())
    assert resp.status_code == 422
    detalle = resp.json()["detail"]
    assert isinstance(detalle, dict)
    assert detalle["linea"] == 3
    assert detalle["codigo_linea"] == "resultado = 1 / 0"
    assert "mensaje" in detalle and "Traceback" not in detalle["mensaje"]


def test_error_sin_linea_atribuible_sigue_siendo_string():
    # 0 solidos tras la resta: rechazado por el kernel, no por una excepcion
    # dentro del script -- sigue siendo un `detail` de texto plano.
    script = "from build123d import Box\nresultado = Box(10, 10, 10) - Box(20, 20, 20)\n"
    resp = client.post("/documentos/script", json={"codigo": script}, headers=_headers())
    assert resp.status_code == 422
    assert isinstance(resp.json()["detail"], str)


def test_ruta_dentro_de_fuentes_permitida():
    resp = client.post("/documentos/script", json={"ruta": _RUTA_CASA_V1}, headers=_headers())
    assert resp.status_code == 200, resp.text
    assert resp.json()["solidos"] == 1


def test_ruta_dentro_de_documentos_data_permitida():
    ruta_script = DOCUMENTOS_DIR / "script_valido.py"
    ruta_script.write_text("from build123d import Box\nresultado = Box(3, 3, 3)\n")
    try:
        resp = client.post("/documentos/script", json={"ruta": str(ruta_script)}, headers=_headers())
        assert resp.status_code == 200, resp.text
    finally:
        ruta_script.unlink(missing_ok=True)


def test_ruta_traversal_rechazada():
    resp = client.post(
        "/documentos/script",
        json={"ruta": "/data/fuentes/forja/casa-munecas/../../../etc/passwd"},
        headers=_headers(),
    )
    assert resp.status_code == 400


def test_ruta_absoluta_fuera_de_raices_rechazada():
    resp = client.post("/documentos/script", json={"ruta": "/etc/passwd"}, headers=_headers())
    assert resp.status_code == 400


def test_ruta_symlink_escapando_raiz_rechazada():
    enlace = DOCUMENTOS_DIR / "escape_symlink.py"
    enlace.symlink_to("/etc/passwd")
    try:
        resp = client.post("/documentos/script", json={"ruta": str(enlace)}, headers=_headers())
        assert resp.status_code == 400
    finally:
        enlace.unlink(missing_ok=True)


def test_ruta_y_codigo_exactamente_uno_requerido():
    resp = client.post("/documentos/script", json={}, headers=_headers())
    assert resp.status_code == 400

    resp2 = client.post(
        "/documentos/script",
        json={"codigo": "resultado = 1", "ruta": _RUTA_CASA_V1},
        headers=_headers(),
    )
    assert resp2.status_code == 400
