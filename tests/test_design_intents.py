"""Unified intents (ADR-0019): indicaciones ligadas a notas/trazos, mensajes,
atención derivada, rutas de usuario/agente y compatibilidad de design.json."""

import json

import pytest
import trimesh
from fastapi.testclient import TestClient

import auth, design_operations as ops, design_state as ds, eventos, notes
from main import app

client = TestClient(app)


def headers():
    return {"X-Forja-Token": auth.obtener_token()}


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def _doc():
    data = trimesh.creation.box(extents=[10, 12, 14]).export(file_type="stl")
    return ok(client.post("/documentos", files={"file": ("indic.stl", data)}))["id"]


def _nota(doc, punto=(1.0, 2.0, 3.0)):
    return ok(
        client.post(
            f"/documentos/{doc}/notas",
            json={
                "comentario": "algo",
                "referencia": {"tipo": "punto", "punto": list(punto)},
            },
        )
    )["n"]


def _indicacion(doc, **body):
    return ok(client.post(f"/documentos/{doc}/design/intents", json=body))


def _vista(doc, iid):
    lectura = ok(client.get(f"/documentos/{doc}/design"))
    return next(v for v in lectura["intents_view"] if v["id"] == iid), lectura


def _agente(doc, iid, **body):
    return client.post(
        f"/documentos/{doc}/design/intents/{iid}/agent", json=body, headers=headers()
    )


def _usuario(doc, iid, **body):
    return client.post(f"/documentos/{doc}/design/intents/{iid}/user", json=body)


def _preparar(doc, lectura, iid, resumen="Mueve la pieza 3 mm en X"):
    return client.post(
        f"/documentos/{doc}/design/candidates",
        headers=headers(),
        json={
            "revision": lectura["revision"],
            "state_revision": lectura["state_revision"],
            "request_id": "intent-" + iid,
            "operations": [
                {
                    "kind": "transform",
                    "piece": lectura["pieces"][0]["name"],
                    "translation": [3, 0, 0],
                }
            ],
            "intent": iid,
            "summary": resumen,
        },
    )


def test_crear_indicacion_es_abierta_y_queda_pendiente_del_agente():
    doc = _doc()
    created = _indicacion(doc, text="Quiero el agujero más grande")
    iid = created["created_intent"]
    view, lectura = _vista(doc, iid)
    assert view["attention"] == "waiting_agent"
    assert view["status"] == "pending"
    assert view["text"] == "Quiero el agujero más grande"
    assert lectura["design"]["intents"][0]["id"] == iid
    # `design` stays the raw saved model; the derived view lives beside it.
    assert "intents_view" not in lectura["design"]


def test_crear_indicacion_vincula_marca_y_region_y_sale_de_loose_marks():
    doc = _doc()
    marca = _nota(doc)
    loose = _nota(doc, (4.0, 4.0, 4.0))
    piece = ok(client.get(f"/documentos/{doc}/design"))["pieces"][0]["name"]
    iid = _indicacion(
        doc, text="", marks=[marca], piece=piece, points=[[1, 2, 3]]
    )["created_intent"]
    view, lectura = _vista(doc, iid)
    assert view["marks"] == [marca]
    assert [m["n"] for m in view["marks_detail"]] == [marca]
    assert view["missing_marks"] == []
    assert view["piece"] == piece
    assert view["region"]["revision"] == lectura["revision"]
    assert [m["n"] for m in lectura["loose_marks"]] == [loose]


def test_crear_indicacion_valida_marcas_piezas_y_textos():
    doc = _doc()
    assert _indicacion(doc, text="x")  # sanity
    r = client.post(f"/documentos/{doc}/design/intents", json={"text": "  "})
    assert r.status_code == 422
    r = client.post(
        f"/documentos/{doc}/design/intents", json={"text": "x", "marks": ["no-existe"]}
    )
    assert r.status_code == 422
    r = client.post(
        f"/documentos/{doc}/design/intents",
        json={"text": "x", "piece": "fantasma"},
    )
    assert r.status_code == 422
    r = client.post(
        f"/documentos/{doc}/design/intents", json={"text": "x", "points": [[0, 0, 0]]}
    )
    assert r.status_code == 422
    r = client.post("/documentos/no-existe/design/intents", json={"text": "x"})
    assert r.status_code == 404


def test_una_marca_no_puede_estar_en_dos_indicaciones():
    doc = _doc()
    marca = _nota(doc)
    primera = _indicacion(doc, text="uno", marks=[marca])["created_intent"]
    segunda = _indicacion(doc, text="dos")["created_intent"]
    assert _usuario(doc, segunda, action="attach", marks=[marca]).status_code == 409
    assert _usuario(doc, primera, action="attach", marks=[marca]).status_code == 200


def test_acciones_del_usuario_reply_edit_dismiss_reopen_resolve():
    doc = _doc()
    marca_a, marca_b = _nota(doc), _nota(doc, (9.0, 9.0, 9.0))
    iid = _indicacion(doc, text="inicial", marks=[marca_a])["created_intent"]

    assert _usuario(doc, iid, action="reply", text="   ").status_code == 422
    assert _usuario(doc, iid, action="attach").status_code == 422
    assert _usuario(doc, "fantasma", action="resolve").status_code == 404

    lectura = ok(_usuario(doc, iid, action="reply", text="Sí, en esa zona"))
    item = next(v for v in lectura["intents_view"] if v["id"] == iid)
    assert item["messages"][-1]["author"] == "user"
    assert item["messages"][-1]["text"] == "Sí, en esa zona"
    assert item["messages"][-1]["created"]

    lectura = ok(_usuario(doc, iid, action="edit", text="Agujero M4 más grande"))
    lectura = ok(_usuario(doc, iid, action="attach", marks=[marca_b]))
    item = next(v for v in lectura["intents_view"] if v["id"] == iid)
    assert item["text"] == "Agujero M4 más grande"
    assert item["marks"] == [marca_a, marca_b]

    lectura = ok(_usuario(doc, iid, action="dismiss"))
    assert next(v for v in lectura["intents_view"] if v["id"] == iid)["attention"] == "dismissed"
    lectura = ok(_usuario(doc, iid, action="reopen"))
    assert next(v for v in lectura["intents_view"] if v["id"] == iid)["status"] == "pending"
    lectura = ok(_usuario(doc, iid, action="resolve"))
    assert next(v for v in lectura["intents_view"] if v["id"] == iid)["attention"] == "resolved"


def test_ruta_del_agente_exige_token_y_no_resuelve():
    doc = _doc()
    iid = _indicacion(doc, text="Más grueso")["created_intent"]
    sin_token = client.post(
        f"/documentos/{doc}/design/intents/{iid}/agent", json={"interpretation": "x"}
    )
    assert sin_token.status_code == 401
    assert _agente(doc, iid).status_code == 422  # sin contenido
    lectura = ok(_agente(doc, iid, interpretation="Aumentar el espesor de pared"))
    item = next(v for v in lectura["intents_view"] if v["id"] == iid)
    assert item["interpretation"] == "Aumentar el espesor de pared"
    assert item["attention"] == "understood"
    assert item["status"] == "pending"
    assert item["messages"][-1]["author"] == "agent"
    assert _agente(doc, "fantasma", message="x").status_code == 404


def test_atencion_pregunta_respuesta_y_vuelta_al_agente():
    doc = _doc()
    iid = _indicacion(doc, text="Que encaje mejor")["created_intent"]
    lectura = ok(_agente(doc, iid, question="¿Qué tolerancia quieres?"))
    assert next(v for v in lectura["intents_view"] if v["id"] == iid)["attention"] == "question"
    ok(_usuario(doc, iid, action="reply", text="0,2 mm"))
    item, _ = _vista(doc, iid)
    assert item["attention"] == "waiting_agent"
    # The agent answers without a new question: the question is cleared.
    ok(_agente(doc, iid, interpretation="Holgura de 0,2 mm"))
    item, _ = _vista(doc, iid)
    assert item["attention"] == "understood"
    assert item["question"] is None


def test_propuesta_aplicada_y_ajuste_del_usuario_vuelve_al_agente():
    doc = _doc()
    iid = _indicacion(doc, text="Mueve 3 mm")["created_intent"]
    ok(_agente(doc, iid, interpretation="Traslado en X"))
    lectura = ok(client.get(f"/documentos/{doc}/design"))
    candidate = ok(_preparar(doc, lectura, iid))
    assert candidate["intent"] == iid
    assert candidate["summary"] == "Mueve la pieza 3 mm en X"
    item, _ = _vista(doc, iid)
    assert item["attention"] == "proposal"
    assert item["candidates"][0]["summary"] == "Mueve la pieza 3 mm en X"
    assert item["candidates"][0]["state"] == "prepared"
    assert item["candidates"][0]["checks"]["approval"] in ("pass", "needs_review", "not_requested")

    applied = ok(
        client.post(
            f"/documentos/{doc}/design/candidates/{candidate['id']}/apply",
            headers=headers(),
        )
    )
    assert applied["before_snapshot"]
    item, _ = _vista(doc, iid)
    assert item["attention"] == "applied"
    assert item["candidates"][0]["before_snapshot"] == applied["before_snapshot"]

    ok(_usuario(doc, iid, action="reply", text="Ahora está muy lejos"))
    item, _ = _vista(doc, iid)
    assert item["attention"] == "waiting_agent"


def test_preparar_con_indicacion_inexistente_es_422():
    doc = _doc()
    lectura = ok(client.get(f"/documentos/{doc}/design"))
    assert _preparar(doc, lectura, "fantasma").status_code == 422


def test_marca_con_referencia_perdida_o_borrada_se_marca_stale_o_missing():
    doc = _doc()
    marca = _nota(doc)
    borrada = _nota(doc, (5.0, 5.0, 5.0))
    iid = _indicacion(doc, text="x", marks=[marca, borrada])["created_intent"]
    assert _vista(doc, iid)[0]["stale"] is False

    datos = notes.cargar(doc)
    next(n for n in datos["notas"] if n["n"] == marca)["referencia_perdida"] = True
    notes.guardar(doc, datos)
    assert _vista(doc, iid)[0]["stale"] is True

    assert client.delete(f"/documentos/{doc}/notas/{borrada}").status_code == 200
    item, _ = _vista(doc, iid)
    assert item["missing_marks"] == [borrada]
    assert [m["n"] for m in item["marks_detail"]] == [marca]


def test_region_obsoleta_tras_aplicar_un_candidato_queda_stale():
    doc = _doc()
    piece = ok(client.get(f"/documentos/{doc}/design"))["pieces"][0]["name"]
    iid = _indicacion(doc, text="zona", piece=piece, points=[[0, 0, 0]])["created_intent"]
    assert _vista(doc, iid)[0]["stale"] is False
    lectura = ok(client.get(f"/documentos/{doc}/design"))
    candidate = ok(_preparar(doc, lectura, iid, resumen="Traslado"))
    ok(
        client.post(
            f"/documentos/{doc}/design/candidates/{candidate['id']}/apply",
            headers=headers(),
        )
    )
    assert _vista(doc, iid)[0]["stale"] is True


def test_restaurar_snapshot_previo_al_candidato_recupera_la_geometria():
    doc = _doc()
    original = ok(client.get(f"/documentos/{doc}/design"))["pieces"][0]["bbox"]
    iid = _indicacion(doc, text="mover")["created_intent"]
    lectura = ok(client.get(f"/documentos/{doc}/design"))
    candidate = ok(_preparar(doc, lectura, iid))
    applied = ok(
        client.post(
            f"/documentos/{doc}/design/candidates/{candidate['id']}/apply",
            headers=headers(),
        )
    )
    moved = ok(client.get(f"/documentos/{doc}/design"))["pieces"][0]["bbox"]
    assert moved[0] == pytest.approx(original[0] + 3, abs=1e-4)
    restored = client.post(
        f"/documentos/{doc}/restaurar",
        json={"snapshot": applied["before_snapshot"]},
        headers=headers(),
    )
    assert restored.status_code == 200, restored.text
    lectura = ok(client.get(f"/documentos/{doc}/design"))
    assert lectura["pieces"][0]["bbox"] == pytest.approx(original)
    # The intent saved before the candidate survives the restore.
    assert any(v["id"] == iid for v in lectura["intents_view"])


def test_crear_indicacion_registra_historial_antes_del_cambio():
    doc = _doc()
    antes = len(ok(client.get(f"/documentos/{doc}/historial")))
    _indicacion(doc, text="entrada de historial")
    despues = ok(client.get(f"/documentos/{doc}/historial"))
    assert len(despues) == antes + 1
    assert "indicación" in despues[-1]["mensaje"]


def test_design_json_antiguo_carga_sin_cambios(tmp_path):
    doc = _doc()
    antiguo = {
        "schema_version": 1,
        "objective": "Pieza previa",
        "parts": {},
        "intents": [
            {
                "id": "legado",
                "text": "formato anterior",
                "status": "accepted",
                "source": "user",
                "images": [],
                "region": None,
                "direction": None,
            }
        ],
        "requirements": [],
        "print_rotations": {},
    }
    ds.path(doc).write_text(json.dumps(antiguo))
    estado = ds.load(doc)
    intent = estado.intents[0]
    assert intent.marks == [] and intent.messages == []
    assert intent.interpretation is None and intent.question is None
    lectura = ok(client.get(f"/documentos/{doc}/design"))
    assert lectura["intents_view"][0]["attention"] == "resolved"
    assert lectura["design"]["intents"][0]["messages"] == []


def test_intent_nuevo_valida_limites():
    with pytest.raises(ValueError):
        ds.Intent(id="a", text="x", marks=["m" * 65])
    with pytest.raises(ValueError):
        ds.Intent(id="a", text="x", messages=[{"author": "robot", "text": "x", "created": "t"}])
    intent = ds.Intent(id="a", text="x", status="dismissed")
    assert intent.status == "dismissed"


def test_cilindro_con_dimension_y_cero_es_valido():
    doc = _doc()
    lectura = ok(client.get(f"/documentos/{doc}/design"))
    response = client.post(
        f"/documentos/{doc}/design/candidates",
        headers=headers(),
        json={
            "revision": lectura["revision"],
            "state_revision": lectura["state_revision"],
            "request_id": "cilindro-y0",
            "operations": [
                {
                    "kind": "union",
                    "piece": lectura["pieces"][0]["name"],
                    "primitive": "cylinder",
                    "size": [4, 0, 6],
                    "center": [0, 0, 0],
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "prepared"


def test_cilindro_con_diametro_cero_sigue_rechazado():
    doc = _doc()
    lectura = ok(client.get(f"/documentos/{doc}/design"))
    response = client.post(
        f"/documentos/{doc}/design/candidates",
        headers=headers(),
        json={
            "revision": lectura["revision"],
            "state_revision": lectura["state_revision"],
            "request_id": "cilindro-d0",
            "operations": [
                {
                    "kind": "union",
                    "piece": lectura["pieces"][0]["name"],
                    "primitive": "cylinder",
                    "size": [0, 0, 6],
                    "center": [0, 0, 0],
                }
            ],
        },
    )
    assert response.status_code == 422


def _aplicar(doc, key):
    return client.post(
        f"/documentos/{doc}/design/candidates/{key}/apply", headers=headers()
    )


def _candidato_vista(doc, iid):
    return _vista(doc, iid)[0]["candidates"][0]


def test_intencion_no_relacionada_no_invalida_el_candidato():
    doc = _doc()
    iid = _indicacion(doc, text="Mover en X")["created_intent"]
    lectura = ok(client.get(f"/documentos/{doc}/design"))
    candidate = ok(_preparar(doc, lectura, iid))
    otra = _indicacion(doc, text="Otra cosa sin relación")["created_intent"]
    ok(_usuario(doc, otra, action="reply", text="Respuesta del usuario"))
    ok(_agente(doc, otra, interpretation="Lectura de la otra indicación"))
    assert _candidato_vista(doc, iid)["stale"] is False
    applied = _aplicar(doc, candidate["id"])
    assert applied.status_code == 200, applied.text
    assert applied.json()["state"] == "applied"


def test_cambio_de_requisito_invalida_el_candidato_y_apply_da_409():
    doc = _doc()
    iid = _indicacion(doc, text="Mover en X")["created_intent"]
    lectura = ok(client.get(f"/documentos/{doc}/design"))
    candidate = ok(_preparar(doc, lectura, iid))
    assert _candidato_vista(doc, iid)["stale"] is False

    lectura = ok(client.get(f"/documentos/{doc}/design"))
    design = lectura["design"]
    design["requirements"] = [
        {"id": "closed", "kind": "closed", "piece": lectura["pieces"][0]["name"]}
    ]
    saved = client.put(
        f"/documentos/{doc}/design",
        json={
            "revision": lectura["revision"],
            "state_revision": lectura["state_revision"],
            "design": design,
        },
        headers=headers(),
    )
    assert saved.status_code == 200, saved.text
    assert _candidato_vista(doc, iid)["stale"] is True
    assert _aplicar(doc, candidate["id"]).status_code == 409


def test_manifest_legado_sin_constraints_revision_sigue_protegido():
    doc = _doc()
    iid = _indicacion(doc, text="Mover en X")["created_intent"]
    lectura = ok(client.get(f"/documentos/{doc}/design"))
    candidate = ok(_preparar(doc, lectura, iid))
    path, manifest = ops.candidate(doc, candidate["id"])
    manifest.pop("constraints_revision", None)
    (path / "manifest.json").write_text(json.dumps(manifest))

    # Legacy fallback: any full-state change blocks the apply...
    _indicacion(doc, text="cambio de estado")
    assert _aplicar(doc, candidate["id"]).status_code == 409
    # ...and the candidate view reports it as stale.
    assert _candidato_vista(doc, iid)["stale"] is True


def test_manifest_legado_sin_cambios_se_aplica():
    doc = _doc()
    iid = _indicacion(doc, text="Mover en X")["created_intent"]
    lectura = ok(client.get(f"/documentos/{doc}/design"))
    candidate = ok(_preparar(doc, lectura, iid))
    path, manifest = ops.candidate(doc, candidate["id"])
    manifest.pop("constraints_revision", None)
    (path / "manifest.json").write_text(json.dumps(manifest))
    assert _aplicar(doc, candidate["id"]).status_code == 200


def _foto(doc, color=(0, 0, 255)):
    import io
    from PIL import Image

    # Distinct content per photo: assets are keyed by content hash.
    buf = io.BytesIO()
    Image.new("RGB", (30, 20), color).save(buf, format="PNG")
    return ok(
        client.post(
            f"/documentos/{doc}/design/images",
            headers=headers(),
            files={"file": ("foto.png", buf.getvalue(), "image/png")},
        )
    )["id"]


def test_attach_fotos_a_indicacion_existente():
    doc = _doc()
    iid = _indicacion(doc, text="Como esta foto")["created_intent"]
    foto = _foto(doc)
    lectura = ok(_usuario(doc, iid, action="attach", images=[foto]))
    item = next(v for v in lectura["intents_view"] if v["id"] == iid)
    assert item["images"] == [foto]
    assert any(i["id"] == foto for i in lectura["images"])


def test_attach_fotos_valida_existencia_y_maximo_seis():
    doc = _doc()
    iid = _indicacion(doc, text="fotos")["created_intent"]
    assert _usuario(doc, iid, action="attach", images=["no-existe"]).status_code == 422
    assert _usuario(doc, iid, action="attach").status_code == 422
    fotos = [_foto(doc, (i * 30, 0, 255)) for i in range(7)]
    assert _usuario(doc, iid, action="attach", images=fotos[:6]).status_code == 200
    assert _usuario(doc, iid, action="attach", images=fotos[6:]).status_code == 422
    item, _ = _vista(doc, iid)
    assert len(item["images"]) == 6


def test_lista_de_candidatos_expone_stale_con_la_regla_nueva():
    doc = _doc()
    iid = _indicacion(doc, text="Mover en X")["created_intent"]
    lectura = ok(client.get(f"/documentos/{doc}/design"))
    candidate = ok(_preparar(doc, lectura, iid))
    listed = ok(client.get(f"/documentos/{doc}/design/candidates"))["candidates"]
    assert listed[0]["stale"] is False
    _usuario(doc, iid, action="reply", text="algo nuevo")
    listed = ok(client.get(f"/documentos/{doc}/design/candidates"))["candidates"]
    assert next(c for c in listed if c["id"] == candidate["id"])["stale"] is False
    lectura = ok(client.get(f"/documentos/{doc}/design"))
    design = lectura["design"]
    design["objective"] = "Objetivo distinto"
    ok(
        client.put(
            f"/documentos/{doc}/design",
            json={
                "revision": lectura["revision"],
                "state_revision": lectura["state_revision"],
                "design": design,
            },
            headers=headers(),
        )
    )
    listed = ok(client.get(f"/documentos/{doc}/design/candidates"))["candidates"]
    assert next(c for c in listed if c["id"] == candidate["id"])["stale"] is True


def test_restaurar_el_antes_deja_el_candidato_revertido_no_resuelto():
    doc = _doc()
    iid = _indicacion(doc, text="Mover en X")["created_intent"]
    lectura = ok(client.get(f"/documentos/{doc}/design"))
    candidate = ok(_preparar(doc, lectura, iid))
    applied = ok(_aplicar(doc, candidate["id"]))
    item, _ = _vista(doc, iid)
    assert item["attention"] == "applied"
    assert item["candidates"][0]["state"] == "applied"

    restored = client.post(
        f"/documentos/{doc}/restaurar",
        json={"snapshot": applied["before_snapshot"]},
        headers=headers(),
    )
    assert restored.status_code == 200, restored.text
    item, _ = _vista(doc, iid)
    assert item["candidates"][0]["state"] == "reverted"
    assert item["attention"] == "reverted"
    assert item["status"] == "pending"  # never marked resolved by an operation
    listed = ok(client.get(f"/documentos/{doc}/design/candidates"))["candidates"]
    entrada = next(c for c in listed if c["id"] == candidate["id"])
    assert entrada["state"] == "reverted"
    assert entrada["manifest_state"] == "applied"


def test_prepare_apply_y_discard_publican_evento(monkeypatch):
    capturados = []
    monkeypatch.setattr(
        eventos,
        "publicar",
        lambda tipo, doc_id, revision=None, cambios=None: capturados.append(
            (tipo, doc_id, revision, cambios)
        ),
    )
    doc = _doc()
    iid = _indicacion(doc, text="Mover en X")["created_intent"]
    lectura = ok(client.get(f"/documentos/{doc}/design"))
    candidate = ok(_preparar(doc, lectura, iid))
    tras_preparar = capturados[-1]
    assert tras_preparar[0] == "anotaciones_actualizadas"
    assert tras_preparar[1] == doc
    assert tras_preparar[2] == lectura["revision"]
    assert "design" in tras_preparar[3]

    otro = ok(
        client.post(
            f"/documentos/{doc}/design/candidates",
            headers=headers(),
            json={
                "revision": lectura["revision"],
                "state_revision": lectura["state_revision"],
                "request_id": "descartar-" + iid,
                "operations": [
                    {
                        "kind": "transform",
                        "piece": lectura["pieces"][0]["name"],
                        "translation": [2, 0, 0],
                    }
                ],
                "intent": iid,
            },
        )
    )
    capturados.clear()
    assert (
        client.delete(
            f"/documentos/{doc}/design/candidates/{otro['id']}", headers=headers()
        ).status_code
        == 200
    )
    assert capturados and capturados[-1][0] == "anotaciones_actualizadas"

    capturados.clear()
    _aplicar(doc, candidate["id"])
    assert capturados and capturados[-1][0] == "anotaciones_actualizadas"
    assert "design" in capturados[-1][3]
