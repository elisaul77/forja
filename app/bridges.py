"""Read-only integrations with local engineering services (Phase 6)."""
from __future__ import annotations

import json
import math
import os
import socket
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

import auth

router = APIRouter(prefix="/puentes")
_MAX_RESPONSE = 262144


def _numero_finito(texto: str) -> float:
    """`json.loads(..., parse_float=...)`: every float literal in the upstream
    body passes through here, so `1e999` — which overflows to `inf` without
    ever being a `parse_constant` token — is refused too (Phase 6.4 closing
    round: a non-finite number cannot be serialised by the API layer, so it
    would answer 500 where the bridge promises 502)."""
    valor = float(texto)
    if not math.isfinite(valor):
        raise ValueError(f"numero no finito en la respuesta: {texto}")
    return valor


def _constante_rechazada(nombre: str) -> float:
    """`json.loads(..., parse_constant=...)`: `Infinity`/`-Infinity`/`NaN` are
    not JSON, and a simulator spelling one out is refused here instead of
    being parsed into a float no response can carry."""
    raise ValueError(f"valor no finito en la respuesta: {nombre}")


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Geometry(Payload):
    theta_marcha: float = Field(default=27.5, ge=-85, le=85)
    Lb: float = Field(default=45, gt=0, le=1000)
    recorrido: float = Field(default=7, ge=0, le=30)
    L_min: float = Field(default=56, gt=0, le=1000)
    L_max: float = Field(default=65.8, gt=0, le=1000)
    rueda_r: float = Field(default=30, gt=0, le=1000)


class Anchor(Payload):
    lado: str = Field(min_length=1, max_length=32)
    chasis: list[float] = Field(min_length=3, max_length=3)
    caja: list[float] = Field(min_length=3, max_length=3)


class PoseRequest(Payload):
    geo: Geometry | None = None
    anclajes: list[Anchor] | None = Field(default=None, min_length=1, max_length=8)
    subida_mm: list[float] = Field(default_factory=lambda: [-4, 0, 4], max_length=31)
    cabeceo_deg: list[float] = Field(default_factory=lambda: [-3, 0, 3], max_length=31)
    articulacion_deg: list[float] = Field(default_factory=lambda: [-6, 0, 6], max_length=31)


class PointRequest(Payload):
    cuerpo: Literal["caja", "chasis"] = "caja"
    puntos: list[list[float]] = Field(min_length=1, max_length=30)


def enabled(service: str) -> bool:
    default = "1" if service == "suspension" else "0"
    return os.environ.get(f"FORJA_{service.upper()}_ENABLED", default).lower() in ("1", "true", "yes")


def _require(service: str) -> None:
    if not enabled(service):
        raise HTTPException(503, f"puente {service} desactivado")


def _http(service: str, method: str, path: str, payload=None):
    # Never accept a URL or arbitrary route from the caller.
    allowed = {"suspension": {("GET", "/salud"), ("GET", "/config"), ("POST", "/pose"), ("POST", "/punto")},
               "kybercore": {("GET", "/health")}}
    if (method, path) not in allowed.get(service, set()):
        raise HTTPException(400, "operacion no permitida en el puente de solo lectura")
    _require(service)
    port = 8683 if service == "suspension" else 8100
    url = os.environ.get(f"FORJA_{service.upper()}_URL", f"http://host.docker.internal:{port}")
    try:
        with httpx.Client(base_url=url, timeout=8, follow_redirects=False) as client:
            with client.stream(method, path, json=payload) as response:
                response.raise_for_status()
                chunks = bytearray()
                for chunk in response.iter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > _MAX_RESPONSE:
                        raise ValueError("respuesta demasiado grande")
                return json.loads(chunks, parse_float=_numero_finito,
                                  parse_constant=_constante_rechazada)
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, f"no se pudo consultar {service}: {type(exc).__name__}") from exc


def _blender_scene():
    _require("blender")
    host = os.environ.get("FORJA_BLENDER_HOST", "host.docker.internal")
    port = int(os.environ.get("FORJA_BLENDER_PORT", "9877"))
    try:
        with socket.create_connection((host, port), timeout=8) as connection:
            connection.sendall(json.dumps({"type": "get_scene_info", "params": {}}).encode())
            content = bytearray()
            while len(content) < _MAX_RESPONSE:
                chunk = connection.recv(min(65536, _MAX_RESPONSE - len(content)))
                if not chunk:
                    break
                content.extend(chunk)
                try:
                    data = json.loads(content)
                    break
                except ValueError:
                    continue
            else:
                raise ValueError("respuesta demasiado grande")
        data = json.loads(content)
        if data.get("status") == "error":
            raise ValueError("Blender rechazo la consulta")
        scene = data.get("result", data)
        objects = scene.get("objects", []) if isinstance(scene, dict) else []
        return {"objetos": [{"nombre": obj.get("name"), "tipo": obj.get("type")} for obj in objects[:60]],
                "total": scene.get("object_count", len(objects)) if isinstance(scene, dict) else 0}
    except (OSError, ValueError) as exc:
        raise HTTPException(502, f"no se pudo consultar Blender: {type(exc).__name__}") from exc


@router.get("")
def status():
    result = {}
    for service in ("suspension", "blender", "kybercore"):
        if not enabled(service):
            result[service] = {"habilitado": False, "disponible": False}
            continue
        try:
            if service == "blender":
                _blender_scene()
            else:
                _http(service, "GET", "/salud" if service == "suspension" else "/health")
            result[service] = {"habilitado": True, "disponible": True}
        except HTTPException as exc:
            result[service] = {"habilitado": True, "disponible": False, "mensaje": exc.detail}
    return result


@router.get("/suspension/config")
def suspension_config():
    return _http("suspension", "GET", "/config")


@router.post("/suspension/pose", dependencies=[Depends(auth.requiere_token)])
def suspension_pose(body: PoseRequest):
    config = suspension_config() if body.geo is None or body.anclajes is None else {}
    payload = body.model_dump(exclude_none=True)
    if body.geo is None:
        payload["geo"] = Geometry(**{k: v for k, v in config.get("geo", {}).items() if k in Geometry.model_fields}).model_dump()
    if body.anclajes is None:
        payload["anclajes"] = [Anchor.model_validate(a).model_dump() for a in config["anclajes"][:8]]
    labels = [a["lado"] for a in payload["anclajes"]]
    if len(set(labels)) != len(labels) or payload["geo"]["L_min"] > payload["geo"]["L_max"]:
        raise HTTPException(422, "lados repetidos o limites de amortiguador invalidos")
    return _http("suspension", "POST", "/pose", payload)


@router.post("/suspension/punto", dependencies=[Depends(auth.requiere_token)])
def suspension_point(body: PointRequest):
    if any(len(point) != 3 for point in body.puntos):
        raise HTTPException(422, "cada punto necesita tres coordenadas")
    return _http("suspension", "POST", "/punto", body.model_dump())


@router.get("/blender/escena")
def blender_scene():
    return _blender_scene()


@router.get("/kybercore/salud")
def kybercore_health():
    return _http("kybercore", "GET", "/health")
