"""Assembly REST transactions and their document-history integration.

The wire vocabulary is the project's Spanish (Phase 6.3): the payloads below
are exactly what the viewer and, from Phase 6.4 on, the MCP surface consume.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import assemblies
import auth
import documents
import naming
import parametros
import versioning
from main import app


SCRIPT = """from build123d import Box, Pos
PARAMETROS = {'offset': 10}
def construir(p):
    return {'base': Box(2, 2, 2), 'arm': Pos(p['offset'], 0, 0) * Box(2, 2, 2)}
"""
JOINT = dict(id="slide", tipo="deslizamiento", padre="base", hijo="arm",
             origen=[0, 0, 0], eje=[1, 0, 0], limites=[-5, 20], valor=0)


def headers():
    return {"X-Forja-Token": auth.obtener_token()}


def post(client, path, body, expected=200):
    response = client.post(path, json=body, headers=headers())
    assert response.status_code == expected, response.text
    return response.json()


@pytest.fixture
def document():
    client = TestClient(app)
    record = post(client, "/documentos/script", {"codigo": SCRIPT})
    doc_id = record["id"]
    yield client, doc_id, f"/documentos/{doc_id}/ensamble"
    response = client.delete(f"/documentos/{doc_id}", headers=headers())
    assert response.status_code in (200, 404)


def history(client, doc_id, message):
    response = client.get(f"/documentos/{doc_id}/historial")
    assert response.status_code == 200
    return [entry["id"] for entry in response.json() if entry["mensaje"] == message]


def ensambles_dir(doc_id):
    return documents.DOCUMENTOS_DIR / ".ensambles" / doc_id


def pre_63_state(state):
    """The same persisted state as a pre-6.3 build wrote it: `version: 1` and
    the English joint vocabulary. The one real instance on disk is the orphan
    `.ensambles/1652…/`, which no route can reach any more."""
    renombres = {"tipo": "type", "padre": "parent", "hijo": "child", "origen": "origin",
                 "eje": "axis", "limites": "limits", "valor": "value"}
    joints = [{renombres.get(key, key): value for key, value in joint.items()}
              for joint in state["articulaciones"]]
    return {**{k: v for k, v in state.items() if k not in ("articulaciones", "valores")},
            "version": 1, "joints": joints, "values": state["valores"]}


def tree_keys(node):
    """Every dict key anywhere in a JSON-ish tree: used to assert that no
    English assembly key survives anywhere in a payload."""
    if isinstance(node, dict):
        keys = set(node)
        for value in node.values():
            keys |= tree_keys(value)
        return keys
    if isinstance(node, list):
        keys = set()
        for value in node:
            keys |= tree_keys(value)
        return keys
    return set()


def test_assembly_mutations_require_token():
    client = TestClient(app)
    for method, path, payload in [
        ("POST", "/documentos/missing/ensamble", {"articulaciones": [JOINT]}),
        ("POST", "/documentos/missing/ensamble/pose", {"valores": {"slide": 1}}),
        ("DELETE", "/documentos/missing/ensamble", None),
    ]:
        for supplied in ({}, {"X-Forja-Token": "incorrect"}):
            response = client.request(method, path, json=payload, headers=supplied)
            assert response.status_code == 401, response.text


def test_define_pose_restore_recovers_base_and_values(document):
    client, doc_id, url = document
    initial = client.get(url).json()
    assert initial["piezas"] == ["base", "arm"]
    assert initial["articulaciones"] == []
    defined = post(client, url, {"articulaciones": [JOINT]})
    assert defined["valores"] == {"slide": 0}
    base_files = assemblies.snapshot_files(doc_id)
    assert post(client, url + "/pose", {"valores": {"slide": 5}})["bbox"][1] == pytest.approx(16)
    assert post(client, url + "/pose", {"valores": {"slide": 9}})["bbox"][1] == pytest.approx(20)
    before_second_pose = history(client, doc_id, "antes de mover articulaciones")[-1]
    restored = post(client, f"/documentos/{doc_id}/restaurar", {"snapshot": before_second_pose})
    assert restored["bbox"][1] == pytest.approx(16)
    state = client.get(url).json()
    assert state["valores"] == {"slide": 5}
    assert state["obsoleto"] is False
    assert assemblies.snapshot_files(doc_id)[assemblies.BASE_KEY] == base_files[assemblies.BASE_KEY]
    # Zero means the immutable original pose, not the last saved position.
    assert post(client, url + "/pose", {"valores": {"slide": 0}})["bbox"][1] == pytest.approx(11)


def test_restore_before_definition_removes_assembly_and_can_undo(document):
    client, doc_id, url = document
    post(client, url, {"articulaciones": [JOINT]})
    before_definition = history(client, doc_id, "antes de definir articulaciones")[-1]
    post(client, url + "/pose", {"valores": {"slide": 5}})
    restored = post(client, f"/documentos/{doc_id}/restaurar", {"snapshot": before_definition})
    assert restored["bbox"][1] == pytest.approx(11)
    assert client.get(url).json()["articulaciones"] == []
    assert assemblies.snapshot_files(doc_id) == {}
    post(client, url + "/pose", {"valores": {"slide": 1}}, expected=422)
    undo = history(client, doc_id, f"antes de restaurar {before_definition}")[-1]
    restored = post(client, f"/documentos/{doc_id}/restaurar", {"snapshot": undo})
    assert restored["bbox"][1] == pytest.approx(16)
    assert client.get(url).json()["valores"] == {"slide": 5}
    assert client.get(url).json()["obsoleto"] is False


def test_script_regeneration_stales_pose_and_history_restores_assembly(document):
    client, doc_id, url = document
    post(client, url, {"articulaciones": [JOINT]})
    post(client, url + "/pose", {"valores": {"slide": 5}})
    post(client, "/documentos/script", {
        "documento_id": doc_id, "codigo": SCRIPT.replace("'offset': 10", "'offset': 20"),
    })
    assert client.get(url).json()["obsoleto"] is True
    step_before = documents._files[doc_id].read_bytes()
    post(client, url + "/pose", {"valores": {"slide": 1}}, expected=409)
    assert documents._files[doc_id].read_bytes() == step_before
    before_rebuild = history(client, doc_id, "actualizar documento (script)")[-1]
    restored = post(client, f"/documentos/{doc_id}/restaurar", {"snapshot": before_rebuild})
    assert restored["bbox"][1] == pytest.approx(16)
    assert client.get(url).json()["obsoleto"] is False
    assert client.get(url).json()["valores"] == {"slide": 5}
    assert post(client, url + "/pose", {"valores": {"slide": 0}})["bbox"][1] == pytest.approx(11)


def test_notes_only_restore_preserves_current_assembly_and_pose(document):
    client, doc_id, url = document
    post(client, url, {"articulaciones": [JOINT]})
    post(client, f"/documentos/{doc_id}/notas", {
        "comentario": "revisar unión", "referencia": {"tipo": "punto", "punto": [0, 0, 0]},
    })
    note_snapshot = history(client, doc_id, "crear nota")[-1]
    post(client, url + "/pose", {"valores": {"slide": 5}})
    assembly_before = assemblies.snapshot_files(doc_id)
    restored = post(client, f"/documentos/{doc_id}/restaurar", {"snapshot": note_snapshot})
    assert restored["bbox"][1] == pytest.approx(16)
    assert assemblies.snapshot_files(doc_id) == assembly_before
    assert client.get(url).json()["valores"] == {"slide": 5}
    assert client.get(url).json()["obsoleto"] is False
    assert client.get(f"/documentos/{doc_id}/notas").json()["resumen"] == []


def test_clear_preserves_pose_and_document_delete_removes_assembly(document):
    client, doc_id, url = document
    post(client, url, {"articulaciones": [JOINT]})
    moved = post(client, url + "/pose", {"valores": {"slide": 5}})
    response = client.delete(url, headers=headers())
    assert response.status_code == 200, response.text
    assert response.json()["articulaciones"] == []
    assert client.get(f"/documentos/{doc_id}").json()["bbox"] == moved["bbox"]
    # User-facing string: accented, like every other message in the history.
    assert history(client, doc_id, "antes de quitar articulaciones (conservar posición)")
    # A deliberate redefinition adopts the current pose as its new rest.
    post(client, url, {"articulaciones": [JOINT]})
    assert post(client, url + "/pose", {"valores": {"slide": 1}})["bbox"][1] == pytest.approx(17)
    assert client.delete(f"/documentos/{doc_id}", headers=headers()).status_code == 200
    assert assemblies.snapshot_files(doc_id) == {}


def test_deleting_the_document_removes_its_assembly_directory(document):
    """C1: a deleted document leaves no assembly state behind — not the
    metadata (`assemblies.snapshot_files` -> `{}`) and not the directory
    `.ensambles/<id>/` itself."""
    client, doc_id, url = document
    post(client, url, {"articulaciones": [JOINT]})
    post(client, url + "/pose", {"valores": {"slide": 5}})
    assert ensambles_dir(doc_id).is_dir()
    assert client.delete(f"/documentos/{doc_id}", headers=headers()).status_code == 200
    assert assemblies.snapshot_files(doc_id) == {}
    assert assemblies.load(doc_id) is None
    assert not ensambles_dir(doc_id).exists()


def test_restoring_a_pre_definition_snapshot_removes_the_assembly(document):
    """C3: a geometry snapshot taken before the assembly existed restores
    without one — the state is removed from disk and a later pose has
    nothing to pose (422), not a stale assembly pretending to be current."""
    client, doc_id, url = document
    post(client, url, {"articulaciones": [JOINT]})
    post(client, url + "/pose", {"valores": {"slide": 5}})
    creation = history(client, doc_id, "documento creado (script)")[0]
    restored = post(client, f"/documentos/{doc_id}/restaurar", {"snapshot": creation})
    assert restored["bbox"][1] == pytest.approx(11)
    state = client.get(url).json()
    assert state["articulaciones"] == [] and state["valores"] == {}
    assert assemblies.snapshot_files(doc_id) == {}
    assert not ensambles_dir(doc_id).exists()
    post(client, url + "/pose", {"valores": {"slide": 1}}, expected=422)


def test_restores_never_write_assembly_files_into_the_documents_root(document):
    """C5: the assembly's files belong under `.ensambles/<id>/`. Written
    into the documents root instead, `recargar_documentos()` (which globs
    that root for STEP/STL files at startup) would register a phantom
    `ensamble_base` document on the next restart."""
    client, doc_id, url = document
    raiz = documents.DOCUMENTOS_DIR
    antes = {ruta.name for ruta in raiz.iterdir()}
    post(client, url, {"articulaciones": [JOINT]})
    post(client, url + "/pose", {"valores": {"slide": 5}})
    for snapshot in history(client, doc_id, "antes de mover articulaciones"):
        post(client, f"/documentos/{doc_id}/restaurar", {"snapshot": snapshot})
    assert not (raiz / assemblies.BASE_KEY).exists()
    assert not (raiz / assemblies.STATE_KEY).exists()
    # Nothing else may appear either, beyond the store's own directories
    # (`.ensambles` is legitimate here — defining the assembly creates it, and
    # this test must also pass when selected alone, before any other test has
    # touched it) and the document's own file and sidecars (a restored
    # snapshot written before this document had notes legitimately
    # materializes `{doc_id}.notas.json`). An assembly *file* in the root is
    # neither, so it is still caught here as well as by the two assertions
    # above.
    directorios_de_estado = {".historial", ".ensambles", "exportados"}
    propios = {f"{doc_id}{ext}" for ext in
               (".step", ".stl", ".notas.json", ".meta.json", ".solidos.json")}
    nuevos = {ruta.name for ruta in raiz.iterdir()} - antes
    assert nuevos <= propios | directorios_de_estado


def test_script_rerun_snapshot_carries_the_assembly(document):
    """C6: every geometry snapshot `documents.py` builds for itself carries
    the assembly, so undoing a rebuild cannot silently drop the joints."""
    client, doc_id, url = document
    post(client, url, {"articulaciones": [JOINT]})
    post(client, url + "/pose", {"valores": {"slide": 5}})
    post(client, "/documentos/script", {
        "documento_id": doc_id, "codigo": SCRIPT.replace("'offset': 10", "'offset': 20"),
    })
    snapshot = history(client, doc_id, "actualizar documento (script)")[-1]
    archivos = versioning.leer_snapshot(doc_id, snapshot)
    assert assemblies.STATE_KEY in archivos and assemblies.BASE_KEY in archivos
    restored = post(client, f"/documentos/{doc_id}/restaurar", {"snapshot": snapshot})
    assert restored["bbox"][1] == pytest.approx(16)
    state = client.get(url).json()
    assert state["valores"] == {"slide": 5}
    assert state["obsoleto"] is False


def test_read_payload_is_spanish_and_never_echoes_the_fingerprint(document):
    """Phase 6.3 (decision D5): the read payload is `{id, piezas,
    articulaciones, valores, obsoleto}` — the only English-keyed payload in
    the project, renamed before Phase 6.4 froze it in the MCP surface — and
    a reference comes back as the documented `{tipo, id, punto}`, never the
    internal `huella` fingerprint `naming.resolver_referencia` echoes
    (`GET /ensamble` used to return the joint's centroid/normal/area)."""
    client, doc_id, url = document
    caras = client.get(f"/documentos/{doc_id}/caras").json()
    cara = min(caras, key=lambda c: sum(abs(v - t) for v, t in zip(c["centroide"], (11, 0, 0))))
    huella = {"tipo": "cara", "subtipo": cara["tipo"], "centroide": cara["centroide"],
              "direccion": cara["normal"], "medida": cara["area"]}
    referencia = {"tipo": "cara", "id": naming.fingerprint_desde_dict(huella).id,
                  "punto": cara["centroide"], "huella": huella}
    joint = {**JOINT, "id": "weld", "tipo": "fijo", "limites": [0, 0],
             "referencia_hijo": referencia}
    assert set(client.get(url).json()) == {"id", "piezas", "articulaciones", "valores", "obsoleto"}
    defined = post(client, url, {"articulaciones": [joint]})
    assert defined["valores"] == {"weld": 0}
    assert set(defined["articulaciones"][0]) == {
        "id", "tipo", "padre", "hijo", "origen", "eje", "limites", "valor", "referencia_hijo",
    }
    assert defined["articulaciones"][0]["referencia_hijo"] == {
        "tipo": "cara", "id": referencia["id"], "punto": cara["centroide"],
    }
    assert "huella" not in json.dumps(defined)
    assert tree_keys(defined) & {"joints", "values", "stale", "type", "parent", "child",
                                 "origin", "axis", "limits", "value"} == set()
    posed = post(client, url + "/pose", {"valores": {"weld": 0}})
    assert posed["valores"] == {"weld": 0} and "values" not in posed


def test_english_assembly_vocabulary_is_rejected(document):
    """Phase 6.3: no compatibility aliases — nothing had shipped on the old
    English wire (only the viewer, these tests and the Phase-6 baseline), so
    the old keys are refused, not translated."""
    client, doc_id, url = document
    english = {"id": "slide", "type": "prismatic", "parent": "base", "child": "arm",
               "origin": [0, 0, 0], "axis": [1, 0, 0], "limits": [-5, 20], "value": 0}
    for payload in ({"joints": [JOINT]}, {"articulaciones": [english]},
                    {"articulaciones": [JOINT], "joints": [JOINT]},
                    {"articulaciones": [{**JOINT, "type": "prismatic"}]},
                    {"articulaciones": [{**JOINT, "junk": {"anidado": 1}}]}):
        response = client.post(url, json=payload, headers=headers())
        assert response.status_code == 422, response.text
    assert client.post(
        url + "/pose", json={"values": {"slide": 1}}, headers=headers()
    ).status_code == 422
    assert client.get(url).json()["articulaciones"] == []


def test_unknown_ids_never_grow_the_lock_table_and_keep_their_codes():
    """Phase 6.3 (the 6.2 review's residual): `parametros.bloqueo` *creates
    and keeps* one lock per id it is asked about — the table is never
    evicted — so a document id that names nothing must be refused *before*
    the lock, mirroring `app/notes.py`. Covers every token-guarded route
    that reaches `parametros.bloqueo` with a client-supplied id: the three
    assembly mutations and `documents.py`'s script update (`documento_id`),
    parametros apply, restore and delete — the last three already validated
    first and stay covered as regressions. Status codes are unchanged: 404
    unknown, 401 without the token, 422 for a body the schema refuses."""
    client = TestClient(app)
    desconocido = "0" * 32
    antes = len(parametros._bloqueos)
    for method, path, payload in [
        ("POST", f"/documentos/{desconocido}/ensamble", {"articulaciones": [JOINT]}),
        ("DELETE", f"/documentos/{desconocido}/ensamble", None),
        ("POST", f"/documentos/{desconocido}/ensamble/pose", {"valores": {"slide": 1}}),
        ("POST", "/documentos/script", {"documento_id": desconocido, "codigo": SCRIPT}),
        ("POST", f"/documentos/{desconocido}/parametros", {"valores": {}}),
        ("POST", f"/documentos/{desconocido}/restaurar", {"snapshot": "nada"}),
        ("DELETE", f"/documentos/{desconocido}", None),
    ]:
        response = client.request(method, path, json=payload, headers=headers())
        assert response.status_code == 404, (method, path, response.text)
        assert desconocido not in parametros._bloqueos
    # 401 without the token, 422 for an invalid body (schema and route level).
    for method, path, payload in [
        ("POST", f"/documentos/{desconocido}/ensamble", {"articulaciones": [JOINT]}),
        ("DELETE", f"/documentos/{desconocido}/ensamble", None),
        ("DELETE", f"/documentos/{desconocido}", None),
    ]:
        assert client.request(method, path, json=payload).status_code == 401
    assert client.post(f"/documentos/{desconocido}/ensamble",
                       json={"articulaciones": []}, headers=headers()).status_code == 422
    assert client.post(f"/documentos/{desconocido}/ensamble/pose",
                       json={"valores": {"slide": "mucho"}}, headers=headers()).status_code == 422
    assert len(parametros._bloqueos) == antes


def test_a_pre_6_3_state_is_refused_and_delete_recovers(document):
    """Phase 6.3 fix round: renaming the vocabulary made a pre-6.3
    `state.json` (English keys, `version: 1` — the real orphan
    `.ensambles/1652…/` is one) unreadable. It used to surface as a bare
    `KeyError` 500 on the whole document, and `POST /restaurar` answered 200
    while *planting* one. Every read path now refuses it with a clear
    message and a 409, a restore is refused before it writes anything at
    all, and `DELETE /ensamble` — which never reads the state — stays the
    documented recovery."""
    client, doc_id, url = document
    post(client, url, {"articulaciones": [JOINT]})
    post(client, url + "/pose", {"valores": {"slide": 5}})
    state_path = ensambles_dir(doc_id) / "state.json"
    step_path = documents._files[doc_id]
    antiguo = json.dumps(pre_63_state(json.loads(state_path.read_text()))).encode()
    # A snapshot taken by that same pre-6.3 build: STEP + old-format assembly.
    versioning.crear_snapshot(doc_id, "snapshot pre-6.3", {
        step_path.name: step_path.read_bytes(),
        assemblies.STATE_KEY: antiguo,
        assemblies.BASE_KEY: (ensambles_dir(doc_id) / "base.step").read_bytes(),
    })
    snapshot = history(client, doc_id, "snapshot pre-6.3")[-1]
    # Move on: the live geometry and state must stay *distinguishable* from
    # the snapshot's, or "refused" would be indistinguishable from "applied".
    post(client, url + "/pose", {"valores": {"slide": 9}})
    step_bytes = step_path.read_bytes()
    state_path.write_bytes(antiguo)  # the live state, as that build left it
    for method, path, payload in [
        ("GET", url, None),
        ("POST", url + "/pose", {"valores": {"slide": 1}}),
        ("POST", url, {"articulaciones": [JOINT]}),
    ]:
        response = client.request(method, path, json=payload, headers=headers())
        assert response.status_code == 409, (method, path, response.text)
        assert "formato anterior; quitar las articulaciones y redefinirlas" in response.json()["detail"], \
            response.text

    refused = client.post(f"/documentos/{doc_id}/restaurar", json={"snapshot": snapshot},
                          headers=headers())
    assert refused.status_code == 409, refused.text
    assert "formato anterior" in refused.json()["detail"]
    assert state_path.read_bytes() == antiguo   # nothing planted ...
    assert step_path.read_bytes() == step_bytes  # ... and no half-applied restore

    recovered = client.delete(url, headers=headers())
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["articulaciones"] == []
    assert not state_path.exists()
    # Usable again: the document kept the pose it had (slide 9), so the new
    # rest is that geometry and slide 1 moves it one millimetre further.
    post(client, url, {"articulaciones": [JOINT]})
    assert post(client, url + "/pose", {"valores": {"slide": 1}})["bbox"][1] == pytest.approx(21)
