"""Phase 3 review fix: shared-token guard on the code-execution routes.

`POST /documentos/script` (arbitrary script execution) and `POST
/documentos/desde_ruta` (arbitrary path read) require the `X-Forja-Token`
header (see `app/auth.py`); `POST /documentos` (web UI upload) and every GET
route stay open, since the web viewer is reached from the user's phone on
the LAN (`192.168.1.50:8710`) without a token.
"""
from __future__ import annotations

import io

from fastapi.testclient import TestClient

import auth
from main import app

client = TestClient(app)

_SCRIPT = "from build123d import Box\nresultado = Box(1, 1, 1)\n"


def test_script_sin_token_401():
    resp = client.post("/documentos/script", json={"codigo": _SCRIPT})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "token requerido"


def test_script_con_token_incorrecto_401():
    resp = client.post(
        "/documentos/script",
        json={"codigo": _SCRIPT},
        headers={"X-Forja-Token": "token-incorrecto"},
    )
    assert resp.status_code == 401


def test_script_con_token_correcto_200():
    token = auth.obtener_token()
    resp = client.post(
        "/documentos/script",
        json={"codigo": _SCRIPT},
        headers={"X-Forja-Token": token},
    )
    assert resp.status_code == 200
    assert resp.json()["solidos"] == 1


def test_desde_ruta_sin_token_401():
    resp = client.post("/documentos/desde_ruta", json={"ruta": "/etc/passwd"})
    assert resp.status_code == 401


def test_desde_ruta_con_token_correcto_no_es_401():
    """Con token correcto la ruta ya no se rechaza por auth (puede seguir
    fallando por la validacion de raiz permitida, pero nunca con 401)."""
    token = auth.obtener_token()
    resp = client.post(
        "/documentos/desde_ruta",
        json={"ruta": "/etc/passwd"},
        headers={"X-Forja-Token": token},
    )
    assert resp.status_code != 401
    assert resp.status_code == 400  # fuera de ALLOWED_READ_ROOTS


def test_restaurar_sin_token_401():
    """POST /documentos/{id}/restaurar mutates state (overwrites the live
    file/notes) just like `/script`/`/desde_ruta`, so it must require the
    token too (Phase 4 fix-review: ADR-0005 applies to every write route,
    not just the two named in its original decision)."""
    token = auth.obtener_token()
    creado = client.post(
        "/documentos/script", json={"codigo": _SCRIPT}, headers={"X-Forja-Token": token}
    ).json()
    historial = client.get(f"/documentos/{creado['id']}/historial").json()
    snapshot_id = historial[0]["id"]

    resp = client.post(f"/documentos/{creado['id']}/restaurar", json={"snapshot": snapshot_id})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "token requerido"


def test_restaurar_con_token_correcto_200():
    token = auth.obtener_token()
    creado = client.post(
        "/documentos/script", json={"codigo": _SCRIPT}, headers={"X-Forja-Token": token}
    ).json()
    historial = client.get(f"/documentos/{creado['id']}/historial").json()
    snapshot_id = historial[0]["id"]

    resp = client.post(
        f"/documentos/{creado['id']}/restaurar",
        json={"snapshot": snapshot_id},
        headers={"X-Forja-Token": token},
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == creado["id"]


def test_eliminar_documento_sin_token_401():
    token = auth.obtener_token()
    creado = client.post(
        "/documentos/script", json={"codigo": _SCRIPT}, headers={"X-Forja-Token": token}
    ).json()
    resp = client.delete(f"/documentos/{creado['id']}")
    assert resp.status_code == 401


def test_eliminar_documento_con_token_correcto_200_y_ya_no_aparece_listado():
    token = auth.obtener_token()
    creado = client.post(
        "/documentos/script", json={"codigo": _SCRIPT}, headers={"X-Forja-Token": token}
    ).json()
    resp = client.delete(f"/documentos/{creado['id']}", headers={"X-Forja-Token": token})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    ids = [d["id"] for d in client.get("/documentos").json()]
    assert creado["id"] not in ids


def test_upload_no_requiere_token():
    """La subida del visor web (POST /documentos) sigue abierta sin token."""
    resp = client.post(
        "/documentos",
        files={"file": ("modelo.obj", io.BytesIO(b"no real"), "text/plain")},
    )
    # rechazado por formato no soportado (400), nunca por falta de token (401)
    assert resp.status_code == 400


def test_salud_no_requiere_token():
    assert client.get("/salud").status_code == 200


def test_listar_documentos_no_requiere_token():
    assert client.get("/documentos").status_code == 200
