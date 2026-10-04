"""fdm-A tests: printer tolerance profiles (`app/perfil_fdm.py`,
`app/perfiles.py`), their REST routes, the calibration coupon and the
injection of `PERFIL` + `agujero()`/`eje()`/`ajuste()` into the sandbox."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import auth
import perfil_fdm
import perfiles
import scripts_runner
from main import app

client = TestClient(app)


def _headers() -> dict[str, str]:
    return {"X-Forja-Token": auth.obtener_token()}


@pytest.fixture(autouse=True)
def _perfiles_aislados(tmp_path, monkeypatch):
    monkeypatch.setattr(perfiles, "PERFILES_DIR", tmp_path / ".perfiles")
    yield


# ---------------------------------------------------------------- math


def test_compensacion_semilla():
    s = perfil_fdm.SEMILLA
    assert perfil_fdm.agujero(3, s) == pytest.approx(3.15)
    assert perfil_fdm.eje(5, s) == pytest.approx(4.95)
    assert perfil_fdm.ranura(2, s) == pytest.approx(2.1)
    assert perfil_fdm.ajuste("M3_pasante", perfil=s) == pytest.approx(3.55)
    assert perfil_fdm.ajuste("M3_roscado", perfil=s) == pytest.approx(2.65)
    assert perfil_fdm.ajuste("eje_deslizante", perfil=s) == pytest.approx(0.25)
    assert perfil_fdm.ajuste("eje_deslizante", 5, s) == pytest.approx(5.40)
    assert perfil_fdm.ajuste("eje_presion", 5, s) == pytest.approx(5.25)
    with pytest.raises(ValueError):
        perfil_fdm.ajuste("M7_raro", perfil=s)


def test_ajuste_lineal_y_compensacion_inversa():
    # Holes print 0.1 + 2 % of d small.
    medidas = [{"nominal": d, "medido": d - (0.1 + 0.02 * d)} for d in (3, 5, 8)]
    perfil = perfil_fdm.perfil_desde_mediciones(perfil_fdm.SEMILLA, {"agujeros": medidas})
    assert perfil["agujero"] == {"a": pytest.approx(-0.1), "b": pytest.approx(-0.02)}
    assert perfil["semilla"] is False
    m = perfil_fdm.agujero(5, perfil)
    assert m + perfil["agujero"]["a"] + perfil["agujero"]["b"] * m == pytest.approx(5, abs=1e-3)


def test_un_solo_diametro_da_offset_constante_y_ranura_media():
    perfil = perfil_fdm.perfil_desde_mediciones(perfil_fdm.SEMILLA, {
        "ejes": [{"nominal": 5, "medido": 5.1}, {"nominal": 5, "medido": 5.14}],
        "ranuras": [{"nominal": 2, "medido": 1.8}, {"nominal": 5, "medido": 4.9}],
        "holgura_deslizante": 0.3,
    })
    assert perfil["eje"] == {"a": pytest.approx(0.12), "b": 0.0}
    assert perfil["ranura_offset"] == pytest.approx(-0.15)
    assert perfil["holgura_deslizante"] == 0.3
    assert perfil["agujero"] == perfil_fdm.SEMILLA["agujero"]


def test_medicion_invalida():
    with pytest.raises(ValueError):
        perfil_fdm.perfil_desde_mediciones(perfil_fdm.SEMILLA, {"agujeros": [{"nominal": 3}]})


# ---------------------------------------------------------------- REST


def test_sin_perfiles_activo_es_la_semilla_y_get_no_escribe():
    resp = client.get("/perfiles")
    assert resp.status_code == 200
    datos = resp.json()
    assert datos["activo"] == perfil_fdm.SEMILLA["nombre"]
    assert datos["perfiles"][0]["semilla"] is True
    activo = client.get("/perfiles/activo").json()
    assert activo["semilla"] is True and activo["activo"] is True
    assert not perfiles.PERFILES_DIR.exists()
    assert client.get("/perfiles/no_existe").status_code == 404
    assert client.get("/perfiles/..%2Fx").status_code in (400, 404)


def test_post_requiere_token():
    assert client.post("/perfiles", json={"nombre": "x"}).status_code == 401
    assert client.post("/perfiles/probeta").status_code == 401


def test_post_guarda_atomico_y_activa():
    cuerpo = {
        "nombre": "petg_04", "material": "PETG", "altura_capa": 0.16,
        "mediciones": {"agujeros": [{"nominal": 3, "medido": 2.8}, {"nominal": 8, "medido": 7.7}],
                       "holgura_presion": 0.15},
    }
    resp = client.post("/perfiles", json=cuerpo, headers=_headers())
    assert resp.status_code == 200, resp.text
    perfil = resp.json()
    assert perfil["semilla"] is False and perfil["activo"] is True
    assert perfil["agujero"] == {"a": pytest.approx(-0.14), "b": pytest.approx(-0.02)}
    archivo = perfiles.PERFILES_DIR / "petg_04.json"
    assert json.loads(archivo.read_text())["material"] == "PETG"
    assert not list(perfiles.PERFILES_DIR.glob(".tmp-*"))
    assert client.get("/perfiles").json()["activo"] == "petg_04"
    assert perfiles.perfil_activo()["holgura_presion"] == 0.15
    # Second profile without activating keeps the first active.
    resp = client.post("/perfiles", json={"nombre": "otro", "activar": False}, headers=_headers())
    assert resp.status_code == 200 and resp.json()["activo"] is False
    assert perfiles.nombre_activo() == "petg_04"


def test_post_rechaza_nombre_y_medidas_invalidas():
    assert client.post("/perfiles", json={"nombre": "../x"}, headers=_headers()).status_code == 422
    malo = {"nombre": "ok", "mediciones": {"ranuras": [{"nominal": -1, "medido": 2}]}}
    assert client.post("/perfiles", json=malo, headers=_headers()).status_code == 422


# ---------------------------------------------------------------- sandbox


_SCRIPT_AGUJERO = '''\
from build123d import Box, Cylinder
assert isinstance(PERFIL, dict)
d = agujero(3)
resultado = Box(20, 20, 4) - Cylinder(d / 2, 10)
'''


def _diametro_agujero(salida) -> float:
    from build123d import import_step
    pieza = import_step(str(salida))
    caja = pieza.bounding_box()
    vol_agujero = caja.size.X * caja.size.Y * caja.size.Z - pieza.volume
    import math
    return 2 * math.sqrt(vol_agujero / (math.pi * 4))


def test_sandbox_inyecta_perfil_y_agujero():
    import shutil
    perfiles.guardar({**perfil_fdm.SEMILLA, "nombre": "prueba", "semilla": False,
                      "agujero": {"a": -0.3, "b": 0.0}})
    salida, _ = scripts_runner.ejecutar_script(_SCRIPT_AGUJERO, timeout=60)
    try:
        assert _diametro_agujero(salida) == pytest.approx(3.3, abs=0.01)
    finally:
        shutil.rmtree(salida.parent, ignore_errors=True)
    # A dict passed by the caller wins over the active profile.
    salida, _ = scripts_runner.ejecutar_script(
        _SCRIPT_AGUJERO, timeout=60, variables={"PERFIL": {"agujero": {"a": 0.0, "b": 0.0}}}
    )
    try:
        assert _diametro_agujero(salida) == pytest.approx(3.0, abs=0.01)
    finally:
        shutil.rmtree(salida.parent, ignore_errors=True)


def test_probeta_crea_documento_compacto():
    resp = client.post("/perfiles/probeta", headers=_headers())
    assert resp.status_code == 200, resp.text
    doc = resp.json()
    try:
        assert doc["nombre"].startswith("Calibración de tolerancias")
        assert doc["valido"] is True
        assert doc["solidos"] == 2
        xmin, xmax, ymin, ymax, zmin, zmax = doc["bbox"]
        assert xmax - xmin < 80 and ymax - ymin < 60 and zmax - zmin < 20
        detalle = client.get(f"/documentos/{doc['id']}").json()["solidos_detalle"]
        assert "probeta" in json.dumps(detalle) and "pin_5mm" in json.dumps(detalle)
    finally:
        client.delete(f"/documentos/{doc['id']}", headers=_headers())
