"""Pure schemas shared by MCP and HTTP; no backend registry imports."""

from typing import Annotated, Literal
import numpy as np
from pydantic import Field, FiniteFloat, model_validator
import design_state as ds


Scale = Annotated[float, Field(ge=0.01, le=100, allow_inf_nan=False)]
PRIMITIVES = Literal["box", "cylinder", "sphere", "cone", "torus", "wedge", "tube"]
BOOLEAN_KINDS = ("union", "subtract", "intersect")
# Campos que solo tienen sentido en algunos tipos. Se comprueban por valor (no por
# campos enviados): el MCP vuelca el modelo completo con model_dump().
UNIT_FACTOR = (1, 1, 1)
DEFAULT_NORMAL = (0, 0, -1)


class Operation(ds.Model):
    kind: Literal[
        "transform",
        "union",
        "subtract",
        "intersect",
        "deform_region",
        "scale",
        "align",
        "drop_to_bed",
    ]
    piece: str = Field(min_length=1, max_length=128)
    translation: tuple[FiniteFloat, FiniteFloat, FiniteFloat] = (0, 0, 0)
    rotation_deg: tuple[FiniteFloat, FiniteFloat, FiniteFloat] = (0, 0, 0)
    origin: tuple[FiniteFloat, FiniteFloat, FiniteFloat] = (0, 0, 0)
    matrix: list[tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat]] | None = (
        Field(default=None, min_length=4, max_length=4)
    )
    # Forma del útil de booleana (tamaños en mm: ver design_operations.primitiva_*).
    primitive: PRIMITIVES = "box"
    size: tuple[FiniteFloat, FiniteFloat, FiniteFloat] = (10, 10, 10)
    center: tuple[FiniteFloat, FiniteFloat, FiniteFloat] = (0, 0, 0)
    radius_mm: FiniteFloat = Field(default=5, gt=0, le=1000)
    # Pieza auxiliar: cuerpo de la booleana (en lugar de una primitiva) o
    # referencia de alineación. Solo lee la otra pieza; nunca la modifica.
    other: str | None = Field(default=None, min_length=1, max_length=128)
    # Escala por eje, en torno al centro de la caja de la pieza.
    factor: tuple[Scale, Scale, Scale] = UNIT_FACTOR
    # Alinear: center | min | max (caras) | stack (apoyar sobre la cara superior de other).
    mode: Literal["center", "min", "max", "stack"] | None = None
    axes: list[Literal["x", "y", "z"]] | None = Field(
        default=None, min_length=1, max_length=3
    )
    # Apoyar sobre cama: normal de la cara que queda abajo.
    normal: tuple[FiniteFloat, FiniteFloat, FiniteFloat] = DEFAULT_NORMAL

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

    @model_validator(mode="after")
    def validate_fields_for_kind(self):
        def usado(nombre, por_defecto):
            return getattr(self, nombre) != por_defecto

        if self.other is not None:
            if self.kind not in (*BOOLEAN_KINDS, "align"):
                raise ValueError("La pieza auxiliar solo aplica a booleanas o alinear")
            if self.other == self.piece:
                raise ValueError("La pieza auxiliar debe ser distinta de la pieza")
        if self.kind == "align":
            if self.other is None:
                raise ValueError("Alinear necesita una pieza de referencia (other)")
            if self.mode is None:
                raise ValueError("Alinear necesita un modo: center, min, max o stack")
            if self.mode == "stack" and self.axes not in (None, ["z"]):
                raise ValueError("Apilar solo actúa sobre el eje z")
            if self.axes is not None and len(set(self.axes)) != len(self.axes):
                raise ValueError("Los ejes de alineación no pueden repetirse")
        elif self.mode is not None or self.axes is not None:
            raise ValueError("mode y axes solo aplican a alinear")
        if usado("factor", UNIT_FACTOR) and self.kind != "scale":
            raise ValueError("factor solo aplica a escalar")
        if self.kind == "drop_to_bed":
            if self.other is not None:
                raise ValueError("Apoyar sobre cama no usa pieza auxiliar")
            if float(np.linalg.norm(self.normal)) <= 1e-9:
                raise ValueError("La normal de la cara debe ser distinta de cero")
        elif usado("normal", DEFAULT_NORMAL):
            raise ValueError("normal solo aplica a apoyar sobre cama")
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
