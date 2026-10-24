"""Galería de ejemplos al abrir: scripts de construcción con nombre que el
visor ofrece cuando no hay ningún documento abierto.

Los ejemplos son scripts build123d paramétricos en `ejemplos/` (en el
contenedor, `/app/ejemplos`) y `ejemplos/index.json` describe cada uno. El
id de una entrada es el nombre de su archivo (`<id>.py`): la ruta nunca sale
de la petición, solo del índice, y un id desconocido es 404.

- `GET /ejemplos`: listado público (id, título, descripción, técnicas).
- `POST /ejemplos/{id}/abrir`: ejecuta el script por la misma ruta que
  `POST /documentos/script` y crea un documento nuevo «Ejemplo — <título>».
  Exige el token.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

import auth
import documents

router = APIRouter()

DIRECTORIO = Path(__file__).resolve().parent / "ejemplos"


def _indice() -> list[dict[str, Any]]:
    return json.loads((DIRECTORIO / "index.json").read_text(encoding="utf-8"))["ejemplos"]


def _entrada(ejemplo_id: str) -> dict[str, Any]:
    for entrada in _indice():
        if entrada["id"] == ejemplo_id:
            return entrada
    raise HTTPException(status_code=404, detail="ejemplo no encontrado")


@router.get("/ejemplos")
def listar_ejemplos() -> list[dict[str, Any]]:
    return [
        {"id": e["id"], "titulo": e["titulo"], "descripcion": e["descripcion"], "tecnicas": e["tecnicas"]}
        for e in _indice()
    ]


@router.post("/ejemplos/{ejemplo_id}/abrir", dependencies=[Depends(auth.requiere_token)])
def abrir_ejemplo(ejemplo_id: str) -> dict[str, Any]:
    entrada = _entrada(ejemplo_id)
    codigo = (DIRECTORIO / f"{entrada['id']}.py").read_text(encoding="utf-8")
    return documents.crear_documento_desde_script(
        documents._ScriptBody(codigo=codigo, nombre=f"Ejemplo — {entrada['titulo']}")
    )
