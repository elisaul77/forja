"""Printer tolerance profiles (fdm-A): storage, REST and calibration coupon.

Profiles live in ``<FORJA_DATA_DIR>/.perfiles/<nombre>.json`` (atomic
tmp + replace); the active profile's name is in ``.perfiles/.activo``. With
nothing saved, the seed profile (`perfil_fdm.SEMILLA`, ``semilla: true``) is
the active one, served from memory (a GET never writes).

`scripts_runner.ejecutar_script` injects the active profile into every
sandboxed script as the ``PERFIL`` global (see `perfil_activo`), and
`sandbox/bootstrap.py` binds ``agujero``/``eje``/``ranura``/``ajuste`` to it.

This module must not import `documents` at module level (`documents` ->
`scripts_runner` -> here): the coupon route imports it lazily.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import auth
import perfil_fdm

router = APIRouter()

PERFILES_DIR = Path(os.environ.get("FORJA_DATA_DIR", "/data/documentos")) / ".perfiles"
_ARCHIVO_ACTIVO = ".activo"
_NOMBRE_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")

NOMBRE_PROBETA = "Calibración de tolerancias"


def _validar_nombre(nombre: str) -> str:
    if not isinstance(nombre, str) or not _NOMBRE_RE.match(nombre):
        raise ValueError("nombre de perfil invalido (letras, numeros, '_' o '-', maximo 40)")
    return nombre


def _escribir_atomico(destino: Path, texto: str) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=destino.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(texto)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, destino)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _leer(nombre: str) -> dict[str, Any] | None:
    try:
        datos = json.loads((PERFILES_DIR / f"{_validar_nombre(nombre)}.json").read_text())
    except (FileNotFoundError, ValueError):
        return None
    return datos if isinstance(datos, dict) else None


def nombre_activo() -> str:
    try:
        nombre = (PERFILES_DIR / _ARCHIVO_ACTIVO).read_text().strip()
        if _NOMBRE_RE.match(nombre) and (nombre == perfil_fdm.SEMILLA["nombre"] or _leer(nombre)):
            return nombre
    except OSError:
        pass
    return perfil_fdm.SEMILLA["nombre"]


def cargar(nombre: str) -> dict[str, Any] | None:
    """Saved profile, or the seed under its own name if not saved."""
    perfil = _leer(nombre)
    if perfil is None and nombre == perfil_fdm.SEMILLA["nombre"]:
        perfil = json.loads(json.dumps(perfil_fdm.SEMILLA))
    return perfil


def perfil_activo() -> dict[str, Any]:
    return cargar(nombre_activo()) or json.loads(json.dumps(perfil_fdm.SEMILLA))


def listar() -> list[dict[str, Any]]:
    nombres = set()
    if PERFILES_DIR.is_dir():
        nombres = {p.stem for p in PERFILES_DIR.glob("*.json") if _NOMBRE_RE.match(p.stem)}
    nombres.add(perfil_fdm.SEMILLA["nombre"])
    activo = nombre_activo()
    salida = []
    for nombre in sorted(nombres):
        perfil = cargar(nombre)
        if perfil is None:
            continue
        salida.append({
            "nombre": nombre,
            "material": perfil.get("material"),
            "boquilla": perfil.get("boquilla"),
            "altura_capa": perfil.get("altura_capa"),
            "semilla": bool(perfil.get("semilla")),
            "activo": nombre == activo,
        })
    return salida


def guardar(perfil: dict[str, Any], activar: bool = True) -> dict[str, Any]:
    nombre = _validar_nombre(perfil["nombre"])
    _escribir_atomico(PERFILES_DIR / f"{nombre}.json", json.dumps(perfil, ensure_ascii=False, indent=2))
    if activar:
        _escribir_atomico(PERFILES_DIR / _ARCHIVO_ACTIVO, nombre)
    return perfil


def resolver_para_script(valor: Any) -> dict[str, Any]:
    """``PERFIL`` for a sandbox job: a dict passed by the caller is kept, a
    string names a saved profile, anything else -> the active profile."""
    if isinstance(valor, dict):
        return valor
    if isinstance(valor, str):
        try:
            perfil = cargar(valor)
        except ValueError:
            perfil = None
        if perfil is not None:
            return perfil
    return perfil_activo()


# ------------------------------------------------------------------ REST


class _PerfilBody(BaseModel):
    nombre: str
    material: str | None = Field(default=None, max_length=40)
    boquilla: float | None = None
    altura_capa: float | None = None
    mediciones: dict[str, Any] | None = None
    activar: bool = True


@router.get("/perfiles")
def obtener_perfiles() -> dict[str, Any]:
    return {"activo": nombre_activo(), "perfiles": listar(), "ajustes": perfil_fdm.NOMBRES_AJUSTE}


@router.get("/perfiles/{nombre}")
def obtener_perfil(nombre: str) -> dict[str, Any]:
    """``activo`` is an alias for the active profile."""
    if nombre == "activo":
        nombre = nombre_activo()
    try:
        perfil = cargar(nombre)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if perfil is None:
        raise HTTPException(status_code=404, detail="perfil no encontrado")
    return {**perfil, "activo": nombre == nombre_activo()}


@router.post("/perfiles", dependencies=[Depends(auth.requiere_token)])
def guardar_perfil(body: _PerfilBody) -> dict[str, Any]:
    """Create/update a profile: starts from the saved profile of that name
    (else the active one), applies metadata, refits from ``mediciones``."""
    try:
        _validar_nombre(body.nombre)
        base = cargar(body.nombre) or perfil_activo()
        perfil = {**base, "nombre": body.nombre}
        for campo in ("material", "boquilla", "altura_capa"):
            valor = getattr(body, campo)
            if valor is not None:
                perfil[campo] = valor
        if body.boquilla is not None and not 0.1 <= body.boquilla <= 2:
            raise ValueError("boquilla fuera de rango (0.1-2 mm)")
        if body.altura_capa is not None and not 0.04 <= body.altura_capa <= 1:
            raise ValueError("altura_capa fuera de rango (0.04-1 mm)")
        if body.mediciones:
            perfil = perfil_fdm.perfil_desde_mediciones(perfil, body.mediciones)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    guardar(perfil, activar=body.activar)
    return {**perfil, "activo": body.nombre == nombre_activo()}


@router.post("/perfiles/probeta", dependencies=[Depends(auth.requiere_token)])
def generar_probeta() -> dict[str, Any]:
    """New document «Calibración de tolerancias» (nominal geometry)."""
    import documents
    import parametros

    def _recordar(doc_id: str) -> None:
        parametros.guardar(
            doc_id, codigo=CODIGO_PROBETA, ruta=None, variables=None, timeout=None, esquema={}, valores={}
        )

    doc_id = uuid.uuid4().hex
    with documents._construyendo(doc_id):
        return documents._crear_documento_desde_script(
            doc_id, CODIGO_PROBETA, 120.0, None, NOMBRE_PROBETA, _recordar
        )


# Calibration coupon (74 x 54 mm), every feature labelled with its value:
# row 1 holes + slots, row 2 fit holes for the loose 5 mm pin (5 + .N),
# row 3 pins + horizontal holes. NOMINAL size on purpose: it measures the printer.
CODIGO_PROBETA = '''\
from build123d import *

ESPESOR = 2.0
RELIEVE = 0.6
LARGO, ANCHO = 74.0, 54.0
# Rotulos en relieve con digitos de 7 segmentos hechos de cajas: las
# fuentes de texto de una linea no sobreviven al STEP.
ALTO_DIG, ANCHO_DIG, TRAZO = 4.0, 2.4, 0.6
SEGMENTOS = {
    "0": "abcdef", "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc",
    "5": "afgcd", "6": "afgedc", "7": "abc", "8": "abcdefg", "9": "abcdfg",
}

placa = Pos(LARGO / 2, ANCHO / 2, ESPESOR / 2) * Box(LARGO, ANCHO, ESPESOR)
rotulos = []


def _segmento(cx, cy, seg):
    w, h, t = ANCHO_DIG, ALTO_DIG, TRAZO
    horizontal = {"a": h / 2 - t / 2, "g": 0.0, "d": -h / 2 + t / 2}
    if seg in horizontal:
        return Pos(cx, cy + horizontal[seg], ESPESOR + RELIEVE / 2) * Box(w, t, RELIEVE)
    x = w / 2 - t / 2 if seg in "bc" else -w / 2 + t / 2
    y = h / 4 if seg in "bf" else -h / 4
    return Pos(cx + x, cy + y, ESPESOR + RELIEVE / 2) * Box(t, h / 2 + t / 2, RELIEVE)


def rotulo(texto, x, y):
    paso = ANCHO_DIG + 0.8
    anchos = [TRAZO + 0.4 if c == "." else paso for c in texto]
    cursor = x - (sum(anchos) - 0.8) / 2
    for c, ancho in zip(texto, anchos):
        if c == ".":
            rotulos.append(Pos(cursor + TRAZO / 2, y - ALTO_DIG / 2 + TRAZO / 2, ESPESOR + RELIEVE / 2) * Box(TRAZO, TRAZO, RELIEVE))
        else:
            for seg in SEGMENTOS[c]:
                rotulos.append(_segmento(cursor + ANCHO_DIG / 2, y, seg))
        cursor += ancho


# Fila 1: agujeros verticales (3, 5, 8) y ranuras (2, 3, 5)
for d, x in ((3, 7), (5, 17), (8, 30)):
    placa -= Pos(x, 8, ESPESOR / 2) * Cylinder(d / 2, ESPESOR * 2)
    rotulo(str(d), x, 15)
for w, x in ((2, 44), (3, 54), (5, 66)):
    placa -= Pos(x, 8, ESPESOR / 2) * Box(w, 8, ESPESOR * 2)
    rotulo(str(w), x, 15)

# Fila 2: holguras de encaje (agujero 5 + .1 ... .5) para el pin suelto de 5 mm
for i, x in enumerate((8, 20, 32, 44, 56)):
    placa -= Pos(x, 25, ESPESOR / 2) * Cylinder((5 + 0.1 * (i + 1)) / 2, ESPESOR * 2)
    rotulo(f".{i + 1}", x, 31.5)
# El pin se imprime suelto, de pie dentro de un hueco de 9 mm de la placa.
placa -= Pos(68, 25, ESPESOR / 2) * Cylinder(4.5, ESPESOR * 2)
pin = Pos(68, 25, 5) * Cylinder(2.5, 10)

# Fila 3: ejes (3, 5, 8) y bloque de agujeros horizontales (3, 5, 8)
for d, x in ((3, 7), (5, 17), (8, 30)):
    placa += Pos(x, 42, ESPESOR + 4) * Cylinder(d / 2, 8)
    rotulo(str(d), x, 50)
ALTO = 11
bloque = Pos(57, 42, ESPESOR + ALTO / 2) * Box(30, 8, ALTO)
for d, x in ((3, 47), (5, 55.5), (8, 66)):
    bloque -= Pos(x, 42, ESPESOR + 5) * Rot(90, 0, 0) * Cylinder(d / 2, 10)
    rotulo(str(d), x, 50)
placa += bloque
for r in rotulos:
    placa += r

resultado = {"probeta": placa, "pin_5mm": pin}
'''
