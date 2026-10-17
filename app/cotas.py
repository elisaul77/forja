"""Cotas vivas (F23a, ADR-0023): the dimensions of the drawing (F22) that trace
to a PARAMETROS entry of a script document become live controls.

Pure logic, no HTTP and no geometry. `clasificar` annotates the F22
descriptors with `operacion` / `solo_lectura` / `razon`; `resolver_edicion`
turns `{id_cota: valor}` into `{clave: valor}` for `parametros.aplicar_valores`;
`tras_aplicar` compares the re-measured dimensions with what was asked. The
write itself runs in `app/documents.py` through the same path as `parametros`.
"""
from __future__ import annotations

import math
import re
from typing import Any

OPERACION_PARAMETRO = "parametro"
# F23b (ADR-0025): operations on imported documents, one per recognised feature.
OPERATION_SCALE_AXIS = "escalar_eje"  # medida total -> non-uniform scale of that axis
OPERATION_REDRILL = "rellenar_y_taladrar"  # group of cylinders -> fill and re-drill
OPERATION_STRETCH = "mover_cara"  # two parallel planar faces -> stretch by bisection
RAZON_SIN_PARAMETRO = "no ligada a un parametro del script"
RAZON_MALLA = "malla sin B-rep"
REASON_POSITION = "posición no editable: se edita el diámetro del agujero"
REASON_SEVERAL_SOLIDS = "varios sólidos: el estiramiento solo aplica a una pieza"
REASON_ONE_AT_A_TIME = "las cotas de un documento importado se editan una a una"
MESSAGE_NO_CHANGE = "la cota ya mide ese valor: no hay cambio que guardar"
MENSAJE_NO_SIGUE = (
    "El script no mueve esa cota con ese valor: nada se guardó y no hay revisión nueva."
)
_RE_PARAMS = re.compile(r"""PARAMS\[\s*(['"])([^'"\n]+)\1\s*\]""")
TOLERANCIA_MM = 0.01  # re-measured value vs requested value
FACTOR_TOPE_SIN_MAX = 10.0  # no PARAMETROS max: at most 10x the current size
FACTOR_MINIMO_GEOMETRICO = 0.1  # imported dimensions: at least a tenth of the current size
MAX_COTAS_POR_EDICION = 40


class CotaNoEncontrada(LookupError):
    """The id is not a dimension of the current revision."""


class CotaInvalida(ValueError):
    """Read-only dimension, or a value that is not a finite positive number in range."""


class CotaNoSigue(Exception):
    """An edited dimension does not follow its parameter (F23a revision 1): the
    staged build refused the edit before anything was written (ADR-0023)."""

    def __init__(self, informe: list[dict[str, Any]]) -> None:
        self.informe = informe
        super().__init__(mensaje_no_sigue(informe))


def razon_valor_no_leida(clave: str) -> str:
    return f"ligada por valor: el script no lee PARAMS['{clave}']"


def claves_leidas(codigo: str) -> set[str]:
    """PARAMETROS keys the script reads with a literal ``PARAMS["clave"]``. Static
    and cheap (no run): a key the script never reads cannot drive a dimension, so
    its value link is coincidental. A key read through an alias or a loop is not
    seen: its dimensions are listed read-only (the conservative side)."""
    return {m.group(2) for m in _RE_PARAMS.finditer(codigo)}


def mensaje_no_sigue(informe: list[dict[str, Any]]) -> str:
    partes = [
        f"la cota {e['id']} no sigue al parámetro '{e['parametro']}'"
        for e in informe if e.get("no_sigue_al_parametro")
    ]
    texto = "; ".join(partes)
    return texto[:1].upper() + texto[1:] + ". " + MENSAJE_NO_SIGUE


def exigir_seguimiento(informe: list[dict[str, Any]]) -> None:
    """Raise `CotaNoSigue` when any edited dimension was not re-measured at its target."""
    if any(e.get("no_sigue_al_parametro") for e in informe):
        raise CotaNoSigue(informe)


def _es_numero(valor: Any) -> bool:
    if not isinstance(valor, (int, float)) or isinstance(valor, bool):
        return False
    try:
        return math.isfinite(valor)
    except OverflowError:  # int too large for a float
        return False


def clasificar(
    descriptores: list[dict[str, Any]], esquema: dict[str, dict[str, Any]], malla: bool = False,
    leidas: set[str] | None = None,
) -> list[dict[str, Any]]:
    """F22 descriptors + ``operacion`` (``"parametro"`` when editable) or
    ``solo_lectura: true`` with ``razon``. ``min``/``max`` come from the
    PARAMETROS entry so the editor can show the range. ``leidas`` (see
    `claves_leidas`): a value link to a key the script never reads is read-only
    (``ligada por valor``); ``None`` skips that check."""
    resultado = []
    for descriptor in descriptores:
        cota = dict(descriptor)
        clave = descriptor.get("parametro")
        if malla:
            cota.update(operacion=None, solo_lectura=True, razon=RAZON_MALLA)
        elif descriptor.get("origen") == "parametro" and clave in esquema and leidas is not None and clave not in leidas:
            cota.update(operacion=None, solo_lectura=True, razon=razon_valor_no_leida(clave))
        elif descriptor.get("origen") == "parametro" and clave in esquema:
            cota["operacion"] = OPERACION_PARAMETRO
            for tope in ("min", "max"):
                if tope in esquema[clave]:
                    cota[tope] = esquema[clave][tope]
        else:
            cota.update(operacion=None, solo_lectura=True, razon=RAZON_SIN_PARAMETRO)
        resultado.append(cota)
    return resultado


def resolver_edicion(listado: list[dict[str, Any]], cambios: Any) -> dict[str, float]:
    """``{id_cota: valor}`` -> ``{clave: valor}``. Unknown id ->
    `CotaNoEncontrada`; read-only, non-finite, non-positive, beyond the
    no-max cap, or two dimensions on one key with different values ->
    `CotaInvalida`. Nothing is written here: the caller applies the result."""
    if not isinstance(cambios, dict) or not cambios:
        raise CotaInvalida("indica al menos una cota y su valor")
    if len(cambios) > MAX_COTAS_POR_EDICION:
        raise CotaInvalida(f"como maximo {MAX_COTAS_POR_EDICION} cotas por edicion")
    por_id = {c["id"]: c for c in listado}
    destino: dict[str, float] = {}
    for id_cota, valor in cambios.items():
        cota = por_id.get(id_cota)
        if cota is None:
            raise CotaNoEncontrada(f"cota desconocida en esta revision: {id_cota}")
        if cota.get("solo_lectura"):
            raise CotaInvalida(f"cota solo lectura: {cota['razon']}")
        if not _es_numero(valor) or valor <= 0:
            raise CotaInvalida(f"'{id_cota}': el valor debe ser un numero finito mayor que 0")
        if "max" not in cota and valor > FACTOR_TOPE_SIN_MAX * cota["valor_mm"]:
            raise CotaInvalida(f"'{id_cota}': mas de {FACTOR_TOPE_SIN_MAX:g} veces la medida actual")
        clave = cota["parametro"]
        if destino.get(clave, valor) != valor:
            raise CotaInvalida(f"dos cotas ligadas a '{clave}' piden valores distintos")
        destino[clave] = float(valor)
    return destino


def tras_aplicar(
    antes: list[dict[str, Any]], despues: list[dict[str, Any]], cambios: dict[str, Any]
) -> list[dict[str, Any]]:
    """Per edited dimension: the value before, the value re-measured after the
    re-run and the error. The id carries the value, so the after-dimension is
    matched by type, axis, view and PARAMETROS key (closest value wins).
    ``no_sigue_al_parametro`` when nothing matches within TOLERANCIA_MM: the
    parameter did not drive that dimension (the link is by value, see F22)."""
    previas = {c["id"]: c for c in antes}
    informe = []
    for id_cota, pedido in cambios.items():
        previa = previas[id_cota]
        firma = (previa["tipo"], previa["eje"], previa["vista"], previa.get("parametro"))
        candidatas = [c for c in despues if (c["tipo"], c["eje"], c["vista"], c.get("parametro")) == firma]
        tras = min(candidatas, key=lambda c: abs(c["valor_mm"] - pedido), default=None)
        entrada: dict[str, Any] = {
            "id": id_cota,
            "parametro": previa.get("parametro"),
            "valor_antes": previa["valor_mm"],
            "pedido_mm": pedido,
            "valor_despues": tras["valor_mm"] if tras else None,
            "error_mm": round(tras["valor_mm"] - pedido, 3) if tras else None,
        }
        if tras is None or abs(tras["valor_mm"] - pedido) > TOLERANCIA_MM:
            entrada["no_sigue_al_parametro"] = True
        informe.append(entrada)
    return informe


def classify_imported(
    descriptores: list[dict[str, Any]], *, malla: bool, multisolido: bool
) -> list[dict[str, Any]]:
    """Dimensions of an imported document (no script, F23b, ADR-0025). A medida
    total scales its axis (B-rep and mesh alike); a group of cylinders is re-drilled;
    two parallel faces are stretched, unless the document has several solids. Any
    other dimension (a hole position, a mesh feature) is read-only with its reason."""
    resultado = []
    for descriptor in descriptores:
        cota = dict(descriptor)
        tipo = descriptor.get("tipo")
        if tipo == "tamano_total":
            cota.update(operacion=OPERATION_SCALE_AXIS)
        elif malla:
            cota.update(operacion=None, solo_lectura=True, razon=RAZON_MALLA)
        elif tipo == "diametro":
            cota.update(operacion=OPERATION_REDRILL)
        elif tipo == "distancia" and not multisolido:
            cota.update(operacion=OPERATION_STRETCH)
        elif tipo == "distancia":
            cota.update(operacion=None, solo_lectura=True, razon=REASON_SEVERAL_SOLIDS)
        else:
            cota.update(operacion=None, solo_lectura=True, razon=REASON_POSITION)
        resultado.append(cota)
    return resultado


def resolve_geometric(listado: list[dict[str, Any]], cambios: Any) -> tuple[dict[str, Any], float]:
    """`{id_cota: valor}` of an imported document -> the one dimension and its value.
    One dimension per request (its ids change with the revision it creates). Unknown
    id -> `CotaNoEncontrada`; read-only, non-finite, non-positive, outside
    [0.1x, 10x] of the current size, or equal to it -> `CotaInvalida`."""
    if not isinstance(cambios, dict) or not cambios:
        raise CotaInvalida("indica al menos una cota y su valor")
    if len(cambios) != 1:
        raise CotaInvalida(REASON_ONE_AT_A_TIME)
    (id_cota, valor), = cambios.items()
    por_id = {c["id"]: c for c in listado}
    cota = por_id.get(id_cota)
    if cota is None:
        raise CotaNoEncontrada(f"cota desconocida en esta revision: {id_cota}")
    if cota.get("solo_lectura"):
        raise CotaInvalida(f"cota solo lectura: {cota['razon']}")
    if not _es_numero(valor) or valor <= 0:
        raise CotaInvalida(f"'{id_cota}': el valor debe ser un numero finito mayor que 0")
    actual = cota["valor_mm"]
    if valor > FACTOR_TOPE_SIN_MAX * actual:
        raise CotaInvalida(f"'{id_cota}': mas de {FACTOR_TOPE_SIN_MAX:g} veces la medida actual")
    if valor < FACTOR_MINIMO_GEOMETRICO * actual:
        raise CotaInvalida(f"'{id_cota}': menos de {FACTOR_MINIMO_GEOMETRICO:g} veces la medida actual")
    if abs(valor - actual) <= 0.0005:
        raise CotaInvalida(MESSAGE_NO_CHANGE)
    return cota, float(valor)
