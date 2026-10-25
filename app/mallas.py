"""Cuerpos de malla de un documento mixto (galeria ordenada, fase 3a).

Un documento STEP puede llevar cuerpos de malla (STL) junto a sus solidos
B-rep. Viven en el directorio ``{id}.mallas/``:

- ``manifest.json`` con ``schema: "mallas-v1"`` y la lista ``cuerpos`` (nombre,
  archivo, sha256, origen, transform4x4, estanca, triangulos, volumen);
- un STL binario por cuerpo, nombrado ``{nombre}.stl``.

Los cuerpos NO entran en ``solidos.json`` ni en el registro publico: los
nombres si comparten un unico espacio con los solidos (ver ``incorporar``).

Las funciones reciben el directorio de documentos (``base``) para no importar
``documents`` (ciclo). El llamador mantiene el lock del documento. Las escrituras
son atomicas a nivel de directorio: se publica una carpeta nueva y se
reemplaza la anterior; un fallo deja el almacen como estaba.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

import solids

SCHEMA = "mallas-v1"
NOMBRE_MANIFIESTO = "manifest.json"
# Claves del snapshot (ver `archivos_snapshot`): planas, sin separadores, como
# exige `git_store.validar_nombre`.
CLAVE_MANIFIESTO = "mallas-manifest.json"
# Mismo limite que `design_geometry.MAX_FACES`; no se importa porque
# `design_geometry` importa `incorporar`, que importa este modulo.
# Debe ser igual a design_geometry.MAX_FACES (lo verifica test_mallas_store.py).
MAX_TRIANGULOS = 500_000
TRANSFORM_IDENTIDAD = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]


class MallaInvalida(ValueError):
    """Malla o almacen que no se puede usar; el mensaje es una linea en espanol."""


def ruta_dir(base: Path, doc_id: str) -> Path:
    return base / f"{doc_id}.mallas"


def es_clave_snapshot(nombre: str) -> bool:
    return nombre == CLAVE_MANIFIESTO or (nombre.startswith("mallas-") and nombre.endswith(".stl"))


def _clave_stl(sha256: str) -> str:
    return f"mallas-{sha256}.stl"


def _parsear(contenido: bytes) -> dict[str, Any]:
    """Manifiesto validado. Rechaza esquemas desconocidos y rutas raras: el
    ``archivo`` de cada cuerpo debe ser exactamente ``{nombre}.stl``."""
    try:
        datos = json.loads(contenido)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MallaInvalida(f"almacen de mallas ilegible: {exc}") from exc
    if not isinstance(datos, dict) or datos.get("schema") != SCHEMA or not isinstance(datos.get("cuerpos"), list):
        raise MallaInvalida("almacen de mallas con un esquema desconocido")
    for cuerpo in datos["cuerpos"]:
        if not isinstance(cuerpo, dict) or not isinstance(cuerpo.get("nombre"), str):
            raise MallaInvalida("almacen de mallas con un cuerpo sin nombre")
        solids.validar_nombres([cuerpo["nombre"]])
        if cuerpo.get("archivo") != f"{cuerpo['nombre']}.stl" or not isinstance(cuerpo.get("sha256"), str):
            raise MallaInvalida(f"almacen de mallas con un archivo inesperado para {cuerpo['nombre']!r}")
    return datos


def leer_manifiesto(base: Path, doc_id: str) -> dict[str, Any] | None:
    """Manifiesto del documento, o ``None`` si no tiene almacen (documento
    B-rep puro o STL: se comportan exactamente como antes)."""
    ruta = ruta_dir(base, doc_id) / NOMBRE_MANIFIESTO
    if not ruta.exists():
        return None
    return _parsear(ruta.read_bytes())


def cuerpos(base: Path, doc_id: str) -> list[dict[str, Any]]:
    manifiesto = leer_manifiesto(base, doc_id)
    return list(manifiesto["cuerpos"]) if manifiesto else []


def nombres(base: Path, doc_id: str) -> list[str]:
    return [c["nombre"] for c in cuerpos(base, doc_id)]


def cargar(base: Path, doc_id: str, nombre: str) -> trimesh.Trimesh:
    """Malla de un cuerpo, verificada contra el sha256 del manifiesto."""
    for cuerpo in cuerpos(base, doc_id):
        if cuerpo["nombre"] == nombre:
            contenido = (ruta_dir(base, doc_id) / cuerpo["archivo"]).read_bytes()
            if hashlib.sha256(contenido).hexdigest() != cuerpo["sha256"]:
                raise MallaInvalida(f"la malla {nombre!r} no coincide con su manifiesto")
            return desde_stl(contenido)
    raise MallaInvalida(f"no hay un cuerpo de malla llamado {nombre!r}")


def firma_revision(base: Path, doc_id: str) -> list[tuple[str, bytes | None]]:
    """Sidecar para ``revision.calcular``: el manifiesto contiene el sha256 de
    cada cuerpo, asi que un cambio de malla cambia la revision. Sin almacen
    devuelve ``None`` y la revision no cambia."""
    ruta = ruta_dir(base, doc_id) / NOMBRE_MANIFIESTO
    return [(CLAVE_MANIFIESTO, ruta.read_bytes() if ruta.exists() else None)]


def archivos_snapshot(base: Path, doc_id: str) -> dict[str, bytes]:
    """Archivos planos del almacen para un snapshot: el manifiesto y un blob
    ``mallas-{sha256}.stl`` por cuerpo. Vacio si el documento no tiene almacen."""
    ruta = ruta_dir(base, doc_id) / NOMBRE_MANIFIESTO
    if not ruta.exists():
        return {}
    archivos = {CLAVE_MANIFIESTO: ruta.read_bytes()}
    for cuerpo in cuerpos(base, doc_id):
        archivos[_clave_stl(cuerpo["sha256"])] = (ruta_dir(base, doc_id) / cuerpo["archivo"]).read_bytes()
    return archivos


def _piezas_de_snapshot(archivos: dict[str, bytes]) -> tuple[bytes | None, dict[str, bytes]]:
    manifiesto_bytes = archivos.get(CLAVE_MANIFIESTO)
    if manifiesto_bytes is None:
        return None, {}
    piezas: dict[str, bytes] = {}
    for cuerpo in _parsear(manifiesto_bytes)["cuerpos"]:
        contenido = archivos.get(_clave_stl(cuerpo["sha256"]))
        if contenido is None or hashlib.sha256(contenido).hexdigest() != cuerpo["sha256"]:
            raise MallaInvalida(f"snapshot de mallas incompleto: falta o no coincide {cuerpo['nombre']!r}")
        piezas[cuerpo["archivo"]] = contenido
    return manifiesto_bytes, piezas


def validar_snapshot(archivos: dict[str, bytes]) -> None:
    """Comprueba un snapshot sin escribir nada (la restauracion lo llama antes)."""
    _piezas_de_snapshot(archivos)


def _publicar(base: Path, doc_id: str, manifiesto_bytes: bytes | None, piezas: dict[str, bytes]) -> None:
    """Reemplaza el almacen por ``piezas`` + manifiesto. Escribe en una carpeta
    nueva y solo al final intercambia; si algo falla, restaura la anterior."""
    destino = ruta_dir(base, doc_id)
    if manifiesto_bytes is None:
        quitar(base, doc_id)
        return
    nuevo = base / f".{doc_id}.mallas.nuevo"
    viejo = base / f".{doc_id}.mallas.viejo"
    shutil.rmtree(nuevo, ignore_errors=True)  # restos de un intento anterior fallido
    shutil.rmtree(viejo, ignore_errors=True)  # idem: si quedo, el intercambio fallaria
    nuevo.mkdir()
    try:
        for nombre_archivo, contenido in piezas.items():
            (nuevo / nombre_archivo).write_bytes(contenido)
        (nuevo / NOMBRE_MANIFIESTO).write_bytes(manifiesto_bytes)
        if destino.exists():
            os.replace(destino, viejo)
        os.replace(nuevo, destino)
    except BaseException:
        if not destino.exists() and viejo.exists():
            os.replace(viejo, destino)
        shutil.rmtree(nuevo, ignore_errors=True)  # la excepcion original se relanza
        raise
    if viejo.exists():
        shutil.rmtree(viejo)


def quitar(base: Path, doc_id: str) -> None:
    """Borra el almacen del documento si existe (purga y restauracion sin almacen)."""
    directorio = ruta_dir(base, doc_id)
    if directorio.exists():
        shutil.rmtree(directorio)


def restaurar_snapshot(base: Path, doc_id: str, archivos: dict[str, bytes]) -> None:
    """Deja el almacen igual que en ``archivos``. Un snapshot sin manifiesto
    describe un documento sin almacen, asi que el almacen se quita."""
    manifiesto_bytes, piezas = _piezas_de_snapshot(archivos)
    _publicar(base, doc_id, manifiesto_bytes, piezas)


def preparar(malla: trimesh.Trimesh) -> tuple[trimesh.Trimesh, dict[str, Any]]:
    """Valida y limpia una malla antes de guardarla. Refusa lo vacio, lo no
    finito y lo que supera el limite de triangulos. Una malla abierta se
    acepta con aviso (no sirve para booleanas, si para captura y colisiones)."""
    if len(malla.faces) == 0:
        raise MallaInvalida("la malla STL esta vacia (sin triangulos)")
    if not np.isfinite(malla.vertices).all():
        raise MallaInvalida("la malla tiene coordenadas NaN o infinitas")
    if len(malla.faces) > MAX_TRIANGULOS:
        raise MallaInvalida(f"la malla tiene {len(malla.faces)} triangulos (maximo {MAX_TRIANGULOS})")
    limpia = malla.copy()
    limpia.merge_vertices()
    limpia.update_faces(limpia.nondegenerate_faces())
    if len(limpia.faces) == 0:
        raise MallaInvalida("la malla no tiene triangulos validos tras limpiarla")
    trimesh.repair.fix_normals(limpia)
    estanca = bool(limpia.is_watertight)
    avisos: list[str] = []
    if not estanca:
        avisos.append("malla abierta (no estanca): se acepta, pero no sirve para booleanas")
    volumen = float(limpia.volume) if estanca else None
    if volumen is not None and volumen <= 0:
        avisos.append("volumen no positivo: revisa la orientacion de las caras")
    info = {"estanca": estanca, "triangulos": int(len(limpia.faces)), "volumen": volumen, "avisos": avisos}
    return limpia, info


def desde_stl(contenido: bytes) -> trimesh.Trimesh:
    """Lee un STL (binario o ASCII) desde bytes. Nunca toca el disco ni la ruta."""
    try:
        malla = trimesh.load(io.BytesIO(contenido), file_type="stl", force="mesh")
    except Exception as exc:  # noqa: BLE001 - el mensaje lleva la causa
        raise MallaInvalida(f"STL no legible: {exc}") from exc
    if not isinstance(malla, trimesh.Trimesh):
        raise MallaInvalida("STL no legible como malla")
    return malla


def anadir_cuerpos(base: Path, doc_id: str, nuevos: list[dict[str, Any]]) -> None:
    """Anade cuerpos al almacen. Cada ``nuevo`` lleva ``nombre``, ``stl`` (bytes
    binarios ya preparados), ``origen`` y los campos de ``preparar``. Los nombres
    deben estar libres: el llamador resuelve choques con ``_nombre_libre``."""
    piezas = {c["archivo"]: (ruta_dir(base, doc_id) / c["archivo"]).read_bytes() for c in cuerpos(base, doc_id)}
    lista = cuerpos(base, doc_id)
    for nuevo in nuevos:
        sha = hashlib.sha256(nuevo["stl"]).hexdigest()
        archivo = f"{nuevo['nombre']}.stl"
        if archivo in piezas:
            raise MallaInvalida(f"ya existe un cuerpo de malla llamado {nuevo['nombre']!r}")
        piezas[archivo] = nuevo["stl"]
        lista.append({
            "nombre": nuevo["nombre"], "archivo": archivo, "sha256": sha, "origen": nuevo["origen"],
            "transform4x4": TRANSFORM_IDENTIDAD, "estanca": nuevo["estanca"],
            "triangulos": nuevo["triangulos"], "volumen": nuevo["volumen"],
        })
    manifiesto = json.dumps({"schema": SCHEMA, "cuerpos": lista}, ensure_ascii=False, indent=2).encode()
    _publicar(base, doc_id, manifiesto, piezas)
