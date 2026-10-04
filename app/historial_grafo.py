"""Historial 2.0: commit graph, per-step thumbnails, per-piece history and
«restore one piece to a version» (amplía ADR-0014/0015, sin sustituirlos).

- ``GET /documentos/{id}/grafo``: every step of every branch (topological
  order, newest first) with its parents (merges have two), the branches
  that contain it, milestones, author/date/message/revision and a compact
  summary of changes against its FIRST parent (G0/G3 rule, `ramas.comparar`).
  Summaries are immutable per commit sha and cached on disk
  (``.cache/grafo/<doc>/<sha>.v1.json``). ``?pieza=`` keeps only the steps
  where that piece was added/removed/changed and rewrites ``padres`` to the
  nearest kept ancestors (history simplification, like ``git log -- path``).
- ``GET /documentos/{id}/pasos/{sha}/miniatura.png[?pieza=]``: small PNG
  rendered server-side (z-buffer, `render.py`) from the step's geometry,
  cached by geometric revision (``.cache/miniaturas_pasos/<doc>/``). No
  token (read-only); ``sha`` must be 7–40 hex (never a revision expression).
- ``POST /documentos/{id}/piezas/{pieza}/restaurar {desde}`` 🔑: the
  per-piece cherry-pick of `fusion.fusionar` (verification, all-or-nothing,
  one-parent commit with ``Forja-Desde``) with the message
  «restaurar pieza X a <sha>».
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

import auth
import documents
import fusion
import git_store
import parametros
import ramas
import revision as revision_mod

logger = logging.getLogger(__name__)
router = APIRouter()

GRAFO_LIMITE = 80
GRAFO_MAX = 300
ESCANEO_PIEZA_MAX = 400
MINI_ANCHO, MINI_ALTO = 256, 192
_VERSION_RESUMEN = "v1"
_VERSION_MINI = "v1"
# Same colour as the viewer's sunk surface (`--fj-sunk` #14110f).
_FONDO_MINI = (0x14 / 255.0, 0x11 / 255.0, 0x0F / 255.0)

_CACHE = documents.DOCUMENTOS_DIR / ".cache"
_CACHE_GRAFO = _CACHE / "grafo"
_CACHE_MINI = _CACHE / "miniaturas_pasos"

_render_semaforo = threading.Semaphore(2)
_candados_mini: dict[str, threading.Lock] = {}
_candados_lock = threading.Lock()


def borrar_cache(doc_id: str) -> None:
    git_store.validar_doc_id(doc_id)
    for base in (_CACHE_GRAFO, _CACHE_MINI):
        shutil.rmtree(base / doc_id, ignore_errors=True)


def _escribir_atomico(destino: Path, contenido: bytes) -> None:
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        tmp = destino.with_name(destino.name + f".tmp{os.getpid()}.{threading.get_ident()}")
        tmp.write_bytes(contenido)
        os.replace(tmp, destino)
    except OSError:
        logger.warning("no se pudo guardar %s en cache", destino.name)


# ------------------------------------------------------------ resumen por paso

def _resumen_raiz(doc_id: str, sha: str) -> dict[str, Any]:
    archivos = git_store.leer(doc_id, sha)
    piezas = ramas._piezas(ramas._json(archivos, "solidos.json"))
    meta = ramas._json(archivos, "meta.json") or {}
    valores = meta.get("valores") if isinstance(meta, dict) else None
    return {"añadidas": sorted(piezas) or ["documento"], "quitadas": [], "cambiadas": [],
            "volumen_pct": None, "volumen_delta": None,
            "parametros": [{"nombre": k, "antes": None, "despues": v}
                           for k, v in sorted((valores or {}).items())][:12] if isinstance(valores, dict) else [],
            "materiales": [], "script_cambiado": False, "sin_cambios": False, "raiz": True}


def resumen_paso(doc_id: str, sha: str, padre: str | None) -> dict[str, Any]:
    """Compact change summary of step ``sha`` against ``padre`` (its first
    parent; ``None`` = root). Immutable, cached on disk by sha."""
    destino = _CACHE_GRAFO / doc_id / f"{sha}.{_VERSION_RESUMEN}.json"
    try:
        return json.loads(destino.read_bytes())
    except (OSError, ValueError):
        pass
    if padre is None:
        datos = _resumen_raiz(doc_id, sha)
    else:
        r = ramas.comparar(doc_id, padre, sha)
        por = lambda e: sorted(n for n, x in r["piezas"].items() if x == e)  # noqa: E731
        datos = {"añadidas": por("añadida"), "quitadas": por("quitada"), "cambiadas": por("cambiada"),
                 "volumen_pct": r["volumen"]["pct"], "volumen_delta": r["volumen"]["delta"],
                 "parametros": r["parametros"][:12], "materiales": [m["pieza"] for m in r["materiales"]][:24],
                 "script_cambiado": r["script_cambiado"], "sin_cambios": r["sin_cambios"], "raiz": False}
    _escribir_atomico(destino, json.dumps(datos, ensure_ascii=False).encode())
    return datos


def _toca(resumen: dict[str, Any], pieza: str) -> bool:
    return any(pieza in resumen.get(k, []) for k in ("añadidas", "quitadas", "cambiadas")) or \
        pieza in resumen.get("materiales", [])


# ------------------------------------------------------------ grafo

def grafo(doc_id: str, limite: int = GRAFO_LIMITE, pieza: str | None = None) -> dict[str, Any]:
    limite = max(1, min(int(limite), GRAFO_MAX))
    activa = git_store.rama_activa(doc_id)
    tips = git_store.listar_ramas(doc_id)
    hitos = git_store.listar_hitos(doc_id)
    cur = git_store.leer_curacion(doc_id)
    escanear = ESCANEO_PIEZA_MAX if pieza else limite
    entradas = git_store.log_grafo(doc_id, list(dict.fromkeys(tips.values())), escanear + 1)
    truncado = len(entradas) > escanear
    entradas = entradas[:escanear]
    por_sha = {e["sha"]: e for e in entradas}

    # Branches containing each loaded commit (walk parents from every tip).
    contiene: dict[str, list[str]] = {}
    orden_ramas = sorted(tips, key=lambda n: (n != git_store.RAMA_PRINCIPAL, n != activa, n))
    for nombre in orden_ramas:
        pila = [tips[nombre]]
        vistos: set[str] = set()
        while pila:
            s = pila.pop()
            if s in vistos or s not in por_sha:
                continue
            vistos.add(s)
            contiene.setdefault(s, []).append(nombre)
            pila.extend(por_sha[s]["padres"])
    hitos_por_sha: dict[str, list[str]] = {}
    for h, s in hitos.items():
        hitos_por_sha.setdefault(s, []).append(h)
    puntas: dict[str, list[str]] = {}
    for n, s in tips.items():
        puntas.setdefault(s, []).append(n)

    nodos = []
    resumenes: dict[str, dict[str, Any]] = {}
    for e in entradas:
        if not pieza and len(nodos) >= limite:
            break
        padre = e["padres"][0] if e["padres"] else None
        try:
            resumenes[e["sha"]] = resumen_paso(doc_id, e["sha"], padre)
        except Exception:  # noqa: BLE001 - one broken step must not hide the graph
            logger.exception("resumen del paso %s de %s", e["sha"][:10], doc_id)
            resumenes[e["sha"]] = {"error": True}
        tr = e.get("trailers", {})
        nodos.append({
            "sha": e["sha"], "sha_corto": e["sha"][:10], "padres": [p[:10] for p in e["padres"]],
            "ramas": contiene.get(e["sha"], []), "puntas": sorted(puntas.get(e["sha"], [])),
            "hitos": hitos_por_sha.get(e["sha"], []), "autor": e.get("autor"), "fecha": e.get("fecha"),
            "mensaje": e.get("mensaje"), "revision": e.get("revision"),
            "fusion": len(e["padres"]) > 1,
            "desde": tr.get("Desde", "")[:10] or None,
            "oculto": e["sha"] in cur["ocultos"],
            "cambios": resumenes[e["sha"]],
        })

    if pieza:
        nodos = _simplificar(nodos, lambda n: _toca(n["cambios"], pieza))[:limite]
    return {
        "activa": activa,
        "ramas": [{"nombre": n, "sha_corto": tips[n][:10], "activa": n == activa} for n in orden_ramas],
        "hitos": [{"nombre": h, "sha_corto": s[:10], "descripcion": cur["descripciones"].get(h)}
                  for h, s in hitos.items()],
        "pieza": pieza, "total": git_store.contar_varios(doc_id, list(dict.fromkeys(tips.values()))),
        "truncado": truncado or (not pieza and len(entradas) > len(nodos)), "nodos": nodos,
    }


def _simplificar(nodos: list[dict[str, Any]], conservar) -> list[dict[str, Any]]:
    """Keep the nodes where ``conservar`` is true and point each one's
    ``padres`` at its nearest kept ancestors (deduplicated, order kept)."""
    por = {n["sha_corto"]: n for n in nodos}
    memo: dict[str, list[str]] = {}

    def cercanos(sha: str, profundidad: int = 0) -> list[str]:
        if sha in memo:
            return memo[sha]
        nodo = por.get(sha)
        if nodo is None or profundidad > 2000:
            return []
        if conservar(nodo):
            memo[sha] = [sha]
            return memo[sha]
        salida: list[str] = []
        for p in nodo["padres"]:
            for c in cercanos(p, profundidad + 1):
                if c not in salida:
                    salida.append(c)
        memo[sha] = salida
        return salida

    # Iterate oldest first so memo fills bottom-up (no deep recursion).
    for n in reversed(nodos):
        cercanos(n["sha_corto"])
    kept = []
    for n in nodos:
        if not conservar(n):
            continue
        padres: list[str] = []
        for p in n["padres"]:
            for c in cercanos(p):
                if c not in padres:
                    padres.append(c)
        kept.append({**n, "padres": padres})
    return kept


# ------------------------------------------------------------ miniaturas

def _candado(clave: str) -> threading.Lock:
    with _candados_lock:
        if len(_candados_mini) > 256:
            _candados_mini.clear()
        return _candados_mini.setdefault(clave, threading.Lock())


def miniatura(doc_id: str, sha_ref: str, pieza: str | None = None) -> bytes:
    if not git_store._RE_SHA_CORTO.match(sha_ref or ""):
        raise ValueError("sha no valido (7 a 40 hex)")
    sha = git_store.resolver(doc_id, sha_ref)
    archivos = git_store.leer(doc_id, sha)
    geo = next((n for n in archivos if n.lower().endswith((".step", ".stp", ".stl"))), None)
    if geo is None:
        raise LookupError("el paso no tiene geometria")
    es_step = not geo.lower().endswith(".stl")
    rev = revision_mod.calcular(archivos[geo], es_step, [("solidos.json", archivos.get("solidos.json"))])
    sufijo = "" if not pieza else "." + hashlib.sha1(pieza.encode()).hexdigest()[:12]
    destino = _CACHE_MINI / doc_id / f"{rev}{sufijo}.{_VERSION_MINI}.png"
    if destino.is_file():
        return destino.read_bytes()
    with _candado(str(destino)):
        if destino.is_file():
            return destino.read_bytes()
        with _render_semaforo:
            png = _renderizar(archivos, geo, es_step, pieza)
        _escribir_atomico(destino, png)
    return png


def _renderizar(archivos: dict[str, bytes], geo: str, es_step: bool, pieza: str | None) -> bytes:
    import render
    from kernel import b123d_kernel, mesh
    with tempfile.TemporaryDirectory(prefix="forja-mini-") as tmp:
        camino = Path(tmp) / geo
        camino.write_bytes(archivos[geo])
        if es_step:
            forma = b123d_kernel.import_from_step(camino)
            entradas = ramas._json(archivos, "solidos.json")
            if isinstance(entradas, list) and entradas:
                if pieza and pieza not in {e.get("nombre") for e in entradas if isinstance(e, dict)}:
                    raise LookupError(f"la pieza {pieza!r} no existe en ese paso")
                info = b123d_kernel.analyze(forma)
                bb = list(info.bbox)
                diag = sum((bb[i + 3] - bb[i]) ** 2 for i in range(3)) ** 0.5 if len(bb) == 6 else 100.0
                tol = max(render.TOLERANCIA_RENDER_MM, diag / 250.0)
                grupos, colores, _ = render.grupos_desde_shape(forma, entradas, [pieza] if pieza else None, tol)
            else:
                if pieza:
                    raise LookupError("el paso no tiene piezas con nombre")
                tri = mesh.tessellate_to_trimesh(forma, render.TOLERANCIA_RENDER_MM)
                grupos, colores, _ = render.grupo_desde_malla(tri)
        else:
            if pieza:
                raise LookupError("un documento STL no tiene piezas con nombre")
            grupos, colores, _ = render.grupo_desde_malla(mesh.load_stl(camino))
    png, _ = render.renderizar_png(grupos, colores, ancho=MINI_ANCHO, alto=MINI_ALTO,
                                   leyenda_visible=False, fondo=_FONDO_MINI)
    return png


# ------------------------------------------------------------ restaurar pieza

def restaurar_pieza(doc_id: str, pieza: str, desde: str, forzar: bool = False,
                    simular: bool = False) -> dict[str, Any]:
    """Put ONE named piece back as it was in step ``desde`` (or remove it
    if it did not exist there). Caller holds `parametros.bloqueo(doc_id)`."""
    if not git_store._RE_SHA_CORTO.match(desde or ""):
        raise fusion.FusionInvalida("desde: sha de un paso (7 a 40 hex)")
    ramas.asegurar(doc_id)
    sha = git_store.resolver(doc_id, desde)
    tip = git_store.tip_rama(doc_id, git_store.rama_activa(doc_id))
    if tip:
        estado = ramas.comparar(doc_id, tip, sha)["piezas"].get(pieza)
        if estado == "igual":
            return {"resultado": "sin_cambios", "confirmada": False, "pieza": pieza, "desde": sha[:10],
                    "rama": git_store.rama_activa(doc_id), "cambios": [], "conflictos": [], "avisos": [],
                    "verificacion": None, "modo": "piezas"}
    resultado = fusion.fusionar(doc_id, sha, [pieza], None, forzar, simular,
                                mensaje=f"restaurar pieza {pieza} a {sha[:10]}")
    resultado["cambios"] = [c.replace("de la otra rama", f"del paso {sha[:10]}").replace(
        "no existe en la otra rama", f"no existia en el paso {sha[:10]}") for c in resultado.get("cambios", [])]
    return {**resultado, "pieza": pieza}


def restaurar_paso(doc_id: str, desde: str) -> dict[str, Any]:
    """Put the WHOLE document back to step ``desde`` as a new step on the
    active branch (one parent, ``Forja-Desde``; like ``git checkout <sha>
    -- . && git commit``). Same all-or-nothing write as a merge. Caller
    holds `parametros.bloqueo(doc_id)`."""
    if not git_store._RE_SHA_CORTO.match(desde or ""):
        raise fusion.FusionInvalida("desde: sha de un paso (7 a 40 hex)")
    ruta = documents._files.get(doc_id)
    if ruta is None or not ruta.exists():
        raise LookupError("archivo del documento no encontrado")
    ramas.asegurar(doc_id)
    sha = git_store.resolver(doc_id, desde)
    archivos = git_store.leer(doc_id, sha)
    if ruta.name not in archivos:
        raise fusion.FusionInvalida("ese paso no contiene la geometria de este documento")
    activa = git_store.rama_activa(doc_id)
    tip = ramas.registrar(doc_id, "estado sin registrar", solo_si_cambia=True)
    if git_store.arbol_de(doc_id, tip) == git_store.arbol_de(doc_id, sha):
        return {"resultado": "sin_cambios", "confirmada": False, "rama": activa, "desde": sha[:10]}
    plan = {"archivos": archivos, "avisos": []}
    datos = fusion._confirmar(doc_id, ruta, activa, tip, sha, sha, plan, [],
                              f"restaurar todo el documento a {sha[:10]}")
    return {**datos, "resultado": "restaurado", "confirmada": True, "rama": activa, "desde": sha[:10]}


# ------------------------------------------------------------ REST

def _asegurar_get(doc_id: str) -> None:
    ramas._doc_o_404(doc_id)
    if not git_store.listar_ramas(doc_id):
        ramas._bajo_candado(doc_id, ramas.asegurar)


@router.get("/documentos/{doc_id}/grafo")
def obtener_grafo(doc_id: str, limite: int = Query(default=GRAFO_LIMITE, ge=1, le=GRAFO_MAX),
                  pieza: str | None = Query(default=None, min_length=1, max_length=128)) -> dict[str, Any]:
    """Commit graph of every branch: ``{activa, ramas, hitos, pieza, total,
    truncado, nodos: [{sha_corto, padres, ramas, puntas, hitos, autor,
    fecha, mensaje, revision, fusion, desde, oculto, cambios}]}``."""
    _asegurar_get(doc_id)
    return ramas._traducir(grafo, doc_id, limite, pieza)


@router.get("/documentos/{doc_id}/pasos/{sha}/miniatura.png")
def obtener_miniatura(doc_id: str, sha: str,
                      pieza: str | None = Query(default=None, min_length=1, max_length=128)) -> Response:
    ramas._doc_o_404(doc_id)
    if not git_store._RE_SHA_CORTO.match(sha):
        raise HTTPException(status_code=400, detail="sha no valido (7 a 40 hex)")
    try:
        png = ramas._traducir(miniatura, doc_id, sha, pieza)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - kernel/render failure
        logger.exception("miniatura %s de %s", sha, doc_id)
        raise HTTPException(status_code=422, detail="no se pudo renderizar la miniatura") from exc
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "private, max-age=86400"})


class _RestaurarPiezaBody(BaseModel):
    desde: str = Field(..., min_length=7, max_length=40)
    forzar: bool = False
    simular: bool = False


@router.post("/documentos/{doc_id}/piezas/{pieza}/restaurar", dependencies=[Depends(auth.requiere_token)])
def restaurar_pieza_ruta(doc_id: str, pieza: str, body: _RestaurarPiezaBody) -> dict[str, Any]:
    """Restore piece ``pieza`` to step ``desde``: same engine, checks and
    response as «traer pieza» (+ ``resultado: sin_cambios``)."""
    if not pieza or len(pieza) > 128:
        raise HTTPException(status_code=400, detail="nombre de pieza no valido")
    ramas._doc_o_404(doc_id)
    with parametros.bloqueo(doc_id):
        ramas._doc_o_404(doc_id)
        return fusion._traducir(restaurar_pieza, doc_id, pieza, body.desde, body.forzar, body.simular)


@router.post("/documentos/{doc_id}/pasos/{sha}/restaurar", dependencies=[Depends(auth.requiere_token)])
def restaurar_paso_ruta(doc_id: str, sha: str) -> dict[str, Any]:
    """Restore the whole document to step ``sha`` as a new step of the
    active branch (nothing is lost: the previous state stays a step and a
    G1 snapshot). ``resultado``: restaurado | sin_cambios."""
    ramas._doc_o_404(doc_id)
    if not git_store._RE_SHA_CORTO.match(sha):
        raise HTTPException(status_code=400, detail="sha no valido (7 a 40 hex)")
    with parametros.bloqueo(doc_id):
        ramas._doc_o_404(doc_id)
        return fusion._traducir(restaurar_paso, doc_id, sha)
