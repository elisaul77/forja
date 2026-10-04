"""Branches, steps and comparison of design states (Plan G · G2/G3).

Model (ADR-0014, «G2/G3» section):

- ``refs/heads/main`` keeps being the G1 chain of «before the change»
  snapshots: `leer_historial`/`restaurar` are untouched.
- Each branch is ``refs/forja/ramas/<nombre>``; every commit on it is the
  COMPLETE state of the document right AFTER an accepted change (a «paso»):
  geometry, ``meta.json`` (name + script + parameters), ``notas.json``,
  ``solidos.json``, ``materiales.json``, ``ensamble.json`` +
  ``ensamble_base.step``, and ``_fuente/script.py`` with the TEXT of a
  script remembered by ``ruta``. Trailers add ``Forja-Revision`` (the
  geometric revision) and ``Forja-Rama``.
- The active branch name lives in ``.repos/<id>.git/forja-rama-activa``
  (default ``main``). A document's first branch is created lazily from its
  current state.
- Post-change commits are written by the middleware in `main.py` once a
  mutating request succeeded (`registrar_pendientes`), from the list
  `versioning.crear_snapshot` fills during the request.

Comparison (G3) follows the G0 rule: equal ``revision`` ⇒ no changes;
otherwise per named piece: |Δvol|/vol ≤ 1e-9, |Δbbox| ≤ 1e-6 mm, same solid
count and (only for the pieces that pass those) same face count ⇒ equal.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

import assemblies
import auth
import documents
import eventos
import git_store
import materiales
import notes
import parametros
import piece_mesh
import revision as revision_mod
import solids
import versioning

logger = logging.getLogger(__name__)
router = APIRouter()

TOL_VOL_REL = 1e-9
TOL_BBOX_MM = 1e-6
PASOS_MAX = 200
CAMBIOS_RAMA = ["geometria", "notas", "historial", "parametros", "ensamble", "materiales", "ramas"]


def _ahora() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


# ------------------------------------------------------------ captura

def _archivo_script(meta: dict[str, Any]) -> bytes | None:
    script = meta.get("script")
    if isinstance(script, dict) and isinstance(script.get("ruta"), str):
        try:
            ruta = documents._validar_ruta_permitida(script["ruta"])
            return ruta.read_bytes()
        except (HTTPException, OSError):
            return None
    if isinstance(script, dict) and isinstance(script.get("codigo"), str):
        return script["codigo"].encode()
    return None


def capturar(doc_id: str) -> tuple[dict[str, bytes], dict[str, bytes], str | None]:
    """Complete current state on disk: ``(archivos, fuente, revision)``.
    Caller holds `parametros.bloqueo(doc_id)`."""
    ruta = documents._files.get(doc_id)
    if ruta is None or not ruta.exists():
        raise LookupError("archivo del documento no encontrado")
    archivos: dict[str, bytes] = {ruta.name: ruta.read_bytes()}
    meta_ruta = documents._ruta_meta(doc_id)
    meta: dict[str, Any] = {}
    if meta_ruta.exists():
        archivos["meta.json"] = meta_ruta.read_bytes()
        try:
            meta = json.loads(archivos["meta.json"])
        except (json.JSONDecodeError, UnicodeDecodeError):
            meta = {}
    for nombre, camino in (("notas.json", notes.ruta_notas(doc_id)),
                           ("solidos.json", solids.ruta_solidos(doc_id)),
                           (materiales.NOMBRE_SNAPSHOT, materiales.ruta(doc_id))):
        if camino.exists():
            archivos[nombre] = camino.read_bytes()
    archivos.update(assemblies.snapshot_files(doc_id))
    fuente: dict[str, bytes] = {}
    texto = _archivo_script(meta)
    if texto is not None:
        fuente["script.py"] = texto
    rev = revision_mod.calcular(archivos[ruta.name], ruta.suffix.lower() in documents._STEP_EXTS,
                                [("solidos.json", archivos.get("solidos.json"))])
    return archivos, fuente, rev


def _mensaje_posterior(mensaje: str) -> str:
    return mensaje[len("antes de "):] if mensaje.startswith("antes de ") else mensaje


def registrar(doc_id: str, mensaje: str, autor: str | None = None,
              solo_si_cambia: bool = False) -> str:
    """Commit the current complete state on the active branch; returns the
    new tip. With ``solo_si_cambia`` an unchanged tree adds nothing. Caller
    holds `parametros.bloqueo(doc_id)`."""
    archivos, fuente, rev = capturar(doc_id)
    with git_store.bloqueo(doc_id):
        activa = git_store.rama_activa(doc_id)
        tip = git_store.tip_rama(doc_id, activa)
        if solo_si_cambia and tip:
            if git_store.construir_arbol(doc_id, archivos, fuente) == git_store.arbol_de(doc_id, tip):
                return tip
        nuevo = git_store.escribir_commit(
            doc_id, archivos, mensaje, uuid.uuid4().hex[:12], _ahora(),
            autor or versioning.autor_actual.get(), fuente, padre=tip,
            extra={"Revision": rev or "", "Rama": activa})
        git_store.mover_ref_rama(doc_id, activa, nuevo, tip)
        git_store.recoger(doc_id)
        return nuevo


def asegurar(doc_id: str) -> None:
    """Lazily create ``main`` from the current state when the document has
    no branch yet. Caller holds `parametros.bloqueo(doc_id)`."""
    if not git_store.listar_ramas(doc_id):
        git_store.fijar_rama_activa(doc_id, git_store.RAMA_PRINCIPAL)
        registrar(doc_id, "estado inicial", "forja")


def registrar_pendientes(pendientes: list[tuple[str, str, str]]) -> None:
    """Middleware hook: one post-change commit per document touched by a
    successful request. Never raises (best effort, like events)."""
    por_doc: dict[str, tuple[list[str], str]] = {}
    for doc_id, mensaje, autor in pendientes:
        mensajes, _ = por_doc.setdefault(doc_id, ([], autor))
        texto = _mensaje_posterior(mensaje)
        if texto not in mensajes:
            mensajes.append(texto)
    for doc_id, (mensajes, autor) in por_doc.items():
        try:
            if doc_id not in documents._registry:
                continue
            with parametros.bloqueo(doc_id):
                if doc_id not in documents._registry:
                    continue
                if not git_store.listar_ramas(doc_id):
                    git_store.fijar_rama_activa(doc_id, git_store.RAMA_PRINCIPAL)
                registrar(doc_id, "; ".join(mensajes)[:2000], autor)
        except Exception:  # noqa: BLE001
            logger.exception("no se pudo registrar el paso de %s", doc_id)


# ------------------------------------------------------------ lectura

def _paso(entrada: dict[str, Any]) -> dict[str, Any]:
    return {"sha_corto": entrada["sha"][:10], "fecha": entrada.get("fecha"),
            "autor": entrada.get("autor"), "mensaje": entrada.get("mensaje"),
            "revision": entrada.get("revision")}


def listar(doc_id: str) -> dict[str, Any]:
    activa = git_store.rama_activa(doc_id)
    ramas = []
    for nombre, sha in git_store.listar_ramas(doc_id).items():
        ultimo = git_store.log_limitado(doc_id, sha, 1)
        ramas.append({"nombre": nombre, "activa": nombre == activa,
                      "pasos": git_store.contar(doc_id, sha),
                      "ultimo": _paso(ultimo[0]) if ultimo else None})
    return {"activa": activa, "ramas": ramas}


def pasos(doc_id: str, nombre: str | None, limite: int = 50) -> dict[str, Any]:
    rama = nombre or git_store.rama_activa(doc_id)
    sha = git_store.tip_rama(doc_id, rama)
    if sha is None:
        raise LookupError(f"rama {rama!r} no encontrada")
    limite = max(1, min(int(limite), PASOS_MAX))
    return {"rama": rama, "total": git_store.contar(doc_id, sha),
            "pasos": [_paso(e) for e in git_store.log_limitado(doc_id, sha, limite)]}


# ------------------------------------------------------------ mutaciones

def crear(doc_id: str, nombre: str, desde: str | None = None) -> dict[str, Any]:
    git_store.validar_rama(nombre)
    if desde is None:
        registrar(doc_id, "estado sin registrar", solo_si_cambia=True)
    with git_store.bloqueo(doc_id):
        if git_store.tip_rama(doc_id, nombre):
            raise FileExistsError(f"la rama {nombre!r} ya existe")
        origen = (git_store.tip_rama(doc_id, git_store.rama_activa(doc_id)) if desde is None
                  else git_store.resolver(doc_id, desde))
        if origen is None:
            raise LookupError("no hay estado del que partir")
        git_store.mover_ref_rama(doc_id, nombre, origen, None)
    return {"rama": nombre, "desde": origen[:10]}


def renombrar(doc_id: str, nombre: str, a: str) -> dict[str, Any]:
    git_store.validar_rama(nombre)
    git_store.validar_rama(a)
    if nombre == git_store.RAMA_PRINCIPAL:
        raise PermissionError("la rama main no se renombra")
    with git_store.bloqueo(doc_id):
        sha = git_store.tip_rama(doc_id, nombre)
        if sha is None:
            raise LookupError(f"rama {nombre!r} no encontrada")
        if git_store.tip_rama(doc_id, a):
            raise FileExistsError(f"la rama {a!r} ya existe")
        git_store.mover_ref_rama(doc_id, a, sha, None)
        if git_store.rama_activa(doc_id) == nombre:
            git_store.fijar_rama_activa(doc_id, a)
        git_store.mover_ref_rama(doc_id, nombre, None, sha)
    return {"rama": a, "antes": nombre}


def borrar(doc_id: str, nombre: str) -> dict[str, Any]:
    git_store.validar_rama(nombre)
    if nombre == git_store.RAMA_PRINCIPAL:
        raise PermissionError("la rama main no se borra")
    with git_store.bloqueo(doc_id):
        if git_store.rama_activa(doc_id) == nombre:
            raise PermissionError("no se borra la rama activa; cambia antes a otra")
        sha = git_store.tip_rama(doc_id, nombre)
        if sha is None:
            raise LookupError(f"rama {nombre!r} no encontrada")
        git_store.mover_ref_rama(doc_id, nombre, None, sha)
    # The sha lets the commits be recovered until git prunes them.
    return {"borrada": nombre, "sha": sha}


def _snapshot_actual(doc_id: str, ruta: Path) -> dict[str, bytes]:
    actuales: dict[str, bytes] = {ruta.name: ruta.read_bytes()}
    for nombre, camino in (("notas.json", notes.ruta_notas(doc_id)),
                           ("solidos.json", solids.ruta_solidos(doc_id))):
        if camino.exists():
            actuales[nombre] = camino.read_bytes()
    actuales = documents._con_parametros_snapshot(actuales, doc_id)
    return documents._con_ensamble_snapshot(actuales, doc_id)


def _meta_sin_rama(doc_id: str) -> bytes | None:
    """``meta.json`` for a state that recorded none: only today's display
    name survives (it is not branch data); ``None`` = no meta at all."""
    try:
        actual = json.loads(documents._ruta_meta(doc_id).read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if isinstance(actual, dict) and isinstance(actual.get("nombre"), str):
        return json.dumps({"nombre": actual["nombre"]}).encode()
    return None


def _materializar(doc_id: str, ruta: Path, archivos: dict[str, bytes]) -> None:
    """Write a stored state over the document. Phase 1 writes every target
    as a temporary next to it (a full disk fails here, before touching
    anything); phase 2 renames them in and removes the sidecars the state
    lacks. A failure in phase 2 is undone by `cambiar`."""
    meta = archivos.get("meta.json")
    if meta is None:
        meta = _meta_sin_rama(doc_id)
    destinos: list[tuple[Path, bytes | None]] = [(ruta, archivos[ruta.name]),
                                                 (documents._ruta_meta(doc_id), meta)]
    for nombre, camino in (("notas.json", notes.ruta_notas(doc_id)),
                           ("solidos.json", solids.ruta_solidos(doc_id)),
                           (materiales.NOMBRE_SNAPSHOT, materiales.ruta(doc_id))):
        destinos.append((camino, archivos.get(nombre)))
    preparados: list[tuple[Path, Path | None]] = []
    try:
        for destino, contenido in destinos:
            if contenido is None:
                preparados.append((destino, None))
                continue
            temporal = destino.with_name(destino.name + ".rama.tmp")
            temporal.write_bytes(contenido)
            preparados.append((destino, temporal))
    except BaseException:
        for _destino, temporal in preparados:
            if temporal is not None:
                temporal.unlink(missing_ok=True)
        raise
    for destino, temporal in preparados:
        if temporal is None:
            destino.unlink(missing_ok=True)
        else:
            os.replace(temporal, destino)
    ensamble = {k: archivos[k] for k in (assemblies.STATE_KEY, assemblies.BASE_KEY) if k in archivos}
    assemblies.restore_files(doc_id, ensamble)
    if ensamble:
        # `restore_files` re-serialises state.json: put back the exact bytes.
        estado = assemblies._directory(doc_id) / "state.json"
        temporal = estado.with_name("state.json.rama.tmp")
        temporal.write_bytes(ensamble[assemblies.STATE_KEY])
        os.replace(temporal, estado)


def _sha256(contenido: bytes) -> str:
    return hashlib.sha256(contenido).hexdigest()


def _revisar_script(doc_id: str, rama: str, sha: str, archivos: dict[str, bytes]) -> list[str]:
    """Scripts remembered by ``ruta`` live OUTSIDE Forja's data and are
    never written by a switch. When the branch's recorded text differs from
    that file, mark the document (regenerating then needs confirmation, see
    `documents.aplicar_parametros`) and return the warning."""
    meta = _json(archivos, "meta.json")
    script = meta.get("script") if isinstance(meta, dict) else None
    texto = git_store.leer_fuente(doc_id, sha).get("script.py")
    if not (isinstance(script, dict) and isinstance(script.get("ruta"), str)) or texto is None:
        git_store.fijar_script_divergente(doc_id, None, None)
        return []
    try:
        en_disco = documents._validar_ruta_permitida(script["ruta"]).read_bytes()
    except (HTTPException, OSError):
        en_disco = None
    if en_disco == texto:
        git_store.fijar_script_divergente(doc_id, None, None)
        return []
    git_store.fijar_script_divergente(doc_id, script["ruta"], _sha256(texto))
    return [f"script_divergente: el script guardado en la rama {rama} no coincide con el archivo "
            f"{script['ruta']}; Forja no escribe fuera de sus datos. Regenerar parametros queda "
            "bloqueado hasta que restaures ese archivo o confirmes usarlo (confirmar_script=true)."]


def cambiar(doc_id: str, nombre: str) -> dict[str, Any]:
    """Materialise branch ``nombre`` into the document. Caller holds
    `parametros.bloqueo(doc_id)`. The current state is first saved on the
    branch being left (if it had unrecorded changes) and as a G1 snapshot,
    so switching never loses anything and is undoable with `restaurar`.

    All-or-nothing: any failure from the first file written to the new
    revision being confirmed puts back the files of the step just recorded
    (the state before the switch), the active branch, the registry entry,
    the revision and the script mark, then re-raises."""
    git_store.validar_rama(nombre)
    ruta = documents._files.get(doc_id)
    if ruta is None or not ruta.exists():
        raise LookupError("archivo del documento no encontrado")
    asegurar(doc_id)
    activa = git_store.rama_activa(doc_id)
    destino = git_store.tip_rama(doc_id, nombre)
    if destino is None:
        raise LookupError(f"rama {nombre!r} no encontrada")
    if nombre == activa:
        aviso = documents.aviso_script_divergente(doc_id, parametros.cargar(doc_id)["script"])
        return {**documents._registry[doc_id], "revision": documents._revisiones.get(doc_id),
                "rama": nombre, "sha_corto": destino[:10], "avisos": [aviso] if aviso else []}
    previo = registrar(doc_id, "estado sin registrar", solo_si_cambia=True)
    archivos = git_store.leer(doc_id, destino)
    if ruta.name not in archivos:
        raise ValueError("la rama no contiene la geometria de este documento")
    assemblies.validate_snapshot(archivos)
    versioning.crear_snapshot(doc_id, f"antes de cambiar a la rama {nombre}",
                              _snapshot_actual(doc_id, ruta), pendiente=False)
    registro_previo = documents._registry[doc_id]
    tenia_revision = doc_id in documents._revisiones
    revision_previa = documents._revisiones.get(doc_id)
    marca_previa = git_store.leer_script_divergente(doc_id)
    try:
        _materializar(doc_id, ruta, archivos)
        git_store.fijar_rama_activa(doc_id, nombre)
        avisos = _revisar_script(doc_id, nombre, destino, archivos)
        analisis = documents._analyze(ruta, ruta.suffix.lower())
        nombre_doc = documents._leer_meta(doc_id, registro_previo["nombre"])
        registro = {"id": doc_id, "nombre": nombre_doc, **analisis}
        documents._registry[doc_id] = registro
        documents.confirmar_revision(doc_id)
        nueva = documents._revisiones.get(doc_id)
    except BaseException:
        logger.exception("cambio a la rama %s fallido en %s; se restablece %s", nombre, doc_id, activa)
        try:
            _materializar(doc_id, ruta, git_store.leer(doc_id, previo))
        finally:
            git_store.fijar_rama_activa(doc_id, activa)
            if marca_previa:
                git_store.fijar_script_divergente(doc_id, marca_previa["ruta"], marca_previa["sha256"])
            else:
                git_store.fijar_script_divergente(doc_id, None, None)
            documents._registry[doc_id] = registro_previo
            if tenia_revision:
                documents._revisiones[doc_id] = revision_previa
            else:
                documents._revisiones.pop(doc_id, None)
        raise
    if nueva == revision_previa:
        eventos.publicar("anotaciones_actualizadas", doc_id, nueva,
                         [c for c in CAMBIOS_RAMA if c != "geometria"])
    return {**registro, "revision": nueva, "rama": nombre, "sha_corto": destino[:10], "avisos": avisos}


# ------------------------------------------------------------ comparar

_caras_cache: OrderedDict[str, dict[int, int]] = OrderedDict()
_caras_lock = threading.Lock()


def _blob_sha(contenido: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(contenido) + contenido).hexdigest()


def _caras_por_indice(step: bytes) -> dict[int, int]:
    """Face count per solid index of a STEP (cached by content hash)."""
    clave = _blob_sha(step)
    with _caras_lock:
        if clave in _caras_cache:
            _caras_cache.move_to_end(clave)
            return _caras_cache[clave]
    from kernel import b123d_kernel
    with tempfile.NamedTemporaryFile(suffix=".step") as tmp:
        tmp.write(step)
        tmp.flush()
        forma = b123d_kernel.import_from_step(Path(tmp.name))
    resultado = {i: len(s.faces()) for i, s in enumerate(forma.solids())}
    with _caras_lock:
        _caras_cache[clave] = resultado
        while len(_caras_cache) > 16:
            _caras_cache.popitem(last=False)
    return resultado


def _json(archivos: dict[str, bytes], nombre: str) -> Any:
    try:
        return json.loads(archivos[nombre]) if nombre in archivos else None
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _piezas(solidos: Any) -> dict[str, dict[str, Any]]:
    piezas: dict[str, dict[str, Any]] = {}
    for e in solidos if isinstance(solidos, list) else []:
        if not isinstance(e, dict) or not isinstance(e.get("nombre"), str):
            continue
        p = piezas.setdefault(e["nombre"], {"volumen": 0.0, "bbox": None, "solidos": 0, "indices": []})
        p["volumen"] += float(e.get("volumen") or 0.0)
        p["solidos"] += 1
        p["indices"].append(e.get("indice"))
        bb = e.get("bbox")
        if isinstance(bb, list) and len(bb) == 6:
            if p["bbox"] is None:
                p["bbox"] = list(bb)
            else:
                p["bbox"] = [min(p["bbox"][i], bb[i]) if i % 2 == 0 else max(p["bbox"][i], bb[i])
                             for i in range(6)]
    return piezas


def _bbox_total(piezas: dict[str, dict[str, Any]]) -> list[float] | None:
    total = None
    for p in piezas.values():
        bb = p["bbox"]
        if bb is None:
            continue
        total = list(bb) if total is None else [min(total[i], bb[i]) if i % 2 == 0 else max(total[i], bb[i])
                                                for i in range(6)]
    return total


def _equivalentes(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if a["solidos"] != b["solidos"]:
        return False
    base = max(abs(a["volumen"]), abs(b["volumen"]), 1e-30)
    if abs(a["volumen"] - b["volumen"]) / base > TOL_VOL_REL:
        return False
    if (a["bbox"] is None) != (b["bbox"] is None):
        return False
    if a["bbox"] is not None and any(abs(x - y) > TOL_BBOX_MM for x, y in zip(a["bbox"], b["bbox"])):
        return False
    return True


def _r(x: float, n: int = 6) -> float:
    return round(x, n)


def comparar(doc_id: str, a: str, b: str) -> dict[str, Any]:
    """Diff of two states (branch names or step shas). Lock-free: reads git
    objects only."""
    sha_a = git_store.resolver(doc_id, a)
    sha_b = git_store.resolver(doc_id, b)
    meta_a = git_store.log_limitado(doc_id, sha_a, 1)
    meta_b = git_store.log_limitado(doc_id, sha_b, 1)
    arch_a = git_store.leer(doc_id, sha_a)
    arch_b = git_store.leer(doc_id, sha_b)
    rev_a = meta_a[0].get("revision") if meta_a else None
    rev_b = meta_b[0].get("revision") if meta_b else None
    geo = next((n for n in arch_a if n.lower().endswith((".step", ".stp", ".stl"))), None)
    piezas_a = _piezas(_json(arch_a, "solidos.json"))
    piezas_b = _piezas(_json(arch_b, "solidos.json"))
    totales_a, totales_b = piezas_a, piezas_b
    if bool(piezas_a) != bool(piezas_b):
        # Only one side names its solids: there is no common naming to diff
        # by piece, so both fall back to the single `documento` entry
        # (volume/bbox still come from the side that has them).
        piezas_a, piezas_b = {}, {}
    if not rev_a or not rev_b:  # older step without trailer: recompute
        def _rev(arch: dict[str, bytes]) -> str | None:
            g = next((n for n in arch if n.lower().endswith((".step", ".stp", ".stl"))), None)
            return revision_mod.calcular(arch[g], not g.lower().endswith(".stl"),
                                         [("solidos.json", arch.get("solidos.json"))]) if g else None
        rev_a, rev_b = rev_a or _rev(arch_a), rev_b or _rev(arch_b)
    identica = rev_a is not None and rev_a == rev_b

    estado: dict[str, str] = {}
    for nombre in list(piezas_a) + [n for n in piezas_b if n not in piezas_a]:
        if nombre not in piezas_b:
            estado[nombre] = "quitada"
        elif nombre not in piezas_a:
            estado[nombre] = "añadida"
        elif identica:
            estado[nombre] = "igual"
        else:
            estado[nombre] = "igual" if _equivalentes(piezas_a[nombre], piezas_b[nombre]) else "cambiada"
    candidatas = [n for n, e in estado.items() if e == "igual"] if not identica else []
    if candidatas and geo and geo in arch_a and geo in arch_b and not geo.lower().endswith(".stl"):
        caras_a = _caras_por_indice(arch_a[geo])
        caras_b = _caras_por_indice(arch_b[geo])
        for nombre in candidatas:
            ca = sorted(caras_a.get(i, -1) for i in piezas_a[nombre]["indices"])
            cb = sorted(caras_b.get(i, -1) for i in piezas_b[nombre]["indices"])
            if ca != cb:
                estado[nombre] = "cambiada"
    if not piezas_a and not piezas_b:  # STL or no named solids
        estado["documento"] = "igual" if identica else "cambiada"

    vol_a = sum(p["volumen"] for p in totales_a.values())
    vol_b = sum(p["volumen"] for p in totales_b.values())
    bb_a, bb_b = _bbox_total(totales_a), _bbox_total(totales_b)

    def _valores(arch: dict[str, bytes]) -> dict[str, Any]:
        meta = _json(arch, "meta.json") or {}
        v = meta.get("valores") if isinstance(meta, dict) else None
        return v if isinstance(v, dict) else {}

    va, vb = _valores(arch_a), _valores(arch_b)
    params = [{"nombre": k, "antes": va.get(k), "despues": vb.get(k)}
              for k in sorted(set(va) | set(vb)) if va.get(k) != vb.get(k)]

    def _mats(arch: dict[str, bytes]) -> dict[str, Any]:
        datos = _json(arch, materiales.NOMBRE_SNAPSHOT) or {}
        m = datos.get("materiales") if isinstance(datos, dict) else None
        return m if isinstance(m, dict) else {}

    ma, mb = _mats(arch_a), _mats(arch_b)
    mats = [{"pieza": k, "antes": ma.get(k), "despues": mb.get(k)}
            for k in sorted(set(ma) | set(mb)) if ma.get(k) != mb.get(k)]

    fuente_a = git_store.leer_fuente(doc_id, sha_a).get("script.py")
    fuente_b = git_store.leer_fuente(doc_id, sha_b).get("script.py")

    conteo = {k: sum(1 for e in estado.values() if e == k) for k in ("añadida", "quitada", "cambiada", "igual")}
    return {
        "a": {"sha_corto": sha_a[:10], "revision": rev_a},
        "b": {"sha_corto": sha_b[:10], "revision": rev_b},
        "sin_cambios": identica and not params and not mats and fuente_a == fuente_b,
        "geometria_identica": identica,
        "piezas": estado,
        "resumen": conteo,
        "volumen": {"a": _r(vol_a, 3), "b": _r(vol_b, 3), "delta": _r(vol_b - vol_a, 3),
                    "pct": _r((vol_b - vol_a) / vol_a * 100, 4) if vol_a else None},
        "bbox": {"a": bb_a, "b": bb_b,
                 "delta": [_r(y - x) for x, y in zip(bb_a, bb_b)] if bb_a and bb_b else None},
        "parametros": params,
        "materiales": mats,
        "script_cambiado": fuente_a != fuente_b,
    }


# ------------------------------------------------------------ malla de un paso

_CACHE_COMPARAR = documents.DOCUMENTOS_DIR / ".cache" / "comparar"
_MAX_CACHE_POR_DOC = 8


def malla_de_paso(doc_id: str, referencia: str) -> tuple[bytes, str]:
    """FJP1 piece bundle of the geometry stored in a step (for the overlay
    view), cached on disk by revision. Lock-free."""
    sha = git_store.resolver(doc_id, referencia)
    archivos = git_store.leer(doc_id, sha)
    geo = next((n for n in archivos if n.lower().endswith((".step", ".stp", ".stl"))), None)
    if geo is None:
        raise LookupError("el paso no tiene geometria")
    rev = revision_mod.calcular(archivos[geo], not geo.lower().endswith(".stl"),
                                [("solidos.json", archivos.get("solidos.json"))])
    destino = _CACHE_COMPARAR / f"{doc_id}.{rev}.v{documents._VERSION_MALLA}.piezas"
    if destino.is_file():
        return destino.read_bytes(), rev
    from kernel import b123d_kernel, mesh
    with tempfile.TemporaryDirectory() as tmp:
        camino = Path(tmp) / geo
        camino.write_bytes(archivos[geo])
        if geo.lower().endswith(".stl"):
            tri = mesh.load_stl(camino)
            info = mesh.analyze_stl_path(camino)
            contenido = piece_mesh.from_stl(tri, bbox=list(info.bbox), volume=info.volumen)
        else:
            forma = b123d_kernel.import_from_step(camino)
            info = b123d_kernel.analyze(forma)
            entradas = _json(archivos, "solidos.json")
            contenido = piece_mesh.from_step(forma, entradas, metadata_available=entradas is not None,
                                             bbox=list(info.bbox), volume=info.volumen)
    try:
        _CACHE_COMPARAR.mkdir(parents=True, exist_ok=True)
        tmp_dest = destino.with_name(destino.name + f".tmp{os.getpid()}")
        tmp_dest.write_bytes(contenido)
        os.replace(tmp_dest, destino)
        viejos = sorted(_CACHE_COMPARAR.glob(f"{doc_id}.*.piezas"), key=lambda p: p.stat().st_mtime)
        for viejo in viejos[:-_MAX_CACHE_POR_DOC]:
            viejo.unlink(missing_ok=True)
    except OSError:
        logger.warning("no se pudo guardar la malla de comparar de %s", doc_id)
    return contenido, rev


def borrar_cache(doc_id: str) -> None:
    for viejo in _CACHE_COMPARAR.glob(f"{doc_id}.*"):
        viejo.unlink(missing_ok=True)


# ------------------------------------------------------------ REST

def _doc_o_404(doc_id: str) -> None:
    if doc_id not in documents._registry:
        raise HTTPException(status_code=404, detail="documento no encontrado")


def _traducir(fn, *args):
    try:
        return fn(*args)
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except assemblies.EstadoIncompatible as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except git_store.GitError as exc:
        raise HTTPException(status_code=500, detail="error del almacen de versiones") from exc


def _bajo_candado(doc_id: str, fn, *args):
    _doc_o_404(doc_id)
    with parametros.bloqueo(doc_id):
        _doc_o_404(doc_id)
        return _traducir(fn, doc_id, *args)


class _CrearBody(BaseModel):
    nombre: str
    desde: str | None = None


class _RenombrarBody(BaseModel):
    a: str


@router.get("/documentos/{doc_id}/ramas")
def obtener_ramas(doc_id: str) -> dict[str, Any]:
    """``{activa, ramas: [{nombre, activa, pasos, ultimo{sha_corto, fecha,
    autor, mensaje, revision}}]}``. Creates ``main`` from the current state
    the first time (no visible change to the document)."""
    _doc_o_404(doc_id)
    if not git_store.listar_ramas(doc_id):
        _bajo_candado(doc_id, asegurar)
    return _traducir(listar, doc_id)


@router.post("/documentos/{doc_id}/ramas", dependencies=[Depends(auth.requiere_token)])
def crear_rama(doc_id: str, body: _CrearBody) -> dict[str, Any]:
    def _crear(d: str) -> dict[str, Any]:
        asegurar(d)
        return crear(d, body.nombre, body.desde)
    resultado = _bajo_candado(doc_id, _crear)
    eventos.publicar("anotaciones_actualizadas", doc_id, documents._revisiones.get(doc_id), ["ramas"])
    return resultado


@router.get("/documentos/{doc_id}/ramas/{rama}/pasos")
def obtener_pasos(doc_id: str, rama: str, limite: int = Query(default=50, ge=1, le=PASOS_MAX)) -> dict[str, Any]:
    _doc_o_404(doc_id)
    if not git_store.listar_ramas(doc_id):
        _bajo_candado(doc_id, asegurar)
    return _traducir(pasos, doc_id, rama, limite)


@router.post("/documentos/{doc_id}/ramas/{rama}/activar", dependencies=[Depends(auth.requiere_token)])
def activar_rama(doc_id: str, rama: str) -> dict[str, Any]:
    return _bajo_candado(doc_id, cambiar, rama)


@router.post("/documentos/{doc_id}/ramas/{rama}/renombrar", dependencies=[Depends(auth.requiere_token)])
def renombrar_rama(doc_id: str, rama: str, body: _RenombrarBody) -> dict[str, Any]:
    resultado = _bajo_candado(doc_id, renombrar, rama, body.a)
    eventos.publicar("anotaciones_actualizadas", doc_id, documents._revisiones.get(doc_id), ["ramas"])
    return resultado


@router.delete("/documentos/{doc_id}/ramas/{rama}", dependencies=[Depends(auth.requiere_token)])
def borrar_rama(doc_id: str, rama: str) -> dict[str, Any]:
    resultado = _bajo_candado(doc_id, borrar, rama)
    eventos.publicar("anotaciones_actualizadas", doc_id, documents._revisiones.get(doc_id), ["ramas"])
    return resultado


@router.get("/documentos/{doc_id}/comparar")
def comparar_estados(doc_id: str, a: str = Query(..., max_length=64),
                     b: str = Query(..., max_length=64)) -> dict[str, Any]:
    """Diff between two states (branch names or step shas, 7+ hex).
    Reads git objects without taking the document lock."""
    _doc_o_404(doc_id)
    if not git_store.listar_ramas(doc_id):
        _bajo_candado(doc_id, asegurar)
    return _traducir(comparar, doc_id, a, b)


@router.get("/documentos/{doc_id}/comparar/malla")
def malla_comparar(doc_id: str, ref: str = Query(..., max_length=64)) -> Response:
    """FJP1 piece bundle of one state's geometry, for the overlay view."""
    _doc_o_404(doc_id)
    contenido, rev = _traducir(malla_de_paso, doc_id, ref)
    return Response(content=contenido, media_type="application/vnd.forja.piezas",
                    headers={"X-Forja-Revision": rev, "Cache-Control": "no-store"})
