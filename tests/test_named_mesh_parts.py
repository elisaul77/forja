"""Named STL regions must preserve geometry, identity, and export boundaries."""
import io
import json
import struct
import zipfile

import numpy as np
import pytest
import trimesh
from fastapi.testclient import TestClient

import auth
import documents
import mesh_parts
import piece_mesh
import solids
from main import app

client = TestClient(app)


def _upload(name, mesh):
    response = client.post('/documentos', files={'file': (name + '.stl', mesh.export(file_type='stl'))})
    assert response.status_code == 200, response.text
    return response.json()['id']


def _manifest(data):
    size = struct.unpack_from('<I', data, 4)[0]
    return json.loads(data[8:8 + size])


def test_named_mesh_end_to_end_restore_reload():
    duct = trimesh.creation.box(extents=[2,3,4])
    # One reference part can contain multiple disconnected shells.
    tool = trimesh.util.concatenate([trimesh.creation.box(), trimesh.creation.box(transform=trimesh.transformations.translation_matrix([10,0,0]))])
    a,b = _upload('Ducto',duct),_upload('Referencia',tool)
    header={'X-Forja-Token':auth.obtener_token()}
    source_bytes = documents._files[b].read_bytes()
    try:
        r=client.post(f'/documentos/{a}/incorporar',json={'desde':b},headers=header)
        assert r.status_code==200,r.text
        snapshot=r.json()['snapshot_previo']
        manifest=_manifest(client.get(f'/documentos/{a}/malla?componentes=true').content)
        assert manifest['identificadas']
        assert [p['nombre'] for p in manifest['piezas']]==['Ducto','Referencia']
        assert len(manifest['piezas'])==2 # not three shells
        assert manifest['piezas'][1]['fragmentos']==2
        for name, original in [('Ducto',duct),('Referencia',tool)]:
            r=client.get(f'/documentos/{a}/descarga/{name}/piece.stl')
            assert r.status_code==200,r.text
            loaded=trimesh.load(io.BytesIO(r.content),file_type='stl',force='mesh')
            assert loaded.is_watertight
            assert mesh_parts.fingerprint(loaded)==mesh_parts.fingerprint(original)
        r=client.get(f'/documentos/{a}/descarga/parts.3mf')
        assert r.status_code==200
        with zipfile.ZipFile(io.BytesIO(r.content)) as archive:
            xml=archive.read('3D/3dmodel.model')
            assert b'name="Ducto"' in xml and b'name="Referencia"' in xml
        r=client.get(f'/documentos/{a}/exportar',params={'formato':'stl','por_solido':True})
        assert r.status_code==200,r.text
        assert set(r.json()['archivos'])=={'Ducto.stl','Referencia.stl'}
        assert 'advertencia_malla_abierta' not in r.json()
        # Startup must retain part provenance.
        documents._registry.pop(a);documents.recargar_documentos()
        assert _manifest(client.get(f'/documentos/{a}/malla?componentes=true').content)['identificadas']
        assert documents._files[b].read_bytes()==source_bytes
        r=client.post(f'/documentos/{a}/restaurar',json={'snapshot':snapshot},headers=header)
        assert r.status_code==200,r.text
        assert solids.cargar(a) is None
        assert not _manifest(client.get(f'/documentos/{a}/malla?componentes=true').content)['identificadas']
    finally:
        for id_ in [a,b]:client.delete(f'/documentos/{id_}',headers=header)


@pytest.mark.parametrize('mutation',['hash','overlap','gap','name','out_of_bounds'])
def test_invalid_mapping_never_exports_wrong_piece(mutation):
    from export import objetos_3mf
    mesh,entries=mesh_parts.combine([('first',trimesh.creation.box()),('second',trimesh.creation.box())])
    if mutation=='hash':mesh.vertices[0,0]+=1
    if mutation=='overlap':entries[1]['rangos'][0][0]=0
    if mutation=='gap':entries[1]['rangos'][0][0]+=1
    if mutation=='name':entries[1]['nombre']='../escape'
    if mutation=='out_of_bounds':entries[-1]['rangos'][0][1]+=1
    with pytest.raises(ValueError):objetos_3mf(mesh,entries)
    assert not _manifest(piece_mesh.from_stl(mesh,bbox=mesh.bounds.T.reshape(-1).tolist(),volume=mesh.volume,entries=entries))['identificadas']


def test_coincident_parts_remain_independent():
    mesh,entries=mesh_parts.combine([('A',trimesh.creation.box()),('B',trimesh.creation.box())])
    loaded=trimesh.load(io.BytesIO(mesh.export(file_type='stl')),file_type='stl',force='mesh')
    objects=mesh_parts.objects(loaded,entries,'fallback')
    assert [name for name,_ in objects]==['A','B']
    assert all(part.is_watertight and len(part.faces)==12 for _,part in objects)


def test_corrupt_metadata_refuses_download_and_rolls_back(monkeypatch):
    import incorporar
    a,b=_upload('same',trimesh.creation.box()),_upload('same',trimesh.creation.box())
    header={'X-Forja-Token':auth.obtener_token()}
    try:
        before=documents._files[a].read_bytes()
        real=incorporar._escribir_atomico
        def fail_sidecar(path,data):
            if path==solids.ruta_solidos(a):
                raise OSError('simulated sidecar write failure')
            return real(path,data)
        monkeypatch.setattr(incorporar,'_escribir_atomico',fail_sidecar)
        r=client.post(f'/documentos/{a}/incorporar',json={'desde':b},headers=header)
        assert r.status_code==422
        assert documents._files[a].read_bytes()==before and not solids.ruta_solidos(a).exists()
        monkeypatch.setattr(incorporar,'_escribir_atomico',real)
        r=client.post(f'/documentos/{a}/incorporar',json={'desde':b},headers=header)
        assert r.status_code==200,r.text
        assert r.json()['renombradas']=={'same':'same_2'}
        # Select a named STL source part, not its whole assembly.
        r=client.post(f'/documentos/{b}/incorporar',json={'desde':a,'piezas':['same_2']},headers=header)
        assert r.status_code==200 and r.json()['triangulos_despues']==24
        solids.ruta_solidos(a).write_text('{bad json')
        documents._borrar_cache_malla(a)
        assert client.get(f'/documentos/{a}/descarga/same/file.stl').status_code==400
        assert client.get(f'/documentos/{a}/exportar?formato=3mf').status_code==400
        manifest=_manifest(client.get(f'/documentos/{a}/malla?componentes=true').content)
        assert not manifest['identificadas'] and 'nombres' in manifest['aviso']
    finally:
        for id_ in [a,b]:client.delete(f'/documentos/{id_}',headers=header)
