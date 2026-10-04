"""fdm-B: test coupons (`app/cupon.py`, `POST /documentos/{id}/cupon`).

Synthetic assembly: a 100 mm shaft (r=4) inside a 10 mm bushing (bore r=4.2,
so 0.2 mm radial clearance, outer r=10) and a lid 50 mm away from both. The
coupon must hold the shaft cut to about the bushing's length plus margin and
the whole bushing, never the lid; the source document must stay untouched.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import auth
import cupon
import documents
import solids
from main import app

client = TestClient(app)

_SCRIPT = (
    "from build123d import Box, Cylinder, Pos\n"
    "buje = Pos(0, 0, 50) * (Cylinder(10, 10) - Cylinder(4.2, 12))\n"
    "resultado = {\n"
    "    'eje': Pos(0, 0, 50) * Cylinder(4, 100),\n"
    "    'buje': buje,\n"
    "    'tapa': Pos(60, 0, 50) * Box(20, 20, 4),\n"
    "}\n"
)


def _headers() -> dict[str, str]:
    return {"X-Forja-Token": auth.obtener_token()}


@pytest.fixture(scope="module")
def ensamble() -> str:
    resp = client.post("/documentos/script", json={"codigo": _SCRIPT, "nombre": "ensamble"}, headers=_headers())
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _huella(doc_id: str) -> tuple:
    return (documents._files[doc_id].read_bytes(), solids.cargar(doc_id), dict(documents._registry[doc_id]))


def _bbox(piezas: list[dict], nombre: str) -> list[float]:
    return next(p["bbox"] for p in piezas if p["nombre"] == nombre)


def test_cupon_automatico_eje_y_buje_sin_tapa(ensamble):
    antes = _huella(ensamble)
    resp = client.post(f"/documentos/{ensamble}/cupon", json={}, headers=_headers())
    assert resp.status_code == 200, resp.text
    datos = resp.json()
    nombres = {p["nombre"] for p in datos["piezas"]}
    assert nombres == {"cupon_eje", "cupon_buje"}, datos
    assert len(datos["zonas"]) == 1 and datos["zonas"][0]["pares"][0]["dist"] == pytest.approx(0.2, abs=0.01)
    eje, buje = _bbox(datos["piezas"], "cupon_eje"), _bbox(datos["piezas"], "cupon_buje")
    # Shaft cut to bushing length (10) + 2 x (holgura 1 + margen 4); bushing whole.
    assert eje[5] - eje[4] == pytest.approx(20.0, abs=0.05)
    assert buje[5] - buje[4] == pytest.approx(10.0, abs=0.05)
    assert buje[1] - buje[0] == pytest.approx(20.0, abs=0.05)
    # On the bed, side by side, not overlapping in XY.
    for b in (eje, buje):
        assert b[4] == pytest.approx(0.0, abs=1e-6)
    assert eje[1] <= buje[0] or buje[1] <= eje[0]

    nuevo = datos["id_nuevo"]
    assert nuevo != ensamble and documents._registry[nuevo]["nombre"].startswith("Cupón — ensamble")
    assert {e["nombre"] for e in solids.cargar(nuevo)} == nombres
    assert _huella(ensamble) == antes
    assert client.delete(f"/documentos/{nuevo}", headers=_headers()).status_code == 200


def test_cupon_pieza_sin_contacto_y_pieza_inexistente(ensamble):
    resp = client.post(f"/documentos/{ensamble}/cupon", json={"pieza": "tapa"}, headers=_headers())
    assert resp.status_code == 422 and "sin zonas" in resp.json()["detail"]
    resp = client.post(f"/documentos/{ensamble}/cupon", json={"pieza": "nada"}, headers=_headers())
    assert resp.status_code == 404


def test_cupon_caja_explicita(ensamble):
    caja = {"min": [-15, -15, 0], "max": [15, 15, 8]}
    resp = client.post(f"/documentos/{ensamble}/cupon", json={"caja": caja}, headers=_headers())
    assert resp.status_code == 200, resp.text
    datos = resp.json()
    assert {p["nombre"] for p in datos["piezas"]} == {"cupon_eje"}
    assert _bbox(datos["piezas"], "cupon_eje")[5] == pytest.approx(8.0, abs=0.05)
    client.delete(f"/documentos/{datos['id_nuevo']}", headers=_headers())
    malo = client.post(f"/documentos/{ensamble}/cupon", json={"caja": {"min": [0, 0, 0], "max": [0, 1, 1]}}, headers=_headers())
    assert malo.status_code == 422


def test_cupon_requiere_token(ensamble):
    assert client.post(f"/documentos/{ensamble}/cupon", json={}).status_code == 401


def test_caja_de_interes_conserva_pared():
    eje, buje = [-4, 4, -4, 4, 0, 100], [-10, 10, -10, 10, 45, 55]
    c = cupon.caja_de_interes(eje, buje, 1.0, 4.0)
    assert c[4:] == [40.0, 60.0]
    assert c[0] == -14 and c[1] == 14


def test_mcp_cupon_agrega_enlace_orca(monkeypatch):
    from mcp_server import client as mcp_client

    enviado = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"id_nuevo": "n1", "descarga": "/documentos/n1/descarga/Cupon_-_x.3mf", "piezas": [], "zonas": []}

    class _C:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, ruta, json=None, headers=None):
            enviado.update(ruta=ruta, json=json, con_token="X-Forja-Token" in (headers or {}))
            return _Resp()

    monkeypatch.setattr(mcp_client.httpx, "Client", _C)
    salida = mcp_client.cupon("abc", pieza="eje", margen=3.0)
    assert enviado == {"ruta": "/documentos/abc/cupon", "json": {"pieza": "eje", "margen": 3.0}, "con_token": True}
    assert salida["enlace_orca"].startswith("orcaslicer://open?file=")


@pytest.mark.parametrize("crudo", [
    '{"caja": {"min": [NaN, 0, 0], "max": [1, 1, 1]}}',
    '{"caja": {"min": [0, 0, 0], "max": [Infinity, 1, 1]}}',
    '{"caja": {"min": [0, 0, -Infinity], "max": [1, 1, 1]}}',
    '{"caja": {"min": [0, 0, 0], "max": [1500, 1, 1]}}',
])
def test_cupon_caja_no_finita_o_enorme_422(ensamble, crudo):
    h = {**_headers(), "Content-Type": "application/json"}
    resp = client.post(f"/documentos/{ensamble}/cupon", content=crudo, headers=h)
    assert resp.status_code == 422, resp.text


def test_colocar_envuelve_filas_y_avisa():
    from build123d import Box

    recortes = [(f"p{i}", f"p{i}", Box(60, 30, 10)) for i in range(5)]
    colocados = cupon.colocar(recortes)
    bbs = [f.bounding_box() for _n, _o, f in colocados]
    xs = [v for b in bbs for v in (b.min.X, b.max.X)]
    ys = [v for b in bbs for v in (b.min.Y, b.max.Y)]
    assert max(xs) - min(xs) <= cupon.ANCHO_FILA_MM
    assert max(ys) - min(ys) == pytest.approx(65.0, abs=1e-6)  # 2 filas: 30 + 5 + 30
    assert (min(xs) + max(xs)) / 2 == pytest.approx(110.0, abs=1e-6)
    assert all(b.min.Z == pytest.approx(0.0, abs=1e-6) for b in bbs)
    for i in range(len(bbs)):
        for j in range(i + 1, len(bbs)):
            a, b = bbs[i], bbs[j]
            assert a.max.X <= b.min.X + 1e-6 or b.max.X <= a.min.X + 1e-6 or a.max.Y <= b.min.Y + 1e-6 or b.max.Y <= a.min.Y + 1e-6
    assert cupon.avisos_colocacion(colocados) == []
    grande = cupon.colocar([("g", "g", Box(250, 10, 5))])
    avisos = cupon.avisos_colocacion(grande)
    assert avisos and avisos[0].startswith("g: 250x10")
