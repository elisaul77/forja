"""Exportacion estanca (ADR-0016): las mallas que salen de un solido B-rep
valido son cerradas y conservan su volumen; `advertencia_malla_abierta`
aparece solo cuando alguna malla exportada queda abierta.

Mismo patron in-process (`TestClient` + `FORJA_DATA_DIR` aislado) que
`tests/test_captura_colisiones_exportar.py`.
"""
from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import numpy as np
import trimesh
from build123d import Box, Compound, Face, Helix, Polyline, Pos, Sphere, sweep
from fastapi.testclient import TestClient

import auth
import export as export_solidos
from kernel import b123d_kernel
from kernel import mesh as kernel_mesh
from main import app

client = TestClient(app)

NS_3MF = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
RUTA_TUERCA_REAL = Path(__file__).parent / "fixtures" / "tuerca_p5.step"
TOLERANCIA_VOLUMEN = 0.001  # 0.1 %


def _headers() -> dict[str, str]:
    return {"X-Forja-Token": auth.obtener_token()}


def _crear(script: str) -> dict:
    resp = client.post("/documentos/script", json={"codigo": script}, headers=_headers())
    assert resp.status_code == 200, resp.text
    return resp.json()


def _rosca_helicoidal():
    """Filete helicoidal de perfil triangular: superficies BSpline que
    comparten aristas con caras vecinas. Es el caso duro: la teselacion cara
    a cara abre la malla (el mismo defecto que la tuerca real)."""
    trayectoria = Helix(pitch=1.5, height=5.0, radius=4.5)
    perfil = Face(
        Polyline((5.0, 0, -0.6), (3.3, 0, 0.0), (5.0, 0, 0.6), close=True)
    )
    return sweep(perfil, path=trayectoria, is_frenet=True)


def _mallas_3mf(ruta: Path) -> dict[str, trimesh.Trimesh]:
    """Lee cada objeto de un 3MF a mano (sin depender de trimesh.load 3MF)."""
    with zipfile.ZipFile(ruta) as zf:
        raiz = ET.fromstring(zf.read("3D/3dmodel.model"))
    mallas: dict[str, trimesh.Trimesh] = {}
    for objeto in raiz.iter(f"{NS_3MF}object"):
        vertices = [
            [float(v.get(eje)) for eje in ("x", "y", "z")]
            for v in objeto.iter(f"{NS_3MF}vertex")
        ]
        caras = [
            [int(t.get(eje)) for eje in ("v1", "v2", "v3")]
            for t in objeto.iter(f"{NS_3MF}triangle")
        ]
        mallas[objeto.get("name")] = trimesh.Trimesh(
            vertices=vertices, faces=caras, process=False
        )
    return mallas


def _caja_stl_abierta() -> bytes:
    """Cubo al que le falta un triangulo: dos aristas quedan sin pareja."""
    caja = trimesh.creation.box(extents=(6, 6, 6))
    caja.update_faces(np.arange(len(caja.faces)) != 0)
    return kernel_mesh.to_stl_bytes(caja)


# ----------------------------------------------------- teselacion de exportacion


def test_teselacion_cara_a_cara_deja_la_rosca_abierta():
    """Regresion del defecto: la ruta de vista, unida por vertices, no es estanca."""
    rosca = _rosca_helicoidal()
    antigua = kernel_mesh.tessellate_to_trimesh(rosca, 0.1)
    antigua.merge_vertices()
    assert not antigua.is_watertight


def test_rosca_helicoidal_exportacion_estanca_con_volumen_del_brep():
    rosca = _rosca_helicoidal()
    malla = kernel_mesh.tessellate_para_exportar(rosca)
    assert malla.is_watertight
    referencia = rosca.volume
    assert abs(abs(malla.volume) - referencia) / referencia < TOLERANCIA_VOLUMEN


def test_tuerca_p5_real_exportacion_estanca():
    tuerca = b123d_kernel.import_from_step(RUTA_TUERCA_REAL)
    malla = kernel_mesh.tessellate_para_exportar(tuerca)
    assert malla.is_watertight
    referencia = tuerca.volume
    assert abs(abs(malla.volume) - referencia) / referencia < TOLERANCIA_VOLUMEN


def test_esfera_con_polos_degenerados_es_estanca():
    """OCC emite triangulos de area cero en los polos: no deben abrir la malla.

    Las superficies curvas se desvian sobre todo por la tolerancia angular
    (0,1 rad, ADR-0016): la esfera queda ~0,18 % por debajo del volumen B-rep,
    por eso aqui la cota es 0,5 % y no 0,1 %.
    """
    esfera = Sphere(5)
    malla = kernel_mesh.tessellate_para_exportar(esfera)
    assert malla.is_watertight
    referencia = esfera.volume
    assert abs(abs(malla.volume) - referencia) / referencia < 0.005


def test_compuesto_con_solidos_que_se_tocan_es_estanco():
    """Dos sólidos que comparten una cara: cada uno cierra su propia malla."""
    compuesto = Compound([Box(10, 10, 10), Pos(10, 0, 0) * Box(10, 10, 10)])
    assert len(compuesto.solids()) == 2
    malla = kernel_mesh.tessellate_para_exportar(compuesto)
    assert malla.is_watertight
    referencia = compuesto.volume
    assert abs(abs(malla.volume) - referencia) / referencia < TOLERANCIA_VOLUMEN


def test_tessellate_para_exportar_no_muta_el_shape():
    """El triangulado cacheado del shape (que usa el visor) sigue intacto."""
    rosca = _rosca_helicoidal()
    antes = kernel_mesh.tessellate_to_trimesh_con_caras(rosca)[1].copy()
    kernel_mesh.tessellate_para_exportar(rosca)
    despues = kernel_mesh.tessellate_to_trimesh_con_caras(rosca)[1]
    assert np.array_equal(antes, despues)


def test_vista_no_usa_la_ruta_de_exportacion(monkeypatch):
    """Invariante: la teselacion de vista sigue cara a cara y no llama a la de exportacion."""

    def prohibida(*_args, **_kwargs):
        raise AssertionError("la vista no debe usar tessellate_para_exportar")

    monkeypatch.setattr(kernel_mesh, "tessellate_para_exportar", prohibida)
    rosca = _rosca_helicoidal()
    malla, indices = kernel_mesh.tessellate_to_trimesh_con_caras(rosca)
    assert len(indices) == len(malla.faces)


def test_mallas_abiertas_ignora_mallas_vacias():
    assert kernel_mesh.mallas_abiertas([("vacia", trimesh.Trimesh())]) == []


# ----------------------------------------------------- archivos escritos


def test_stl_y_3mf_escritos_se_releen_estancos(tmp_path):
    rosca = _rosca_helicoidal()
    stl = tmp_path / "rosca.stl"
    export_solidos._escribir(rosca, "stl", stl, None)
    assert trimesh.load(str(stl), file_type="stl", force="mesh").is_watertight

    tres_mf = tmp_path / "rosca.3mf"
    resultado = export_solidos.exportar_3mf_documento(rosca, None, tres_mf, nombre_por_defecto="rosca")
    assert "advertencia_malla_abierta" not in resultado
    mallas = _mallas_3mf(tres_mf)
    assert list(mallas) == ["rosca"]
    assert all(m.is_watertight for m in mallas.values())


def test_por_solido_stl_y_3mf_estancos_y_sin_aviso_en_rest():
    creado = _crear(
        "from build123d import Box, Pos\n"
        "resultado = {'cubo': Box(10, 10, 10), 'par': Pos(20, 0, 0) * Box(4, 4, 4)"
        " + Pos(40, 0, 0) * Box(4, 4, 4)}\n"
    )
    doc_id = creado["id"]
    resp = client.get(f"/documentos/{doc_id}/exportar?formato=stl&por_solido=true", headers=_headers())
    assert resp.status_code == 200, resp.text
    datos = resp.json()
    assert "advertencia_malla_abierta" not in datos
    directorio = Path(datos["directorio"])
    for archivo in datos["archivos"]:
        malla = trimesh.load(str(directorio / archivo), file_type="stl", force="mesh")
        assert malla.is_watertight, archivo

    resp = client.get(f"/documentos/{doc_id}/exportar?formato=3mf", headers=_headers())
    assert resp.status_code == 200, resp.text
    datos = resp.json()
    assert "advertencia_malla_abierta" not in datos
    mallas = _mallas_3mf(Path(datos["ruta"]))
    assert sorted(mallas) == ["cubo", "par"]
    assert all(m.is_watertight for m in mallas.values())


def test_stl_step_rest_estanco_sin_aviso():
    creado = _crear(
        "from build123d import Helix, Face, Polyline, sweep\n"
        "trayectoria = Helix(pitch=1.5, height=5.0, radius=4.5)\n"
        "perfil = Face(Polyline((5.0, 0, -0.6), (3.3, 0, 0.0), (5.0, 0, 0.6), close=True))\n"
        "resultado = {'rosca': sweep(perfil, path=trayectoria, is_frenet=True)}\n"
    )
    resp = client.get(f"/documentos/{creado['id']}/exportar?formato=stl", headers=_headers())
    assert resp.status_code == 200, resp.text
    datos = resp.json()
    assert "advertencia_malla_abierta" not in datos
    assert trimesh.load(datos["ruta"], file_type="stl", force="mesh").is_watertight


# ----------------------------------------------------- aviso de malla abierta


def test_aviso_si_la_malla_importada_esta_abierta():
    resp = client.post(
        "/documentos",
        files={"file": ("caja_abierta.stl", io.BytesIO(_caja_stl_abierta()), "application/sla")},
    )
    assert resp.status_code == 200, resp.text
    doc_id = resp.json()["id"]

    for formato in ("stl", "3mf"):
        resp = client.get(f"/documentos/{doc_id}/exportar?formato={formato}", headers=_headers())
        assert resp.status_code == 200, resp.text
        aviso = resp.json()["advertencia_malla_abierta"]
        assert aviso["piezas"] == ["caja_abierta"]
        assert "bordes abiertos" in aviso["mensaje"]
        assert "caja_abierta" in aviso["mensaje"]


def test_aviso_por_solido_nombra_la_pieza_abierta(monkeypatch):
    """Con una teselacion que deja la malla abierta, `por_solido` nombra la pieza."""
    original = kernel_mesh.tessellate_para_exportar

    def abierta(shape, *args, **kwargs):
        malla = original(shape, *args, **kwargs)
        malla.update_faces(np.arange(len(malla.faces)) != 0)
        return malla

    monkeypatch.setattr(kernel_mesh, "tessellate_para_exportar", abierta)
    creado = _crear("from build123d import Box\nresultado = {'cubo': Box(10, 10, 10)}\n")
    resp = client.get(f"/documentos/{creado['id']}/exportar?formato=stl&por_solido=true", headers=_headers())
    assert resp.status_code == 200, resp.text
    aviso = resp.json()["advertencia_malla_abierta"]
    assert aviso["piezas"] == ["cubo"]
    assert "bordes abiertos" in aviso["mensaje"]
