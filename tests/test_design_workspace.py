"""Integrated workspace contract over isolated STEP/STL documents."""

import hashlib, io, json, time, zipfile
from pathlib import Path
import numpy as np
import pytest
import trimesh
from fastapi.testclient import TestClient
from build123d import Box, Cylinder
import auth, documents, design_state, design_geometry as geo, design_jobs, ramas
from kernel import b123d_kernel
from main import app
from mcp_server.server import crear_servidor

client = TestClient(app)


def headers():
    return {"X-Forja-Token": auth.obtener_token()}


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture(params=["stl", "step"])
def doc(request, tmp_path):
    if request.param == "stl":
        data = trimesh.creation.box(extents=[10, 12, 14]).export(file_type="stl")
    else:
        p = tmp_path / "shape.step"
        b123d_kernel.export_to_step(Box(10, 12, 14), p)
        data = p.read_bytes()
    d = ok(client.post("/documentos", files={"file": ("part." + request.param, data)}))[
        "id"
    ]
    yield d
    client.delete("/documentos/" + d, headers=headers())


def context(d):
    return ok(client.get(f"/documentos/{d}/design"))


def save(d, c):
    return client.put(
        f"/documentos/{d}/design",
        json={k: c[k] for k in ["revision", "state_revision", "design"]},
        headers=headers(),
    )


def prepare(d, c, ops=None, key="move"):
    return client.post(
        f"/documentos/{d}/design/candidates",
        headers=headers(),
        json={
            "revision": c["revision"],
            "state_revision": c["state_revision"],
            "request_id": key,
            "operations": ops
            or [
                {
                    "kind": "transform",
                    "piece": c["pieces"][0]["name"],
                    "translation": [3, 0, 0],
                }
            ],
        },
    )


def query(d, c, queries, background=False):
    return ok(
        client.post(
            f"/documentos/{d}/design/query",
            headers=headers(),
            json={
                "revision": c["revision"],
                "queries": queries,
                "background": background,
            },
        )
    )


def test_design_edit_restore_export(doc):
    c = context(doc)
    n = c["pieces"][0]["name"]
    original = c["pieces"][0]["bbox"]
    c["design"]["objective"] = "Pieza de prueba integrada"
    c["design"]["requirements"] = [
        {"id": "closed", "kind": "closed", "piece": n},
        {"id": "size", "kind": "dimension", "piece": n, "axis": "x", "value": 10},
    ]
    c = ok(save(doc, c))
    candidate = ok(prepare(doc, c))
    assert candidate["checks"]["failed"] == 0
    assert context(doc)["revision"] == c["revision"]
    assert ok(prepare(doc, c))["id"] == candidate["id"]
    applied = ok(
        client.post(
            f"/documentos/{doc}/design/candidates/{candidate['id']}/apply",
            headers=headers(),
        )
    )
    assert applied["state"] == "applied"
    assert context(doc)["pieces"][0]["bbox"][0] == pytest.approx(
        original[0] + 3, abs=1e-4
    )
    # Retrying publish never moves the geometry a second time.
    assert (
        ok(
            client.post(
                f"/documentos/{doc}/design/candidates/{candidate['id']}/apply",
                headers=headers(),
            )
        )["applied_revision"]
        == applied["applied_revision"]
    )
    for fmt in ["stl", "3mf"]:
        report = ok(client.get(f"/documentos/{doc}/design/export/{fmt}?manifest=true"))
        downloaded = client.get(f"/documentos/{doc}/design/export/{fmt}")
        assert report["sha256"] == hashlib.sha256(downloaded.content).hexdigest()
        assert report["pieces"] == [n]
    history = ok(client.get(f"/documentos/{doc}/historial"))
    # The candidate snapshot restores geometry AND the design conditions.
    snap = next(
        h for h in history if "antes de aplicar candidato" in h.get("mensaje", "")
    )
    key = snap.get("id", snap.get("snapshot"))
    restored = client.post(
        f"/documentos/{doc}/restaurar", json={"snapshot": key}, headers=headers()
    )
    assert restored.status_code == 200, restored.text
    assert context(doc)["design"]["objective"] == "Pieza de prueba integrada"
    assert context(doc)["pieces"][0]["bbox"] == pytest.approx(original)


def test_protection_and_revision_conflicts(doc):
    c = context(doc)
    n = c["pieces"][0]["name"]
    old = c.copy()
    c["design"]["parts"][n] = {"role": "reference", "locked": True}
    c = ok(save(doc, c))
    assert prepare(doc, c).status_code == 422
    assert save(doc, old).status_code == 409
    assert client.get(f"/documentos/{doc}/design/export/stl").status_code == 422
    c["design"]["parts"][n] = {"role": "design", "locked": False}
    c = ok(save(doc, c))
    candidate = ok(prepare(doc, c))
    c["design"]["objective"] = "Changed"
    ok(save(doc, c))
    assert (
        client.post(
            f"/documentos/{doc}/design/candidates/{candidate['id']}/apply",
            headers=headers(),
        ).status_code
        == 409
    )


def test_requirement_failure_blocks_publish(doc):
    c = context(doc)
    n = c["pieces"][0]["name"]
    c["design"]["requirements"] = [
        {
            "id": "preserve",
            "kind": "preserve",
            "piece": n,
            "baseline": c["pieces"][0]["signature"],
        }
    ]
    c = ok(save(doc, c))
    candidate = ok(prepare(doc, c))
    assert candidate["checks"]["failed"] == 1
    assert (
        client.post(
            f"/documentos/{doc}/design/candidates/{candidate['id']}/apply",
            headers=headers(),
        ).status_code
        == 409
    )
    assert context(doc)["revision"] == c["revision"]


def test_batch_inspection_cache_and_fdm(doc):
    c = context(doc)
    n = c["pieces"][0]["name"]
    queries = [
        {"kind": "inspect", "piece": n, "points": [[5, 0, 0]]},
        {"kind": "section", "piece": n},
        {"kind": "orientations", "piece": n},
        {"kind": "fdm", "piece": n},
    ]
    first = query(doc, c, queries)
    assert not any("error" in r for r in first["results"]), first
    assert first["results"][0]["result"]["closed"]
    assert len(first["results"][2]["result"]["options"]) == 6
    assert query(doc, c, queries)["cached"]
    job = query(doc, c, [{"kind": "evaluate"}], True)
    for _ in range(100):
        status = ok(client.get("/design/jobs/" + job["id"]))
        if status["state"] in ("done", "error"):
            break
        time.sleep(0.01)
    assert status["state"] == "done", status


def test_human_selection_stale_and_history_state(doc):
    c = context(doc)
    n = c["pieces"][0]["name"]
    c["design"]["intents"] = [
        {
            "id": "mark",
            "text": "Mover esto",
            "region": {"piece": n, "revision": c["revision"], "points": [[1, 2, 3]]},
        }
    ]
    c = ok(save(doc, c))
    candidate = ok(prepare(doc, c))
    ok(
        client.post(
            f"/documentos/{doc}/design/candidates/{candidate['id']}/apply",
            headers=headers(),
        )
    )
    assert context(doc)["stale_intents"] == ["mark"]
    with __import__("parametros").bloqueo(doc):
        files, _, _ = ramas.capturar(doc)
    assert json.loads(files["design.json"])["intents"][0]["text"] == "Mover esto"


def test_roundtrip_protected_reference_export(tmp_path):
    a = ok(
        client.post(
            "/documentos",
            files={
                "file": ("duct.stl", trimesh.creation.box().export(file_type="stl"))
            },
        )
    )["id"]
    m = trimesh.creation.box()
    m.apply_translation([5, 0, 0])
    b = ok(
        client.post(
            "/documentos", files={"file": ("cover.stl", m.export(file_type="stl"))}
        )
    )["id"]
    try:
        ok(
            client.post(
                f"/documentos/{a}/incorporar", headers=headers(), json={"desde": b}
            )
        )
        c = context(a)
        c["design"]["parts"]["cover"] = {"role": "reference"}
        c = ok(save(a, c))
        result = query(
            a, c, [{"kind": "clearance", "piece": "duct", "other": "cover"}]
        )["results"][0]["result"]
        assert result["distance_mm"] == pytest.approx(4)
        report = ok(client.get(f"/documentos/{a}/design/export/3mf?manifest=true"))
        assert report["pieces"] == ["duct"] and report["excluded"] == ["cover"]
        cand = ok(
            prepare(
                a, c, [{"kind": "transform", "piece": "duct", "translation": [0, 2, 0]}]
            )
        )
        assert [p["piece"] for p in cand["difference"]["changed"]] == ["duct"]
    finally:
        client.delete("/documentos/" + a, headers=headers())
        client.delete("/documentos/" + b, headers=headers())


def test_fit_alignment_and_degenerate_input():
    angles = np.linspace(0, 2 * np.pi, 16, endpoint=False)
    points = np.c_[3 * np.cos(angles) + 2, 3 * np.sin(angles) - 1, np.full(16, 4)]
    fitted = geo.fit(points, "circle")
    assert fitted["radius_mm"] == pytest.approx(3)
    target = points + [4, 5, 6]
    result = geo.rigid_matrix(points, target)
    assert result["max_error_mm"] < 1e-10
    with pytest.raises(ValueError):
        geo.rigid_matrix(
            [[0, 0, 0], [1, 0, 0], [2, 0, 0]], [[0, 0, 0], [1, 0, 0], [2, 0, 0]]
        )
    with pytest.raises(ValueError):
        geo.fit([[0, 0, 0], [1, 0, 0], [2, 0, 0]], "circle")


def test_validation_auth_and_idempotency(doc):
    c = context(doc)
    assert client.put(f"/documentos/{doc}/design", json={}).status_code == 401
    assert (
        client.post(f"/documentos/{doc}/design/candidates", json={}).status_code == 401
    )
    assert client.post(f"/documentos/{doc}/design/query", json={}).status_code == 401
    ok(prepare(doc, c, key="one"))
    assert (
        prepare(
            doc,
            c,
            [
                {
                    "kind": "transform",
                    "piece": c["pieces"][0]["name"],
                    "translation": [9, 0, 0],
                }
            ],
            key="one",
        ).status_code
        == 409
    )
    assert prepare(doc, c, key="../bad").status_code == 422
    assert (
        prepare(
            doc,
            c,
            [
                {
                    "kind": "transform",
                    "piece": c["pieces"][0]["name"],
                    "translation": [1e30, 0, 0],
                }
            ],
            key="big",
        ).status_code
        == 422
    )


def test_boolean_candidate_and_matrix(doc):
    c = context(doc)
    n = c["pieces"][0]["name"]
    candidate = ok(
        prepare(
            doc,
            c,
            [
                {
                    "kind": "subtract",
                    "piece": n,
                    "primitive": "cylinder",
                    "size": [3, 3, 40],
                }
            ],
            key="hole",
        )
    )
    assert candidate["integrity"][n]["closed"]
    assert candidate["integrity"][n]["volume_mm3"] < 10 * 12 * 14
    matrix = np.eye(4)
    matrix[:3, 3] = [1, 2, 3]
    candidate = ok(
        prepare(
            doc,
            c,
            [{"kind": "transform", "piece": n, "matrix": matrix.tolist()}],
            key="matrix",
        )
    )
    assert candidate["integrity"][n]["bbox"][0] == pytest.approx(
        c["pieces"][0]["bbox"][0] + 1, abs=1e-5
    )


def test_mcp_typed_contracts():
    registry = crear_servidor()._tool_manager.list_tools()
    names = {x.name for x in registry}
    assert {
        "contexto_diseno",
        "consultar_diseno",
        "preparar_candidato",
        "exportar_diseno",
    } <= names
    tool = next(x for x in registry if x.name == "preparar_candidato")
    assert "operations" in tool.parameters["properties"]


def test_cancel_job():
    from threading import Event

    release = Event()

    def work(progress):
        release.wait(2)
        progress(0.5, "mid")
        return {"unexpected": True}

    job = design_jobs.submit("test", "revision", work)
    design_jobs.read(job["id"], cancel=True)
    release.set()
    for _ in range(100):
        result = design_jobs.read(job["id"])
        if result["state"] == "cancelled":
            break
        time.sleep(0.01)
    assert result["state"] == "cancelled"


def test_reference_photo_is_bounded_versioned_and_non_executable(doc):
    from PIL import Image

    image = Image.new("RGB", (30, 20), "red")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    uploaded = ok(
        client.post(
            f"/documentos/{doc}/design/images",
            headers=headers(),
            files={"file": ("photo.png", buf.getvalue(), "image/png")},
        )
    )
    c = context(doc)
    c["design"]["intents"] = [
        {"id": "photo", "text": "Esta es la referencia", "images": [uploaded["id"]]}
    ]
    c = ok(save(doc, c))
    assert c["images"][0]["id"] == uploaded["id"]
    response = client.get(uploaded["url"])
    assert response.status_code == 200
    assert hashlib.sha256(response.content).hexdigest() == uploaded["id"]
    bad = client.post(
        f"/documentos/{doc}/design/images",
        headers=headers(),
        files={"file": ("x.svg", b'<svg onload="alert(1)"/>', "image/svg+xml")},
    )
    assert bad.status_code == 422


def test_candidate_rollback_on_publish_error(doc, monkeypatch):
    import design_operations as ops

    c = context(doc)
    candidate = ok(prepare(doc, c))
    before = documents._files[doc].read_bytes()
    analyze = documents._analyze

    def fail(*args, **kwargs):
        raise ValueError("forced")

    monkeypatch.setattr(documents, "_analyze", fail)
    with pytest.raises(ValueError):
        ops.apply(doc, candidate["id"])
    monkeypatch.setattr(documents, "_analyze", analyze)
    assert documents._files[doc].read_bytes() == before
    assert context(doc)["revision"] == c["revision"]


def test_preview_piece_bundle(doc):
    c = context(doc)
    candidate = ok(prepare(doc, c, key="bundle"))
    url = candidate["preview_url"]
    plain = client.get(url)
    assert plain.status_code == 200 and not plain.content.startswith(b"FJP1")
    bundle = client.get(url + "?piezas=1")
    assert bundle.status_code == 200 and bundle.content.startswith(b"FJP1")
    size = int.from_bytes(bundle.content[4:8], "little")
    manifest = json.loads(bundle.content[8 : 8 + size])
    assert [p["nombre"] for p in manifest["piezas"]] == [c["pieces"][0]["name"]]


def _cilindro_escalado_step(d_tmp):
    # Cilindro escalado en no uniforme: GTransform lo deja con caras BSPLINE y el
    # .volume de OCC sale ~0,9 % alto. Es el caso que separa B-rep y malla (F24b).
    p = d_tmp / "cilindro.step"
    b123d_kernel.export_to_step(b123d_kernel.scale_axis(Cylinder(5, 10), "x", 1.5, -5.0), p)
    return ok(client.post("/documentos", files={"file": ("cilindro.step", p.read_bytes())}))["id"]


def _aplicar_traslacion(d):
    c = context(d)
    candidato = ok(prepare(d, c, key="vol-brep"))
    return ok(client.post(f"/documentos/{d}/design/candidates/{candidato['id']}/apply", headers=headers()))


def test_apply_conserva_el_volumen_brep_y_sobrevive_a_la_recarga(tmp_path, monkeypatch):
    # F24b (revision): el registro guarda el volumen B-rep (como _analyze, ramas y
    # solids.json); la medida precisa no se usa al aplicar ni se reimporta el STEP.
    from kernel import b123d_kernel

    d = _cilindro_escalado_step(tmp_path)
    try:
        llamadas = []
        monkeypatch.setattr(b123d_kernel, "volumen_preciso", lambda *a, **k: llamadas.append(1) or 0.0)
        _aplicar_traslacion(d)
        assert llamadas == []
        vivo = documents._registry[d]["volumen"]
        assert vivo == pytest.approx(documents._analyze(documents._files[d], ".step")["volumen"], rel=1e-9)
        documents._registry.pop(d)
        documents.recargar_documentos()
        assert documents._registry[d]["volumen"] == pytest.approx(vivo, rel=1e-9)
    finally:
        client.delete("/documentos/" + d, headers=headers())


def test_comparar_tras_apply_no_marca_cambio_de_volumen(tmp_path):
    # F24b (revision): un paso aplicado y su rama reactivada no difieren en volumen.
    d = _cilindro_escalado_step(tmp_path)
    try:
        _aplicar_traslacion(d)
        vivo = documents._registry[d]["volumen"]
        ok(client.post(f"/documentos/{d}/ramas", json={"nombre": "alta"}, headers=headers()))
        activada = ok(client.post(f"/documentos/{d}/ramas/alta/activar", headers=headers()))
        assert activada["volumen"] == pytest.approx(vivo, rel=1e-9)
        ok(client.post(f"/documentos/{d}/ramas/main/activar", headers=headers()))
        cmp = ok(client.get(f"/documentos/{d}/comparar", params={"a": "main", "b": "alta"}))
        assert cmp["geometria_identica"] is True
        assert cmp["volumen"]["delta"] == pytest.approx(0.0, abs=1e-6)
    finally:
        client.delete("/documentos/" + d, headers=headers())
