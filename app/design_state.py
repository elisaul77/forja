"""Versioned human/agent design intent. No geometry or document imports."""

from __future__ import annotations
import hashlib
import os
import re
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

KEY = "design.json"
ROOT = Path(os.environ.get("FORJA_DATA_DIR", "/data/documentos"))


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def bounded_numbers(self):
        def visit(value):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if abs(value) > 1000000:
                    raise ValueError("Magnitud fuera del límite de diseño (1000000)")
            elif isinstance(value, dict):
                for v in value.values():
                    visit(v)
            elif isinstance(value, (list, tuple)):
                for v in value:
                    visit(v)

        visit(self.model_dump())
        return self


class Region(Model):
    piece: str = Field(min_length=1, max_length=128)
    revision: str = Field(min_length=1, max_length=64)
    points: list[tuple[FiniteFloat, FiniteFloat, FiniteFloat]] = Field(
        default_factory=list, max_length=256
    )
    face_id: str | None = Field(default=None, max_length=128)
    radius_mm: FiniteFloat = Field(default=5, gt=0, le=1000)


class Intent(Model):
    images: list[str] = Field(default_factory=list, max_length=6)
    id: str = Field(min_length=1, max_length=64)
    text: str = Field(max_length=4000)
    region: Region | None = None
    direction: tuple[FiniteFloat, FiniteFloat, FiniteFloat] | None = None
    status: Literal["pending", "accepted", "resolved"] = "pending"
    source: Literal["user", "agent", "measurement", "reference", "assumption"] = "user"


class Requirement(Model):
    id: str = Field(min_length=1, max_length=64)
    kind: Literal[
        "closed", "single_body", "clearance", "dimension", "preserve", "manual"
    ]
    piece: str = Field(max_length=128)
    other: str | None = Field(default=None, max_length=128)
    axis: Literal["x", "y", "z"] = "x"
    value: FiniteFloat = Field(default=0, ge=0, le=100000)
    tolerance: FiniteFloat = Field(default=0.1, ge=0, le=1000)
    baseline: str | None = Field(default=None, max_length=64)
    description: str = Field(default="", max_length=2000)
    source: Literal["user", "agent", "measurement", "reference", "assumption"] = "user"


class PartRole(Model):
    role: Literal["design", "reference", "reserved"] = "design"
    locked: bool = False


class Design(Model):
    schema_version: Literal[1] = 1
    objective: str = Field(default="", max_length=8000)
    parts: dict[str, PartRole] = Field(default_factory=dict, max_length=256)
    intents: list[Intent] = Field(default_factory=list, max_length=256)
    requirements: list[Requirement] = Field(default_factory=list, max_length=256)
    print_rotations: dict[str, tuple[FiniteFloat, FiniteFloat, FiniteFloat]] = Field(
        default_factory=dict, max_length=256
    )


def path(doc_id):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", doc_id):
        raise ValueError("Identificador inválido")
    return ROOT / f"{doc_id}.design.json"


def load(doc_id):
    p = path(doc_id)
    return Design.model_validate_json(p.read_bytes()) if p.exists() else Design()


def digest(state):
    return hashlib.sha256(state.model_dump_json().encode()).hexdigest()[:32]


def save(doc_id, state):
    p = path(doc_id)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(state.model_dump_json())
    os.replace(tmp, p)


def snapshot(doc_id):
    p = path(doc_id)
    return {KEY: p.read_bytes()} if p.exists() else {}


def restore(doc_id, data):
    if data is None:
        path(doc_id).unlink(missing_ok=True)
    else:
        save(doc_id, Design.model_validate_json(data))


def require_unprotected_legacy_edit(doc_id):
    """Legacy whole-document rebuilds cannot guarantee per-piece preservation."""
    from fastapi import HTTPException

    if any(p.locked or p.role != "design" for p in load(doc_id).parts.values()):
        raise HTTPException(
            409,
            "Hay referencias protegidas. Usa candidatos por pieza o cambia explícitamente su protección en Diseño.",
        )
