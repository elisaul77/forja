"""HTTP client used by the MCP tools to reach the FastAPI backend.

The MCP server and the `uvicorn` web process are separate OS processes
inside the same `forja` container (see `mcp_server/__init__.py`), so the
document registry is only reachable over the loopback HTTP API — this keeps
documents created via MCP visible in `GET /documentos` and the web viewer,
and vice versa.

Every function here returns a small, already-compact ``dict``/``list`` (the
same shapes the REST API returns) — no raw geometry ever crosses this
boundary (token-economy contract).
"""
from __future__ import annotations

import logging
import os
import math
import re
import time
import urllib.parse
from functools import wraps
from typing import Any

import httpx

import auth

logger = logging.getLogger(__name__)

BASE_URL = os.environ.get("FORJA_HTTP_BASE_URL", "http://localhost:8000")
_TIMEOUT = 30.0
# Exact geometry and software rendering can exceed the lightweight API
# budget. This is a read timeout; connection failures still surface quickly.
_HEAVY_TIMEOUT = float(os.environ.get("FORJA_HEAVY_TIMEOUT", "180"))
if not math.isfinite(_HEAVY_TIMEOUT) or _HEAVY_TIMEOUT <= 0:
    raise ValueError("FORJA_HEAVY_TIMEOUT debe ser un numero positivo y finito")
# Extra headroom over the script's own timeout, so the HTTP client doesn't
# give up before the backend's subprocess sandbox does.
_SCRIPT_TIMEOUT_MARGIN = 10.0


def _heavy_operation(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except httpx.TimeoutException:
            return {
                "error": True,
                "mensaje": (
                    f"{fn.__name__}: el backend no respondio dentro del plazo "
                    f"(lectura: {_HEAVY_TIMEOUT:g} s; conexion: 10 s). "
                    "Puede seguir calculando. Prueba un subconjunto de solidos "
                    "si la herramienta lo permite o aumenta FORJA_HEAVY_TIMEOUT."
                ),
            }
    return wrapped


def _heavy_timeout() -> httpx.Timeout:
    return httpx.Timeout(_HEAVY_TIMEOUT, connect=10.0)


# G1 (ADR-0014): history commits caused through the MCP are authored "agente".
_ORIGEN = {"X-Forja-Origen": "agente"}


def _headers_con_token() -> dict[str, str]:
    """Header for the two dangerous routes (`ejecutar_script`,
    `abrir_archivo`), reusing `app/auth.py`'s `obtener_token()` so the MCP
    client and the web backend always agree on the same token (env
    `FORJA_TOKEN` or the generated `.forja_token` file)."""
    return {"X-Forja-Token": auth.obtener_token()}


def _detalle(resp: httpx.Response) -> str:
    try:
        return str(resp.json().get("detail", resp.text))
    except ValueError:
        return resp.text


def estado() -> dict[str, Any]:
    with httpx.Client(base_url=BASE_URL, headers=_ORIGEN, timeout=_TIMEOUT) as c:
        salud = c.get("/salud").json()
        documentos = c.get("/documentos").json()
    return {
        "ok": bool(salud.get("ok", False)),
        "num_documentos": len(documentos),
        "versiones": {k: v for k, v in salud.items() if k != "ok"},
    }


def listar_documentos() -> list[dict[str, Any]]:
    with httpx.Client(base_url=BASE_URL, headers=_ORIGEN, timeout=_TIMEOUT) as c:
        resp = c.get("/documentos")
    resp.raise_for_status()
    return resp.json()


@_heavy_operation
def abrir_archivo(ruta: str) -> dict[str, Any]:
    # Importing a large STEP (30 MB+) easily exceeds the light 30 s budget.
    with httpx.Client(base_url=BASE_URL, headers=_ORIGEN, timeout=_heavy_timeout()) as c:
        resp = c.post(
            "/documentos/desde_ruta", json={"ruta": ruta}, headers=_headers_con_token()
        )
    if resp.status_code >= 400:
        return {"error": True, "mensaje": _detalle(resp)}
    data = resp.json()
    return {"id": data["id"], "nombre": data["nombre"]}


@_heavy_operation
def resumen_documento(
    doc_id: str, tope_solidos: int = 30, umbral_astilla_mm3: float = 1.0
) -> dict[str, Any]:
    with httpx.Client(base_url=BASE_URL, headers=_ORIGEN, timeout=_heavy_timeout()) as c:
        resp = c.get(
            f"/documentos/{doc_id}",
            params={"tope_solidos": tope_solidos, "umbral_astilla_mm3": umbral_astilla_mm3},
        )
    if resp.status_code >= 400:
        return {"error": True, "mensaje": _detalle(resp)}
    data = resp.json()
    data.pop("revision", None)  # browser cache token; keep the MCP summary compact
    return data


def _detalle_estructurado(resp: httpx.Response) -> dict[str, Any]:
    """Like `_detalle`, but preserves a dict-shaped `detail` instead of
    flattening it to a string — `ejecutar_script`'s errors (Phase 5A) carry
    `linea`/`codigo_linea` alongside `mensaje` when the failure could be
    attributed to a specific line in the user's script."""
    try:
        detalle = resp.json().get("detail", resp.text)
    except ValueError:
        return {"mensaje": resp.text}
    if isinstance(detalle, dict):
        return detalle
    return {"mensaje": str(detalle)}


def ejecutar_script(
    codigo: str | None = None,
    ruta: str | None = None,
    timeout: float = 30.0,
    nombre: str | None = None,
    variables: dict[str, Any] | None = None,
    documento_id: str | None = None,
    actualizar_si_existe: bool = True,
) -> dict[str, Any]:
    reutilizado = False
    if documento_id is None and nombre and actualizar_si_existe:
        # Editing should keep one document (history + live view), not spawn
        # copies: reuse the single existing document with the same name.
        try:
            with httpx.Client(base_url=BASE_URL, headers=_ORIGEN, timeout=_TIMEOUT) as c:
                docs = c.get("/documentos").json()
            iguales = [d["id"] for d in docs if d.get("nombre") == nombre]
            if len(iguales) == 1:
                documento_id, reutilizado = iguales[0], True
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            pass
    payload: dict[str, Any] = {"timeout": timeout}
    if codigo is not None:
        payload["codigo"] = codigo
    if ruta is not None:
        payload["ruta"] = ruta
    if nombre:
        payload["nombre"] = nombre
    if variables is not None:
        payload["variables"] = variables
    if documento_id is not None:
        payload["documento_id"] = documento_id
    http_timeout = timeout + _SCRIPT_TIMEOUT_MARGIN
    with httpx.Client(base_url=BASE_URL, headers=_ORIGEN, timeout=http_timeout) as c:
        resp = c.post("/documentos/script", json=payload, headers=_headers_con_token())
    if resp.status_code >= 400:
        return {"error": True, **_detalle_estructurado(resp)}
    data = resp.json()
    if reutilizado and isinstance(data, dict):
        data["actualizado_por_nombre"] = True
    return data


PUBLIC_URL_DEFAULT = "http://localhost:8710"
_HOST_RE = re.compile(r"[A-Za-z0-9.\-]+(:[0-9]{1,5})?|\[[0-9A-Fa-f:.]+\](:[0-9]{1,5})?")
# `/documentos/<id>/descarga/<slug>.<stl|3mf>` -- nothing after the extension.
_DESCARGA_RE = re.compile(r"/documentos/[A-Za-z0-9_-]+/descarga/[A-Za-z0-9_-]+\.(?:stl|3mf)")


def _base_publica() -> str:
    """`FORJA_PUBLIC_URL` (read at call time) as `scheme://host[:port]`, no
    trailing slash. Anything that is not exactly that (other scheme, userinfo,
    path, query, fragment, whitespace/control chars, non-string) degrades to
    the default instead of raising. The fallback logs a fixed message and
    never the value, since a rejected value may carry credentials."""
    crudo = os.environ.get("FORJA_PUBLIC_URL", PUBLIC_URL_DEFAULT)
    base = _validar_base(crudo)
    if base is None:
        logger.warning("FORJA_PUBLIC_URL invalida (valor omitido del log); se usa %s", PUBLIC_URL_DEFAULT)
        return PUBLIC_URL_DEFAULT
    return base


def _validar_base(crudo: Any) -> str | None:
    if not isinstance(crudo, str) or not crudo.isascii():
        return None
    if any(ord(ch) <= 32 or ord(ch) == 127 for ch in crudo) or "\\" in crudo:
        return None
    try:
        partes = urllib.parse.urlsplit(crudo)
        if partes.scheme not in ("http", "https") or not partes.netloc:
            return None
        if "?" in crudo or "#" in crudo or partes.path not in ("", "/"):
            return None
        if partes.query or partes.fragment or "@" in partes.netloc:
            return None
        if not _HOST_RE.fullmatch(partes.netloc) or not partes.hostname:
            return None
        partes.port  # noqa: B018 - raises ValueError when out of range
    except ValueError:
        return None
    return f"{partes.scheme}://{partes.netloc}"


def _enlace_orca(descarga: Any) -> dict[str, str]:
    """`{url_descarga, enlace_orca}` from the backend's relative `descarga`
    path, or `{}` when it is not exactly a plain download path (a query,
    fragment or trailing junk would end up in the filename Orca saves).
    Pure string work: no network call, no token."""
    if not isinstance(descarga, str) or _DESCARGA_RE.fullmatch(descarga) is None:
        return {}
    url = _base_publica() + descarga
    return {
        "url_descarga": url,
        # Encoded exactly once: Orca decodes `file=` once (curl_easy_unescape).
        "enlace_orca": "orcaslicer://open?file=" + urllib.parse.quote(url, safe=""),
    }


@_heavy_operation
def exportar(doc_id: str, formato: str, por_solido: bool = False) -> dict[str, Any]:
    with httpx.Client(base_url=BASE_URL, headers=_ORIGEN, timeout=_heavy_timeout()) as c:
        resp = c.get(
            f"/documentos/{doc_id}/exportar",
            params={"formato": formato, "por_solido": str(por_solido).lower()},
        )
    if resp.status_code >= 400:
        return {"error": True, "mensaje": _detalle(resp)}
    resultado = resp.json()
    if formato in ("stl", "3mf") and not por_solido and isinstance(resultado, dict):
        resultado.update(_enlace_orca(resultado.get("descarga")))
    return resultado


@_heavy_operation
def check_colisiones(doc_id: str, tolerancia_mm3: float = 0.5) -> dict[str, Any]:
    with httpx.Client(base_url=BASE_URL, headers=_ORIGEN, timeout=_heavy_timeout()) as c:
        resp = c.get(
            f"/documentos/{doc_id}/colisiones", params={"tolerancia_mm3": tolerancia_mm3}
        )
    if resp.status_code >= 400:
        return {"error": True, "mensaje": _detalle(resp)}
    return resp.json()


@_heavy_operation
def cupon(
    doc_id: str,
    pieza: str | None = None,
    holgura_max: float | None = None,
    margen: float | None = None,
    caja: dict[str, list[float]] | None = None,
) -> dict[str, Any]:
    cuerpo = {k: v for k, v in (
        ("pieza", pieza), ("holgura_max", holgura_max), ("margen", margen), ("caja", caja),
    ) if v is not None}
    with httpx.Client(base_url=BASE_URL, headers=_ORIGEN, timeout=_heavy_timeout()) as c:
        resp = c.post(f"/documentos/{doc_id}/cupon", json=cuerpo, headers=_headers_con_token())
    if resp.status_code >= 400:
        return {"error": True, "mensaje": _detalle(resp)}
    resultado = resp.json()
    if isinstance(resultado, dict):
        resultado.update(_enlace_orca(resultado.get("descarga")))
    return resultado


@_heavy_operation
def percibir(
    doc_id: str,
    capas: str = "contactos",
    cortes_z: list[float] | None = None,
    eje: str = "z",
    resolucion: int = 40,
    solidos: list[str] | None = None,
    proximidad_mm: float = 5.0,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "capas": capas,
        "eje": eje,
        "resolucion": resolucion,
        "proximidad_mm": proximidad_mm,
    }
    if cortes_z:
        params["cortes_z"] = cortes_z
    if solidos:
        params["solidos"] = solidos
    with httpx.Client(base_url=BASE_URL, headers=_ORIGEN, timeout=_heavy_timeout()) as c:
        resp = c.get(f"/documentos/{doc_id}/percibir", params=params)
    if resp.status_code >= 400:
        return {"error": True, "mensaje": _detalle(resp)}
    return resp.json()


def parametros(
    doc_id: str, valores: dict[str, Any] | None = None, materiales: dict[str, Any] | None = None,
    confirmar_script: bool = False,
) -> dict[str, Any]:
    """`None` -> read the schema + current values (token-free GET); a dict
    -> apply them (token-protected POST, re-runs the stored script).
    `materiales` (fdm-D) -> POST /materiales first (no re-run); the answer
    then carries `materiales` (and, alone, also the read of the schema)."""
    resultado_materiales: dict[str, Any] | None = None
    if materiales is not None:
        with httpx.Client(base_url=BASE_URL, headers=_ORIGEN, timeout=_TIMEOUT) as c:
            resp = c.post(
                f"/documentos/{doc_id}/materiales",
                json={"materiales": materiales},
                headers=_headers_con_token(),
            )
        if resp.status_code >= 400:
            return {"error": True, **_detalle_estructurado(resp)}
        resultado_materiales = resp.json()["materiales"]
    respuesta = _parametros(doc_id, valores, confirmar_script)
    if resultado_materiales is not None and not respuesta.get("error"):
        respuesta["materiales"] = resultado_materiales
    return respuesta


def _parametros(doc_id: str, valores: dict[str, Any] | None = None,
                confirmar_script: bool = False) -> dict[str, Any]:
    with httpx.Client(
        base_url=BASE_URL, headers=_ORIGEN, timeout=_TIMEOUT if valores is None else _TIMEOUT + _SCRIPT_TIMEOUT_MARGIN
    ) as c:
        if valores is None:
            resp = c.get(f"/documentos/{doc_id}/parametros")
        else:
            resp = c.post(
                f"/documentos/{doc_id}/parametros",
                json={"valores": valores, **({"confirmar_script": True} if confirmar_script else {})},
                headers=_headers_con_token(),
            )
    if resp.status_code >= 400:
        return {"error": True, **_detalle_estructurado(resp)}
    return resp.json()


@_heavy_operation
def check_fdm(
    doc_id: str, boquilla: float = 0.4, cama: str = "220x220x250", angulo_max: float = 45.0
) -> dict[str, Any]:
    with httpx.Client(base_url=BASE_URL, headers=_ORIGEN, timeout=_heavy_timeout()) as c:
        resp = c.get(
            f"/documentos/{doc_id}/fdm",
            params={"boquilla": boquilla, "cama": cama, "angulo_max": angulo_max},
        )
    if resp.status_code >= 400:
        return {"error": True, "mensaje": _detalle(resp)}
    return resp.json()


@_heavy_operation
def captura(
    doc_id: str,
    azimut: float = 45.0,
    elevacion: float = 25.0,
    ancho: int = 512,
    alto: int = 512,
    colores_por_solido: bool = True,
    solidos: list[str] | None = None,
) -> dict[str, Any]:
    """Fetch the rendered PNG bytes + the `X-Forja-Captura` caption header;
    the caller (`mcp_server.tools.captura`) wraps the bytes as an MCP
    `Image` content block."""
    params: dict[str, Any] = {
        "azimut": azimut,
        "elevacion": elevacion,
        "ancho": ancho,
        "alto": alto,
        "colores_por_solido": str(colores_por_solido).lower(),
    }
    if solidos:
        params["solidos"] = solidos
    with httpx.Client(base_url=BASE_URL, headers=_ORIGEN, timeout=_heavy_timeout()) as c:
        resp = c.get(f"/documentos/{doc_id}/captura", params=params)
    if resp.status_code >= 400:
        return {"error": True, "mensaje": _detalle(resp)}
    return {
        "png": resp.content,
        "caption": resp.headers.get("x-forja-captura", ""),
    }


# ---------------------------------------------------------------- Phase 4:
# notes/strokes ("notas"/"trazos") and history/versioning — thin wrappers
# over the same compact backend endpoints the web UI uses (ADR-0005:
# snapshot/rollback/naming logic lives server-side, never here).


def leer_notas(doc_id: str, detalle: bool = False) -> Any:
    with httpx.Client(base_url=BASE_URL, headers=_ORIGEN, timeout=_TIMEOUT) as c:
        resp = c.get(f"/documentos/{doc_id}/notas", params={"detalle": str(detalle).lower()})
    if resp.status_code >= 400:
        return {"error": True, "mensaje": _detalle(resp)}
    return resp.json()["resumen"]


def crear_nota(doc_id: str, comentario: str, referencia: dict[str, Any]) -> dict[str, Any]:
    with httpx.Client(base_url=BASE_URL, headers=_ORIGEN, timeout=_TIMEOUT) as c:
        resp = c.post(
            f"/documentos/{doc_id}/notas",
            json={"comentario": comentario, "referencia": referencia},
        )
    if resp.status_code >= 400:
        return {"error": True, "mensaje": _detalle(resp)}
    return resp.json()


def borrar_nota(doc_id: str, nota_id: str) -> dict[str, Any]:
    with httpx.Client(base_url=BASE_URL, headers=_ORIGEN, timeout=_TIMEOUT) as c:
        resp = c.delete(f"/documentos/{doc_id}/notas/{nota_id}")
    if resp.status_code >= 400:
        return {"error": True, "mensaje": _detalle(resp)}
    return resp.json()


def leer_historial(doc_id: str) -> Any:
    with httpx.Client(base_url=BASE_URL, headers=_ORIGEN, timeout=_TIMEOUT) as c:
        resp = c.get(f"/documentos/{doc_id}/historial")
    if resp.status_code >= 400:
        return {"error": True, "mensaje": _detalle(resp)}
    return resp.json()


def restaurar(doc_id: str, snapshot: str) -> dict[str, Any]:
    with httpx.Client(base_url=BASE_URL, headers=_ORIGEN, timeout=_TIMEOUT) as c:
        resp = c.post(
            f"/documentos/{doc_id}/restaurar",
            json={"snapshot": snapshot},
            headers=_headers_con_token(),
        )
    if resp.status_code >= 400:
        return {"error": True, "mensaje": _detalle(resp)}
    return resp.json()


_ACCIONES_RAMA = ("listar", "crear", "cambiar", "renombrar", "borrar", "pasos", "comparar",
                  "fusionar", "traer_pieza", "hito", "hitos")


def rama(doc_id: str, accion: str, nombre: str | None = None, desde: str | None = None,
         a: str | None = None, piezas: list[str] | None = None, estrategia: str | None = None,
         forzar: bool = False, simular: bool = False) -> Any:
    """G2/G3: thin wrapper over `app/ramas.py`'s REST routes; mutations
    carry the token. Path segments are URL-quoted; the server validates
    every branch name and sha."""
    from urllib.parse import quote

    if accion not in _ACCIONES_RAMA:
        return {"error": True, "mensaje": f"accion desconocida: {accion!r} (usa {'|'.join(_ACCIONES_RAMA)})"}
    base = f"/documentos/{quote(doc_id, safe='')}/ramas"
    q = (lambda v: quote(v or "", safe=""))
    timeout = _heavy_timeout() if accion in ("cambiar", "comparar", "fusionar", "traer_pieza") else _TIMEOUT
    doc_base = f"/documentos/{quote(doc_id, safe='')}"
    with httpx.Client(base_url=BASE_URL, headers=_ORIGEN, timeout=timeout) as c:
        if accion == "listar":
            resp = c.get(base)
        elif accion in ("fusionar", "traer_pieza"):
            if not desde:
                return {"error": True, "mensaje": f"{accion} necesita desde (rama o sha_corto del paso)"}
            if accion == "traer_pieza" and not piezas:
                return {"error": True, "mensaje": "traer_pieza necesita piezas (lista de nombres)"}
            resp = c.post(f"{base}/fusionar", json={
                "desde": desde, "piezas": piezas if accion == "traer_pieza" else None,
                "estrategia": estrategia, "forzar": forzar, "simular": simular}, headers=_headers_con_token())
        elif accion == "hitos":
            resp = c.get(f"{doc_base}/hitos")
        elif accion == "hito":
            if not nombre:
                return {"error": True, "mensaje": "hito necesita nombre"}
            resp = c.post(f"{doc_base}/hitos", json={"nombre": nombre, "paso": desde, "descripcion": a},
                          headers=_headers_con_token())
        elif accion == "pasos":
            if nombre is None:
                actual = c.get(base)
                if actual.status_code >= 400:
                    return {"error": True, "mensaje": _detalle(actual)}
                nombre = actual.json().get("activa", "main")
            resp = c.get(f"{base}/{q(nombre)}/pasos")
        elif accion == "comparar":
            if not desde:
                return {"error": True, "mensaje": "comparar necesita desde (A) y opcionalmente a (B, por defecto la rama activa)"}
            destino = a
            if not destino:
                actual = c.get(base)
                if actual.status_code >= 400:
                    return {"error": True, "mensaje": _detalle(actual)}
                destino = actual.json().get("activa", "main")
            resp = c.get(f"/documentos/{quote(doc_id, safe='')}/comparar", params={"a": desde, "b": destino})
        elif not nombre:
            return {"error": True, "mensaje": f"{accion} necesita nombre"}
        elif accion == "crear":
            resp = c.post(base, json={"nombre": nombre, "desde": desde}, headers=_headers_con_token())
        elif accion == "cambiar":
            resp = c.post(f"{base}/{q(nombre)}/activar", headers=_headers_con_token())
        elif accion == "renombrar":
            if not a:
                return {"error": True, "mensaje": "renombrar necesita a (nombre nuevo)"}
            resp = c.post(f"{base}/{q(nombre)}/renombrar", json={"a": a}, headers=_headers_con_token())
        else:  # borrar
            resp = c.delete(f"{base}/{q(nombre)}", headers=_headers_con_token())
    if resp.status_code >= 400:
        return {"error": True, "mensaje": _detalle(resp)}
    datos = resp.json()
    if accion == "cambiar":
        return {k: datos.get(k) for k in ("rama", "sha_corto", "revision", "volumen", "solidos", "valido", "avisos")}
    return datos


def eliminar_documento(doc_id: str) -> dict[str, Any]:
    """Permanently delete a document (file + notes + history). Token-
    protected (ADR-0005); not an MCP tool — used by tests/tooling that
    create throwaway documents against the live backend (see `tests/
    test_mcp_tools.py`'s cleanup fixture) so they never pollute the real
    `documentos_data/`."""
    with httpx.Client(base_url=BASE_URL, headers=_ORIGEN, timeout=_TIMEOUT) as c:
        resp = c.delete(f"/documentos/{doc_id}", headers=_headers_con_token())
    if resp.status_code >= 400:
        return {"error": True, "mensaje": _detalle(resp)}
    return resp.json()


# ---------------------------------------------------------------- Phase 6:
# assemblies ("ensamble") and the read-only local-service bridges
# ("puentes") — thin wrappers over `app/assembly_routes.py` and
# `app/bridges.py`, the two REST surfaces that own the assembly
# transaction/snapshot logic and the bridge allowlist (ADR-0005: nothing
# here re-implements or widens either one).


def _error_red(contexto: str, exc: Exception) -> dict[str, Any]:
    """Network failure is itself an answer: `{error: true, mensaje}`, the
    client-side twin of `app/bridges.py`'s 502 wording, so no httpx
    exception ever escapes a tool as a traceback."""
    return {"error": True,
            "mensaje": f"{contexto}: no se pudo consultar el backend ({type(exc).__name__})"}


@_heavy_operation
def ensamble(
    doc_id: str,
    articulaciones: list[dict[str, Any]] | None = None,
    valores: dict[str, float] | None = None,
    quitar: bool = False,
) -> dict[str, Any]:
    """Read (`None`), define (`articulaciones`), pose (`valores`) or clear
    (`quitar=True`) a document's assembly, one mode at a time. The GET is
    token-free; the three mutations carry `X-Forja-Token`."""
    modos = [nombre for nombre, activo in (
        ("articulaciones", articulaciones is not None),
        ("valores", valores is not None),
        ("quitar", bool(quitar)),
    ) if activo]
    if len(modos) > 1:
        return {"error": True, "mensaje": "un solo modo a la vez: " + " y ".join(modos)}
    if articulaciones is not None and not isinstance(articulaciones, list):
        return {"error": True, "mensaje": "articulaciones debe ser una lista de articulaciones"}
    if articulaciones is not None and not articulaciones:
        return {"error": True, "mensaje": "articulaciones esta vacio; define al menos una"}
    if valores is not None and not isinstance(valores, dict):
        return {"error": True, "mensaje": "valores debe ser un objeto {articulacion: numero}"}
    if valores is not None and not valores:
        return {"error": True, "mensaje": "valores esta vacio; indica al menos una articulacion"}

    ruta = f"/documentos/{doc_id}/ensamble"
    try:
        # The pose rebuilds the document through the kernel (de/hacia STEP +
        # reimport), so it gets the heavy budget like `percibir`/`exportar`.
        with httpx.Client(base_url=BASE_URL, headers=_ORIGEN, timeout=_heavy_timeout()) as c:
            if articulaciones is not None:
                resp = c.post(ruta, json={"articulaciones": articulaciones}, headers=_headers_con_token())
            elif valores is not None:
                resp = c.post(f"{ruta}/pose", json={"valores": valores}, headers=_headers_con_token())
            elif quitar:
                resp = c.delete(ruta, headers=_headers_con_token())
            else:
                resp = c.get(ruta)
    except httpx.HTTPError as exc:
        return _error_red("ensamble", exc)
    if resp.status_code >= 400:
        # `_detalle_estructurado` keeps a dict-shaped `detail` intact; the
        # 409 format refusal (`assemblies.MENSAJE_FORMATO`) travels as its
        # own Spanish sentence, verbatim.
        return {"error": True, **_detalle_estructurado(resp)}
    return resp.json()


_MENSAJE_POSE = "respuesta inesperada del puente de suspension (/pose)"
_MENSAJE_CONFIG = "respuesta inesperada del puente de suspension (/config)"

# The three cases a `/pose` table can carry, with the row key that holds the
# value: `subida_rueda` rows are millimetres, the other two degrees.
_CASOS_POSE = (("subida_rueda", "mm"), ("cabeceo_sobre_eje_ruedas", "grados"),
               ("articulacion", "grados"))


def _numero_del_puente(valor: Any) -> float:
    """A FINITE number out of the `/pose` table, or a clean refusal.

    A numeric *string* is refused on purpose: the table documents numbers, and
    coercing `"4"` (or worse, a number with an unknown unit baked into it) is
    how a wrong value would slip through as if it had been measured. So is a
    non-finite one: `Infinity`/`NaN` are not measurements, and the backend
    cannot even serialise them (its own 500 is what the agent would read)."""
    if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not math.isfinite(valor):
        raise TypeError(_MENSAJE_POSE)
    return float(valor)


def _signo_del_puente(datos: dict[str, Any]) -> bool | None:
    """The simulator's own verdict on the compression sign, or `None` when it
    did not give one. The absence is reported by OMITTING the key, never as an
    invented `false`: "the sign is wrong" and "nobody measured it" are
    different answers and an agent acts on the difference."""
    if "signo_correcto_al_subir" not in datos:
        return None
    valor = datos["signo_correcto_al_subir"]
    if not isinstance(valor, bool):
        raise TypeError(_MENSAJE_POSE)
    return valor


def _proyeccion_pose(datos: dict[str, Any], limites: list[float]) -> dict[str, Any]:
    """Compact, exception-first projection of a suspension-sim `/pose` table
    (Phase 6.4, decision D3): one row per requested value and case, `dentro`
    dropped, and the value-0 row — the march length, the baseline
    `compresion` is measured against — reported once as `marcha` instead of
    once per case.

    Every shape it walks is validated first (payload object, case list, row
    object, value key, side object with numeric `L`/`compresion`): a payload
    that is not the documented table raises `TypeError` with the `(/pose)`
    wording rather than being half-read, because a half-read table is how an
    upstream surprise would reach the agent as a traceback — or worse, as
    invented numbers.

    `fuera_de_carrera` is decided by the EVIDENCE, not by a flag alone: a
    side is out of range when its observed `L` violates `limites`, or when
    the simulator's own `dentro` says so (a secondary signal — belt and
    braces in that direction, never a way to hide an `L` that is out of
    range). `limites` is therefore load-bearing: an agent that never sees
    `mensaje` numbers cannot read a bottomed-out shock absorber as inside."""
    if not isinstance(datos, dict):
        raise TypeError(_MENSAJE_POSE)
    fuera_de_carrera: list[dict[str, Any]] = []
    marcha: dict[str, Any] = {}
    proyectado: dict[str, Any] = {}
    for caso, clave in _CASOS_POSE:
        filas = datos.get(caso)
        if filas is None:
            continue
        if not isinstance(filas, list):
            raise TypeError(_MENSAJE_POSE)
        compactas = []
        for fila in filas:
            if not isinstance(fila, dict) or clave not in fila:
                raise TypeError(_MENSAJE_POSE)
            valor = _numero_del_puente(fila[clave])
            lados: dict[str, Any] = {}
            for lado, medida in fila.items():
                if lado in ("mm", "grados"):
                    continue
                if not isinstance(medida, dict):
                    raise TypeError(_MENSAJE_POSE)
                try:
                    largo = _numero_del_puente(medida["L"])
                    compresion = _numero_del_puente(medida["compresion"])
                except KeyError as exc:
                    raise TypeError(_MENSAJE_POSE) from exc
                dentro = medida.get("dentro", True)
                if not isinstance(dentro, bool):
                    raise TypeError(_MENSAJE_POSE)
                lados[lado] = {"L": largo, "compresion": compresion}
                if not limites[0] <= largo <= limites[1] or not dentro:
                    fuera_de_carrera.append({"caso": caso, "valor": valor, "lado": lado, "L": largo})
            if valor == 0:
                marcha = marcha or lados
                continue
            compactas.append({clave: valor, **lados})
        if compactas:
            proyectado[caso] = compactas
    return {"marcha": marcha, **proyectado, "fuera_de_carrera": fuera_de_carrera}


@_heavy_operation
def suspension(
    subida_mm: float | None = None,
    cabeceo_deg: float | None = None,
    articulacion_deg: float | None = None,
    crudo: bool = False,
) -> dict[str, Any]:
    """Query the mostertruck suspension simulator through Forja's read-only
    bridge: the shock lengths and compressions at one lift / pitch /
    articulation pose. Omitted anchors and geometry come from the
    simulator's own `/config` (the backend fills them); nothing is ever
    persisted there — `/pose` is pure computation, and `app/bridges.py`'s
    allowlist refuses every mutating route by construction."""
    casos = {"subida_mm": subida_mm, "cabeceo_deg": cabeceo_deg, "articulacion_deg": articulacion_deg}
    if all(valor is None for valor in casos.values()):
        casos["subida_mm"] = 4.0  # the case that verifies the sign, 4 mm up
    pedido: dict[str, list[float]] = {}
    for clave, valor in casos.items():
        if valor is None:
            # Always send every case: an omitted one would make the
            # simulator fall back to its own 5-value sweep, and the agent
            # would get rows (and `fuera_de_carrera` entries) for poses it
            # never asked about. A lone 0 is the march row: no row, no
            # violation, and it is what `marcha` is read from.
            pedido[clave] = [0.0]
            continue
        try:
            numero = float(valor)
        except (TypeError, ValueError):
            return {"error": True, "mensaje": f"{clave} debe ser un numero (mm o grados)"}
        # 0 always rides along: it is the march row (`marcha` in the compact
        # answer) and the baseline every `compresion` is measured against.
        pedido[clave] = [0.0] if numero == 0 else sorted({0.0, numero})

    try:
        with httpx.Client(base_url=BASE_URL, headers=_ORIGEN, timeout=_TIMEOUT) as c:
            config = c.get("/puentes/suspension/config")
        if config.status_code >= 400:
            return {"error": True, "mensaje": _detalle(config)}
        try:
            geo = config.json()["geo"]
            limites = [float(geo["L_min"]), float(geo["L_max"])]
            if not all(math.isfinite(limite) for limite in limites):
                raise ValueError("limites no finitos")
        except (KeyError, TypeError, ValueError):
            return {"error": True, "mensaje": _MENSAJE_CONFIG}
        start = time.perf_counter()
        with httpx.Client(base_url=BASE_URL, headers=_ORIGEN, timeout=_TIMEOUT) as c:
            resp = c.post("/puentes/suspension/pose", json=pedido, headers=_headers_con_token())
        ms = round(1000 * (time.perf_counter() - start))
    except httpx.HTTPError as exc:
        return _error_red("suspension", exc)
    if resp.status_code >= 400:
        return {"error": True, **_detalle_estructurado(resp)}

    datos = resp.json()
    if not isinstance(datos, dict):
        return {"error": True, "mensaje": _MENSAJE_POSE}
    if crudo:
        return {**datos, "limites": limites, "ms": ms}
    try:
        proyectado = _proyeccion_pose(datos, limites)
        signo = _signo_del_puente(datos)
    except (AttributeError, KeyError, TypeError, ValueError):
        # `_proyeccion_pose` and `_signo_del_puente` validate every shape they
        # walk and raise `TypeError` with this same wording; the wider catch is
        # the belt to that braces, so an unforeseen shape still lands as
        # `{error, mensaje}` instead of a traceback on the MCP wire.
        return {"error": True, "mensaje": _MENSAJE_POSE}
    respuesta: dict[str, Any] = {"limites": limites, **proyectado, "ms": ms}
    if signo is not None:
        # First key, as documented — and absent, not `false`, when the
        # simulator gave no verdict at all.
        respuesta = {"signo_correcto_al_subir": signo, **respuesta}
    return respuesta


def puentes() -> dict[str, Any]:
    """Readiness of Forja's three local-service bridges: `{suspension:
    {habilitado, disponible}[, mensaje], blender: {...}, kybercore: {...}}`.
    `habilitado` = the bridge is switched on by configuration; `disponible`
    = it also answers right now. A disabled or unreachable service is a
    `false` (plus its Spanish `mensaje` when it was enabled), never an
    exception — the same answer the web panel reads."""
    try:
        with httpx.Client(base_url=BASE_URL, headers=_ORIGEN, timeout=_TIMEOUT) as c:
            resp = c.get("/puentes")
    except httpx.HTTPError as exc:
        return _error_red("puentes", exc)
    if resp.status_code >= 400:
        return {"error": True, "mensaje": _detalle(resp)}
    return resp.json()
