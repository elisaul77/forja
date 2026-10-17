"""Rasgos de modificación de la fase 24d (ADR-0029): filete, chaflán, vaciado,
desfase de cara, matriz, simetría, dividir por plano y reparación de malla.

Cada rasgo es una `kind` de `design_contracts.Operation` que se aplica dentro
del pipeline de candidatos (`design_operations.prepare`): nunca muta el
documento. Las aristas y caras se resuelven por huella geométrica
(`naming.py`) contra la geometría actual; una huella que ya no existe o es
ambigua es un ValueError (422) y no se crea ningún paso."""

from __future__ import annotations

import math

import numpy as np
import trimesh

import incorporar, naming
from kernel import b123d_kernel as kernel

RASGOS_BREP = ("fillet", "chamfer", "shell", "push_pull", "pattern", "mirror", "split")
RASGOS_MALLA = ("pattern", "mirror", "mesh_repair")
RASGOS = frozenset(RASGOS_BREP) | frozenset(RASGOS_MALLA)
MAX_REFERENCIAS = 400
_EJES = {"x": [1.0, 0.0, 0.0], "y": [0.0, 1.0, 0.0], "z": [0.0, 0.0, 1.0]}
_SOLO_BREP = "solo para sólidos B-rep (este documento es malla)"


def _identidad(tipo: str):
    return naming.fingerprint_arista if tipo == "arista" else naming.fingerprint_cara


def _resolver_ids(forma, huellas, tipo: str) -> set[str]:
    """Id de cada huella resuelto contra `forma`. Una sola huella sin
    coincidencia clara rechaza la operación entera."""
    candidatas = naming.aristas(forma) if tipo == "arista" else naming.caras(forma)
    resueltas: set[str] = set()
    for huella in huellas:
        guardada = naming.fingerprint_desde_dict(huella.model_dump())
        encontrada, ambigua = naming.resolver_detallado(guardada, candidatas)
        if encontrada is None:
            motivo = "ambigua" if ambigua else "no encontrada"
            raise ValueError(
                f"Referencia de {tipo} {motivo} en la revisión actual; vuelve a consultar las referencias"
            )
        resueltas.add(encontrada.id)
    return resueltas


def _por_solido(forma, huellas, tipo: str, aplicar) -> list:
    """Llama a `aplicar(solido, elementos)` en cada sólido que contiene alguna
    referencia y devuelve los sólidos resultantes de toda la pieza."""
    wanted = _resolver_ids(forma, huellas, tipo)
    ident = _identidad(tipo)
    resultado, halladas = [], set()
    for solido in forma.solids():
        elementos = solido.edges() if tipo == "arista" else solido.faces()
        seleccion = [e for e in elementos if ident(e).id in wanted]
        if seleccion:
            halladas.update(ident(e).id for e in seleccion)
            resultado.extend(aplicar(solido, seleccion).solids())
        else:
            resultado.append(solido)
    if halladas != wanted:
        raise ValueError("Una referencia no pertenece a ningún sólido de la pieza")
    return resultado


def _valido(forma, nombre: str):
    if not forma.solids() or not forma.is_valid:
        raise ValueError(f"{nombre} produjo CAD vacío o inválido")
    return forma


def aplicar_brep(forma, op):
    """Aplica un rasgo a una pieza B-rep (Compound o Solid). Devuelve un Compound."""
    if op.kind not in RASGOS_BREP:
        raise ValueError("Este rasgo solo se aplica a mallas (STL)")
    if op.kind == "fillet":
        nuevos = _por_solido(
            forma, op.huellas, "arista",
            lambda s, e: kernel.filete_aristas(s, e, op.valor_mm),
        )
        return _valido(kernel.compuesto(nuevos), "El filete")
    if op.kind == "chamfer":
        nuevos = _por_solido(
            forma, op.huellas, "arista",
            lambda s, e: kernel.chaflan_aristas(s, e, op.valor_mm),
        )
        return _valido(kernel.compuesto(nuevos), "El chaflán")
    if op.kind == "shell":
        nuevos = _por_solido(
            forma, op.huellas, "cara",
            lambda s, c: kernel.vaciar_caras(s, c, op.valor_mm),
        )
        return _valido(kernel.compuesto(nuevos), "El vaciado")
    if op.kind == "push_pull":
        nuevos = _por_solido(
            forma, op.huellas, "cara",
            lambda s, c: kernel.desfasar_cara(s, c[0], op.valor_mm),
        )
        return _valido(kernel.compuesto(nuevos), "El desfase de cara")
    if op.kind == "mirror":
        espejo = kernel.espejar(forma, op.eje, op.plano_mm)
        return _valido(kernel.unir_formas(forma, [espejo]), "La simetría")
    if op.kind == "split":
        return _valido(kernel.dividir_por_plano(forma, op.eje, op.plano_mm), "La división")
    copias = [_copia_brep(forma, op, k) for k in range(1, op.cantidad)]
    return _valido(kernel.unir_formas(forma, copias), "La matriz")


def _copia_brep(forma, op, k: int):
    if op.patron == "lineal":
        return kernel.trasladar_eje(forma, op.eje, k * op.valor_mm)
    return kernel.girar_eje(forma, op.eje, _grados_circular(op, k))


def _grados_circular(op, k: int) -> float:
    """Ángulo de la copia k. Vuelta completa: reparto uniforme; arco: la copia
    final cae en `angulo_deg`."""
    if op.angulo_deg >= 360:
        return k * 360.0 / op.cantidad
    return k * op.angulo_deg / (op.cantidad - 1)


def _matriz_reflexion(eje: str, desplazamiento: float) -> np.ndarray:
    n = np.asarray(_EJES[eje])
    m = np.eye(4)
    m[:3, :3] = np.eye(3) - 2 * np.outer(n, n)
    m[:3, 3] = 2 * desplazamiento * n
    return m


def aplicar_malla(malla: trimesh.Trimesh, op) -> trimesh.Trimesh:
    """Aplica un rasgo a una malla (STL). Los rasgos B-rep se rechazan."""
    if op.kind not in RASGOS_MALLA:
        raise ValueError(_SOLO_BREP)
    if op.kind == "mesh_repair":
        return _reparar_malla(malla)
    if not malla.is_volume:
        raise ValueError("La operación requiere una malla cerrada y orientada")
    if op.kind == "mirror":
        espejo = malla.copy()
        # apply_transform ya corrige el sentido de las caras tras una reflexión.
        espejo.apply_transform(_matriz_reflexion(op.eje, op.plano_mm))
        return _unir_mallas([malla, espejo])
    copias = [malla]
    for k in range(1, op.cantidad):
        c = malla.copy()
        if op.patron == "lineal":
            c.apply_translation(np.asarray(_EJES[op.eje]) * k * op.valor_mm)
        else:
            c.apply_transform(
                trimesh.transformations.rotation_matrix(
                    math.radians(_grados_circular(op, k)), _EJES[op.eje], [0, 0, 0]
                )
            )
        copias.append(c)
    return _unir_mallas(copias)


def _unir_mallas(mallas: list[trimesh.Trimesh]) -> trimesh.Trimesh:
    if not all(m.is_volume for m in mallas):
        raise ValueError("La booleana requiere una malla cerrada y orientada")
    return trimesh.boolean.union(mallas, engine="manifold")


def _reparar_malla(malla: trimesh.Trimesh) -> trimesh.Trimesh:
    """Une vértices duplicados, quita caras degeneradas y orienta las caras.
    Los huecos reales NO se rellenan: `trimesh.repair.fill_holes` exige
    networkx, que la imagen no trae (no se añade dependencia). Una malla que
    sigue abierta queda bloqueada al aplicar por integridad, nunca oculta."""
    r = malla.copy()
    r.merge_vertices()
    r.update_faces(r.nondegenerate_faces())
    trimesh.repair.fix_normals(r)
    return r


def referencias(doc_id: str, pieza: str, tipo: str) -> dict:
    """Aristas o caras de una pieza B-rep con su huella, para elegirlas en la
    interfaz. Solo lectura, acotado a `MAX_REFERENCIAS` elementos."""
    path = incorporar._ruta_doc(doc_id)
    if not incorporar._es_step(path):
        raise ValueError("Solo los sólidos B-rep tienen aristas y caras seleccionables")
    grupos = incorporar._grupos_origen(doc_id, path, None)
    if pieza not in grupos:
        raise ValueError("Pieza desconocida")
    forma = kernel.compuesto(grupos[pieza])
    fuentes = naming.aristas(forma) if tipo == "arista" else naming.caras(forma)
    return {
        "pieza": pieza,
        "tipo": tipo,
        "total": len(fuentes),
        "truncado": len(fuentes) > MAX_REFERENCIAS,
        "elementos": [
            {"id": fp.id, "huella": fp.a_dict()} for fp in fuentes[:MAX_REFERENCIAS]
        ],
    }
