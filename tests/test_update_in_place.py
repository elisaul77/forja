"""Phase 4 fix-review: tests for the `documento_id` update-in-place path
(`documents._actualizar_documento_desde_script`, backing `POST
/documentos/script` with a `documento_id`).

Covers the three acceptance angles the review flagged as untested:
success (the document is genuinely replaced in place, same id), notes
re-resolution across a rebuild that shifts unrelated face indices
(ADR-0003's "rebuild-and-resolve" contract, exercised end-to-end through
the HTTP API this time rather than `naming.py` directly), and a kernel
failure leaving the document byte-identical to before the attempt (never
partially written) plus a compact, well-understood history footprint.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

import auth
from documents import DOCUMENTOS_DIR
from main import app

client = TestClient(app)


def _headers() -> dict[str, str]:
    return {"X-Forja-Token": auth.obtener_token()}


_SCRIPT_CAJA = "from build123d import Box\nresultado = Box(20, 10, 5)\n"
_SCRIPT_CAJA_CON_AGUJERO = (
    "from build123d import Box, Cylinder\nresultado = Box(20, 10, 5) - Cylinder(1.0, 20)\n"
)
# Resta que se cancela por completo: 0 solidos -> el kernel rechaza el
# resultado (ADR-0003: nunca se acepta una figura invalida).
_SCRIPT_KERNEL_INVALIDO = (
    "from build123d import Box\nresultado = Box(10, 10, 10) - Box(20, 20, 20)\n"
)


def _crear_documento_caja() -> dict:
    resp = client.post(
        "/documentos/script", json={"codigo": _SCRIPT_CAJA}, headers=_headers()
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_actualizar_documento_por_script_reemplaza_en_el_mismo_id():
    creado = _crear_documento_caja()
    doc_id = creado["id"]
    assert creado["solidos"] == 1

    resp = client.post(
        "/documentos/script",
        json={"documento_id": doc_id, "codigo": _SCRIPT_CAJA_CON_AGUJERO},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    actualizado = resp.json()

    assert actualizado["id"] == doc_id
    assert actualizado["nombre"] == creado["nombre"]
    assert actualizado["solidos"] == 1
    # el agujero reduce el volumen respecto a la caja original.
    assert actualizado["volumen"] < creado["volumen"]

    # se conserva un unico documento con ese id (no crea uno nuevo).
    ids = [d["id"] for d in client.get("/documentos").json()]
    assert ids.count(doc_id) == 1


def test_actualizar_documento_por_script_reresuelve_nota_tras_cambio_no_relacionado():
    creado = _crear_documento_caja()
    doc_id = creado["id"]

    caras = client.get(f"/documentos/{doc_id}/caras").json()
    assert len(caras) == 6  # caja sin modificar: 6 caras planas
    cara_objetivo = caras[0]
    referencia = {
        "tipo": "cara",
        "id": cara_objetivo["id"],
        "punto": cara_objetivo["centroide"],
        "huella": {
            "tipo": "cara",
            "subtipo": cara_objetivo["tipo"],
            "centroide": cara_objetivo["centroide"],
            "direccion": cara_objetivo["normal"],
            "medida": cara_objetivo["area"],
        },
    }
    nota = client.post(
        f"/documentos/{doc_id}/notas",
        json={"comentario": "cara marcada", "referencia": referencia},
    ).json()
    assert nota["referencia_perdida"] is False

    # Reconstruccion: misma caja, con un agujero cilindrico que atraviesa un
    # eje distinto al de la cara etiquetada -> reordena los indices de cara
    # del kernel (6 -> 7) pero no toca la cara etiquetada (mismo patron que
    # tests/test_naming.py's rebuild-and-resolve acceptance).
    resp = client.post(
        "/documentos/script",
        json={"documento_id": doc_id, "codigo": _SCRIPT_CAJA_CON_AGUJERO},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text

    caras_tras_rebuild = client.get(f"/documentos/{doc_id}/caras").json()
    assert len(caras_tras_rebuild) == 7

    resumen = client.get(f"/documentos/{doc_id}/notas").json()["resumen"]
    assert len(resumen) == 1
    entrada = resumen[0]
    assert entrada["referencia_perdida"] is False
    assert entrada["ambigua"] is False
    # la nota sigue apuntando a una cara geometricamente identica a la
    # etiquetada originalmente (mismo centroide/area) -- nunca se pierde ni
    # se re-enlaza a una cara distinta.
    assert entrada["referencia"]["punto"] == cara_objetivo["centroide"]


def test_actualizar_documento_por_script_kernel_invalido_no_corrompe_estado():
    creado = _crear_documento_caja()
    doc_id = creado["id"]

    ruta_step = DOCUMENTOS_DIR / f"{doc_id}.step"
    contenido_antes = ruta_step.read_bytes()
    ruta_notas = DOCUMENTOS_DIR / f"{doc_id}.notas.json"
    notas_antes = ruta_notas.read_bytes() if ruta_notas.exists() else None
    historial_antes = client.get(f"/documentos/{doc_id}/historial").json()

    resp = client.post(
        "/documentos/script",
        json={"documento_id": doc_id, "codigo": _SCRIPT_KERNEL_INVALIDO},
        headers=_headers(),
    )
    assert resp.status_code == 422
    assert "script invalido" in resp.json()["detail"]

    # Estado byte-identico: el archivo STEP y las notas no cambiaron.
    assert ruta_step.read_bytes() == contenido_antes
    notas_despues = ruta_notas.read_bytes() if ruta_notas.exists() else None
    assert notas_despues == notas_antes

    # El registro en memoria tampoco cambio (mismo bbox/volumen/solidos).
    registro_despues = client.get(f"/documentos/{doc_id}").json()
    assert registro_despues == creado

    # El historial gana exactamente la instantanea "antes de intentar" que
    # ADR-0003 toma preventivamente -- nunca una entrada extra que sugiera
    # un cambio aceptado que en realidad fue rechazado.
    historial_despues = client.get(f"/documentos/{doc_id}/historial").json()
    assert len(historial_despues) == len(historial_antes) + 1
    assert historial_despues[:-1] == historial_antes


def test_actualizar_documento_por_script_requiere_token():
    creado = _crear_documento_caja()
    resp = client.post(
        "/documentos/script",
        json={"documento_id": creado["id"], "codigo": _SCRIPT_CAJA_CON_AGUJERO},
    )
    assert resp.status_code == 401


def test_actualizar_documento_por_script_id_inexistente_404():
    resp = client.post(
        "/documentos/script",
        json={"documento_id": "no-existe", "codigo": _SCRIPT_CAJA},
        headers=_headers(),
    )
    assert resp.status_code == 404
