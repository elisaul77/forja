"""In-memory document registry for uploaded STEP/STL files (Phase 1).

Uploaded files are persisted under ``DOCUMENTOS_DIR`` (bind-mounted at
``/data/documentos``) and indexed in an in-memory dict keyed by a UUID
``id``. Only bbox/volume/solid-count/validity summaries are ever returned —
never raw vertices (ADR-0001 kernel boundary).
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import unicodedata
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import trimesh
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

import assemblies
import auth
import materiales
import eventos
import export as export_solidos
import naming
import notes
import parametros
import percepcion
import piece_mesh
import render
import revision
import scripts_runner
import solids
import versioning
from checks import colisiones as colisiones_checks
from checks import fdm as fdm_checks
from kernel import b123d_kernel, mesh

router = APIRouter()

# Overridable via env `FORJA_DATA_DIR` (default `/data/documentos`, the real
# bind-mounted volume) so the test suite can point every bit of state
# derived from it (this dir, `.historial/`, `{doc_id}.notas.json`,
# `{doc_id}.meta.json`, `.forja_token`) at a throwaway tmp directory instead
# of polluting the user's real `documentos_data/` (see `tests/conftest.py`).
# Must be read at import time, before `DOCUMENTOS_DIR.mkdir()` below.
DOCUMENTOS_DIR = Path(os.environ.get("FORJA_DATA_DIR", "/data/documentos"))
DOCUMENTOS_DIR.mkdir(parents=True, exist_ok=True)

EXPORTADOS_DIR = DOCUMENTOS_DIR / "exportados"

# Roots an MCP `abrir_archivo`/`POST /documentos/desde_ruta` call is allowed
# to read from: the documentos storage itself (e.g. re-opening an exported
# file) and the read-only mounted sources volume (Phase 3 contract: reject
# traversal/absolute paths outside these roots).
_FUENTES_DIR = Path("/data/fuentes")
ALLOWED_READ_ROOTS = (DOCUMENTOS_DIR, _FUENTES_DIR)

_registry: dict[str, dict[str, Any]] = {}
# doc_id -> path of the stored source file (STEP or STL). Kept out of
# ``_registry`` so the public registro dict (returned by POST/GET /documentos)
# never grows extra keys beyond {id, nombre, bbox, volumen, solidos, valido}.
_files: dict[str, Path] = {}
# doc_id -> last committed geometry revision, separate from the established
# six-field registry record.
_revisiones: dict[str, str] = {}

_STEP_EXTS = (".step", ".stp")
_STL_EXTS = (".stl",)

_DEFAULT_MAX_UPLOAD_MB = "100"
_UPLOAD_CHUNK_SIZE = 1024 * 1024  # 1 MiB


def _max_upload_bytes() -> int:
    """Read the configured upload cap fresh on every call (env `FORJA_MAX_UPLOAD_MB`).

    Read at call-time rather than at import-time so tests can override it via
    ``monkeypatch.setenv`` without reloading the module.
    """
    mb = float(os.environ.get("FORJA_MAX_UPLOAD_MB", _DEFAULT_MAX_UPLOAD_MB))
    return int(mb * 1024 * 1024)


async def _read_upload_bounded(file: UploadFile, max_bytes: int) -> bytes:
    """Stream-read an upload in chunks, aborting once ``max_bytes`` is exceeded.

    Guards against a missing/forged ``Content-Length`` header: the cap is
    enforced against bytes actually read, not against the declared size.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_UPLOAD_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail="archivo demasiado grande")
        chunks.append(chunk)
    return b"".join(chunks)


def _validar_ruta_permitida(ruta_str: str) -> Path:
    """Resolve ``ruta_str`` and reject it unless it sits inside one of
    ``ALLOWED_READ_ROOTS`` (path-traversal guard for `abrir_archivo` /
    `POST /documentos/desde_ruta`, Phase 3 contract)."""
    try:
        candidato = Path(ruta_str).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail="ruta invalida o inexistente") from exc

    for raiz in ALLOWED_READ_ROOTS:
        raiz_resuelta = raiz.resolve()
        if candidato == raiz_resuelta or raiz_resuelta in candidato.parents:
            return candidato

    raise HTTPException(
        status_code=400,
        detail="ruta fuera de las raices permitidas (/data/documentos o /data/fuentes)",
    )


def _ruta_meta(doc_id: str) -> Path:
    """Sidecar carrying just the original upload filename (Phase 4:
    `recargar_documentos` needs it to rebuild `nombre` after a restart —
    the stored file itself is only ever named ``{doc_id}{ext}``)."""
    return DOCUMENTOS_DIR / f"{doc_id}.meta.json"


def _guardar_meta(doc_id: str, nombre: str) -> None:
    _ruta_meta(doc_id).write_text(json.dumps({"nombre": nombre}))


def _leer_meta(doc_id: str, nombre_por_defecto: str) -> str:
    ruta = _ruta_meta(doc_id)
    if not ruta.exists():
        return nombre_por_defecto
    try:
        return json.loads(ruta.read_text()).get("nombre", nombre_por_defecto)
    except (json.JSONDecodeError, OSError):
        return nombre_por_defecto


def recargar_documentos() -> None:
    """Rebuild the in-memory registry from files already on ``DOCUMENTOS_DIR``
    at startup (Phase 4 contract: a container restart must not orphan a
    document's notes — the registry is what makes a doc_id "exist" for the
    notes/historial routes' 404 checks and the web viewer's document list).
    Corrupt/unreadable leftovers are skipped rather than crashing startup.

    Phase 5A: an existing ``{doc_id}.solidos.json`` sidecar is left exactly
    as-is (its bbox/volume/names are still correct — the STEP bytes on disk
    haven't changed since it was written) rather than recomputed, since a
    fresh recompute has no way to know which children were genuinely named
    by a script's dict vs auto-generated ``solido_N`` (`app/solids.py`'s
    naming can only be trusted straight from `scripts_runner`, never
    re-derived from the STEP file's own labels). Only a STEP document that
    never got one (predates this phase, or was never opened via a script)
    gets a fresh, flat ``solido_N`` breakdown here, so `resumen_documento`
    still has *something* to show.
    """
    for ruta in sorted(DOCUMENTOS_DIR.glob("*")):
        ext = ruta.suffix.lower()
        if ruta.is_dir() or ext not in _STEP_EXTS + _STL_EXTS:
            continue
        doc_id = ruta.stem
        if doc_id in _registry:
            continue
        try:
            analisis = _analyze(ruta, ext)
        except Exception:  # noqa: BLE001 - skip, never crash startup
            continue
        nombre = _leer_meta(doc_id, ruta.name)
        _registry[doc_id] = {"id": doc_id, "nombre": nombre, **analisis}
        _files[doc_id] = ruta
        if ext in _STEP_EXTS and solids.cargar(doc_id) is None:
            try:
                shape = b123d_kernel.import_from_step(ruta)
                solids.guardar(doc_id, solids.construir_entradas(shape))
            except Exception:  # noqa: BLE001 - best-effort bootstrap only
                pass
        _fijar_revision(doc_id)


def calcular_revision(doc_id: str) -> str | None:
    """``revision`` of ``doc_id`` from its files on disk right now (STEP/STL
    + `solidos.json`), or ``None`` if the stored file is gone."""
    ruta = _files.get(doc_id)
    if ruta is None:
        return None
    try:
        geometria = ruta.read_bytes()
    except OSError:
        return None
    ruta_solidos_doc = solids.ruta_solidos(doc_id)
    sidecar = ruta_solidos_doc.read_bytes() if ruta_solidos_doc.exists() else None
    return revision.calcular(geometria, ruta.suffix.lower() in _STEP_EXTS, [("solidos.json", sidecar)])


def _fijar_revision(doc_id: str) -> tuple[str | None, str | None]:
    """Recompute and store ``doc_id``'s revision in `_revisiones`;
    returns ``(previous, new)``. Publishes nothing."""
    previa = _revisiones.get(doc_id)
    nueva = calcular_revision(doc_id)
    if nueva is not None:
        _revisiones[doc_id] = nueva
    return previa, nueva


def confirmar_revision(doc_id: str, tipo: str = "documento_actualizado") -> str | None:
    """Call after every commit that may have changed ``doc_id``'s geometry
    (F11.1): stores the new ``revision`` and publishes
    ``tipo`` — always for ``documento_creado``, otherwise only when the
    revision actually changed. Returns the new revision."""
    previa, nueva = _fijar_revision(doc_id)
    if nueva is not None and (tipo == "documento_creado" or nueva != previa):
        eventos.publicar(
            tipo, doc_id, nueva,
            ["geometria", "notas", "parametros", "ensamble", "historial", "materiales"]
            if tipo == "documento_actualizado" else None,
        )
    return nueva


@contextlib.contextmanager
def _construyendo(doc_id: str):
    """Bracket a script run with ``construyendo`` and either
    ``construccion_terminada`` (with the resulting revision, emitted even
    when nothing changed so the viewer can drop its indicator) or
    ``construccion_fallida``."""
    eventos.publicar("construyendo", doc_id)
    try:
        yield
    except BaseException:
        eventos.publicar("construccion_fallida", doc_id)
        raise
    eventos.publicar("construccion_terminada", doc_id, _revisiones.get(doc_id))


def _ruta_step_o_404(doc_id: str) -> Path:
    if doc_id not in _registry:
        raise HTTPException(status_code=404, detail="documento no encontrado")
    ruta = _files.get(doc_id)
    if ruta is None or not ruta.exists():
        raise HTTPException(status_code=404, detail="archivo del documento no encontrado")
    if ruta.suffix.lower() not in _STEP_EXTS:
        raise HTTPException(
            status_code=400, detail="documento STL sin topologia B-rep (sin caras)"
        )
    return ruta


def _analyze(path: Path, ext: str) -> dict[str, Any]:
    if ext in _STEP_EXTS:
        shape = b123d_kernel.import_from_step(path)
        info = b123d_kernel.analyze(shape)
        if info.solidos == 0:
            raise ValueError("archivo STEP sin solidos")
    else:
        info = mesh.analyze_stl_path(path)
    return {
        "bbox": list(info.bbox),
        "volumen": info.volumen,
        "solidos": info.solidos,
        "valido": info.valido,
    }


def _analizar_con_solidos(
    path: Path, ext: str, nombres_ordenados: list[str] | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]] | None]:
    """Like ``_analyze``, but for STEP documents also computes the per-solid
    named breakdown (``app/solids.py``) from the same re-imported shape —
    STL documents have no B-rep solids to name (mesh-only), so this is
    ``None`` for them.

    ``nombres_ordenados`` (Phase 5A), when the caller ran a script whose
    ``resultado`` was a ``{nombre: Shape}`` dict, is the ordered list of
    keys straight from ``scripts_runner.ejecutar_script`` — passed through
    untouched to ``solids.construir_entradas`` (never re-derived from the
    STEP file's own labels, see that module's docstring). ``None`` for a
    plain upload/`abrir_archivo`/script-without-dict, which falls back to
    auto-generated ``solido_N`` names.

    Callers persist the returned entries via ``solids.guardar`` when not
    ``None``."""
    analisis = _analyze(path, ext)
    if ext in _STEP_EXTS:
        shape = b123d_kernel.import_from_step(path)
        entradas: list[dict[str, Any]] | None = solids.construir_entradas(shape, nombres_ordenados)
    else:
        entradas = None
    return analisis, entradas


def _con_solidos_snapshot(
    archivos: dict[str, bytes], doc_id: str, entradas: list[dict[str, Any]] | None
) -> dict[str, bytes]:
    """Add ``solidos.json`` to a creation snapshot's file set (mirrors how
    ``notas.json`` is added once it exists) — the sidecar `solids.guardar`
    just wrote already exists on disk by the time every creation route
    calls this, so a restore back to "right after creation" brings the
    named-solid breakdown back too, not just the STEP bytes."""
    if entradas is not None:
        archivos["solidos.json"] = solids.ruta_solidos(doc_id).read_bytes()
    return _con_parametros_snapshot(archivos, doc_id)


def _con_parametros_snapshot(archivos: dict[str, bytes], doc_id: str) -> dict[str, bytes]:
    """Add the parametric state (``parametros.json``, Phase 5C fix-review)
    to a snapshot's file set when the document has a stored script."""
    estado = parametros.estado_para_snapshot(doc_id)
    if estado is not None:
        archivos[parametros.NOMBRE_SNAPSHOT] = estado
    # fdm-D: the per-piece materials travel with every geometry snapshot.
    estado_materiales = materiales.estado_para_snapshot(doc_id)
    if estado_materiales is not None:
        archivos[materiales.NOMBRE_SNAPSHOT] = estado_materiales
    return archivos


def _nombres_piezas(doc_id: str) -> list[str]:
    """Unique piece names of ``doc_id`` in `solidos.json` order (fdm-D)."""
    nombres: list[str] = []
    for entrada in solids.cargar(doc_id) or []:
        nombre = entrada.get("nombre") if isinstance(entrada, dict) else None
        if isinstance(nombre, str) and nombre not in nombres:
            nombres.append(nombre)
    return nombres


def _con_ensamble_snapshot(archivos: dict[str, bytes], doc_id: str) -> dict[str, bytes]:
    """Add the assembly state (``ensamble.json`` + ``ensamble_base.step``,
    Phase 6) to a *geometry* snapshot's file set, mirroring
    ``_con_parametros_snapshot``: a snapshot that carries the document's
    STEP must carry the pose state belonging to those exact bytes, or
    restoring it would leave today's joints pinned to yesterday's geometry
    (or drop them silently). A document without an assembly adds nothing.

    Never applied to a notes-only snapshot (`app/notes.py`): undoing an
    annotation must leave the assembly alone. Lock-free by design — every
    caller may already hold `parametros.bloqueo(doc_id)`, which is not
    reentrant; this helper only reads files."""
    archivos.update(assemblies.snapshot_files(doc_id))
    return archivos


@router.post("/documentos")
async def crear_documento(request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
    nombre = file.filename or "documento"
    ext = Path(nombre).suffix.lower()
    if ext not in _STEP_EXTS + _STL_EXTS:
        raise HTTPException(status_code=400, detail="formato no soportado (usar STEP o STL)")

    max_bytes = _max_upload_bytes()
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > max_bytes:
                raise HTTPException(status_code=413, detail="archivo demasiado grande")
        except ValueError:
            pass  # missing/malformed header: fall through to the bounded read below

    contenido = await _read_upload_bounded(file, max_bytes)

    doc_id = uuid.uuid4().hex
    destino = DOCUMENTOS_DIR / f"{doc_id}{ext}"

    try:
        destino.write_bytes(contenido)
        analisis, entradas = _analizar_con_solidos(destino, ext)
    except HTTPException:
        destino.unlink(missing_ok=True)
        raise
    except Exception as exc:  # noqa: BLE001 - unparsable geometry is a client error
        destino.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=f"archivo invalido: {exc}") from exc

    registro = {"id": doc_id, "nombre": nombre, **analisis}
    _registry[doc_id] = registro
    _files[doc_id] = destino
    _guardar_meta(doc_id, nombre)
    if entradas is not None:
        solids.guardar(doc_id, entradas)
    versioning.crear_snapshot(
        doc_id, "documento creado (subida)", _con_solidos_snapshot({destino.name: contenido}, doc_id, entradas)
    )
    confirmar_revision(doc_id, "documento_creado")
    return {**registro, "revision": _revisiones.get(doc_id)}


class _DesdeRutaBody(BaseModel):
    ruta: str


@router.post("/documentos/desde_ruta", dependencies=[Depends(auth.requiere_token)])
def crear_documento_desde_ruta(body: _DesdeRutaBody) -> dict[str, Any]:
    """Register a document from a file already on disk (used by the MCP
    `abrir_archivo` tool). Only paths inside `ALLOWED_READ_ROOTS` are
    accepted; the file is copied into `DOCUMENTOS_DIR`, so the read-only
    `/data/fuentes` mount is never written to."""
    origen = _validar_ruta_permitida(body.ruta)
    if not origen.is_file():
        raise HTTPException(status_code=404, detail="archivo no encontrado")

    ext = origen.suffix.lower()
    if ext not in _STEP_EXTS + _STL_EXTS:
        raise HTTPException(status_code=400, detail="formato no soportado (usar STEP o STL)")

    doc_id = uuid.uuid4().hex
    destino = DOCUMENTOS_DIR / f"{doc_id}{ext}"

    try:
        shutil.copyfile(origen, destino)
        analisis, entradas = _analizar_con_solidos(destino, ext)
    except Exception as exc:  # noqa: BLE001 - unparsable geometry is a client error
        destino.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=f"archivo invalido: {exc}") from exc

    registro = {"id": doc_id, "nombre": origen.name, **analisis}
    _registry[doc_id] = registro
    _files[doc_id] = destino
    _guardar_meta(doc_id, origen.name)
    if entradas is not None:
        solids.guardar(doc_id, entradas)
    versioning.crear_snapshot(
        doc_id,
        "documento creado (desde ruta)",
        _con_solidos_snapshot({destino.name: destino.read_bytes()}, doc_id, entradas),
    )
    confirmar_revision(doc_id, "documento_creado")
    return {**registro, "revision": _revisiones.get(doc_id)}


class _ScriptBody(BaseModel):
    codigo: str | None = None
    ruta: str | None = None
    variables: dict[str, Any] | None = None
    nombre: str | None = None
    timeout: float | None = None
    documento_id: str | None = None


def _detalle_script_error(exc: scripts_runner.ScriptError) -> dict[str, Any] | str:
    """``HTTPException.detail`` for a ``ScriptError``: a plain string when
    there is no script line to point at (kernel/export failures), or a
    compact ``{mensaje, linea[, codigo_linea]}`` dict when there is (Phase
    5A error reporting) — never a full traceback either way."""
    if exc.linea is None:
        return exc.mensaje
    detalle: dict[str, Any] = {"mensaje": exc.mensaje, "linea": exc.linea}
    if exc.codigo_linea is not None:
        detalle["codigo_linea"] = exc.codigo_linea
    return detalle


def _actualizar_documento_desde_script(
    doc_id: str,
    codigo: str,
    timeout: float,
    variables: dict[str, Any] | None = None,
    mensaje_snapshot: str = "actualizar documento (script)",
) -> dict[str, Any]:
    """Re-run a build123d script against an EXISTING document (Phase 4:
    "script re-run... updating a document", ADR-0003 versioning + naming).

    Snapshots the current STEP file (+ notes, if any) *before* applying;
    if the script fails or the kernel rejects the result, the file is
    rolled back to that snapshot and the in-memory registry is left
    untouched — the error is returned, the document is not corrupted. On
    success, every note/stroke `referencia` pointing at a face/edge is
    re-resolved against the rebuilt shape (`naming.resolver_referencia`),
    never left silently pointing at a stale index.
    """
    registro_previo = _registry.get(doc_id)
    if registro_previo is None:
        raise HTTPException(status_code=404, detail="documento no encontrado")
    ruta = _files.get(doc_id)
    if ruta is None or not ruta.exists():
        raise HTTPException(status_code=404, detail="archivo del documento no encontrado")
    if ruta.suffix.lower() not in _STEP_EXTS:
        raise HTTPException(
            status_code=400,
            detail="solo se puede actualizar por script un documento STEP (B-rep)",
        )

    archivo_previo = ruta.read_bytes()
    ruta_notas_doc = notes.ruta_notas(doc_id)
    notas_previas = ruta_notas_doc.read_bytes() if ruta_notas_doc.exists() else None
    ruta_solidos_doc = solids.ruta_solidos(doc_id)
    solidos_previos = ruta_solidos_doc.read_bytes() if ruta_solidos_doc.exists() else None
    archivos_snapshot = {ruta.name: archivo_previo}
    if notas_previas is not None:
        archivos_snapshot["notas.json"] = notas_previas
    if solidos_previos is not None:
        archivos_snapshot["solidos.json"] = solidos_previos
    archivos_snapshot = _con_parametros_snapshot(archivos_snapshot, doc_id)
    versioning.crear_snapshot(
        doc_id, mensaje_snapshot, _con_ensamble_snapshot(archivos_snapshot, doc_id)
    )

    try:
        salida_temporal, nombres_ordenados = scripts_runner.ejecutar_script(
            codigo, timeout=timeout, variables=variables
        )
    except scripts_runner.ScriptTimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except scripts_runner.ScriptError as exc:
        raise HTTPException(status_code=422, detail=_detalle_script_error(exc)) from exc

    tmp_dir = salida_temporal.parent
    try:
        nueva_shape = b123d_kernel.import_from_step(salida_temporal)
        analisis = b123d_kernel.analyze(nueva_shape)
        if analisis.solidos == 0:
            raise ValueError("script produjo una figura sin solidos")
        shutil.move(str(salida_temporal), str(ruta))
    except Exception as exc:  # noqa: BLE001 - rollback: state unchanged, error returned
        ruta.write_bytes(archivo_previo)
        raise HTTPException(status_code=422, detail=f"script invalido: {exc}") from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    registro = {
        "id": doc_id,
        "nombre": registro_previo["nombre"],
        "bbox": list(analisis.bbox),
        "volumen": analisis.volumen,
        "solidos": analisis.solidos,
        "valido": analisis.valido,
    }
    _registry[doc_id] = registro
    # `nueva_shape` is already the re-imported STEP shape (needed below for
    # note re-resolution too) — reuse it instead of importing the file a
    # second time (Phase 5A named solids).
    solids.guardar(doc_id, solids.construir_entradas(nueva_shape, nombres_ordenados))

    if notas_previas is not None:
        datos_notas = json.loads(notas_previas)
        cambiaron = False
        for coleccion in ("notas", "trazos"):
            for item in datos_notas.get(coleccion, []):
                referencia = item.get("referencia")
                if referencia is None:
                    continue
                resultado = naming.resolver_referencia(nueva_shape, referencia)
                # `resolver_referencia` carries `referencia_perdida`/`ambigua`
                # inside the returned dict itself (its own return contract,
                # exercised directly in `tests/test_naming.py`); those flags
                # belong on `item` (the note/stroke) as the single top-level
                # source of truth, never duplicated inside the nested
                # `referencia` sub-dict that gets persisted (Phase 4 review
                # finding).
                perdida = resultado.pop("referencia_perdida")
                ambigua = resultado.pop("ambigua", False)
                if resultado != referencia or item.get("referencia_perdida") != perdida:
                    item["referencia"] = resultado
                    item["referencia_perdida"] = perdida
                    cambiaron = True
                if ambigua:
                    if item.get("ambigua") is not True:
                        cambiaron = True
                    item["ambigua"] = True
                elif item.pop("ambigua", None) is not None:
                    cambiaron = True
        if cambiaron:
            notes.guardar(doc_id, datos_notas)

    return registro


@router.post("/documentos/script", dependencies=[Depends(auth.requiere_token)])
def crear_documento_desde_script(body: _ScriptBody) -> dict[str, Any]:
    """Run a build123d script (must assign the built shape — or a
    ``{nombre: Shape}`` dict, Phase 5A named solids — to a variable named
    ``resultado``) in an isolated, time-limited subprocess (`scripts_runner`)
    and register the resulting solid as a new document — or, if
    ``documento_id`` is given, update that existing document in place (see
    `_actualizar_documento_desde_script`). Backs the MCP `ejecutar_script`
    tool.

    Exactly one of ``codigo``/``ruta`` must be given (Phase 5A: ``ruta``
    avoids re-pasting a 200-line script on every iteration). ``ruta`` is
    subject to the same allowed-roots guard as `abrir_archivo`
    (`_validar_ruta_permitida`: only `/data/documentos` or the read-only
    `/data/fuentes` mount, no traversal/symlink escapes).
    """
    if (body.codigo is None) == (body.ruta is None):
        raise HTTPException(status_code=400, detail="usar exactamente uno de 'codigo' o 'ruta'")

    if body.ruta is not None:
        origen = _validar_ruta_permitida(body.ruta)
        if not origen.is_file():
            raise HTTPException(status_code=404, detail="archivo no encontrado")
        try:
            codigo = origen.read_text()
        except OSError as exc:
            raise HTTPException(status_code=400, detail=f"no se pudo leer la ruta: {exc}") from exc
    else:
        codigo = body.codigo

    timeout = body.timeout or scripts_runner.DEFAULT_TIMEOUT

    # Phase 5C: a top-level PARAMETROS literal becomes the document's
    # schema; the script runs with its defaults injected as `PARAMS`.
    try:
        esquema = parametros.esquema_desde_codigo(codigo)
    except parametros.ParametrosInvalidos as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    valores = parametros.valores_por_defecto(esquema)
    variables = parametros.variables_efectivas(body.variables, valores)
    # fdm-D: a top-level MATERIALES literal, validated before running.
    try:
        declarados = materiales.desde_codigo(codigo)
    except materiales.MaterialesInvalidos as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    ignorados: list[str] = []

    def _con_ignorados(respuesta: dict[str, Any]) -> dict[str, Any]:
        return {**respuesta, "materiales_ignorados": ignorados} if ignorados else respuesta

    def _recordar_script(doc_id_: str) -> None:
        parametros.guardar(
            doc_id_,
            codigo=codigo,
            ruta=str(origen) if body.ruta is not None else None,
            variables=body.variables,
            timeout=body.timeout,
            esquema=esquema,
            valores=valores,
        )
        ignorados[:] = materiales.aplicar_declaracion(doc_id_, declarados, _nombres_piezas(doc_id_))

    if body.documento_id is not None:
        # Validated *before* the lock (Phase 6.3): `parametros.bloqueo`
        # creates and keeps one lock per id it is asked about and never
        # evicts, so an id that names no document must not create one
        # (mirrors `app/notes.py`). `_actualizar_documento_desde_script`
        # re-checks under the lock, so a document deleted in between still
        # gets today's 404 rather than a stale update.
        if body.documento_id not in _registry:
            raise HTTPException(status_code=404, detail="documento no encontrado")
        with parametros.bloqueo(body.documento_id), _construyendo(body.documento_id):
            registro_actualizado = _actualizar_documento_desde_script(
                body.documento_id, codigo, timeout, variables
            )
            _recordar_script(body.documento_id)
            confirmar_revision(body.documento_id)
        return _con_ignorados({**registro_actualizado, "revision": _revisiones.get(body.documento_id)})

    # Id chosen before the run (F11.1) so the viewer's "construyendo..."
    # event and the final `documento_creado` name the same document.
    doc_id = uuid.uuid4().hex
    with _construyendo(doc_id):
        return _con_ignorados(
            _crear_documento_desde_script(doc_id, codigo, timeout, variables, body.nombre, _recordar_script)
        )


def _crear_documento_desde_script(
    doc_id: str,
    codigo: str,
    timeout: float,
    variables: dict[str, Any] | None,
    nombre_pedido: str | None,
    recordar_script: Any,
) -> dict[str, Any]:
    try:
        salida_temporal, nombres_ordenados = scripts_runner.ejecutar_script(
            codigo, timeout=timeout, variables=variables
        )
    except scripts_runner.ScriptTimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except scripts_runner.ScriptError as exc:
        raise HTTPException(status_code=422, detail=_detalle_script_error(exc)) from exc

    tmp_dir = salida_temporal.parent
    nombre = nombre_pedido or f"{doc_id}.step"
    if not nombre.lower().endswith((".step", ".stp")):
        nombre = f"{nombre}.step"
    destino = DOCUMENTOS_DIR / f"{doc_id}.step"

    try:
        shutil.move(str(salida_temporal), str(destino))
        analisis, entradas = _analizar_con_solidos(destino, ".step", nombres_ordenados)
    except Exception as exc:  # noqa: BLE001 - unparsable geometry is a client error
        destino.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=f"script invalido: {exc}") from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    registro = {"id": doc_id, "nombre": nombre, **analisis}
    _registry[doc_id] = registro
    _files[doc_id] = destino
    _guardar_meta(doc_id, nombre)
    if entradas is not None:
        solids.guardar(doc_id, entradas)
    # After `solids.guardar` (fdm-D): the materials declaration is checked
    # against the piece names just saved.
    recordar_script(doc_id)
    versioning.crear_snapshot(
        doc_id,
        "documento creado (script)",
        _con_solidos_snapshot({destino.name: destino.read_bytes()}, doc_id, entradas),
    )
    confirmar_revision(doc_id, "documento_creado")
    return {**registro, "revision": _revisiones.get(doc_id)}


def crear_documento_desde_formas(nombrados: dict[str, Any], nombre_pedido: str) -> dict[str, Any]:
    """New STEP document straight from in-process build123d shapes
    (``{nombre: Shape}``, names validated here) — fdm-B test coupons. Same
    bookkeeping as a script-created document, minus the stored script."""
    solids.validar_nombres(nombrados.keys())
    doc_id = uuid.uuid4().hex
    nombre = nombre_pedido if nombre_pedido.lower().endswith((".step", ".stp")) else f"{nombre_pedido}.step"
    destino = DOCUMENTOS_DIR / f"{doc_id}.step"
    with _construyendo(doc_id):
        try:
            b123d_kernel.export_to_step(b123d_kernel.combinar_nombrados(dict(nombrados)), destino)
            analisis, entradas = _analizar_con_solidos(destino, ".step", list(nombrados.keys()))
        except Exception as exc:  # noqa: BLE001
            destino.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail=f"geometria invalida: {exc}") from exc
        registro = {"id": doc_id, "nombre": nombre, **analisis}
        _registry[doc_id] = registro
        _files[doc_id] = destino
        _guardar_meta(doc_id, nombre)
        if entradas is not None:
            solids.guardar(doc_id, entradas)
        versioning.crear_snapshot(
            doc_id,
            "documento creado (cupon)",
            _con_solidos_snapshot({destino.name: destino.read_bytes()}, doc_id, entradas),
        )
        confirmar_revision(doc_id, "documento_creado")
    return {**registro, "revision": _revisiones.get(doc_id)}


@router.get("/documentos/{doc_id}/parametros")
def obtener_parametros(doc_id: str) -> dict[str, Any]:
    """Read-only, token-free (Phase 5C): ``{esquema, valores}`` of the
    document's parametric script — both ``{}`` when it was not created from
    a script declaring ``PARAMETROS``. Shared by the web UI and the MCP
    `parametros` tool (one endpoint for both, Phase 5 contract)."""
    if doc_id not in _registry:
        raise HTTPException(status_code=404, detail="documento no encontrado")
    datos = parametros.cargar(doc_id)
    return {"esquema": datos["parametros"], "valores": datos["valores"]}


class _ParametrosBody(BaseModel):
    valores: dict[str, Any]


@router.post("/documentos/{doc_id}/parametros", dependencies=[Depends(auth.requiere_token)])
def aplicar_parametros(doc_id: str, body: _ParametrosBody) -> dict[str, Any]:
    """Re-run the document's stored script with new parameter values
    (Phase 5C), in place, through the same path as `ejecutar_script` with
    `documento_id` — so the pre-change snapshot, named solids and note
    re-resolution all keep working. Token-protected like script execution
    (it runs code). Values are validated against the schema's min/max
    first (422, nothing run). Returns the compact document summary +
    ``valores`` + ``ms`` (server-side elapsed time of the re-run)."""
    if doc_id not in _registry:
        raise HTTPException(status_code=404, detail="documento no encontrado")

    # Everything from reading the stored state to saving the new values
    # happens under the per-document lock (fix-review): a concurrent apply
    # or restore can't hand us a stale script/values pair.
    with parametros.bloqueo(doc_id):
        if doc_id not in _registry:
            raise HTTPException(status_code=404, detail="documento no encontrado")
        datos = parametros.cargar(doc_id)
        script = datos["script"]
        if not script:
            raise HTTPException(
                status_code=400, detail="documento sin script parametrico (crearlo con ejecutar_script)"
            )
        if "ruta" in script:
            origen = _validar_ruta_permitida(script["ruta"])
            try:
                codigo = origen.read_text()
            except OSError as exc:
                raise HTTPException(status_code=400, detail=f"no se pudo leer la ruta: {exc}") from exc
        else:
            codigo = script["codigo"]
        try:
            esquema = parametros.esquema_desde_codigo(codigo)
            valores = parametros.aplicar_valores(esquema, datos["valores"], body.valores)
            declarados = materiales.desde_codigo(codigo)
        except (parametros.ParametrosInvalidos, materiales.MaterialesInvalidos) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        inicio = time.perf_counter()
        with _construyendo(doc_id):
            registro = _actualizar_documento_desde_script(
                doc_id,
                codigo,
                datos["timeout"] or scripts_runner.DEFAULT_TIMEOUT,
                parametros.variables_efectivas(datos["variables"], valores),
                mensaje_snapshot=parametros.resumen_valores(valores),
            )
            ms = round((time.perf_counter() - inicio) * 1000)
            parametros.guardar_valores(doc_id, esquema, valores)
            materiales.aplicar_declaracion(doc_id, declarados, _nombres_piezas(doc_id))
            confirmar_revision(doc_id)
    return {**registro, "revision": _revisiones.get(doc_id), "valores": valores, "ms": ms}


@router.get("/documentos/{doc_id}/materiales")
def obtener_materiales(doc_id: str) -> dict[str, Any]:
    """Token-free (fdm-D): ``{materiales: {pieza: {material?, color?,
    extrusor?}}, piezas: [nombres]}`` — only pieces that exist now."""
    if doc_id not in _registry:
        raise HTTPException(status_code=404, detail="documento no encontrado")
    nombres = _nombres_piezas(doc_id)
    return {"materiales": materiales.cargar(doc_id, nombres), "piezas": nombres}


class _MaterialesBody(BaseModel):
    materiales: dict[str, Any]
    reemplazar: bool = False


@router.post("/documentos/{doc_id}/materiales", dependencies=[Depends(auth.requiere_token)])
def cambiar_materiales(doc_id: str, body: _MaterialesBody) -> dict[str, Any]:
    """Change per-piece materials WITHOUT re-running the script (fdm-D):
    ``{materiales: {pieza: material | null}, reemplazar?}`` — short text or
    ``{material?, color?, extrusor?}``; ``null`` removes; unknown pieces or
    bad values -> 422 and nothing written. Snapshots the current state
    first (undoable via `restaurar`). Geometry revision is unchanged."""
    if doc_id not in _registry:
        raise HTTPException(status_code=404, detail="documento no encontrado")
    with parametros.bloqueo(doc_id):
        if doc_id not in _registry:
            raise HTTPException(status_code=404, detail="documento no encontrado")
        ruta = _files.get(doc_id)
        if ruta is None or not ruta.exists():
            raise HTTPException(status_code=404, detail="archivo del documento no encontrado")
        nombres = _nombres_piezas(doc_id)
        try:
            # Validate first (dry run on the merge rules) so a bad request
            # leaves neither a snapshot nor a write behind.
            if len(body.materiales) > materiales.MAX_MATERIALES:
                raise materiales.MaterialesInvalidos(
                    f"demasiadas piezas (maximo {materiales.MAX_MATERIALES})"
                )
            for nombre, valor in body.materiales.items():
                if nombre not in nombres:
                    raise materiales.MaterialesInvalidos(f"pieza inexistente en el documento: {nombre!r}")
                if valor is not None:
                    materiales.normalizar_entrada(nombre, valor)
        except materiales.MaterialesInvalidos as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        actuales: dict[str, bytes] = {ruta.name: ruta.read_bytes()}
        ruta_notas_doc = notes.ruta_notas(doc_id)
        if ruta_notas_doc.exists():
            actuales["notas.json"] = ruta_notas_doc.read_bytes()
        ruta_solidos_doc = solids.ruta_solidos(doc_id)
        if ruta_solidos_doc.exists():
            actuales["solidos.json"] = ruta_solidos_doc.read_bytes()
        actuales = _con_parametros_snapshot(actuales, doc_id)
        versioning.crear_snapshot(
            doc_id, "materiales: " + ", ".join(list(body.materiales)[:5]),
            _con_ensamble_snapshot(actuales, doc_id),
        )
        try:
            resultado = materiales.actualizar(doc_id, body.materiales, nombres, body.reemplazar)
        except materiales.MaterialesInvalidos as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    eventos.publicar("anotaciones_actualizadas", doc_id, _revisiones.get(doc_id),
                     ["materiales", "historial"])
    return {"materiales": resultado, "piezas": nombres}


@router.get("/documentos/{doc_id}/fdm")
def verificar_fdm_documento(
    doc_id: str,
    boquilla: float = Query(default=fdm_checks.BOQUILLA_POR_DEFECTO, gt=0.05, le=3.0),
    cama: str = Query(default="220x220x250"),
    angulo_max: float = Query(default=fdm_checks.ANGULO_MAX_POR_DEFECTO, ge=0.0, le=89.0),
) -> dict[str, Any]:
    """FDM printability check (Phase 5C) per named solid: bed fit,
    overhangs, thin walls, first-layer contact, sub-nozzle features — only
    problems. Read-only, token-free like `/colisiones`. See
    `app/checks/fdm.py`; backs the MCP `check_fdm` tool."""
    try:
        dims_cama = fdm_checks.parsear_cama(cama)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    registro = _registry.get(doc_id)
    if registro is None:
        raise HTTPException(status_code=404, detail="documento no encontrado")
    ruta = _files.get(doc_id)
    if ruta is None or not ruta.exists():
        raise HTTPException(status_code=404, detail="archivo del documento no encontrado")
    if ruta.suffix.lower() not in _STEP_EXTS:
        raise HTTPException(
            status_code=400, detail="check_fdm solo esta disponible para documentos STEP/script (no malla STL)"
        )
    entradas = solids.cargar(doc_id)
    if not entradas:
        raise HTTPException(
            status_code=400, detail="este documento no tiene desglose de solidos (solidos.json ausente)"
        )
    shape = b123d_kernel.import_from_step(ruta)
    try:
        return fdm_checks.verificar(
            shape, entradas, boquilla=boquilla, cama=dims_cama, angulo_max=angulo_max
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/documentos")
def listar_documentos() -> list[dict[str, Any]]:
    """Compact picker/gallery listing, newest geometry first.

    Geometry mtime survives restarts and changes on rebuild/restore, unlike
    registry insertion order or metadata timestamps (which notes may change).
    Format comes from the stored file, not its user-facing display name.
    """
    entries = []
    for registro in list(_registry.values()):
        path = _files.get(registro["id"])
        if path is None:
            continue
        try:
            modified = path.stat().st_mtime
        except FileNotFoundError:
            # A concurrent deletion must not break the entire gallery.
            continue
        entries.append({
            "id": registro["id"],
            "nombre": registro["nombre"],
            "volumen": registro["volumen"],
            "solidos": registro["solidos"],
            "formato": "step" if path.suffix.lower() in _STEP_EXTS else "stl",
            "actualizado": datetime.fromtimestamp(modified, timezone.utc).isoformat(),
            "revision": _revisiones.get(registro["id"]),
        })
    return sorted(entries, key=lambda entry: (
        -datetime.fromisoformat(entry["actualizado"]).timestamp(), entry["id"]
    ))


@router.get("/documentos/{doc_id}")
def obtener_documento(
    doc_id: str,
    tope_solidos: int = Query(default=solids.TOPE_FILAS_POR_DEFECTO, ge=1, le=500),
    umbral_astilla_mm3: float = Query(default=solids.UMBRAL_ASTILLA_MM3_POR_DEFECTO, ge=0.0),
) -> dict[str, Any]:
    """Document summary. When the document has more than one solid, adds a
    compact ``solidos_detalle: {lista, mas, astillas}`` block (Phase 5A,
    feedback item 4) — a single-solid document's response stays exactly as
    compact as before (no extra keys at all)."""
    registro = _registry.get(doc_id)
    if registro is None:
        raise HTTPException(status_code=404, detail="documento no encontrado")
    entradas = solids.cargar(doc_id)
    respuesta: dict[str, Any] = {**registro, "revision": _revisiones.get(doc_id)}
    if entradas and len(entradas) > 1:
        respuesta["solidos_detalle"] = solids.resumen(
            entradas, tope=tope_solidos, umbral_astilla_mm3=umbral_astilla_mm3
        )
    # fdm-D: only present when some piece has a material (stays compact).
    asignados = materiales.cargar(doc_id, _nombres_piezas(doc_id))
    if asignados:
        respuesta["materiales"] = asignados
    return respuesta


# Viewer meshes cached on disk per document revision: re-opening an unchanged
# document reads a file instead of re-importing the STEP and re-tessellating.
# Bump _VERSION_MALLA whenever the tessellation/serialisation output changes.
_VERSION_MALLA = "1"
_CACHE_MALLAS = DOCUMENTOS_DIR / ".cache" / "mallas"


def _ruta_cache_malla(doc_id: str, revision: str, components: bool) -> Path:
    tipo = "piezas" if components else "stl"
    return _CACHE_MALLAS / f"{doc_id}.{revision}.v{_VERSION_MALLA}.{tipo}"


def _guardar_cache_malla(doc_id: str, revision: str, components: bool, contenido: bytes) -> None:
    try:
        _CACHE_MALLAS.mkdir(parents=True, exist_ok=True)
        destino = _ruta_cache_malla(doc_id, revision, components)
        tmp = destino.with_name(destino.name + f".tmp{os.getpid()}")
        tmp.write_bytes(contenido)
        os.replace(tmp, destino)
        # Drop this document's meshes for older revisions/versions.
        sufijo = destino.name.rsplit(".", 1)[-1]
        for viejo in _CACHE_MALLAS.glob(f"{doc_id}.*.{sufijo}"):
            if viejo != destino:
                viejo.unlink(missing_ok=True)
    except OSError:
        logger.warning("no se pudo guardar la malla en cache de %s", doc_id)


def _borrar_cache_malla(doc_id: str) -> None:
    for viejo in _CACHE_MALLAS.glob(f"{doc_id}.*"):
        viejo.unlink(missing_ok=True)


@router.get("/documentos/{doc_id}/malla")
def obtener_malla(
    doc_id: str, components: bool = Query(default=False, alias="componentes")
) -> Response:
    """Binary STL for the viewer, or an FJP1 envelope with per-piece ranges."""
    if doc_id not in _registry:
        raise HTTPException(status_code=404, detail="documento no encontrado")
    tipo_mime = "application/vnd.forja.piezas" if components else "model/stl"
    revision = _revisiones.get(doc_id, "")
    if revision:
        cache = _ruta_cache_malla(doc_id, revision, components)
        if cache.is_file():
            return Response(content=cache.read_bytes(), media_type=tipo_mime, headers={"X-Forja-Revision": revision, "X-Forja-Cache": "hit", "Cache-Control": "no-store"})
    contenido = _calcular_malla(doc_id, components)
    revision_final = _revisiones.get(doc_id, "")
    if revision and revision_final == revision:
        _guardar_cache_malla(doc_id, revision, components, contenido)
    return Response(content=contenido, media_type=tipo_mime, headers={"X-Forja-Revision": revision_final, "X-Forja-Cache": "miss", "Cache-Control": "no-store"})


def _calcular_malla(doc_id: str, components: bool) -> bytes:
    with parametros.bloqueo(doc_id):
        # Deletion and geometry updates use the same per-document lock. Check
        # again after acquiring it so a request that waited behind a delete
        # cannot read stale registry or sidecar state.
        registro = _registry.get(doc_id)
        if registro is None:
            raise HTTPException(status_code=404, detail="documento no encontrado")
        ruta = _files.get(doc_id)
        if ruta is None or not ruta.exists():
            raise HTTPException(status_code=404, detail="archivo del documento no encontrado")

        ext = ruta.suffix.lower()
        if ext in _STEP_EXTS:
            shape = b123d_kernel.import_from_step(ruta)
            if components:
                entries = solids.cargar(doc_id)
                content = piece_mesh.from_step(
                    shape,
                    entries,
                    metadata_available=entries is not None,
                    bbox=registro["bbox"],
                    volume=registro["volumen"],
                )
                return content
            tri_mesh = mesh.tessellate_to_trimesh(shape)
        elif ext in _STL_EXTS:
            tri_mesh = mesh.load_stl(ruta)
            if components:
                content = piece_mesh.from_stl(
                    tri_mesh, bbox=registro["bbox"], volume=registro["volumen"]
                )
                return content
        else:
            raise HTTPException(status_code=400, detail="formato de documento no soportado")

        return mesh.to_stl_bytes(tri_mesh)


# Phase 10.1 — read-only download under a real-filename path, for OrcaSlicer's
# `orcaslicer://open?file=<url>` downloader: it names the saved file after the
# text following the LAST "/" of the decoded URL (query string included), so
# the path must end in `name.stl|name.3mf`. The client-supplied stem is ignored.
_RE_ARCHIVO_DESCARGA = re.compile(r"[A-Za-z0-9_-]{1,64}\.(stl|3mf)")
_TIPOS_DESCARGA = {"stl": "model/stl", "3mf": "model/3mf"}
# Same charset as named solids (letters/digits/_/- incl. accents and ñ).
_RE_PIEZA_DESCARGA = re.compile(r"[\w-]{1,64}")
# Upper bound on triangles a download builds in memory (checked after
# tessellation, so it bounds the 3MF/STL serialisation, not the B-rep
# tessellation). Measured 2026-10-01 on the largest documents in
# `documentos_data/`: 535,596 tri (STL) -> 3MF in 3.5 s; 395,222 tri (STEP)
# -> 5.5 s; 646,158 tri (STEP, the largest) -> 15 s, 8.2 MB 3MF, process peak
# RSS 761 MB. `casa_del_arbol` (178,472 tri) is far below. 1,000,000 keeps
# every existing document downloadable (1.5x the largest) while bounding the
# worst case at ~25 s / ~1.2 GB (linear extrapolation).
MAX_TRIANGULOS_DESCARGA = 1_000_000

logger = logging.getLogger(__name__)


def _geometria_vacia(forma: Any) -> bool:
    """True for a shape/mesh without a single face (e.g. a corrupt STEP,
    which imports as an empty compound instead of raising)."""
    caras = forma.faces if isinstance(forma, trimesh.Trimesh) else forma.faces()
    return len(caras) == 0


def _slug_descarga(nombre: str) -> str:
    """ASCII `[A-Za-z0-9_-]{1,64}` stem derived from a document name
    (accents folded, emoji/other dropped, empty -> `modelo`). Never contains
    CR/LF or quotes, so it is safe inside a quoted header value."""
    base = Path(str(nombre)).stem
    ascii_ = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Za-z0-9_-]+", "_", ascii_).strip("_")[:64].strip("_") or "modelo"


@router.api_route("/documentos/{doc_id}/descarga/{archivo:path}", methods=["GET", "HEAD"])
def descargar_documento(request: Request, doc_id: str, archivo: str) -> Response:
    """STL or 3MF bytes of a document, built in memory (nothing is written
    under `documentos_data`). Token-free like `/malla` and `/exportar`:
    OrcaSlicer's downloader cannot send custom headers.

    A bare trailing "?" (`x.stl?`) is NOT observable here: uvicorn (both
    httptools and h11, verified 2026-10-01) splits the target at the first
    "?" and hands the app an empty `query_string` and a `raw_path` without
    it, so it is served exactly like `x.stl`. Link builders must therefore
    never emit a "?"."""
    # Optional single piece: `.../descarga/<pieza>/<slug>.<ext>` (Orca names
    # the file after the last segment, so the piece goes before it).
    pieza: str | None = None
    if "/" in archivo:
        pieza, archivo = archivo.rsplit("/", 1)
        if not _RE_PIEZA_DESCARGA.fullmatch(pieza):
            raise HTTPException(status_code=404, detail="archivo no encontrado")
    coincidencia = _RE_ARCHIVO_DESCARGA.fullmatch(archivo)
    # Orca keeps everything after the last "/" (query string included) as the
    # file name, so a URL carrying a query would be saved as `x.stl?a=b`.
    if request.url.query:
        raise HTTPException(status_code=404, detail="archivo no encontrado")
    # Starlette's `{archivo:path}` regex ends in `$`, which lets a trailing
    # "\n" slip out of the captured value: also require the real path to end
    # exactly with the captured tail.
    sufijo = "/descarga/" + (f"{pieza}/" if pieza else "") + archivo
    if coincidencia is None or not request.scope["path"].endswith(sufijo):
        raise HTTPException(status_code=404, detail="archivo no encontrado")
    formato = coincidencia.group(1)
    # Clamp the id (characters AND UTF-8 bytes) before any lookup.
    if not notes._doc_id_valido(doc_id) or doc_id not in _registry:
        raise HTTPException(status_code=404, detail="documento no encontrado")
    with parametros.bloqueo(doc_id):
        # Re-check inside the lock: a delete may have won the race.
        registro = _registry.get(doc_id)
        if registro is None:
            raise HTTPException(status_code=404, detail="documento no encontrado")
        ruta = _files.get(doc_id)
        if ruta is None or not ruta.exists():
            raise HTTPException(status_code=404, detail="archivo del documento no encontrado")
        ext_origen = ruta.suffix.lower()
        too_big = HTTPException(
            status_code=413,
            detail=f"malla demasiado grande para descargar (maximo {MAX_TRIANGULOS_DESCARGA} triangulos)",
        )
        # A corrupt STEP can import as an empty shape instead of raising.
        sin_geometria = HTTPException(
            status_code=400, detail="el documento no tiene geometria (archivo vacio o danado)"
        )
        # Error policy (Phase 10.1 fix round 2). Expected on-disk drift ->
        # clean 4xx and NO traceback in the log: a missing/corrupt STEP or STL
        # (OSError, kernel import errors), a `solidos.json` that disagrees
        # with the geometry (`objetos_3mf` -> ValueError, the same 400
        # `exportar` returns) or has an unexpected shape (KeyError/TypeError).
        # `solids.cargar` already maps unreadable JSON to None (one default
        # object). Anything else is a server defect: logged WITH its
        # traceback and answered with a generic 500 (never blamed on the
        # user's data). HTTPExceptions raised here pass through untouched.
        try:
            if ext_origen in _STEP_EXTS:
                shape = b123d_kernel.import_from_step(ruta)
                entradas = solids.cargar(doc_id) if (formato == "3mf" or pieza) else None
            elif ext_origen in _STL_EXTS:
                shape = mesh.load_stl(ruta)
                entradas = None
            else:
                raise HTTPException(status_code=400, detail="formato de documento no soportado")

            # One emptiness check for both formats, BEFORE the format-specific
            # builders: otherwise a corrupt STEP that has a sidecar reaches
            # `objetos_3mf` first and surfaces as a misleading sidecar
            # mismatch instead of the real cause.
            if _geometria_vacia(shape):
                raise sin_geometria

            if pieza:
                objetos, _inciertos = export_solidos.objetos_3mf(
                    shape, entradas, Path(registro["nombre"]).stem or "pieza"
                )
                objetos = [o for o in objetos if o[0] == pieza]
                if not objetos:
                    raise HTTPException(status_code=404, detail="pieza no encontrada")
                if sum(len(m.faces) for _n, m in objetos) > MAX_TRIANGULOS_DESCARGA:
                    raise too_big
                contenido = (
                    mesh.to_stl_bytes(objetos[0][1])
                    if formato == "stl"
                    else export_solidos.bytes_3mf(objetos, materiales.cargar(doc_id))
                )
            elif formato == "stl":
                tri_mesh = shape if ext_origen in _STL_EXTS else mesh.tessellate_to_trimesh(shape)
                if len(tri_mesh.faces) > MAX_TRIANGULOS_DESCARGA:
                    raise too_big
                contenido = mesh.to_stl_bytes(tri_mesh)
            else:
                objetos, _inciertos = export_solidos.objetos_3mf(
                    shape, entradas, Path(registro["nombre"]).stem or "pieza"
                )
                if sum(len(m.faces) for _n, m in objetos) > MAX_TRIANGULOS_DESCARGA:
                    raise too_big
                contenido = export_solidos.bytes_3mf(
                    objetos, materiales.cargar(doc_id) if entradas else None
                )
        except HTTPException:
            raise
        except ValueError as exc:  # same mapping and message as `exportar`
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (OSError, KeyError, TypeError) as exc:  # expected drift: clean 400, no traceback
            raise HTTPException(
                status_code=400,
                detail=f"no se pudo generar la descarga: el documento no se puede leer ({type(exc).__name__})",
            ) from exc
        except Exception:  # noqa: BLE001 - a defect: leave a trace, answer 500
            logger.exception("descarga %s: fallo inesperado al generar %s", doc_id, formato)
            raise HTTPException(
                status_code=500, detail="error interno al generar la descarga"
            ) from None
        slug = _slug_descarga(registro["nombre"])
        if pieza:
            slug = f"{slug}_{_slug_descarga(pieza)}"[:64]

    return Response(
        content=contenido,
        media_type=_TIPOS_DESCARGA[formato],
        headers={
            "Content-Disposition": f'attachment; filename="{slug}.{formato}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/documentos/{doc_id}/exportar")
def exportar_documento(
    doc_id: str, formato: str = Query(...), por_solido: bool = Query(default=False)
) -> dict[str, Any]:
    """Materialize a document under `DOCUMENTOS_DIR/exportados` and return its
    path + size (never the file bytes) — or, with `por_solido=true`, one file
    per named solid (Phase 5B, feedback item 6). Backs the MCP `exportar`
    tool.

    STL is always available (STEP documents are re-tessellated, STL documents
    are copied as-is). STEP is only available for documents that already
    carry a B-rep (i.e. were imported/created as STEP) — a pure mesh (STL)
    document has no solid to re-export as STEP. `3mf` (Phase 5C) is written
    by hand (`export.exportar_3mf_documento`, no `networkx`): ONE package
    with one named object per named solid, for both `por_solido` values —
    a multi-object 3MF is what a slicer wants.

    `por_solido=true` requires a STEP document with a named-solid breakdown
    (`solidos.json`, see `app/solids.py`): geometrically-identical named
    pieces (e.g. 4 chairs from the same script function) collapse into ONE
    file, and the response maps each written filename to every solid name it
    represents (`app/export.py`).
    """
    if formato not in export_solidos.FORMATOS_SOPORTADOS:
        raise HTTPException(status_code=400, detail="formato no soportado (usar stl, step o 3mf)")

    registro = _registry.get(doc_id)
    if registro is None:
        raise HTTPException(status_code=404, detail="documento no encontrado")
    ruta = _files.get(doc_id)
    if ruta is None or not ruta.exists():
        raise HTTPException(status_code=404, detail="archivo del documento no encontrado")

    EXPORTADOS_DIR.mkdir(parents=True, exist_ok=True)
    ext_origen = ruta.suffix.lower()

    if formato == "3mf":
        destino = EXPORTADOS_DIR / f"{doc_id}.3mf"
        nombre_objeto = Path(registro["nombre"]).stem or "pieza"
        if ext_origen in _STEP_EXTS:
            shape = b123d_kernel.import_from_step(ruta)
            entradas_3mf = solids.cargar(doc_id)
        else:
            shape = mesh.load_stl(ruta)
            entradas_3mf = None
        try:
            resultado_3mf = export_solidos.exportar_3mf_documento(
                shape, entradas_3mf, destino, nombre_por_defecto=nombre_objeto,
                materiales=materiales.cargar(doc_id) if entradas_3mf else None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not por_solido:
            resultado_3mf["descarga"] = (
                f"/documentos/{doc_id}/descarga/{_slug_descarga(registro['nombre'])}.3mf"
            )
        return resultado_3mf

    if por_solido:
        if ext_origen not in _STEP_EXTS:
            raise HTTPException(
                status_code=400,
                detail="por_solido solo esta disponible para documentos STEP (con desglose de solidos)",
            )
        entradas = solids.cargar(doc_id)
        if not entradas:
            raise HTTPException(
                status_code=400,
                detail="este documento no tiene desglose de solidos (solidos.json ausente)",
            )
        shape = b123d_kernel.import_from_step(ruta)
        directorio = EXPORTADOS_DIR / doc_id
        mapa, nombres_inciertos = export_solidos.exportar_por_solido(
            shape, entradas, formato, directorio
        )
        respuesta: dict[str, Any] = {"directorio": str(directorio), "archivos": mapa}
        if nombres_inciertos:
            # Phase 5B fix-review: `solidos_por_indice` fell back to flat
            # `solido_N` naming (couldn't confidently match stored names to
            # this reimport's geometry) — the filenames above ARE those
            # `solido_N` names, never a silently mislabeled piece.
            respuesta["nombres_inciertos"] = True
        return respuesta

    destino = EXPORTADOS_DIR / f"{doc_id}.{formato}"

    if formato == "stl":
        if ext_origen in _STEP_EXTS:
            shape = b123d_kernel.import_from_step(ruta)
            tri_mesh = mesh.tessellate_to_trimesh(shape)
            destino.write_bytes(mesh.to_stl_bytes(tri_mesh))
        else:
            shutil.copyfile(ruta, destino)
    elif formato == "step":
        if ext_origen not in _STEP_EXTS:
            raise HTTPException(
                status_code=400,
                detail="no se puede exportar a STEP un documento importado como malla STL",
            )
        shutil.copyfile(ruta, destino)

    resultado: dict[str, Any] = {"ruta": str(destino), "tamano_bytes": destino.stat().st_size}
    if formato == "stl":
        resultado["descarga"] = (
            f"/documentos/{doc_id}/descarga/{_slug_descarga(registro['nombre'])}.stl"
        )
    return resultado


@router.get("/documentos/{doc_id}/colisiones")
def verificar_colisiones_documento(
    doc_id: str,
    tolerancia_mm3: float = Query(default=colisiones_checks.TOLERANCIA_MM3_POR_DEFECTO, ge=0.0),
) -> dict[str, Any]:
    """Pairwise collision / split-piece / floating-piece check over a STEP
    document's named solids (Phase 5B, feedback item 2). Backs the MCP
    `check_colisiones` tool — see `app/checks/colisiones.py` for the full
    contract (only lists problems; `ok: true` when everything is empty).

    Only available for STEP/script documents with a named-solid breakdown; a
    mesh-only (STL) document, or a STEP document that somehow lacks
    `solidos.json`, gets a clear 400 instead.
    """
    registro = _registry.get(doc_id)
    if registro is None:
        raise HTTPException(status_code=404, detail="documento no encontrado")
    ruta = _files.get(doc_id)
    if ruta is None or not ruta.exists():
        raise HTTPException(status_code=404, detail="archivo del documento no encontrado")
    if ruta.suffix.lower() not in _STEP_EXTS:
        raise HTTPException(
            status_code=400,
            detail="check_colisiones solo esta disponible para documentos STEP/script (no malla STL)",
        )
    entradas = solids.cargar(doc_id)
    if not entradas:
        raise HTTPException(
            status_code=400,
            detail="este documento no tiene desglose de solidos (solidos.json ausente)",
        )

    shape = b123d_kernel.import_from_step(ruta)
    return colisiones_checks.verificar(shape, entradas, tolerancia_mm3=tolerancia_mm3)


@router.get("/documentos/{doc_id}/percibir")
def percibir_documento(
    doc_id: str,
    capas: str = Query(default="contactos"),
    cortes_z: list[float] | None = Query(default=None),
    eje: str = Query(default="z"),
    resolucion: int = Query(
        default=40, ge=percepcion.RESOLUCION_MIN, le=percepcion.RESOLUCION_MAX
    ),
    solidos: list[str] | None = Query(default=None),
    proximidad_mm: float = Query(
        default=percepcion.PROXIMIDAD_MM_POR_DEFECTO, ge=0.0, le=percepcion.PROXIMIDAD_MM_MAX
    ),
) -> dict[str, Any]:
    """Spatial perception as compact TEXT (Phase 5E) — measured relations
    (contacts/supports/clearances/cross-sections), not a picture. Read-only,
    token-free like `/colisiones`. See `app/percepcion.py` for the full
    layer contract; backs the MCP `percibir` tool.

    `capas` is a comma-separated subset of `contactos` (default),
    `cortes`, `huecos` (stub, not implemented this phase). Only available
    for STEP/script documents with a named-solid breakdown; a mesh-only
    (STL) document, or a STEP document that somehow lacks `solidos.json`,
    gets a clear 400 instead (same restriction as `check_colisiones`, and
    for the same reason: `contactos`/`cortes` both need actual named
    build123d solids, not just a triangle mesh).

    Input bounds (5E fix-review): at most `percepcion.CORTES_MAX` values in
    `cortes_z` and `proximidad_mm` <= `percepcion.PROXIMIDAD_MM_MAX` — 422
    above either, before any geometry is loaded.
    """
    if cortes_z is not None and len(cortes_z) > percepcion.CORTES_MAX:
        raise HTTPException(
            status_code=422,
            detail=(
                f"'cortes_z' admite como maximo {percepcion.CORTES_MAX} valores "
                f"({len(cortes_z)} recibidos)"
            ),
        )
    registro = _registry.get(doc_id)
    if registro is None:
        raise HTTPException(status_code=404, detail="documento no encontrado")
    ruta = _files.get(doc_id)
    if ruta is None or not ruta.exists():
        raise HTTPException(status_code=404, detail="archivo del documento no encontrado")
    if ruta.suffix.lower() not in _STEP_EXTS:
        raise HTTPException(
            status_code=400,
            detail="percibir solo esta disponible para documentos STEP/script (no malla STL)",
        )
    entradas = solids.cargar(doc_id)
    if not entradas:
        raise HTTPException(
            status_code=400,
            detail="este documento no tiene desglose de solidos (solidos.json ausente)",
        )

    shape = b123d_kernel.import_from_step(ruta)
    try:
        texto, nombres_inciertos = percepcion.percibir_texto(
            shape,
            entradas,
            capas=capas,
            cortes_z=cortes_z,
            eje=eje,
            resolucion=resolucion,
            solidos_filtro=solidos,
            proximidad_mm=proximidad_mm,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    salida: dict[str, Any] = {"texto": texto, "tokens_aprox": max(1, round(len(texto) / 4))}
    if nombres_inciertos:
        salida["nombres_inciertos"] = True
    return salida


_CAPTURA_LADO_MIN = 100
_CAPTURA_LADO_MAX = 2000


@router.get("/documentos/{doc_id}/captura")
def capturar_documento(
    doc_id: str,
    azimut: float = Query(default=45.0),
    elevacion: float = Query(default=25.0),
    ancho: int = Query(default=512, ge=_CAPTURA_LADO_MIN, le=_CAPTURA_LADO_MAX),
    alto: int = Query(default=512, ge=_CAPTURA_LADO_MIN, le=_CAPTURA_LADO_MAX),
    colores_por_solido: bool = Query(default=True),
    solidos: list[str] | None = Query(default=None),
) -> Response:
    """Headless PNG render of a document (Phase 5B, feedback item 1) — see
    `app/render.py` for the full rendering recipe. Backs the MCP `captura`
    tool, which replaces the Phase 3 stub.

    `solidos` (repeated query param), when given, renders only those named
    solids — the cheap way to "see the interior" of an assembly (e.g. omit
    the house shell's name to look inside it). The one-line caption is
    echoed back in the `X-Forja-Captura` response header (ASCII-only) so an
    HTTP caller gets it without parsing PNG bytes.
    """
    registro = _registry.get(doc_id)
    if registro is None:
        raise HTTPException(status_code=404, detail="documento no encontrado")
    ruta = _files.get(doc_id)
    if ruta is None or not ruta.exists():
        raise HTTPException(status_code=404, detail="archivo del documento no encontrado")

    ext = ruta.suffix.lower()
    entradas = solids.cargar(doc_id) if ext in _STEP_EXTS else None
    if ext in _STEP_EXTS and not entradas:
        raise HTTPException(
            status_code=400,
            detail="este documento no tiene desglose de solidos (solidos.json ausente)",
        )

    try:
        if ext in _STEP_EXTS:
            shape = b123d_kernel.import_from_step(ruta)
            grupos, colores, nombres_inciertos = render.grupos_desde_shape(shape, entradas, solidos)
        else:
            malla = mesh.load_stl(ruta)
            grupos, colores, nombres_inciertos = render.grupo_desde_malla(malla, solidos)
        png_bytes, caption = render.renderizar_png(
            grupos,
            colores,
            azimut=azimut,
            elevacion=elevacion,
            ancho=ancho,
            alto=alto,
            colores_por_solido=colores_por_solido,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if nombres_inciertos:
        # Phase 5B fix-review: `solidos_por_indice` fell back to flat
        # `solido_N` naming (couldn't confidently match stored names to
        # this reimport's geometry) — the legend above is that fallback,
        # never a silently mislabeled piece.
        caption += " (nombres inciertos)"

    return Response(content=png_bytes, media_type="image/png", headers={"X-Forja-Captura": caption})


@router.get("/documentos/{doc_id}/caras")
def obtener_caras(doc_id: str) -> list[dict[str, Any]]:
    """Compact per-face list ``[{id, tipo, centroide, normal, area}]``
    (Phase 4 stable naming, `app/naming.py`). List position ``i`` is the
    same face `/documentos/{id}/caras_triangulos`'s value ``i`` (a triangle
    index into `/documentos/{id}/malla`) points back at. Only STEP (B-rep)
    documents have faces; a pure-mesh STL document has none (400)."""
    ruta = _ruta_step_o_404(doc_id)
    shape = b123d_kernel.import_from_step(ruta)
    return [
        {
            "id": fp.id,
            "tipo": fp.subtipo,
            "centroide": list(fp.centroide),
            "normal": list(fp.direccion),
            "area": fp.medida,
        }
        for fp in naming.caras(shape)
    ]


@router.get("/documentos/{doc_id}/caras_triangulos")
def obtener_caras_triangulos(doc_id: str) -> Response:
    """Binary little-endian ``uint32`` array, one entry per triangle of
    `/documentos/{id}/malla`'s STL, each entry a position in
    `/documentos/{id}/caras`'s list. Lets the web viewer turn a three.js
    raycast triangle hit into a stable face id without shipping any raw
    geometry over JSON."""
    ruta = _ruta_step_o_404(doc_id)
    shape = b123d_kernel.import_from_step(ruta)
    _tri_mesh, indices = mesh.tessellate_to_trimesh_con_caras(shape)
    return Response(content=indices.tobytes(), media_type="application/octet-stream")


@router.get("/documentos/{doc_id}/historial")
def obtener_historial(doc_id: str) -> list[dict[str, Any]]:
    """Compact snapshot history: ``[{id, fecha, mensaje}]``, one entry per
    accepted mutation (creation, script update, notes/strokes CRUD — ADR-0003).
    Never file contents/diffs; see `POST /documentos/{id}/restaurar`."""
    if doc_id not in _registry:
        raise HTTPException(status_code=404, detail="documento no encontrado")
    return versioning.listar_historial(doc_id)


class _RestaurarBody(BaseModel):
    snapshot: str


@router.post("/documentos/{doc_id}/restaurar", dependencies=[Depends(auth.requiere_token)])
def restaurar_documento(doc_id: str, body: _RestaurarBody) -> dict[str, Any]:
    """Restore ``doc_id`` (file + notes) to a previous snapshot. The
    CURRENT state is snapshotted first too, so restoring is itself an
    undoable edit — never a one-way door.

    Token-protected (ADR-0005: every write route that mutates state, not
    just the code-execution/arbitrary-read ones, must require the shared
    token) — restoring silently overwrites the live file/notes, so it is
    exactly the kind of state-mutating route ADR-0005's consequence already
    anticipated."""
    if doc_id not in _registry:
        raise HTTPException(status_code=404, detail="documento no encontrado")
    ruta = _files.get(doc_id)
    if ruta is None:
        raise HTTPException(status_code=404, detail="archivo del documento no encontrado")

    with parametros.bloqueo(doc_id):
        if doc_id not in _registry:
            raise HTTPException(status_code=404, detail="documento no encontrado")
        try:
            return _restaurar_documento_bloqueado(doc_id, ruta, body.snapshot)
        except assemblies.EstadoIncompatible as exc:
            # A snapshot from a build before the Phase 6.3 vocabulary rename
            # cannot be applied as-is; the message says what to do instead.
            raise HTTPException(status_code=409, detail=str(exc)) from exc


def _restaurar_documento_bloqueado(doc_id: str, ruta: Path, snapshot: str) -> dict[str, Any]:
    try:
        archivos = versioning.leer_snapshot(doc_id, snapshot)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # Refuse an assembly state this build cannot read *before* the first
    # write (Phase 6.3 fix round): the loop below restores the STEP first and
    # the assembly later, so checking only in `assemblies.restore_files`
    # would report failure after replacing the geometry — and before that
    # check existed this route answered 200 while planting state the very
    # next read could not parse.
    assemblies.validate_snapshot(archivos)

    actuales: dict[str, bytes] = {}
    if ruta.exists():
        actuales[ruta.name] = ruta.read_bytes()
    ruta_notas_doc = notes.ruta_notas(doc_id)
    if ruta_notas_doc.exists():
        actuales["notas.json"] = ruta_notas_doc.read_bytes()
    ruta_solidos_doc = solids.ruta_solidos(doc_id)
    if ruta_solidos_doc.exists():
        actuales["solidos.json"] = ruta_solidos_doc.read_bytes()
    actuales = _con_parametros_snapshot(actuales, doc_id)
    versioning.crear_snapshot(
        doc_id, f"antes de restaurar {snapshot}", _con_ensamble_snapshot(actuales, doc_id)
    )

    for nombre, contenido in archivos.items():
        if nombre == "notas.json":
            ruta_notas_doc.write_bytes(contenido)
        elif nombre == "solidos.json":
            ruta_solidos_doc.write_bytes(contenido)
        elif nombre in (parametros.NOMBRE_SNAPSHOT, materiales.NOMBRE_SNAPSHOT):
            continue  # applied below with the geometry, never written as-is
        elif nombre in (assemblies.STATE_KEY, assemblies.BASE_KEY):
            # Applied through `assemblies.restore_files` below: written here
            # instead, `ensamble.json`/`ensamble_base.step` would land in the
            # documents ROOT, where `recargar_documentos()` globs STEP files
            # at startup and would register a phantom `ensamble_base`
            # document (Phase 6.0 finding 7 / contract C5).
            continue
        else:
            (DOCUMENTOS_DIR / nombre).write_bytes(contenido)
    if "notas.json" not in archivos:
        # Ese snapshot es anterior a que existieran notas: restaurarlo debe
        # reflejar fielmente ese estado (sin notas), no dejar las de hoy.
        notes.guardar(doc_id, {"notas": [], "trazos": []})
    # Parametric state (fix-review): snapshot without parametros.json ->
    # the document of that time had no stored script -> non-parametric now.
    # Notes snapshots deliberately contain only notas.json. They undo an
    # annotation, not the current geometry/script or named-solid metadata.
    restaura_geometria = ruta.name in archivos
    if restaura_geometria:
        parametros.restaurar_estado(doc_id, archivos.get(parametros.NOMBRE_SNAPSHOT))
        materiales.restaurar_estado(doc_id, archivos.get(materiales.NOMBRE_SNAPSHOT))
        # The assembly belongs to the geometry: a geometry snapshot carries
        # its pose state (C2), and one taken before the assembly existed
        # carries none, which removes it (`restore_files({})`) rather than
        # leaving joints pinned to the restored STEP (C3). A notes-only
        # snapshot (no STEP key) skips this entirely: undoing an annotation
        # must not touch the live assembly or its pose (C4).
        assemblies.restore_files(doc_id, {
            nombre: archivos[nombre]
            for nombre in (assemblies.STATE_KEY, assemblies.BASE_KEY)
            if nombre in archivos
        })

    analisis = _analyze(ruta, ruta.suffix.lower())
    registro = {"id": doc_id, "nombre": _registry[doc_id]["nombre"], **analisis}
    _registry[doc_id] = registro
    if restaura_geometria and "solidos.json" not in archivos:
        # Ese snapshot es anterior a que existieran nombres de solido (o el
        # documento de entonces no era STEP): recomputar un desglose plano
        # fresco en vez de dejar un sidecar de un estado distinto.
        if ruta.suffix.lower() in _STEP_EXTS:
            shape = b123d_kernel.import_from_step(ruta)
            solids.guardar(doc_id, solids.construir_entradas(shape))
        else:
            solids.borrar(doc_id)
    previa = _revisiones.get(doc_id)
    confirmar_revision(doc_id)
    if _revisiones.get(doc_id) == previa:
        eventos.publicar("anotaciones_actualizadas", doc_id, previa,
                        ["notas", "historial", "parametros", "ensamble", "materiales"])
    return {**registro, "revision": _revisiones.get(doc_id)}


@router.delete("/documentos/{doc_id}", dependencies=[Depends(auth.requiere_token)])
def eliminar_documento(doc_id: str) -> dict[str, Any]:
    """Permanently delete ``doc_id``: its stored file, filename sidecar,
    notes file, named-solid metadata, assembly state (`.ensambles/{doc_id}/`,
    Phase 6), any exported artifacts — the ``exportados/{doc_id}.*`` files
    and the per-solid ``exportados/{doc_id}/`` directory — and its full
    snapshot history.

    Token-protected (ADR-0005: deletes state). Not exposed as an MCP tool
    (not needed by the agent workflow) — added for test-isolation: `tests/
    test_mcp_tools.py` exercises the MCP tools against the live backend
    over HTTP (a separate process, so it cannot use the `FORJA_DATA_DIR`
    tmp-dir override `tests/conftest.py` sets for in-process tests) and
    calls this to clean up the throwaway documents it creates instead of
    leaving them in the real `documentos_data/`."""
    # Validated *before* the lock (Phase 6.3): `parametros.bloqueo` creates
    # and keeps one lock per id it is asked about and never evicts, so an id
    # that names no document must not create one (mirrors `app/notes.py`).
    # `_eliminar_documento_bloqueado` re-checks under the lock, so a
    # document deleted in between still gets today's 404.
    if doc_id not in _registry:
        raise HTTPException(status_code=404, detail="documento no encontrado")
    with parametros.bloqueo(doc_id):
        return _eliminar_documento_bloqueado(doc_id)


def _borrar_directorio_exportado(doc_id: str) -> None:
    """Remove ``exportados/{doc_id}/`` — the per-solid export directory
    `exportar_documento`'s ``por_solido=true`` writes (Phase 5B, one file per
    named solid). The sibling file glob only matches ``{doc_id}.*``, so the
    directory outlived every document that ever exported that way.

    The resolved-parent check keeps the deletion inside ``EXPORTADOS_DIR``:
    a ``..``/symlinked id resolves elsewhere and is left alone."""
    directorio = EXPORTADOS_DIR / doc_id
    if directorio.is_dir() and directorio.resolve().parent == EXPORTADOS_DIR.resolve():
        shutil.rmtree(directorio, ignore_errors=True)


def _eliminar_documento_bloqueado(doc_id: str) -> dict[str, Any]:
    if doc_id not in _registry:
        raise HTTPException(status_code=404, detail="documento no encontrado")

    ruta = _files.pop(doc_id, None)
    if ruta is not None:
        ruta.unlink(missing_ok=True)
    _ruta_meta(doc_id).unlink(missing_ok=True)
    _borrar_cache_malla(doc_id)
    notes.ruta_notas(doc_id).unlink(missing_ok=True)
    solids.borrar(doc_id)
    materiales.borrar(doc_id)
    # The assembly is part of the document (Phase 6, ADR-0003/0006: deleting
    # a document must leave nothing of it behind, not even state a restore
    # could not reach); `clear` also removes `.ensambles/<id>/` itself.
    assemblies.clear(doc_id)
    if EXPORTADOS_DIR.exists():
        for ruta_exportada in EXPORTADOS_DIR.glob(f"{doc_id}.*"):
            ruta_exportada.unlink(missing_ok=True)
        _borrar_directorio_exportado(doc_id)
    versioning.borrar_historial(doc_id)
    _registry.pop(doc_id, None)
    _revisiones.pop(doc_id, None)
    eventos.publicar("documento_eliminado", doc_id)
    return {"ok": True}
