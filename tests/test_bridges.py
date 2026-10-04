import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import bridges
from main import app


def test_suspension_mutations_require_the_token():
    """Phase 6.1 review follow-up: both suspension mutation routes are
    token-guarded, so a token-free LAN client can never reach the
    simulator's compute endpoints through Forja."""
    client = TestClient(app)
    for path, body in [("/puentes/suspension/pose", {"subida_mm": [1]}),
                       ("/puentes/suspension/punto", {"puntos": [[0, 0, 0]]})]:
        for supplied in ({}, {"X-Forja-Token": "incorrect"}):
            response = client.post(path, json=body, headers=supplied)
            assert response.status_code == 401, response.text


def test_mutating_simulator_routes_are_never_forwarded(monkeypatch):
    monkeypatch.setattr(httpx, "Client", lambda **kw: pytest.fail("network must not be touched"))
    for route in ["/cinematica", "/golpe", "/buscar", "/registro", "/piedra"]:
        with pytest.raises(HTTPException) as error:
            bridges._http("suspension", "POST", route, {})
        assert error.value.status_code == 400


def test_disabled_bridges_do_not_connect(monkeypatch):
    for service in ("suspension", "blender", "kybercore"):
        monkeypatch.setenv(f"FORJA_{service.upper()}_ENABLED", "0")
    monkeypatch.setattr(httpx, "Client", lambda **kw: pytest.fail("network must not be touched"))
    assert all(not item["habilitado"] for item in bridges.status().values())


def test_pose_uses_current_anchor_config_without_persistence(monkeypatch):
    calls = []
    def remote(service, method, path, payload=None):
        calls.append((method, path, payload))
        if path == "/config":
            return {"geo": {"Lb": 38.8, "bola": [0, 0, 0]}, "anclajes": [{"lado": "der", "chasis": [1,2,3], "caja": [4,5,6]}]}
        return {"signo_correcto_al_subir": True}
    monkeypatch.setattr(bridges, "_http", remote)
    assert bridges.suspension_pose(bridges.PoseRequest())["signo_correcto_al_subir"]
    assert [(a,b) for a,b,c in calls] == [("GET", "/config"), ("POST", "/pose")]
    assert calls[-1][2]["geo"]["Lb"] == 38.8


def test_unavailable_service_returns_actionable_error(monkeypatch):
    monkeypatch.setenv("FORJA_SUSPENSION_ENABLED", "1")
    def unavailable(**kwargs):
        raise httpx.ConnectError("offline")
    monkeypatch.setattr(httpx, "Client", unavailable)
    with pytest.raises(HTTPException) as error:
        bridges.suspension_config()
    assert error.value.status_code == 502


def test_pose_rejects_nonfinite_and_unbounded_batches():
    with pytest.raises(ValueError):
        bridges.PoseRequest(subida_mm=[float("nan")])
    with pytest.raises(ValueError):
        bridges.PoseRequest(subida_mm=list(range(32)))


# ---------------------------------------------------------------- Phase 6.4
# closing round: a non-finite number in the upstream BODY is refused as 502.
# It used to parse into `float('inf')`, which FastAPI could not serialise, so
# the agent got a 500 plus a `ValueError: Out of range float values are not
# JSON compliant: inf` traceback — the only shape still escaping the
# "degrade, never 500" contract. Bytes are stubbed verbatim because httpx's
# own `json=` refuses to *encode* a non-finite float.


class _RespuestaCruda:
    def __init__(self, cuerpo: str):
        self.cuerpo = cuerpo

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        return None

    def iter_bytes(self):
        yield self.cuerpo.encode("utf-8")


class _ClienteFalso:
    def __init__(self, cuerpo: str):
        self.cuerpo = cuerpo

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def stream(self, *args, **kwargs):
        return _RespuestaCruda(self.cuerpo)


_CUERPO_BIEN_FORMADO = (
    '{"geo": {"theta_marcha": 27.5, "L_min": 56.0, "L_max": 65.8},'
    ' "anclajes": [{"lado": "der", "chasis": [1, 2, 3], "caja": [4, 5, 6]}]}'
)


@pytest.mark.parametrize(
    "cuerpo",
    [
        '{"subida_rueda": [{"mm": 4, "der": {"L": Infinity, "compresion": 0.0}}]}',
        '{"subida_rueda": [{"mm": 4, "der": {"L": -Infinity, "compresion": 0.0}}]}',
        '{"subida_rueda": [{"mm": 4, "der": {"L": NaN, "compresion": 0.0}}]}',
        # `1e999` desborda a `inf` sin pasar por `parse_constant`.
        '{"subida_rueda": [{"mm": 1e999, "der": {"L": 60.0, "compresion": 0.0}}]}',
    ],
    ids=["infinito", "menos-infinito", "nan", "desbordamiento"],
)
def test_http_rechaza_no_finitos_del_simulador(monkeypatch, cuerpo):
    monkeypatch.setenv("FORJA_SUSPENSION_ENABLED", "1")
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: _ClienteFalso(cuerpo))
    with pytest.raises(HTTPException) as error:
        bridges._http("suspension", "GET", "/config")
    assert error.value.status_code == 502
    assert "no se pudo consultar suspension" in error.value.detail


def test_http_no_finito_no_escapa_como_500(monkeypatch):
    """The route-level proof: a 502 answer, not an escaping exception (the
    `TestClient` re-raises a server exception, so a 500 could not pass)."""
    monkeypatch.setenv("FORJA_SUSPENSION_ENABLED", "1")
    monkeypatch.setattr(httpx, "Client",
                        lambda **kwargs: _ClienteFalso('{"subida_rueda": [{"L": Infinity}]}'))
    respuesta = TestClient(app).get("/puentes/suspension/config")
    assert respuesta.status_code == 502
    assert "no se pudo consultar suspension" in respuesta.json()["detail"]
    assert set(respuesta.json()) == {"detail"}  # el error del puente, sin internals


def test_http_deja_pasar_un_cuerpo_bien_formado(monkeypatch):
    """Control: the guard must not touch a payload the simulator really sends."""
    monkeypatch.setenv("FORJA_SUSPENSION_ENABLED", "1")
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: _ClienteFalso(_CUERPO_BIEN_FORMADO))
    assert bridges._http("suspension", "GET", "/config")["geo"]["L_max"] == 65.8
    assert TestClient(app).get("/puentes/suspension/config").json()["anclajes"][0]["lado"] == "der"
