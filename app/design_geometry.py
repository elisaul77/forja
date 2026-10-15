"""Bounded, explicit geometric observations for the agent workspace."""
from __future__ import annotations
import numpy as np
import trimesh
from scipy.spatial.transform import Rotation
import incorporar
import documents
import naming
from kernel import mesh as kernel_mesh

MAX_FACES=500000

def objects(doc_id):
    path=incorporar._ruta_doc(doc_id)
    if incorporar._es_step(path):
        groups=incorporar._grupos_origen(doc_id,path,None)
        return {n:trimesh.util.concatenate([kernel_mesh.tessellate_para_exportar(s) for s in ss]) for n,ss in groups.items()}
    return dict(incorporar._mesh_objects(doc_id,path,None))

def signature(m):
    import mesh_parts
    return mesh_parts.fingerprint(m)

def summary(m):
    return {'bbox':m.bounds.T.reshape(-1).tolist(),'dimensions_mm':m.extents.tolist(),
            'volume_mm3':float(abs(m.volume)) if m.is_watertight else None,
            'closed':bool(m.is_watertight),'triangles':len(m.faces),
            'bodies':int(m.body_count),'signature':signature(m),
            'method':'triangle_mesh','volume_reliable':bool(m.is_volume)}

def rigid_matrix(points,target):
    a=np.asarray(points,dtype=float);b=np.asarray(target,dtype=float)
    if a.shape!=b.shape or a.ndim!=2 or a.shape[1]!=3 or not 3<=len(a)<=256:
        raise ValueError('Se necesitan entre 3 y 256 correspondencias 3D')
    if not np.isfinite(a).all() or not np.isfinite(b).all():raise ValueError('Puntos no finitos')
    ac=a-a.mean(0);bc=b-b.mean(0)
    if np.linalg.matrix_rank(ac,tol=1e-8)<2 or np.linalg.matrix_rank(bc,tol=1e-8)<2:
        raise ValueError('Los puntos no pueden ser colineales')
    u,_,vt=np.linalg.svd(ac.T@bc);r=vt.T@u.T
    if np.linalg.det(r)<0:vt[-1]*=-1;r=vt.T@u.T
    t=b.mean(0)-r@a.mean(0);matrix=np.eye(4);matrix[:3,:3]=r;matrix[:3,3]=t
    residual=np.linalg.norm(a@r.T+t-b,axis=1)
    return {'matrix':matrix.tolist(),'residuals_mm':residual.tolist(),'rms_mm':float(np.sqrt(np.mean(residual**2))),
            'max_error_mm':float(residual.max()),'method':'rigid_least_squares','deformation':False}

def fit(points,kind):
    p=np.asarray(points,dtype=float)
    if p.ndim!=2 or p.shape[1]!=3 or len(p)<3 or not np.isfinite(p).all():raise ValueError('Se requieren al menos tres puntos finitos')
    center=p.mean(0);_,s,v=np.linalg.svd(p-center,full_matrices=False)
    if s[1]<1e-8:raise ValueError('Puntos colineales; ajuste indeterminado')
    normal=v[-1];errors=(p-center)@normal
    result={'center':center.tolist(),'normal':normal.tolist(),'plane_rms_mm':float(np.sqrt(np.mean(errors**2))),
            'method':'selected_points_least_squares','confidence':'estimated','samples':len(p)}
    if kind=='circle':
        xy=(p-center)@v[:2].T;coef=np.linalg.lstsq(np.c_[2*xy,np.ones(len(xy))],(xy**2).sum(1),rcond=None)[0]
        radius=np.sqrt(max(0,coef[2]+np.dot(coef[:2],coef[:2])))
        result.update(center=(center+coef[:2]@v[:2]).tolist(),radius_mm=float(radius),
                      radial_rms_mm=float(np.sqrt(np.mean((np.linalg.norm(xy-coef[:2],axis=1)-radius)**2))))
    elif kind!='plane':raise ValueError('Ajuste no admitido')
    return result

def inspect(doc_id,part,points=None,radius=5,limit=30):
    all_=objects(doc_id)
    if part not in all_:raise ValueError('Pieza desconocida')
    m=all_[part]
    result={'piece':part,**summary(m)}
    if points:
        p=np.asarray(points);closest,dist,faces=trimesh.proximity.closest_point(m,p)
        ids=np.flatnonzero(np.min(np.linalg.norm(m.triangles_center[:,None]-p[None,:],axis=2),axis=1)<=radius)
        result['region']={'triangles':len(ids),'area_mm2':float(m.area_faces[ids].sum()),'closest_points':closest.tolist(),'distances_mm':dist.tolist(),'triangle_ids':faces.tolist()}
    path=incorporar._ruta_doc(doc_id)
    if incorporar._es_step(path):
        group=incorporar._grupos_origen(doc_id,path,[part])[part]
        found=[]
        for shape in group:
            for face in shape.faces():
                kind=str(face.geom_type).split('.')[-1]
                if kind not in ('PLANE','CYLINDER'):continue
                data=naming.fingerprint_cara(face).a_dict();data['surface_type']=kind
                if kind=='CYLINDER':
                    from OCP.BRepAdaptor import BRepAdaptor_Surface
                    cyl=BRepAdaptor_Surface(face.wrapped).Cylinder();axis=cyl.Axis()
                    data.update(radius_mm=cyl.Radius(),axis=[axis.Direction().X(),axis.Direction().Y(),axis.Direction().Z()],origin=[axis.Location().X(),axis.Location().Y(),axis.Location().Z()])
                found.append(data)
        result['interfaces']={'items':found[:limit],'more':max(0,len(found)-limit),'method':'analytic_brep','note':'Superficies candidatas; un cilindro no implica un agujero pasante.'}
    else:result['interfaces']={'items':[],'method':'explicit_point_fit','note':'Selecciona puntos para ajustar plano o círculo; no se infieren agujeros de la silueta.'}
    return result

def clearance(a,b):
    if max(len(a.faces),len(b.faces))>MAX_FACES:raise ValueError('Malla demasiado grande; delimita la región')
    manager=trimesh.collision.CollisionManager();manager.add_object('a',a)
    collision=bool(manager.in_collision_single(b));distance=float(manager.min_distance_single(b))
    containment=None
    if a.is_volume and b.is_volume and not collision:
        containment=bool(a.contains(b.vertices[:1])[0] or b.contains(a.vertices[:1])[0])
    return {'distance_mm':max(0,distance),'surface_intersection':collision,'containment':containment,
            'interference':collision or containment is True,'method':'fcl_triangle_surfaces',
            'solid_check_complete':bool(a.is_volume and b.is_volume),
            'note':'Intersección de superficies incluye contacto. Contención requiere volúmenes cerrados.'}

def rotation_matrix(angles):
    matrix=np.eye(4);matrix[:3,:3]=Rotation.from_euler('xyz',angles,degrees=True).as_matrix();return matrix

def section(m,origin,normal,limit=64):
    p=m.section(plane_origin=origin,plane_normal=normal)
    if p is None:return {'polylines':[],'more':0}
    lines=p.discrete
    return {'polylines':[x[::max(1,int(np.ceil(len(x)/256)))].tolist() for x in lines[:limit]],'more':max(0,len(lines)-limit),'method':'mesh_plane_intersection'}
