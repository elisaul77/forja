"""Phase 4 notes/pins ("notas") and whiteboard strokes ("trazos") tests."""
from __future__ import annotations

import io

from fastapi.testclient import TestClient

import auth
from kernel import b123d_kernel, mesh
from main import app

client = TestClient(app)


def _crear_documento_stl() -> str:
    box = b123d_kernel.make_box(10, 10, 10)
    tri_mesh = mesh.tessellate_to_trimesh(box, tolerance=0.05)
    stl_bytes = mesh.to_stl_bytes(tri_mesh)
    resp = client.post(
        "/documentos", files={"file": ("cubo_notas.stl", io.BytesIO(stl_bytes), "application/sla")}
    )
    assert resp.status_code == 200
    return resp.json()["id"]


_REF_PUNTO = {"tipo": "punto", "punto": [1.0, 2.0, 3.0]}


def test_crear_nota_y_listar_resumen():
    doc_id = _crear_documento_stl()
    resp = client.post(
        f"/documentos/{doc_id}/notas",
        json={"comentario": "revisar este borde", "referencia": _REF_PUNTO},
    )
    assert resp.status_code == 200, resp.text
    nota = resp.json()
    assert nota["comentario"] == "revisar este borde"
    assert nota["referencia"]["tipo"] == "punto"
    assert nota["visible"] is True
    assert nota["referencia_perdida"] is False

    resp = client.get(f"/documentos/{doc_id}/notas")
    assert resp.status_code == 200
    resumen = resp.json()["resumen"]
    assert len(resumen) == 1
    entrada = resumen[0]
    assert entrada["tipo"] == "nota"
    assert entrada["n"] == nota["n"]
    assert "puntos" not in entrada  # sin detalle=true


def test_crear_nota_documento_inexistente_404():
    resp = client.post(
        "/documentos/no-existe/notas",
        json={"comentario": "x", "referencia": _REF_PUNTO},
    )
    assert resp.status_code == 404


def test_crear_nota_referencia_invalida_422():
    doc_id = _crear_documento_stl()
    resp = client.post(
        f"/documentos/{doc_id}/notas",
        json={"comentario": "x", "referencia": {"tipo": "planeta", "punto": [0, 0, 0]}},
    )
    assert resp.status_code == 422


def test_crear_nota_comentario_demasiado_largo_422():
    doc_id = _crear_documento_stl()
    resp = client.post(
        f"/documentos/{doc_id}/notas",
        json={"comentario": "x" * 5000, "referencia": _REF_PUNTO},
    )
    assert resp.status_code == 422


def test_borrar_nota():
    doc_id = _crear_documento_stl()
    nota = client.post(
        f"/documentos/{doc_id}/notas",
        json={"comentario": "temporal", "referencia": _REF_PUNTO},
    ).json()

    resp = client.delete(f"/documentos/{doc_id}/notas/{nota['n']}")
    assert resp.status_code == 200

    resumen = client.get(f"/documentos/{doc_id}/notas").json()["resumen"]
    assert resumen == []


def test_borrar_nota_inexistente_404():
    doc_id = _crear_documento_stl()
    resp = client.delete(f"/documentos/{doc_id}/notas/no-existe")
    assert resp.status_code == 404


def test_actualizar_visibilidad_nota():
    doc_id = _crear_documento_stl()
    nota = client.post(
        f"/documentos/{doc_id}/notas",
        json={"comentario": "temporal", "referencia": _REF_PUNTO},
    ).json()

    resp = client.patch(f"/documentos/{doc_id}/notas/{nota['n']}", json={"visible": False})
    assert resp.status_code == 200
    assert resp.json()["visible"] is False


def test_crear_trazo_y_listar_resumen_con_detalle():
    doc_id = _crear_documento_stl()
    body = {
        "tipo": "anadir",
        "comentario": "engordar aqui",
        "puntos": [[0, 0, 0], [1, 0, 0], [1, 1, 0]],
        "plano_origen": [0, 0, 0],
        "plano_normal": [0, 0, 1],
    }
    resp = client.post(f"/documentos/{doc_id}/trazos", json=body)
    assert resp.status_code == 200, resp.text
    trazo = resp.json()
    assert trazo["tipo"] == "anadir"
    assert len(trazo["puntos"]) == 3

    resumen_detalle = client.get(f"/documentos/{doc_id}/notas?detalle=true").json()["resumen"]
    assert len(resumen_detalle) == 1
    assert resumen_detalle[0]["puntos"] == body["puntos"]

    resumen_compacto = client.get(f"/documentos/{doc_id}/notas").json()["resumen"]
    entrada = resumen_compacto[0]
    assert entrada["tipo"] == "anadir"
    assert entrada["puntos_resumen"]["n_puntos"] == 3
    assert "puntos" not in entrada


def test_crear_trazo_tipo_invalido_422():
    doc_id = _crear_documento_stl()
    body = {
        "tipo": "no-es-un-tipo",
        "puntos": [[0, 0, 0]],
        "plano_origen": [0, 0, 0],
        "plano_normal": [0, 0, 1],
    }
    resp = client.post(f"/documentos/{doc_id}/trazos", json=body)
    assert resp.status_code == 422


def test_crear_trazo_sin_puntos_422():
    doc_id = _crear_documento_stl()
    body = {
        "tipo": "quitar",
        "puntos": [],
        "plano_origen": [0, 0, 0],
        "plano_normal": [0, 0, 1],
    }
    resp = client.post(f"/documentos/{doc_id}/trazos", json=body)
    assert resp.status_code == 422


def test_borrar_trazo():
    doc_id = _crear_documento_stl()
    trazo = client.post(
        f"/documentos/{doc_id}/trazos",
        json={
            "tipo": "medida",
            "puntos": [[0, 0, 0], [1, 1, 1]],
            "plano_origen": [0, 0, 0],
            "plano_normal": [0, 0, 1],
        },
    ).json()
    resp = client.delete(f"/documentos/{doc_id}/trazos/{trazo['n']}")
    assert resp.status_code == 200
    assert client.get(f"/documentos/{doc_id}/notas").json()["resumen"] == []


def test_notas_persisten_en_disco():
    from documents import DOCUMENTOS_DIR

    doc_id = _crear_documento_stl()
    client.post(
        f"/documentos/{doc_id}/notas",
        json={"comentario": "persistente", "referencia": _REF_PUNTO},
    )
    ruta = DOCUMENTOS_DIR / f"{doc_id}.notas.json"
    assert ruta.exists()
    assert "persistente" in ruta.read_text()


def test_restaurar_a_snapshot_anterior_a_las_notas_las_deja_vacias():
    doc_id = _crear_documento_stl()
    historial_inicial = client.get(f"/documentos/{doc_id}/historial").json()
    snapshot_sin_notas = historial_inicial[0]["id"]

    client.post(
        f"/documentos/{doc_id}/notas",
        json={"comentario": "se debe perder al restaurar", "referencia": _REF_PUNTO},
    )
    assert len(client.get(f"/documentos/{doc_id}/notas").json()["resumen"]) == 1

    resp = client.post(
        f"/documentos/{doc_id}/restaurar",
        json={"snapshot": snapshot_sin_notas},
        headers={"X-Forja-Token": auth.obtener_token()},
    )
    assert resp.status_code == 200, resp.text
    assert client.get(f"/documentos/{doc_id}/notas").json()["resumen"] == []


def test_notas_mutacion_crea_entrada_de_historial():
    doc_id = _crear_documento_stl()
    antes = len(client.get(f"/documentos/{doc_id}/historial").json())
    client.post(
        f"/documentos/{doc_id}/notas",
        json={"comentario": "genera historial", "referencia": _REF_PUNTO},
    )
    despues = client.get(f"/documentos/{doc_id}/historial").json()
    assert len(despues) == antes + 1


def test_resumen_filtra_claves_internas_de_referencia_anidada():
    """Phase 5A re-review follow-up: `resumen()` must strip
    `{"huella", "referencia_perdida", "ambigua"}` from the nested
    `referencia` sub-dict, not only `"huella"` — defensive, since no current
    write path actually nests those two, but any stored/legacy data that
    does must never leak them back out (they belong on the note/stroke
    itself, the single top-level source of truth)."""
    import notes

    doc_id = _crear_documento_stl()
    datos = notes.cargar(doc_id)
    datos["notas"].append(
        {
            "n": "legado1",
            "comentario": "nota con referencia anidada corrupta",
            "referencia": {
                "tipo": "cara",
                "id": "abc",
                "punto": [0.0, 0.0, 0.0],
                "huella": {"tipo": "cara"},
                "referencia_perdida": True,
                "ambigua": True,
            },
            "referencia_perdida": True,
            "ambigua": True,
            "visible": True,
        }
    )
    notes.guardar(doc_id, datos)

    resumen = client.get(f"/documentos/{doc_id}/notas").json()["resumen"]
    entrada = next(e for e in resumen if e["n"] == "legado1")
    assert set(entrada["referencia"].keys()) == {"tipo", "id", "punto"}
    # las banderas de nivel superior (la unica fuente de verdad) se conservan.
    assert entrada["referencia_perdida"] is True
    assert entrada["ambigua"] is True
