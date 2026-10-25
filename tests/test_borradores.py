"""Borradores que caducan (galeria ordenada, fase 2).

- `POST /documentos/script` sin `documento_id` y sin `permanente=true` crea un
  BORRADOR: fuera de `GET /documentos`, visible con `?borradores=true`.
- Caduca 24 h (`FORJA_BORRADOR_HORAS`) desde la ultima modificacion; al caducar
  va a la papelera (nunca borrado definitivo) con `motivo: borrador_expirado`.
- El barrido corre al arrancar, en cada listado y en una tarea periodica.
- `conservar_documento` / `POST /documentos/{id}/conservar` lo hace permanente;
  `guardar_diseno` tambien. Sin estado = permanente (sin migracion).
- MCP: el nombre se reutiliza comparando sin `.step` y sin mayusculas, y con
  dos o mas coincidencias se actualiza la mas nueva en vez de crear otra.

Las pruebas en proceso usan el `FORJA_DATA_DIR` temporal de `conftest.py`. Las
del MCP van en proceso tambien (`_ClienteEnProceso`), asi no tocan el backend
vivo ni el token real.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import auth
import documents
import papelera
import papelera_routes
from main import app
from mcp_server import client as mcp_client
from mcp_server import tools

client = TestClient(app)
DIR = documents.DOCUMENTOS_DIR
FIXTURE_STEP = Path(__file__).parent / "fixtures" / "tuerca_p5.step"
CODIGO = "from build123d import Box\nresultado = Box(10, 10, 10)\n"
CODIGO_B = "from build123d import Box\nresultado = Box(12, 8, 6)\n"


def _cab() -> dict[str, str]:
    return {"X-Forja-Token": auth.obtener_token()}


def _script(codigo: str = CODIGO, **campos) -> dict:
    resp = client.post("/documentos/script", json={"codigo": codigo, **campos}, headers=_cab())
    assert resp.status_code == 200, resp.text
    return resp.json()


def _galeria(**query) -> list[dict]:
    resp = client.get("/documentos", params=query)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _envejecer(doc_id: str, horas: float) -> None:
    """Pone la ultima modificacion `horas` atras: mtime del archivo y marca
    `creado` del sidecar (la caducidad es el maximo de ambos)."""
    antiguo = time.time() - horas * 3600
    os.utime(documents._files[doc_id], (antiguo, antiguo))
    ruta = papelera.ruta_estado(DIR, doc_id)
    if ruta.exists():
        estado = papelera.leer_estado(DIR, doc_id)
        estado["creado"] = datetime.fromtimestamp(antiguo, timezone.utc).isoformat()
        papelera.guardar_estado(DIR, doc_id, estado)


def _en_papelera(doc_id: str) -> bool:
    resp = client.get("/papelera", headers=_cab())
    assert resp.status_code == 200, resp.text
    return doc_id in [d["id"] for d in resp.json()]


def _purgar(doc_id: str) -> None:
    """Hard delete si esta vivo; si no, purga desde la papelera. Un fallo es error."""
    if client.delete(f"/documentos/{doc_id}", headers=_cab()).status_code == 200:
        return
    resp = client.delete(f"/papelera/{doc_id}", headers=_cab())
    assert resp.status_code == 200, resp.text


@pytest.fixture
def creados():
    ids: list[str] = []
    yield ids
    for doc_id in ids:
        _purgar(doc_id)


class _ClienteEnProceso:
    """Sustituto de `httpx.Client` que habla con la app en proceso (sin backend
    vivo, con el token de prueba). Lo usa `mcp_server/client.py` en estas pruebas."""

    def __init__(self, base_url=None, headers=None, **_ignorado):
        self._c = TestClient(app, headers=headers or {})

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def __getattr__(self, nombre):
        return getattr(self._c, nombre)


@pytest.fixture
def mcp_en_proceso(monkeypatch):
    monkeypatch.setattr(mcp_client.httpx, "Client", _ClienteEnProceso)


# ------------------------------------------------------------ borrador y listado

def test_script_sin_id_crea_borrador_oculto(creados):
    creado = _script(nombre="borrador_oculto")
    creados.append(creado["id"])
    assert creado["borrador"] is True
    assert creado["expira"]
    assert "conservar_documento" in creado["mensaje"]
    assert creado["id"] not in [d["id"] for d in _galeria()]
    entrada = next(d for d in _galeria(borradores="true") if d["id"] == creado["id"])
    assert entrada["borrador"] is True and entrada["expira"] == creado["expira"]


def test_permanente_true_no_es_borrador(creados):
    creado = _script(nombre="permanente_si", permanente=True)
    creados.append(creado["id"])
    assert "borrador" not in creado
    assert creado["id"] in [d["id"] for d in _galeria()]
    entrada = next(d for d in _galeria(borradores="true") if d["id"] == creado["id"])
    assert "borrador" not in entrada


def test_documento_subido_sin_estado_es_permanente_y_listado(creados):
    resp = client.post("/documentos", files={"file": ("pieza.step", FIXTURE_STEP.read_bytes(), "application/step")})
    assert resp.status_code == 200, resp.text
    doc_id = resp.json()["id"]
    creados.append(doc_id)
    assert not papelera.ruta_estado(DIR, doc_id).exists()
    assert doc_id in [d["id"] for d in _galeria()]


def test_actualizar_por_id_mantiene_borrador_y_desliza_la_caducidad(creados):
    creado = _script(nombre="borrador_que_se_itera")
    creados.append(creado["id"])
    _envejecer(creado["id"], 20)
    antes = next(d for d in _galeria(borradores="true") if d["id"] == creado["id"])["expira"]

    _script(codigo=CODIGO_B, documento_id=creado["id"])

    despues = next(d for d in _galeria(borradores="true") if d["id"] == creado["id"])
    assert creado["id"] not in [d["id"] for d in _galeria()]
    assert despues["expira"] > antes


def test_horas_por_entorno(monkeypatch, caplog):
    monkeypatch.delenv("FORJA_BORRADOR_HORAS", raising=False)
    assert papelera.horas_borrador() == 24
    monkeypatch.setenv("FORJA_BORRADOR_HORAS", "2")
    assert papelera.horas_borrador() == 2
    # Valor invalido: vuelve al por defecto y deja un aviso en el log.
    for crudo in ["no", "-1", "0", "nan", "inf"]:
        caplog.clear()
        monkeypatch.setenv("FORJA_BORRADOR_HORAS", crudo)
        assert papelera.horas_borrador() == 24, crudo
        assert "FORJA_BORRADOR_HORAS" in caplog.text, crudo


def test_entorno_invalido_no_tumba_el_listado(monkeypatch, caplog, creados):
    creado = _script(nombre="listado_con_entorno_invalido")
    creados.append(creado["id"])
    monkeypatch.setenv("FORJA_BORRADOR_HORAS", "no")
    with caplog.at_level(logging.WARNING):
        visibles = _galeria(borradores="true")
    assert creado["id"] in [d["id"] for d in visibles]
    assert "FORJA_BORRADOR_HORAS" in caplog.text


# ------------------------------------------------------------ conservar

def test_conservar_quita_el_borrador_y_es_idempotente(creados):
    creado = _script(nombre="a_conservar")
    creados.append(creado["id"])

    primera = client.post(f"/documentos/{creado['id']}/conservar", headers=_cab())
    segunda = client.post(f"/documentos/{creado['id']}/conservar", headers=_cab())

    assert primera.status_code == 200, primera.text
    assert primera.json()["borrador"] is False
    assert segunda.status_code == 200, segunda.text
    assert not papelera.ruta_estado(DIR, creado["id"]).exists()
    assert creado["id"] in [d["id"] for d in _galeria()]


def test_conservar_exige_token_y_rechaza_id_desconocido():
    assert client.post(f"/documentos/{'a' * 32}/conservar").status_code == 401
    assert client.post(f"/documentos/{'a' * 32}/conservar", headers=_cab()).status_code == 404


def test_guardar_diseno_promueve_un_borrador(creados):
    creado = _script(nombre="diseno_promueve")
    creados.append(creado["id"])
    assert creado["id"] not in [d["id"] for d in _galeria()]
    contexto = client.get(f"/documentos/{creado['id']}/design").json()

    resp = client.put(
        f"/documentos/{creado['id']}/design",
        json={k: contexto[k] for k in ["revision", "state_revision", "design"]},
        headers=_cab(),
    )

    assert resp.status_code == 200, resp.text
    assert creado["id"] in [d["id"] for d in _galeria()]


# ------------------------------------------------------------ barrido

def test_barrido_manda_a_papelera_solo_los_borradores_vencidos(creados):
    vencido = _script(nombre="vencido")
    creados.append(vencido["id"])
    fresco = _script(nombre="fresco")
    creados.append(fresco["id"])
    permanente = _script(nombre="permanente_viejo", permanente=True)
    creados.append(permanente["id"])
    _envejecer(vencido["id"], 25)
    _envejecer(permanente["id"], 500)

    enviados = papelera_routes.barrer_borradores()

    assert vencido["id"] in enviados
    assert fresco["id"] not in enviados and permanente["id"] not in enviados
    assert _en_papelera(vencido["id"])
    assert vencido["id"] not in [d["id"] for d in _galeria(borradores="true")]
    assert papelera.leer_estado(DIR, vencido["id"])["motivo"] == "borrador_expirado"
    assert (DIR / f"{vencido['id']}.step").exists(), "caducar no borra archivos"
    assert vencido["id"] not in papelera_routes.barrer_borradores()


def test_listado_barre_antes_de_listar(creados):
    vencido = _script(nombre="vencido_al_listar")
    creados.append(vencido["id"])
    _envejecer(vencido["id"], 25)

    assert vencido["id"] not in [d["id"] for d in _galeria(borradores="true")]
    assert _en_papelera(vencido["id"])


def test_horas_cortas_por_entorno_caducan_antes(creados, monkeypatch):
    creado = _script(nombre="caduca_en_dos_horas")
    creados.append(creado["id"])
    monkeypatch.setenv("FORJA_BORRADOR_HORAS", "2")
    _envejecer(creado["id"], 3)

    assert creado["id"] not in [d["id"] for d in _galeria(borradores="true")]
    assert _en_papelera(creado["id"])


def test_arranque_barre_los_vencidos(creados):
    vencido = _script(nombre="vencido_al_arrancar")
    creados.append(vencido["id"])
    _envejecer(vencido["id"], 25)

    with TestClient(app):
        pass  # lifespan: recargar_documentos + barrido inicial

    assert _en_papelera(vencido["id"])


def test_barrido_periodico_por_defecto_cada_15_minutos(monkeypatch, caplog):
    monkeypatch.delenv("FORJA_BARRIDO_MIN", raising=False)
    assert papelera.barrido_minutos() == 15
    monkeypatch.setenv("FORJA_BARRIDO_MIN", "abc")
    assert papelera.barrido_minutos() == 15
    assert "FORJA_BARRIDO_MIN" in caplog.text


# ------------------------------------------------------------ MCP

def test_mcp_nombre_con_o_sin_step_y_mayusculas_reutiliza_el_documento(mcp_en_proceso, creados):
    primero = mcp_client.ejecutar_script(CODIGO, nombre="pieza_reuso")
    creados.append(primero["id"])
    segundo = mcp_client.ejecutar_script(CODIGO_B, nombre="pieza_reuso.step")
    tercero = mcp_client.ejecutar_script(CODIGO, nombre="PIEZA_REUSO")

    assert segundo["id"] == primero["id"] and segundo["actualizado_por_nombre"] is True
    assert tercero["id"] == primero["id"]
    assert len([d for d in _galeria(borradores="true") if d["nombre"].startswith("pieza_reuso")]) == 1


def test_mcp_con_dos_coincidencias_actualiza_la_mas_nueva(mcp_en_proceso, creados):
    primero = _script(nombre="dup_reuso", permanente=True)
    creados.append(primero["id"])
    mas_nuevo = _script(nombre="dup_reuso", permanente=True)
    creados.append(mas_nuevo["id"])
    antes = len(_galeria(borradores="true"))

    resultado = mcp_client.ejecutar_script(CODIGO_B, nombre="dup_reuso")

    assert resultado["id"] == mas_nuevo["id"]
    assert len(_galeria(borradores="true")) == antes, "no debe crear un tercero"


def test_mcp_permanente_y_listado_con_borradores(mcp_en_proceso, creados):
    borrador = tools.ejecutar_script(CODIGO, nombre="mcp_borrador")
    creados.append(borrador["id"])
    permanente = tools.ejecutar_script(CODIGO, nombre="mcp_permanente", permanente=True)
    creados.append(permanente["id"])
    assert borrador["borrador"] is True and "borrador" not in permanente

    por_defecto = [d["id"] for d in tools.listar_documentos()]
    con_borradores = [d["id"] for d in tools.listar_documentos(incluir_borradores=True)]

    assert borrador["id"] not in por_defecto and permanente["id"] in por_defecto
    assert borrador["id"] in con_borradores


def test_mcp_conservar_documento(mcp_en_proceso, creados):
    borrador = tools.ejecutar_script(CODIGO, nombre="mcp_a_conservar")
    creados.append(borrador["id"])

    conservado = tools.conservar_documento(borrador["id"])

    assert conservado["borrador"] is False
    assert borrador["id"] in [d["id"] for d in tools.listar_documentos()]
