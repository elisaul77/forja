"""Phase 5B tests: `captura` (real headless render), `check_colisiones`, and
`exportar(..., por_solido=True)` (`app/render.py`, `app/checks/colisiones.py`,
`app/export.py`).

Same in-process `TestClient` + isolated `FORJA_DATA_DIR` pattern as
`tests/test_solidos.py`.
"""
from __future__ import annotations

import io

from fastapi.testclient import TestClient
from PIL import Image

import auth
import export as export_solidos
import render
from main import app

client = TestClient(app)


def _headers() -> dict[str, str]:
    return {"X-Forja-Token": auth.obtener_token()}


def _crear(script: str) -> dict:
    resp = client.post("/documentos/script", json={"codigo": script}, headers=_headers())
    assert resp.status_code == 200, resp.text
    return resp.json()


_SCRIPT_DOS_CAJAS_SOLAPADAS = (
    "from build123d import Box, Pos\n"
    "resultado = {'caja_a': Box(10, 10, 10), 'caja_b': Pos(5, 0, 0) * Box(10, 10, 10)}\n"
)

_SCRIPT_CAJAS_TOCANDOSE = (
    "from build123d import Box, Pos\n"
    "resultado = {'caja_a': Box(10, 10, 10), 'caja_b': Pos(10, 0, 0) * Box(10, 10, 10)}\n"
)

_SCRIPT_PIEZA_FLOTANTE = (
    "from build123d import Box, Pos\n"
    "resultado = {\n"
    "    'caja_a': Box(10, 10, 10),\n"
    "    'caja_b': Pos(10, 0, 0) * Box(10, 10, 10),\n"
    "    'suelta': Pos(200, 0, 0) * Box(10, 10, 10),\n"
    "}\n"
)

_SCRIPT_PIEZA_PARTIDA = (
    "from build123d import Box, Pos\n"
    "fragmento = Box(5, 5, 5) + Pos(30, 0, 0) * Box(5, 5, 5)\n"
    "resultado = {'rota': fragmento}\n"
)

_SCRIPT_SILLAS_Y_MESA = (
    "from build123d import Box, Pos, Rotation\n"
    "def silla():\n"
    "    return Box(10, 8, 15)\n"
    "resultado = {\n"
    "    'silla_1': Pos(0, 0, 0) * silla(),\n"
    "    'silla_2': Pos(20, 0, 0) * silla(),\n"
    "    'silla_3': Pos(40, 0, 0) * Rotation(0, 0, 90) * silla(),\n"
    "    'silla_4': Pos(60, 0, 0) * Rotation(0, 0, 180) * silla(),\n"
    "    'mesa': Pos(100, 0, 0) * Box(30, 20, 22),\n"
    "}\n"
)


# --------------------------------------------------------------- captura


def test_captura_devuelve_png_valido_del_tamano_pedido():
    creado = _crear(_SCRIPT_DOS_CAJAS_SOLAPADAS)
    resp = client.get(f"/documentos/{creado['id']}/captura?ancho=400&alto=300")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "image/png"
    assert len(resp.content) <= render.LIMITE_PNG_BYTES

    imagen = Image.open(io.BytesIO(resp.content))
    assert imagen.format == "PNG"
    assert imagen.size == (400, 300)
    assert "X-Forja-Captura" in resp.headers
    assert "2 solido" in resp.headers["X-Forja-Captura"]


def test_captura_subconjunto_de_solidos():
    creado = _crear(_SCRIPT_PIEZA_FLOTANTE)
    resp = client.get(f"/documentos/{creado['id']}/captura?solidos=suelta")
    assert resp.status_code == 200, resp.text
    assert "1 solido" in resp.headers["X-Forja-Captura"]


def test_captura_nombre_de_solido_inexistente_400():
    creado = _crear(_SCRIPT_DOS_CAJAS_SOLAPADAS)
    resp = client.get(f"/documentos/{creado['id']}/captura?solidos=no-existe")
    assert resp.status_code == 400


def test_captura_documento_inexistente_404():
    resp = client.get("/documentos/no-existe/captura")
    assert resp.status_code == 404


# --------------------------------------------------------- check_colisiones


def test_colisiones_dos_cajas_solapadas_un_choque_con_volumen_analitico():
    creado = _crear(_SCRIPT_DOS_CAJAS_SOLAPADAS)
    resp = client.get(f"/documentos/{creado['id']}/colisiones")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is False
    assert len(data["choques"]) == 1
    choque = data["choques"][0]
    assert {choque["a"], choque["b"]} == {"caja_a", "caja_b"}
    # overlap: 5 x 10 x 10 = 500 mm3 (within 1%)
    assert abs(choque["vol"] - 500.0) / 500.0 < 0.01
    assert data["partidas"] == []


def test_colisiones_cajas_tocandose_sin_choque():
    creado = _crear(_SCRIPT_CAJAS_TOCANDOSE)
    resp = client.get(f"/documentos/{creado['id']}/colisiones")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["choques"] == []
    assert data["ok"] is True


def test_colisiones_pieza_flotante_detectada():
    creado = _crear(_SCRIPT_PIEZA_FLOTANTE)
    resp = client.get(f"/documentos/{creado['id']}/colisiones")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["flotantes"] == ["suelta"]
    assert data["ok"] is False


def test_colisiones_pieza_partida_en_varios_solidos():
    creado = _crear(_SCRIPT_PIEZA_PARTIDA)
    resp = client.get(f"/documentos/{creado['id']}/colisiones")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["partidas"] == [{"pieza": "rota", "solidos": 2}]
    assert data["ok"] is False


def test_colisiones_documento_stl_400():
    box_stl = client.post(
        "/documentos",
        files={"file": ("cubo.stl", io.BytesIO(_stl_de_caja()), "application/sla")},
    )
    doc_id = box_stl.json()["id"]
    resp = client.get(f"/documentos/{doc_id}/colisiones")
    assert resp.status_code == 400


def _stl_de_caja() -> bytes:
    from kernel import b123d_kernel, mesh

    box = b123d_kernel.make_box(6, 6, 6)
    tri_mesh = mesh.tessellate_to_trimesh(box, tolerance=0.05)
    return mesh.to_stl_bytes(tri_mesh)


# ------------------------------------------------------- exportar por_solido


def test_exportar_por_solido_dedupe_sillas_identicas():
    creado = _crear(_SCRIPT_SILLAS_Y_MESA)
    resp = client.get(f"/documentos/{creado['id']}/exportar?formato=stl&por_solido=true")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    archivos = data["archivos"]
    assert len(archivos) == 2, archivos

    grupos_de_nombres = sorted(sorted(nombres) for nombres in archivos.values())
    assert grupos_de_nombres == [
        ["mesa"],
        ["silla_1", "silla_2", "silla_3", "silla_4"],
    ]

    from pathlib import Path

    directorio = Path(data["directorio"])
    for archivo in archivos:
        ruta = directorio / archivo
        assert ruta.resolve().parent == directorio.resolve(), "archivo escapo del directorio de export"
        assert ruta.exists()
        assert ruta.stat().st_size > 0


def test_exportar_por_solido_requiere_step_con_solidos():
    box_stl = client.post(
        "/documentos",
        files={"file": ("cubo.stl", io.BytesIO(_stl_de_caja()), "application/sla")},
    )
    doc_id = box_stl.json()["id"]
    resp = client.get(f"/documentos/{doc_id}/exportar?formato=stl&por_solido=true")
    assert resp.status_code == 400


def test_nombre_traversal_es_imposible_ya_desde_la_creacion():
    """Defense in depth (Phase 5A review finding): a solid name is
    regex-validated at script-creation time, long before `app/export.py`
    ever builds a filename from it — a traversal-shaped name never even
    reaches the export path."""
    script = "from build123d import Box\nresultado = {'../evil': Box(1, 1, 1)}\n"
    resp = client.post("/documentos/script", json={"codigo": script}, headers=_headers())
    assert resp.status_code == 422


def test_formato_3mf_disponible_sin_networkx():
    """Phase 5C: 3MF is written by hand (no `networkx`), so the old 501
    path is gone -- see tests/test_parametros_fdm_3mf.py for the full
    round-trip."""
    creado = _crear(_SCRIPT_DOS_CAJAS_SOLAPADAS)
    resp = client.get(f"/documentos/{creado['id']}/exportar?formato=3mf")
    assert resp.status_code == 200, resp.text
    assert resp.json()["objetos"] == ["caja_a", "caja_b"]


def test_formato_no_soportado_400():
    creado = _crear(_SCRIPT_DOS_CAJAS_SOLAPADAS)
    resp = client.get(f"/documentos/{creado['id']}/exportar?formato=obj")
    assert resp.status_code == 400
    assert export_solidos.FORMATOS_SOPORTADOS == ("stl", "step", "3mf")
