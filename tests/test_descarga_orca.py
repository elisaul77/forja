"""Phase 10.1 tests: the read-only `GET /documentos/{id}/descarga/{slug}.{stl|3mf}`
route that OrcaSlicer's `orcaslicer://open?file=<url>` downloader accepts
(real filename at the end of the path, quoted ASCII `Content-Disposition`,
token-free, nothing written to disk)."""
from __future__ import annotations

import io
import traceback
import xml.etree.ElementTree as ET
import zipfile

import numpy as np
import pytest
import trimesh
from fastapi.testclient import TestClient

import auth
import documents
from main import app

# raise_server_exceptions=False: a 500 must show up as a status code here,
# not as an exception that hides which request caused it.
client = TestClient(app, raise_server_exceptions=False)

_NS = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
_SCRIPT_TRES_PIEZAS = (
    "from build123d import Box, Pos\n"
    "resultado = {\n"
    "    'mesa': Box(30, 20, 5),\n"
    "    'silla_1': Pos(40, 0, 0) * Box(10, 10, 10),\n"
    "    'silla_2': Pos(60, 0, 0) * Box(10, 10, 10),\n"
    "}\n"
)
_SCRIPT_UNA_PIEZA = "from build123d import Box\nresultado = Box(10, 10, 10)\n"


def _crear(script: str) -> dict:
    resp = client.post(
        "/documentos/script", json={"codigo": script}, headers={"X-Forja-Token": auth.obtener_token()}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _crear_stl(nombre: str = "cubo.stl") -> dict:
    malla = trimesh.creation.box(extents=(10, 10, 10))
    resp = client.post("/documentos", files={"file": (nombre, malla.export(file_type="stl"), "model/stl")})
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture(scope="module")
def tres() -> dict:
    return _crear(_SCRIPT_TRES_PIEZAS)


def _leer_3mf(datos: bytes) -> tuple[str, list[tuple[str, trimesh.Trimesh]]]:
    with zipfile.ZipFile(io.BytesIO(datos)) as zf:
        assert {"[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model"} <= set(zf.namelist())
        raiz = ET.fromstring(zf.read("3D/3dmodel.model"))
    objetos = []
    for obj in raiz.findall("m:resources/m:object", _NS):
        vertices = np.array(
            [[float(v.get(k)) for k in "xyz"] for v in obj.findall("m:mesh/m:vertices/m:vertex", _NS)]
        )
        caras = np.array(
            [[int(t.get(k)) for k in ("v1", "v2", "v3")] for t in obj.findall("m:mesh/m:triangles/m:triangle", _NS)]
        )
        objetos.append((obj.get("name"), trimesh.Trimesh(vertices, caras, process=False)))
    return raiz.get("unit"), objetos


def _listado_exportados() -> list[str]:
    if not documents.EXPORTADOS_DIR.exists():
        return []
    return sorted(str(p.relative_to(documents.EXPORTADOS_DIR)) for p in documents.EXPORTADOS_DIR.rglob("*"))


# ------------------------------------------------------------------ happy path


def test_3mf_de_tres_solidos(tres):
    resp = client.get(f"/documentos/{tres['id']}/descarga/mesa.3mf")  # no token
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "model/3mf"
    assert resp.headers["content-disposition"] == f'attachment; filename="{documents._slug_descarga(tres["nombre"])}.3mf"'
    unidad, objetos = _leer_3mf(resp.content)
    assert unidad == "millimeter"
    assert [n for n, _ in objetos] == ["mesa", "silla_1", "silla_2"]
    volumen = sum(m.volume for _, m in objetos)
    assert abs(volumen - tres["volumen"]) / tres["volumen"] < 1e-3


def test_stl_de_tres_solidos(tres):
    resp = client.get(f"/documentos/{tres['id']}/descarga/x.stl")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "model/stl"
    malla = trimesh.load(io.BytesIO(resp.content), file_type="stl")
    assert abs(malla.volume - tres["volumen"]) / tres["volumen"] < 1e-3


def test_un_solido_stl_y_3mf():
    creado = _crear(_SCRIPT_UNA_PIEZA)
    _unidad, objetos = _leer_3mf(client.get(f"/documentos/{creado['id']}/descarga/a.3mf").content)
    assert len(objetos) == 1
    assert objetos[0][1].volume == pytest.approx(1000, rel=1e-3)
    malla = trimesh.load(io.BytesIO(client.get(f"/documentos/{creado['id']}/descarga/a.stl").content), file_type="stl")
    assert malla.volume == pytest.approx(1000, rel=1e-3)


def test_documento_stl_un_objeto_con_nombre_del_archivo():
    creado = _crear_stl("cubo.stl")
    resp = client.get(f"/documentos/{creado['id']}/descarga/z.3mf")
    assert resp.status_code == 200
    _unidad, objetos = _leer_3mf(resp.content)
    assert [n for n, _ in objetos] == ["cubo"]
    resp = client.get(f"/documentos/{creado['id']}/descarga/z.stl")
    assert resp.status_code == 200
    assert trimesh.load(io.BytesIO(resp.content), file_type="stl").volume == pytest.approx(1000, rel=1e-3)


def test_head_devuelve_las_mismas_cabeceras(tres):
    resp = client.head(f"/documentos/{tres['id']}/descarga/x.3mf")
    assert resp.status_code == 200
    assert resp.headers["content-disposition"].startswith('attachment; filename="')


def test_el_stem_del_cliente_se_ignora(tres):
    a = client.get(f"/documentos/{tres['id']}/descarga/uno.3mf")
    b = client.get(f"/documentos/{tres['id']}/descarga/otro_nombre-9.3mf")
    assert a.status_code == b.status_code == 200
    assert a.headers["content-disposition"] == b.headers["content-disposition"]


# ------------------------------------------------------------------ bad tails


@pytest.mark.parametrize(
    "cola",
    [
        "x.step",
        "x.stl/",
        "x",
        "x.obj",
        ".stl",
        "x.STL.exe",
        "..%2f..%2fetc%2fpasswd",
        "..%2f..%2fetc%2fpasswd.stl",
        "../../etc/passwd.stl",
        "a%00b.stl",
        "a" * 200 + ".stl",
        "con espacio.stl",
        "x.stl%0a",
    ],
)
def test_colas_invalidas_dan_404(tres, cola):
    assert client.get(f"/documentos/{tres['id']}/descarga/{cola}").status_code == 404


# ------------------------------------------------------------------ bad ids


@pytest.mark.parametrize(
    "doc_id",
    ["noexiste", "a" * 128, "a" * 129, "🔧" * 63, "🔧" * 100, "%00", "..", "%2e%2e", "ñ" * 100],
)
@pytest.mark.parametrize("ext", ["stl", "3mf"])
def test_ids_invalidos_dan_404_nunca_500(doc_id, ext):
    resp = client.get(f"/documentos/{doc_id}/descarga/x.{ext}")
    assert resp.status_code == 404, resp.text


# ------------------------------------------------------------------ headers


@pytest.mark.parametrize(
    ("nombre", "esperado"),
    [
        ("a\r\nX-Evil: 1.stl", "a_X-Evil_1"),
        ('co"mi"llas.stl', "co_mi_llas"),
        ("Ñandú café.stl", "Nandu_cafe"),
        ("🔧🔧.stl", "modelo"),
        ("", "modelo"),
        (".stl", "stl"),  # Path(".stl").stem is ".stl" (a dotfile has no suffix)
        ("x" * 200 + ".stl", "x" * 64),
    ],
)
@pytest.mark.parametrize("ext", ["stl", "3mf"])
def test_nombre_hostil_da_una_sola_cabecera_limpia(nombre, esperado, ext):
    creado = _crear_stl()
    documents._registry[creado["id"]]["nombre"] = nombre
    resp = client.get(f"/documentos/{creado['id']}/descarga/x.{ext}")
    assert resp.status_code == 200
    assert resp.headers["content-disposition"] == f'attachment; filename="{esperado}.{ext}"'
    crudas = [v for k, v in resp.headers.raw if k.lower() == b"content-disposition"]
    assert len(crudas) == 1
    assert crudas[0].isascii() and b"\r" not in crudas[0] and b"\n" not in crudas[0]
    assert not any(k.lower() == b"x-evil" for k, _ in resp.headers.raw)


# ------------------------------------------------------------------ side effects


def test_no_escribe_nada(tres):
    antes = _listado_exportados()
    for ext in ("stl", "3mf"):
        assert client.get(f"/documentos/{tres['id']}/descarga/x.{ext}").status_code == 200
    assert _listado_exportados() == antes
    assert not (documents.DOCUMENTOS_DIR / f"{tres['id']}.3mf").exists()


def test_documento_borrado_da_404():
    creado = _crear(_SCRIPT_UNA_PIEZA)
    assert client.get(f"/documentos/{creado['id']}/descarga/x.stl").status_code == 200
    resp = client.delete(f"/documentos/{creado['id']}", headers={"X-Forja-Token": auth.obtener_token()})
    assert resp.status_code == 200, resp.text
    assert client.get(f"/documentos/{creado['id']}/descarga/x.stl").status_code == 404


def test_borrado_entre_comprobacion_y_cerrojo_da_404(tres, monkeypatch):
    creado = _crear(_SCRIPT_UNA_PIEZA)
    real = documents.parametros.bloqueo

    def bloqueo_que_borra(doc_id):
        documents._registry.pop(doc_id, None)  # delete "wins the race"
        return real(doc_id)

    monkeypatch.setattr(documents.parametros, "bloqueo", bloqueo_que_borra)
    assert client.get(f"/documentos/{creado['id']}/descarga/x.stl").status_code == 404


def test_tope_de_triangulos_da_413(tres, monkeypatch):
    monkeypatch.setattr(documents, "MAX_TRIANGULOS_DESCARGA", 5)
    assert client.get(f"/documentos/{tres['id']}/descarga/x.stl").status_code == 413
    assert client.get(f"/documentos/{tres['id']}/descarga/x.3mf").status_code == 413


def test_tope_real_admite_la_casa_del_arbol():
    assert documents.MAX_TRIANGULOS_DESCARGA >= 646_158  # largest measured document


# ------------------------------------------------------------------ parity + exportar key


def test_paridad_3mf_en_memoria_vs_archivo(tres):
    exportado = client.get(f"/documentos/{tres['id']}/exportar", params={"formato": "3mf"}).json()
    with open(exportado["ruta"], "rb") as fh:
        u_archivo, o_archivo = _leer_3mf(fh.read())
    u_mem, o_mem = _leer_3mf(client.get(f"/documentos/{tres['id']}/descarga/x.3mf").content)
    assert u_archivo == u_mem == "millimeter"
    assert [n for n, _ in o_archivo] == [n for n, _ in o_mem] == ["mesa", "silla_1", "silla_2"]
    assert sum(m.volume for _, m in o_archivo) == pytest.approx(sum(m.volume for _, m in o_mem), abs=1e-3)


@pytest.mark.parametrize("formato", ["stl", "3mf"])
def test_exportar_anade_la_clave_descarga(tres, formato):
    data = client.get(f"/documentos/{tres['id']}/exportar", params={"formato": formato}).json()
    assert {"ruta", "tamano_bytes"} <= set(data)
    assert data["descarga"] == f"/documentos/{tres['id']}/descarga/{documents._slug_descarga(tres['nombre'])}.{formato}"
    assert data["descarga"].endswith(f".{formato}") and "?" not in data["descarga"]
    assert client.get(data["descarga"]).status_code == 200


def test_exportar_sin_clave_descarga_para_step_y_por_solido(tres):
    assert "descarga" not in client.get(f"/documentos/{tres['id']}/exportar", params={"formato": "step"}).json()
    for formato in ("stl", "3mf"):
        data = client.get(
            f"/documentos/{tres['id']}/exportar", params={"formato": formato, "por_solido": "true"}
        ).json()
        assert "descarga" not in data
    assert "descarga" not in client.get("/documentos/noexiste/exportar", params={"formato": "stl"}).json()


# ------------------------------------------------------------------ fix round


def _truncar_solidos_a_uno(doc_id: str) -> None:
    import json

    ruta = documents.DOCUMENTOS_DIR / f"{doc_id}.solidos.json"
    entradas = json.loads(ruta.read_text())
    assert len(entradas) == 3
    ruta.write_text(json.dumps(entradas[:1]))


def test_solidos_json_desactualizado_da_400_como_exportar(caplog):
    creado = _crear(_SCRIPT_TRES_PIEZAS)
    _truncar_solidos_a_uno(creado["id"])
    antes = _listado_exportados()
    caplog.clear()
    exp = client.get(f"/documentos/{creado['id']}/exportar", params={"formato": "3mf"})
    assert exp.status_code == 400
    _listado_tras_exportar = _listado_exportados()  # exportar itself may touch exportados/
    resp = client.get(f"/documentos/{creado['id']}/descarga/x.3mf")
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == exp.json()["detail"]
    assert "solidos.json" in resp.json()["detail"]
    assert _listado_exportados() == _listado_tras_exportar
    assert not [r for r in caplog.records if r.name == documents.__name__ or r.exc_info]
    # the STL path never reads solidos.json: still served
    assert client.get(f"/documentos/{creado['id']}/descarga/x.stl").status_code == 200
    del antes


_SIN_GEOMETRIA = "el documento no tiene geometria (archivo vacio o danado)"


def test_step_corrupto_o_ausente_da_4xx_nunca_500():
    creado = _crear(_SCRIPT_UNA_PIEZA)
    ruta = documents._files[creado["id"]]
    original = ruta.read_bytes()
    try:
        ruta.write_bytes(b"esto no es un STEP")
        for ext in ("stl", "3mf"):
            resp = client.get(f"/documentos/{creado['id']}/descarga/x.{ext}")
            assert (resp.status_code, resp.json()["detail"]) == (400, _SIN_GEOMETRIA)
        ruta.unlink()
        for ext in ("stl", "3mf"):
            assert client.get(f"/documentos/{creado['id']}/descarga/x.{ext}").status_code == 404
    finally:
        ruta.write_bytes(original)


@pytest.mark.parametrize(
    ("contenido", "estado", "detalle"),
    [
        # `solids.cargar` maps unreadable JSON to None: one default object.
        ("{no es json", 200, None),
        # a dict with 1 entry vs 3 reimported solids: the same ValueError `exportar` gives
        (
            '{"a": 1}',
            400,
            "desacuerdo entre solidos.json (1 entradas) y la geometria reimportada (3 solidos); reconstruir el documento",
        ),
        # right count, but entries are ints: TypeError -> generic drift 400
        ("[1, 2, 3]", 400, "no se pudo generar la descarga: el documento no se puede leer (TypeError)"),
        # empty list is falsy: one default object
        ("[]", 200, None),
    ],
)
def test_solidos_json_corrupto_o_de_forma_inesperada(contenido, estado, detalle):
    creado = _crear(_SCRIPT_TRES_PIEZAS)
    ruta = documents.DOCUMENTOS_DIR / f"{creado['id']}.solidos.json"
    ruta.write_text(contenido)
    resp = client.get(f"/documentos/{creado['id']}/descarga/x.3mf")
    assert resp.status_code == estado, resp.text
    if detalle is None:
        _unidad, objetos = _leer_3mf(resp.content)
        assert len(objetos) == 1  # the default object, not the three named ones
    else:
        assert resp.json()["detail"] == detalle
    # the STL path never reads solidos.json
    assert client.get(f"/documentos/{creado['id']}/descarga/x.stl").status_code == 200


def test_stl_corrupto_da_400_sin_geometria():
    # trimesh loads these junk bytes as an empty mesh (measured), so the
    # hoisted emptiness check answers for both formats.
    creado = _crear_stl()
    documents._files[creado["id"]].write_bytes(b"\x00\x01basura")
    for ext in ("stl", "3mf"):
        resp = client.get(f"/documentos/{creado['id']}/descarga/x.{ext}")
        assert (resp.status_code, resp.json()["detail"]) == (400, _SIN_GEOMETRIA)


@pytest.mark.parametrize("con_sidecar", [True, False])
def test_step_corrupto_da_el_mensaje_de_geometria_vacia_en_ambos_formatos(con_sidecar):
    """The real cause must win over the sidecar mismatch (`objetos_3mf`
    would otherwise report '... 0 solidos' for a corrupt STEP + sidecar)."""
    creado = _crear(_SCRIPT_TRES_PIEZAS)
    sidecar = documents.DOCUMENTOS_DIR / f"{creado['id']}.solidos.json"
    if not con_sidecar:
        sidecar.unlink()
    else:
        assert sidecar.exists()
    documents._files[creado["id"]].write_bytes(b"esto no es un STEP")
    for ext in ("stl", "3mf"):
        resp = client.get(f"/documentos/{creado['id']}/descarga/x.{ext}")
        assert (resp.status_code, resp.json()["detail"]) == (400, _SIN_GEOMETRIA), ext


@pytest.mark.parametrize("fallo", ["bytes_3mf", "to_stl_bytes"])
def test_fallo_inesperado_da_500_generico_y_deja_traza(fallo, monkeypatch, caplog, tres):
    def revienta(*_a, **_k):
        raise RuntimeError("secreto-interno-xyz")

    if fallo == "bytes_3mf":
        monkeypatch.setattr(documents.export_solidos, "bytes_3mf", revienta)
        ext = "3mf"
    else:
        monkeypatch.setattr(documents.mesh, "to_stl_bytes", revienta)
        ext = "stl"
    caplog.clear()
    resp = client.get(f"/documentos/{tres['id']}/descarga/x.{ext}")
    assert resp.status_code == 500
    assert "secreto-interno-xyz" not in resp.text and "RuntimeError" not in resp.text
    registros = [r for r in caplog.records if r.exc_info]
    assert len(registros) == 1
    assert registros[0].exc_info[0] is RuntimeError
    assert tres["id"] in registros[0].getMessage()
    assert "Traceback" in "".join(traceback.format_exception(*registros[0].exc_info))
    # the lock was released on that path: the next (unpatched) request works
    monkeypatch.undo()
    assert client.get(f"/documentos/{tres['id']}/descarga/x.{ext}").status_code == 200


def test_deriva_esperada_no_deja_traza_en_el_log(caplog):
    """Non-vacuous companion of the stale-sidecar check: the same capture
    DOES see a record for a real defect (test above), so an empty list here
    means the drift paths really log nothing."""
    creado = _crear(_SCRIPT_TRES_PIEZAS)
    ruta = documents.DOCUMENTOS_DIR / f"{creado['id']}.solidos.json"
    caplog.clear()
    with caplog.at_level("DEBUG"):
        ruta.write_text("[1, 2, 3]")  # TypeError drift
        assert client.get(f"/documentos/{creado['id']}/descarga/x.3mf").status_code == 400
        ruta.write_text('{"a": 1}')  # ValueError drift
        assert client.get(f"/documentos/{creado['id']}/descarga/x.3mf").status_code == 400
        documents._files[creado["id"]].write_bytes(b"no es un STEP")  # empty geometry
        assert client.get(f"/documentos/{creado['id']}/descarga/x.stl").status_code == 400
    assert not [r for r in caplog.records if r.name == documents.__name__]


@pytest.mark.parametrize("doc_id", ["a" * 129, "🔧" * 63, "🔧" * 100, "ñ" * 100])
def test_el_clamp_del_id_actua_antes_del_cerrojo(doc_id, monkeypatch):
    """Even when the id IS in the registry, an over-long id (chars or UTF-8
    bytes) must be rejected before `parametros.bloqueo` is ever taken."""
    monkeypatch.setitem(documents._registry, doc_id, {"nombre": "x"})

    def no_debe_llamarse(_doc_id):
        raise AssertionError("bloqueo tomado con un id sin validar")

    monkeypatch.setattr(documents.parametros, "bloqueo", no_debe_llamarse)
    assert client.get(f"/documentos/{doc_id}/descarga/x.stl").status_code == 404


@pytest.mark.parametrize("cola", ["X.STL", "x.STL", "x.3MF", "x.Stl", "x.3Mf"])
def test_extension_en_mayusculas_da_404(tres, cola):
    assert client.get(f"/documentos/{tres['id']}/descarga/{cola}").status_code == 404


@pytest.mark.parametrize("ext", ["stl", "3mf"])
def test_cache_control_no_store(tres, ext):
    resp = client.get(f"/documentos/{tres['id']}/descarga/x.{ext}")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("consulta", ["formato=step", "a=b.stl", "x", "?"])
def test_query_string_no_vacia_da_404(tres, consulta):
    assert client.get(f"/documentos/{tres['id']}/descarga/x.stl?{consulta}").status_code == 404


def test_signo_de_interrogacion_final_vacio_es_indistinguible_de_la_ruta_limpia(tres):
    """`x.stl?` (a bare trailing "?") is NOT observable by the application:
    uvicorn (httptools and h11, verified 2026-10-01) splits the target at the
    first "?" and gives the ASGI app `query_string == b""` and a `raw_path`
    without it, exactly as for `x.stl`. It is therefore served like `x.stl`
    -- documented here, not worked around. Link builders must never emit a
    "?" (Orca would save the file as `x.stl?`)."""
    resp = client.get(f"/documentos/{tres['id']}/descarga/x.stl?")
    assert resp.status_code == 200
    assert resp.headers["content-disposition"] == client.get(
        f"/documentos/{tres['id']}/descarga/x.stl"
    ).headers["content-disposition"]
