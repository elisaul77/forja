"""Pure schemas shared by MCP and HTTP; no backend registry imports."""

from typing import Literal
import numpy as np
from pydantic import Field, FiniteFloat, model_validator
import design_state as ds


class Huella(ds.Model):
    """Huella geométrica (`naming.Fingerprint`) de una arista o cara del estado
    actual. El servidor la vuelve a resolver contra la geometría; si ya no
    existe, la operación se rechaza (ADR-0029)."""

    tipo: Literal["cara", "arista"]
    subtipo: str = Field(min_length=1, max_length=32)
    centroide: tuple[FiniteFloat, FiniteFloat, FiniteFloat]
    direccion: tuple[FiniteFloat, FiniteFloat, FiniteFloat]
    medida: FiniteFloat = Field(ge=0, le=1000000)


# Tipo de referencia que exige cada rasgo que las usa.
REFERENCIA_RASGO = {
    "fillet": "arista",
    "chamfer": "arista",
    "shell": "cara",
    "push_pull": "cara",
}


class Operation(ds.Model):
    kind: Literal[
        "transform",
        "union",
        "subtract",
        "intersect",
        "deform_region",
        "fillet",
        "chamfer",
        "shell",
        "push_pull",
        "pattern",
        "mirror",
        "split",
        "mesh_repair",
    ]
    piece: str = Field(min_length=1, max_length=128)
    translation: tuple[FiniteFloat, FiniteFloat, FiniteFloat] = (0, 0, 0)
    rotation_deg: tuple[FiniteFloat, FiniteFloat, FiniteFloat] = (0, 0, 0)
    origin: tuple[FiniteFloat, FiniteFloat, FiniteFloat] = (0, 0, 0)
    matrix: list[tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat]] | None = (
        Field(default=None, min_length=4, max_length=4)
    )
    primitive: Literal["box", "cylinder"] = "box"
    size: tuple[FiniteFloat, FiniteFloat, FiniteFloat] = (10, 10, 10)
    center: tuple[FiniteFloat, FiniteFloat, FiniteFloat] = (0, 0, 0)
    radius_mm: FiniteFloat = Field(default=5, gt=0, le=1000)
    # Rasgos de modificación (fase 24d, ADR-0029). Cada `kind` usa solo sus campos.
    huellas: list[Huella] = Field(default_factory=list, max_length=64)
    valor_mm: FiniteFloat = Field(default=1, ge=-1000, le=1000)
    eje: Literal["x", "y", "z"] = "z"
    plano_mm: FiniteFloat = Field(default=0, ge=-1000, le=1000)
    patron: Literal["lineal", "circular"] = "lineal"
    cantidad: int = Field(default=2, ge=2, le=32)
    angulo_deg: FiniteFloat = Field(default=360, gt=0, le=360)

    @model_validator(mode="after")
    def validate_rasgo(self):
        esperada = REFERENCIA_RASGO.get(self.kind)
        if esperada is not None:
            if not self.huellas or any(h.tipo != esperada for h in self.huellas):
                raise ValueError(f"{self.kind} requiere referencias de tipo {esperada}")
            if self.kind == "push_pull" and len(self.huellas) != 1:
                raise ValueError("Desfasar cara requiere exactamente una cara")
        elif self.huellas:
            raise ValueError(f"{self.kind} no admite referencias de aristas o caras")
        if self.kind in ("fillet", "chamfer", "shell") and self.valor_mm <= 0:
            raise ValueError("El valor debe ser positivo")
        if self.kind == "push_pull" and self.valor_mm == 0:
            raise ValueError("La distancia de desfase no puede ser cero")
        if self.kind == "pattern" and self.patron == "lineal" and self.valor_mm <= 0:
            raise ValueError("La separación de la matriz debe ser positiva")
        return self

    @model_validator(mode="after")
    def validate_matrix(self):
        if self.matrix is not None:
            m = np.asarray(self.matrix)
            if (
                self.kind != "transform"
                or not np.allclose(m[3], [0, 0, 0, 1])
                or not np.allclose(m[:3, :3].T @ m[:3, :3], np.eye(3), atol=1e-8)
                or not np.isclose(np.linalg.det(m[:3, :3]), 1)
            ):
                raise ValueError(
                    "La matriz debe ser una transformación rígida sin reflexión"
                )
        return self


class Prepare(ds.Model):
    revision: str
    state_revision: str
    request_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    operations: list[Operation] = Field(min_length=1, max_length=16)
    # Optional link to an existing intent and the agent's human explanation.
    intent: str | None = Field(default=None, min_length=1, max_length=64)
    summary: str = Field(default="", max_length=500)


class IntentCreate(ds.Model):
    """Open route (like notes): the user explains a change, optionally
    linking marks (notes/strokes ids) and a region of a piece."""

    text: str = Field(default="", max_length=4000)
    marks: list[ds.Mark] = Field(default_factory=list, max_length=64)
    piece: str | None = Field(default=None, min_length=1, max_length=128)
    points: list[tuple[FiniteFloat, FiniteFloat, FiniteFloat]] = Field(
        default_factory=list, max_length=256
    )
    radius_mm: FiniteFloat = Field(default=5, gt=0, le=1000)
    images: list[str] = Field(default_factory=list, max_length=6)


class IntentUserAction(ds.Model):
    """Open route: what the user does with an intent. Only the user marks an
    intent resolved or dismissed."""

    action: Literal["reply", "attach", "resolve", "reopen", "dismiss", "edit"]
    text: str | None = Field(default=None, max_length=4000)
    marks: list[ds.Mark] = Field(default_factory=list, max_length=64)
    images: list[str] = Field(default_factory=list, max_length=6)


class IntentAgent(ds.Model):
    """Token-protected route for the agent: its reading of an intent. A
    question is replaced on every call (omitted = answered/cleared)."""

    interpretation: str | None = Field(default=None, max_length=4000)
    question: str | None = Field(default=None, max_length=2000)
    message: str | None = Field(default=None, max_length=4000)


class Save(ds.Model):
    revision: str
    state_revision: str
    design: ds.Design


class Query(ds.Model):
    kind: Literal[
        "inspect",
        "fit_plane",
        "fit_circle",
        "align",
        "clearance",
        "section",
        "evaluate",
        "fdm",
        "orientations",
        "compare",
    ]
    piece: str | None = Field(default=None, max_length=128)
    other: str | None = Field(default=None, max_length=128)
    points: list[tuple[FiniteFloat, FiniteFloat, FiniteFloat]] = Field(
        default_factory=list, max_length=256
    )
    target: list[tuple[FiniteFloat, FiniteFloat, FiniteFloat]] = Field(
        default_factory=list, max_length=256
    )
    radius_mm: FiniteFloat = Field(default=5, gt=0, le=1000)
    origin: tuple[FiniteFloat, FiniteFloat, FiniteFloat] = (0, 0, 0)
    normal: tuple[FiniteFloat, FiniteFloat, FiniteFloat] = (0, 0, 1)
    nozzle: FiniteFloat = Field(default=0.4, ge=0.1, le=3)
    bed: tuple[FiniteFloat, FiniteFloat, FiniteFloat] = (220, 220, 250)
    angle: FiniteFloat = Field(default=45, ge=0, le=89)
    candidate: str | None = None
    limit: int = Field(default=30, ge=1, le=100)


class Batch(ds.Model):
    revision: str
    queries: list[Query] = Field(min_length=1, max_length=16)
    background: bool = False
