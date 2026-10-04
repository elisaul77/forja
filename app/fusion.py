"""Verified merge, per-piece cherry-pick (Plan G · G4) and milestones (G5).

Model (ADR-0015):

- ``fusionar(doc, desde)`` merges the state ``desde`` (branch or step) into
  the ACTIVE branch with its common ancestor (``git merge-base``):
  parameters per key, materials per piece, notes/strokes per id, assembly
  per joint, script text with ``git merge-file`` (3-way). Both sides
  changed the same key differently -> conflict (nothing written) unless
  ``estrategia`` is ``nuestra``/``suya``.
- Geometry is DERIVED: a scripted document is rebuilt in the sandbox with
  the merged script + parameters (or taken from a side when the merged
  sources equal that side's); a document without script merges its named
  pieces 3-way (piece signature = volume/bbox/solid count, the G3 rule).
- ``fusionar(doc, desde, piezas=[...])`` is the cherry-pick: those named
  pieces (and their materials) come from ``desde``, the rest stays. A
  scripted document is then marked ``geometria_editada`` in its meta (the
  script alone no longer produces it; regenerating needs confirmation).
- Mandatory verification before confirming: validity + `check_colisiones`
  on the result against BOTH parents; a collision that neither parent had
  (or an invalid solid) -> ``conflicto_geometrico``, not confirmed unless
  ``forzar``.
- A confirmed merge is all-or-nothing like a branch switch (same
  `ramas._materializar` + rollback) and ends as a commit with two parents
  (cherry-pick: one parent, ``Forja-Desde`` trailer — it does not include
  the whole other branch). ``simular`` computes everything (including the
  rebuild and checks) and writes nothing.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

import assemblies
import auth
import documents
import eventos
import git_store
import materiales
import parametros
import ramas
import scripts_runner
import solids
import versioning
from checks import colisiones as colisiones_checks

logger = logging.getLogger(__name__)
router = APIRouter()

ESTRATEGIAS = (None, "auto", "nuestra", "suya")
_FALTA = object()
MAX_PIEZAS = 64
MAX_LINEAS_CONFLICTO = 60


class FusionInvalida(ValueError):
    """Request that cannot be planned (unknown piece, no geometry...)."""


# ------------------------------------------------------------ 3 vías

def _json(datos: bytes | None) -> Any:
    if datos is None:
        return None
    try:
        return json.loads(datos)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _tres(o: Any, a: Any, b: Any) -> tuple[Any, bool]:
    """``(valor, conflicto)``: a side that did not change yields the other."""
    if a == b:
        return a, False
    if a == o:
        return b, False
    if b == o:
        return a, False
    return a, True


class _Conflictos:
    def __init__(self, estrategia: str | None):
        self.estrategia = None if estrategia == "auto" else estrategia
        self.lista: list[dict[str, Any]] = []

    def resolver(self, tipo: str, clave: str, o: Any, a: Any, b: Any) -> Any:
        valor, choque = _tres(o, a, b)
        if not choque:
            return valor
        if self.estrategia == "nuestra":
            return a
        if self.estrategia == "suya":
            return b
        limpio = (lambda v: None if v is _FALTA else v)
        self.lista.append({"tipo": tipo, "clave": clave, "base": limpio(o), "a": limpio(a), "b": limpio(b),
                           "mensaje": _mensaje_conflicto(tipo, clave, o, a, b)})
        return a


def _mensaje_conflicto(tipo: str, clave: str, o: Any, a: Any, b: Any) -> str:
    def _d(v: Any) -> str:
        return "(no existe)" if v is _FALTA or v is None else json.dumps(v, ensure_ascii=False)[:80]
    nombres = {"parametro": "el parametro", "material": "el material de la pieza", "nota": "la nota",
               "trazo": "el trazo", "articulacion": "la articulacion", "variables": "las variables",
               "timeout": "el tiempo maximo", "declarado": "los materiales declarados",
               "ensamble": "el ensamble", "pieza": "la pieza", "script_origen": "el origen del script"}
    return (f"{nombres.get(tipo, tipo)} «{clave}» cambio en las dos ramas: activa {_d(a)}, "
            f"fusionada {_d(b)} (ancestro {_d(o)})")


def _fusionar_dict(c: _Conflictos, tipo: str, o: dict, a: dict, b: dict) -> dict:
    claves = list(a) + [k for k in b if k not in a] + [k for k in o if k not in a and k not in b]
    salida = {}
    for k in claves:
        v = c.resolver(tipo, str(k), o.get(k, _FALTA), a.get(k, _FALTA), b.get(k, _FALTA))
        if v is not _FALTA:
            salida[k] = v
    return salida


def _por_id(items: Any) -> dict[str, Any]:
    """Notes/strokes are keyed by ``n``, joints by ``id``."""
    if not isinstance(items, list):
        return {}
    salida = {}
    for i in items:
        if isinstance(i, dict):
            clave = i.get("n", i.get("id"))
            if clave is not None:
                salida[str(clave)] = i
    return salida


def _fusionar_notas(c: _Conflictos, o: bytes | None, a: bytes | None, b: bytes | None) -> bytes | None:
    if a == b:
        return a
    if a == o:
        return b
    if b == o:
        return a
    do, da, db = (_json(x) or {} for x in (o, a, b))
    salida = dict(da) if isinstance(da, dict) else {}
    for coleccion, tipo in (("notas", "nota"), ("trazos", "trazo")):
        fusion = _fusionar_dict(c, tipo, _por_id(do.get(coleccion)), _por_id(da.get(coleccion)),
                                _por_id(db.get(coleccion)))
        salida[coleccion] = list(fusion.values())
    return json.dumps(salida, ensure_ascii=False).encode()


def _fusionar_materiales(c: _Conflictos, o: bytes | None, a: bytes | None, b: bytes | None) -> bytes | None:
    if a == b:
        return a
    if a == o:
        return b
    if b == o:
        return a
    def _m(x: bytes | None) -> tuple[dict, Any]:
        d = _json(x)
        if not isinstance(d, dict):
            return {}, None
        return (d.get("materiales") if isinstance(d.get("materiales"), dict) else {}), d.get("declarado")
    (mo, deco), (ma, deca), (mb, decb) = _m(o), _m(a), _m(b)
    mats = _fusionar_dict(c, "material", mo, ma, mb)
    declarado = c.resolver("declarado", "MATERIALES", deco, deca, decb)
    return json.dumps({"materiales": mats, "declarado": declarado}, ensure_ascii=False).encode()


def _fusionar_ensamble(c: _Conflictos, o: dict[str, bytes], a: dict[str, bytes],
                       b: dict[str, bytes]) -> dict[str, bytes]:
    claves = (assemblies.STATE_KEY, assemblies.BASE_KEY)
    eo, ea, eb = ({k: x[k] for k in claves if k in x} for x in (o, a, b))
    if ea == eb:
        return ea
    if ea == eo:
        return eb
    if eb == eo:
        return ea
    sa, sb = _json(ea.get(assemblies.STATE_KEY)), _json(eb.get(assemblies.STATE_KEY))
    so = _json(eo.get(assemblies.STATE_KEY)) or {}
    if not (isinstance(sa, dict) and isinstance(sb, dict)) or sa.get("base_sha256") != sb.get("base_sha256"):
        c.resolver("ensamble", "pieza de reposo", eo.get(assemblies.STATE_KEY), "rama activa", "rama fusionada")
        return ea
    estado = dict(sa)
    arts = _fusionar_dict(c, "articulacion", _por_id(so.get("articulaciones")),
                          _por_id(sa.get("articulaciones")), _por_id(sb.get("articulaciones")))
    estado["articulaciones"] = list(arts.values())
    estado["valores"] = _fusionar_dict(c, "articulacion", so.get("valores") or {}, sa.get("valores") or {},
                                       sb.get("valores") or {})
    return {assemblies.STATE_KEY: json.dumps(estado).encode(), assemblies.BASE_KEY: ea[assemblies.BASE_KEY]}


# ------------------------------------------------------------ geometría

def _geo(archivos: dict[str, bytes]) -> str | None:
    return next((n for n in archivos if n.lower().endswith((".step", ".stp", ".stl"))), None)


def _grupos(step: bytes, solidos_json: bytes | None) -> dict[str, list[Any]]:
    """Named pieces of a stored STEP: ``{nombre: [Solid]}`` (stored order)."""
    from kernel import b123d_kernel
    entradas = _json(solidos_json)
    if not isinstance(entradas, list) or not entradas:
        raise FusionInvalida("el estado no tiene piezas con nombre (solidos.json)")
    with tempfile.NamedTemporaryFile(suffix=".step") as tmp:
        tmp.write(step)
        tmp.flush()
        forma = b123d_kernel.import_from_step(Path(tmp.name))
    grupos, inciertos = solids.solidos_por_indice(forma, entradas)
    if inciertos:
        raise FusionInvalida("no se pudieron casar los nombres de las piezas con la geometria")
    salida: dict[str, list[Any]] = {}
    for nombre, _i, solido, _bb, _v in grupos:
        salida.setdefault(nombre, []).append(solido)
    return salida


def _componer(lados: dict[str, dict[str, bytes]], plan: list[tuple[str, str]], geo: str) -> tuple[bytes, bytes]:
    """New STEP + ``solidos.json`` with each piece of ``plan`` (``[(nombre,
    lado)]``, in order) taken from that side's geometry."""
    from build123d import Compound
    from kernel import b123d_kernel
    if not plan:
        raise FusionInvalida("el resultado no tendria ninguna pieza")
    cache: dict[str, dict[str, list[Any]]] = {}
    nombrados: dict[str, Any] = {}
    for nombre, lado in plan:
        if lado not in cache:
            cache[lado] = _grupos(lados[lado][geo], lados[lado].get("solidos.json"))
        solidos_pieza = cache[lado][nombre]
        nombrados[nombre] = solidos_pieza[0] if len(solidos_pieza) == 1 else Compound(children=solidos_pieza)
    with tempfile.TemporaryDirectory(prefix="forja-fusion-") as tmp:
        destino = Path(tmp) / "fusion.step"
        b123d_kernel.export_to_step(b123d_kernel.combinar_nombrados(nombrados), destino)
        forma = b123d_kernel.import_from_step(destino)
        entradas = solids.construir_entradas(forma, list(nombrados))
        step = destino.read_bytes()
    return step, json.dumps(entradas).encode()


def _firmas(archivos: dict[str, bytes]) -> dict[str, dict[str, Any]]:
    return ramas._piezas(_json(archivos.get("solidos.json")))


def _misma(x: dict[str, Any] | None, y: dict[str, Any] | None) -> bool:
    if x is None or y is None:
        return x is None and y is None
    return ramas._equivalentes(x, y)


def _plan_por_piezas(c: _Conflictos, o: dict[str, bytes], a: dict[str, bytes],
                     b: dict[str, bytes]) -> list[tuple[str, str]]:
    """Per-piece 3-way plan for documents without script."""
    fo, fa, fb = _firmas(o), _firmas(a), _firmas(b)
    if not fa or not fb:
        c.resolver("geometria", "documento", None, "rama activa", "rama fusionada")
        return []
    plan: list[tuple[str, str]] = []
    for nombre in list(fa) + [n for n in fb if n not in fa]:
        pa, pb, po = fa.get(nombre), fb.get(nombre), fo.get(nombre)
        if _misma(pa, pb):
            lado = "a" if pa is not None else None
        elif _misma(pa, po):
            lado = "b" if pb is not None else None
        elif _misma(pb, po):
            lado = "a" if pa is not None else None
        elif c.estrategia == "nuestra":
            lado = "a" if pa is not None else None
        elif c.estrategia == "suya":
            lado = "b" if pb is not None else None
        else:
            c.lista.append({"tipo": "pieza", "clave": nombre, "base": po is not None, "a": pa is not None,
                            "b": pb is not None,
                            "mensaje": f"la pieza «{nombre}» cambio de forma distinta en las dos ramas"})
            lado = "a" if pa is not None else None
        if lado:
            plan.append((nombre, lado))
    return plan


def _reconstruir(codigo: str, variables: dict | None, valores: dict, timeout: float | None) -> tuple[bytes, bytes]:
    """Run the merged script in the sandbox (same path as `parametros`)."""
    from kernel import b123d_kernel
    salida, nombres = scripts_runner.ejecutar_script(
        codigo, timeout=timeout or scripts_runner.DEFAULT_TIMEOUT,
        variables=parametros.variables_efectivas(variables, valores))
    try:
        forma = b123d_kernel.import_from_step(salida)
        if b123d_kernel.analyze(forma).solidos == 0:
            raise FusionInvalida("el script fusionado produjo una figura sin solidos")
        entradas = solids.construir_entradas(forma, nombres)
        return salida.read_bytes(), json.dumps(entradas).encode()
    finally:
        shutil.rmtree(salida.parent, ignore_errors=True)


# ------------------------------------------------------------ verificación

_cache_checks: dict[str, dict[str, Any]] = {}


def _revisar(archivos: dict[str, bytes]) -> dict[str, Any] | None:
    """``{valido, choques: [(a, b)]}`` of a state's geometry (cached by
    content), ``None`` when it cannot be checked (STL, no named pieces)."""
    geo = _geo(archivos)
    if geo is None or geo.lower().endswith(".stl"):
        return None
    entradas = _json(archivos.get("solidos.json"))
    if not isinstance(entradas, list) or not entradas:
        return None
    clave = hashlib.sha256(archivos[geo] + b"\0" + archivos["solidos.json"]).hexdigest()
    if clave in _cache_checks:
        return _cache_checks[clave]
    from kernel import b123d_kernel
    with tempfile.NamedTemporaryFile(suffix=".step") as tmp:
        tmp.write(archivos[geo])
        tmp.flush()
        forma = b123d_kernel.import_from_step(Path(tmp.name))
    valido = bool(b123d_kernel.analyze(forma).valido)
    try:
        informe = colisiones_checks.verificar(forma, entradas)
        choques = sorted({tuple(sorted((x["a"], x["b"]))) for x in informe.get("choques", [])})
        inciertos = bool(informe.get("nombres_inciertos"))
    except ValueError:
        choques, inciertos = [], True
    resultado = {"valido": valido, "choques": choques, "nombres_inciertos": inciertos}
    if len(_cache_checks) > 32:
        _cache_checks.clear()
    _cache_checks[clave] = resultado
    return resultado


def _verificar(resultado: dict[str, bytes], a: dict[str, bytes], b: dict[str, bytes]) -> dict[str, Any]:
    r, ra, rb = _revisar(resultado), _revisar(a), _revisar(b)
    if r is None:
        return {"comprobada": False, "valido": None, "choques": [], "choques_nuevos": [], "ok": True}
    previos = set(map(tuple, (ra or {}).get("choques", []))) | set(map(tuple, (rb or {}).get("choques", [])))
    nuevos = [list(p) for p in r["choques"] if tuple(p) not in previos]
    padres_validos = (ra is None or ra["valido"]) and (rb is None or rb["valido"])
    invalido_nuevo = not r["valido"] and padres_validos
    return {"comprobada": True, "valido": r["valido"], "choques": [list(p) for p in r["choques"]],
            "choques_nuevos": nuevos, "invalido_nuevo": invalido_nuevo,
            "padres": {"a": len((ra or {}).get("choques", [])), "b": len((rb or {}).get("choques", []))},
            "ok": not nuevos and not invalido_nuevo}


# ------------------------------------------------------------ plan

def _texto_script(fuente: dict[str, bytes]) -> bytes | None:
    return fuente.get("script.py")


def _lineas_conflicto(texto: bytes) -> list[str]:
    lineas = texto.decode(errors="replace").splitlines()
    salida: list[str] = []
    dentro = False
    for n, linea in enumerate(lineas, 1):
        if linea.startswith("<<<<<<< "):
            dentro = True
        if dentro:
            salida.append(f"{n}: {linea}")
        if linea.startswith(">>>>>>> "):
            dentro = False
        if len(salida) >= MAX_LINEAS_CONFLICTO:
            salida.append("…")
            break
    return salida


def planificar(doc_id: str, archivos_a: dict[str, bytes], fuente_a: dict[str, bytes],
               tip_a: str, sha_b: str, piezas: list[str] | None,
               estrategia: str | None) -> dict[str, Any]:
    """Pure computation of the merged state; never writes the document.
    Returns ``{archivos, conflictos, cambios, avisos, base, verificacion}``."""
    c = _Conflictos(estrategia)
    a = archivos_a
    b = git_store.leer(doc_id, sha_b)
    fuente_b = git_store.leer_fuente(doc_id, sha_b)
    base = git_store.base_comun(doc_id, tip_a, sha_b)
    o = git_store.leer(doc_id, base) if base else {}
    fuente_o = git_store.leer_fuente(doc_id, base) if base else {}
    geo = _geo(a)
    if geo is None:
        raise FusionInvalida("el documento no tiene geometria")
    if geo not in b:
        raise FusionInvalida("el estado a fusionar no contiene la geometria de este documento")
    meta_a = _json(a.get("meta.json")) or {}
    meta_b = _json(b.get("meta.json")) or {}
    meta_o = _json(o.get("meta.json")) or {}
    resultado: dict[str, bytes] = {}
    cambios: list[str] = []
    avisos: list[str] = []
    lados = {"a": a, "b": b}

    if piezas is not None:  # ---------------- cherry-pick por pieza
        if geo.lower().endswith(".stl"):
            raise FusionInvalida("traer piezas solo funciona con documentos STEP con piezas con nombre")
        fa, fb = _firmas(a), _firmas(b)
        if not fa or not fb:
            raise FusionInvalida("traer piezas necesita piezas con nombre en las dos ramas (solidos.json)")
        desconocidas = [p for p in piezas if p not in fa and p not in fb]
        if desconocidas:
            raise FusionInvalida(f"piezas que no existen en ninguna de las dos ramas: {desconocidas[:10]}")
        plan: list[tuple[str, str]] = []
        for n in fa:
            if n in piezas:
                if n in fb:
                    plan.append((n, "b"))
                    cambios.append(f"pieza {n}: sustituida por la de la otra rama")
                else:
                    cambios.append(f"pieza {n}: quitada (no existe en la otra rama)")
            else:
                plan.append((n, "a"))
        for n in fb:
            if n in piezas and n not in fa:
                plan.append((n, "b"))
                cambios.append(f"pieza {n}: añadida desde la otra rama")
        resultado = {k: v for k, v in a.items()}
        resultado[geo], resultado["solidos.json"] = _componer(lados, plan, geo)
        # materiales of those pieces come along too
        da, db = _json(a.get(materiales.NOMBRE_SNAPSHOT)), _json(b.get(materiales.NOMBRE_SNAPSHOT))
        ma = dict(da.get("materiales") or {}) if isinstance(da, dict) else {}
        mb = (db.get("materiales") or {}) if isinstance(db, dict) else {}
        for n in piezas:
            if n in mb and n in fb:
                ma[n] = mb[n]
            else:
                ma.pop(n, None)
        if ma or materiales.NOMBRE_SNAPSHOT in a:
            resultado[materiales.NOMBRE_SNAPSHOT] = json.dumps(
                {"materiales": ma, "declarado": da.get("declarado") if isinstance(da, dict) else None},
                ensure_ascii=False).encode()
        if isinstance(meta_a.get("script"), dict):
            previas = (meta_a.get("geometria_editada") or {}).get("piezas") or []
            meta_n = dict(meta_a)
            meta_n["geometria_editada"] = {"desde": sha_b[:10],
                                           "piezas": sorted(set(previas) | set(piezas))[:MAX_PIEZAS]}
            resultado["meta.json"] = json.dumps(meta_n).encode()
            avisos.append("geometria_editada: el documento tiene script; la geometria resultante ya no sale "
                          "del script tal cual (las piezas traidas no estan en su codigo). Regenerar con "
                          "parametros pedira confirmacion y las sustituiria por lo que produce el script.")
        verif = _verificar(resultado, a, b)
        return {"archivos": resultado, "fuente": dict(fuente_a), "conflictos": c.lista, "cambios": cambios,
                "avisos": avisos, "base": base, "verificacion": verif, "modo": "piezas"}

    # -------------------------------------------- fusión completa
    resultado["notas.json"] = _fusionar_notas(c, o.get("notas.json"), a.get("notas.json"), b.get("notas.json"))
    resultado.update(_fusionar_ensamble(c, o, a, b))
    mats = _fusionar_materiales(c, o.get(materiales.NOMBRE_SNAPSHOT), a.get(materiales.NOMBRE_SNAPSHOT),
                                b.get(materiales.NOMBRE_SNAPSHOT))
    if mats is not None:
        resultado[materiales.NOMBRE_SNAPSHOT] = mats

    ta, tb, to = _texto_script(fuente_a), _texto_script(fuente_b), _texto_script(fuente_o)
    script_a, script_b = meta_a.get("script"), meta_b.get("script")
    fuente_res: dict[str, bytes] = {}
    meta_res = dict(meta_a)
    lado_geo: str | None = None
    if isinstance(script_a, dict) and isinstance(script_b, dict) and ta is not None and tb is not None:
        # texto del script, 3 vías
        if ta == tb or tb == to:
            texto = ta
        elif ta == to:
            texto = tb
        else:
            texto, n = git_store.fusionar_texto(to or b"", ta, tb, c.estrategia)
            if n:
                c.lista.append({"tipo": "script", "clave": "script.py", "conflictos": n,
                                "lineas": _lineas_conflicto(texto),
                                "mensaje": f"el script tiene {n} conflicto(s) de texto entre las dos ramas; "
                                           "resuelvelo en el codigo (o usa estrategia nuestra/suya)"})
            else:
                cambios.append("script: fusionado linea a linea (git merge-file)")
        valores = _fusionar_dict(c, "parametro", meta_o.get("valores") or {}, meta_a.get("valores") or {},
                                 meta_b.get("valores") or {})
        variables = c.resolver("variables", "variables", meta_o.get("variables") or {},
                               meta_a.get("variables") or {}, meta_b.get("variables") or {})
        timeout = c.resolver("timeout", "timeout", meta_o.get("timeout"), meta_a.get("timeout"),
                             meta_b.get("timeout"))
        texto_str = texto.decode(errors="replace")
        esquema = parametros.esquema_desde_codigo(texto_str)
        if esquema:
            try:
                valores = parametros.aplicar_valores(esquema, {}, {k: v for k, v in valores.items() if k in esquema})
            except parametros.ParametrosInvalidos as exc:
                c.lista.append({"tipo": "parametro", "clave": "*", "mensaje": f"valores fusionados no validos: {exc}"})
        else:
            valores = {}
        if "ruta" in script_a and texto != ta:
            meta_res["script"] = {"codigo": texto_str}
            avisos.append("script_a_codigo: el script fusionado ya no es el archivo de la ruta "
                          f"{script_a['ruta']}; Forja no escribe fuera de sus datos, asi que el documento "
                          "guarda ahora el texto fusionado como codigo.")
        elif "codigo" in script_a:
            meta_res["script"] = {"codigo": texto_str}
        meta_res.update({"parametros": esquema, "valores": valores, "variables": variables})
        if timeout:
            meta_res["timeout"] = timeout
        else:
            meta_res.pop("timeout", None)
        fuente_res["script.py"] = texto
        for k, v in valores.items():
            if (meta_a.get("valores") or {}).get(k) != v:
                cambios.append(f"parametro {k}: {(meta_a.get('valores') or {}).get(k)} -> {v}")
        firma = (texto, valores, variables or {})
        if firma == (ta, meta_a.get("valores") or {}, meta_a.get("variables") or {}):
            lado_geo = "a"
        elif firma == (tb, meta_b.get("valores") or {}, meta_b.get("variables") or {}):
            lado_geo = "b"
        if lado_geo:
            lado = lados[lado_geo]
            resultado[geo] = lado[geo]
            if "solidos.json" in lado:
                resultado["solidos.json"] = lado["solidos.json"]
            marca = (_json(lado.get("meta.json")) or {}).get("geometria_editada")
            if marca:
                meta_res["geometria_editada"] = marca
            else:
                meta_res.pop("geometria_editada", None)
        elif not c.lista:
            if meta_a.get("geometria_editada") or meta_b.get("geometria_editada"):
                avisos.append("geometria_editada: una rama tenia piezas traidas de otra; la reconstruccion "
                              "con el script fusionado las sustituye por lo que produce el script.")
            meta_res.pop("geometria_editada", None)
            try:
                resultado[geo], resultado["solidos.json"] = _reconstruir(texto_str, variables, valores, timeout)
            except (scripts_runner.ScriptError, scripts_runner.ScriptTimeoutError) as exc:
                c.lista.append({"tipo": "construccion", "clave": "script.py",
                                "mensaje": f"el script fusionado no construye: {exc}"})
            cambios.append("geometria: reconstruida con el script y los parametros fusionados")
    else:
        # sin script en alguna rama: el origen del script se toma entero y
        # la geometria se fusiona pieza a pieza
        claves_script = parametros._CLAVES_ESTADO
        estado = c.resolver("script_origen", "script",
                            {k: meta_o.get(k) for k in claves_script if k in meta_o},
                            {k: meta_a.get(k) for k in claves_script if k in meta_a},
                            {k: meta_b.get(k) for k in claves_script if k in meta_b})
        for k in claves_script:
            meta_res.pop(k, None)
        meta_res.update(estado if isinstance(estado, dict) else {})
        if estado == {k: meta_b.get(k) for k in claves_script if k in meta_b} and tb is not None:
            fuente_res["script.py"] = tb
        elif ta is not None and estado:
            fuente_res["script.py"] = ta
        ga, gb, go = ((x.get(geo), x.get("solidos.json")) for x in (a, b, o))
        if ga == gb or gb == go:
            lado_geo = "a"
        elif ga == go:
            lado_geo = "b"
        if lado_geo:
            resultado[geo] = lados[lado_geo][geo]
            if "solidos.json" in lados[lado_geo]:
                resultado["solidos.json"] = lados[lado_geo]["solidos.json"]
        elif geo.lower().endswith(".stl"):
            c.resolver("geometria", "documento", None, "rama activa", "rama fusionada")
        else:
            plan = _plan_por_piezas(c, o, a, b)
            if not c.lista:
                resultado[geo], resultado["solidos.json"] = _componer(lados, plan, geo)
                cambios.append("geometria: piezas fusionadas una a una")
    meta_res["nombre"] = meta_a.get("nombre", meta_res.get("nombre"))
    if meta_a or meta_res:
        resultado["meta.json"] = json.dumps(meta_res).encode()
    resultado = {k: v for k, v in resultado.items() if v is not None}
    if geo not in resultado:  # conflict before the geometry was decided
        resultado[geo] = a[geo]
        if "solidos.json" in a:
            resultado["solidos.json"] = a["solidos.json"]
    if materiales.NOMBRE_SNAPSHOT in resultado and "solidos.json" in resultado:
        # materials only for pieces that exist in the result
        datos = _json(resultado[materiales.NOMBRE_SNAPSHOT]) or {}
        nombres = set(_firmas(resultado))
        m = {k: v for k, v in (datos.get("materiales") or {}).items() if k in nombres}
        if m != (datos.get("materiales") or {}):
            datos["materiales"] = m
            resultado[materiales.NOMBRE_SNAPSHOT] = json.dumps(datos, ensure_ascii=False).encode()
    if (_json(resultado.get("notas.json")) or {}) != (_json(a.get("notas.json")) or {}):
        cambios.append("notas/trazos: unidas por id")
    if resultado.get(materiales.NOMBRE_SNAPSHOT) != a.get(materiales.NOMBRE_SNAPSHOT):
        cambios.append("materiales: fusionados por pieza")
    if resultado.get(assemblies.STATE_KEY) != a.get(assemblies.STATE_KEY):
        cambios.append("ensamble: fusionado por articulacion")
    verif = _verificar(resultado, a, b) if not c.lista else None
    return {"archivos": resultado, "fuente": fuente_res, "conflictos": c.lista, "cambios": cambios,
            "avisos": avisos, "base": base, "verificacion": verif, "modo": "completa"}


# ------------------------------------------------------------ fusionar

def _nombre_ref(referencia: str) -> str:
    return referencia if not git_store._RE_SHA_CORTO.match(referencia) else referencia[:10]


def fusionar(doc_id: str, desde: str, piezas: list[str] | None = None, estrategia: str | None = None,
             forzar: bool = False, simular: bool = False) -> dict[str, Any]:
    """Merge ``desde`` into the active branch (or bring ``piezas`` from it).
    Caller holds `parametros.bloqueo(doc_id)`."""
    if estrategia not in ESTRATEGIAS:
        raise FusionInvalida("estrategia: auto | nuestra | suya")
    if piezas is not None:
        piezas = list(dict.fromkeys(piezas))
        if not piezas or len(piezas) > MAX_PIEZAS or not all(isinstance(p, str) and p for p in piezas):
            raise FusionInvalida(f"piezas: lista de 1 a {MAX_PIEZAS} nombres")
    ruta = documents._files.get(doc_id)
    if ruta is None or not ruta.exists():
        raise LookupError("archivo del documento no encontrado")
    ramas.asegurar(doc_id)
    activa = git_store.rama_activa(doc_id)
    sha_b = git_store.resolver(doc_id, desde)
    if simular:
        archivos_a, fuente_a, _rev = ramas.capturar(doc_id)
        tip_a = git_store.tip_rama(doc_id, activa)
    else:
        tip_a = ramas.registrar(doc_id, "estado sin registrar", solo_si_cambia=True)
        archivos_a, fuente_a = git_store.leer(doc_id, tip_a), git_store.leer_fuente(doc_id, tip_a)
    base_info = {"rama": activa, "desde": sha_b[:10], "simulada": simular}
    if piezas is None and git_store.base_comun(doc_id, tip_a, sha_b) == sha_b:
        return {**base_info, "resultado": "ya_incluida", "confirmada": False, "base": sha_b[:10],
                "conflictos": [], "cambios": [], "avisos": [], "verificacion": None, "modo": "completa"}
    plan = planificar(doc_id, archivos_a, fuente_a, tip_a, sha_b, piezas, estrategia)
    publico = {**base_info, "modo": plan["modo"], "base": plan["base"][:10] if plan["base"] else None,
               "conflictos": plan["conflictos"], "cambios": plan["cambios"], "avisos": plan["avisos"],
               "verificacion": plan["verificacion"]}
    if plan["conflictos"]:
        return {**publico, "resultado": "conflicto", "confirmada": False}
    verif = plan["verificacion"] or {"ok": True}
    if not verif.get("ok") and not forzar:
        return {**publico, "resultado": "conflicto_geometrico", "confirmada": False}
    if simular:
        return {**publico, "resultado": "simulada", "confirmada": False}
    return {**publico, **_confirmar(doc_id, ruta, activa, tip_a, sha_b, desde, plan, piezas),
            "resultado": "fusionada", "confirmada": True, "forzada": bool(forzar and not verif.get("ok"))}


def _confirmar(doc_id: str, ruta: Path, activa: str, tip_a: str, sha_b: str, desde: str,
               plan: dict[str, Any], piezas: list[str] | None) -> dict[str, Any]:
    """All-or-nothing write of the merged state + its commit (two parents
    for a merge, one for a cherry-pick), mirroring `ramas.cambiar`."""
    archivos = plan["archivos"]
    assemblies.validate_snapshot(archivos)
    if piezas is None:
        mensaje = f"fusionar {_nombre_ref(desde)} en {activa}"
    else:
        mensaje = f"traer {', '.join(piezas[:6])} de {_nombre_ref(desde)}"
    versioning.crear_snapshot(doc_id, f"antes de {mensaje}", ramas._snapshot_actual(doc_id, ruta), pendiente=False)
    registro_previo = documents._registry[doc_id]
    tenia_revision = doc_id in documents._revisiones
    revision_previa = documents._revisiones.get(doc_id)
    marca_previa = git_store.leer_script_divergente(doc_id)
    try:
        ramas._materializar(doc_id, ruta, archivos)
        analisis = documents._analyze(ruta, ruta.suffix.lower())
        registro = {"id": doc_id, "nombre": documents._leer_meta(doc_id, registro_previo["nombre"]), **analisis}
        documents._registry[doc_id] = registro
        documents.confirmar_revision(doc_id)
        nueva = documents._revisiones.get(doc_id)
        estado, fuente, rev = ramas.capturar(doc_id)
        with git_store.bloqueo(doc_id):
            nuevo = git_store.escribir_commit(
                doc_id, estado, mensaje, uuid.uuid4().hex[:12], ramas._ahora(), versioning.autor_actual.get(),
                fuente, padre=tip_a, extra={"Revision": rev or "", "Rama": activa,
                                             ("Fusion" if piezas is None else "Desde"): sha_b},
                padres_extra=[sha_b] if piezas is None else None)
            git_store.mover_ref_rama(doc_id, activa, nuevo, tip_a)
        avisos = ramas._revisar_script(doc_id, activa, nuevo, estado)
    except BaseException:
        logger.exception("fusion de %s en %s fallida; se restablece", desde, doc_id)
        try:
            ramas._materializar(doc_id, ruta, git_store.leer(doc_id, tip_a))
        finally:
            if git_store.tip_rama(doc_id, activa) != tip_a:
                with git_store.bloqueo(doc_id):
                    git_store.mover_ref_rama(doc_id, activa, tip_a, git_store.tip_rama(doc_id, activa))
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
    git_store.recoger(doc_id)
    if nueva == revision_previa:
        eventos.publicar("anotaciones_actualizadas", doc_id, nueva,
                         [c for c in ramas.CAMBIOS_RAMA if c != "geometria"])
    else:
        eventos.publicar("anotaciones_actualizadas", doc_id, nueva, ["ramas", "parametros", "materiales", "notas"])
    return {**registro, "revision": nueva, "sha_corto": nuevo[:10],
            "padres": [tip_a[:10]] + ([sha_b[:10]] if piezas is None else []),
            "avisos": plan["avisos"] + avisos}


# ------------------------------------------------------------ G5: hitos

def crear_hito(doc_id: str, nombre: str, paso: str | None, descripcion: str | None) -> dict[str, Any]:
    git_store.validar_rama(nombre)
    if paso is None:
        ramas.registrar(doc_id, "estado sin registrar", solo_si_cambia=True)
        sha = git_store.tip_rama(doc_id, git_store.rama_activa(doc_id))
    else:
        sha = git_store.resolver(doc_id, paso)
    if sha is None:
        raise LookupError("no hay paso al que poner el hito")
    with git_store.bloqueo(doc_id):
        if nombre in git_store.listar_hitos(doc_id):
            raise FileExistsError(f"el hito {nombre!r} ya existe")
        git_store.mover_ref_hito(doc_id, nombre, sha, None)
        cur = git_store.leer_curacion(doc_id)
        if descripcion:
            cur["descripciones"][nombre] = str(descripcion)[:500]
            git_store.guardar_curacion(doc_id, cur)
    return {"hito": nombre, "sha_corto": sha[:10]}


def borrar_hito(doc_id: str, nombre: str) -> dict[str, Any]:
    git_store.validar_rama(nombre)
    with git_store.bloqueo(doc_id):
        sha = git_store.listar_hitos(doc_id).get(nombre)
        if sha is None:
            raise LookupError(f"hito {nombre!r} no encontrado")
        git_store.mover_ref_hito(doc_id, nombre, None, sha)
        cur = git_store.leer_curacion(doc_id)
        if cur["descripciones"].pop(nombre, None) is not None:
            git_store.guardar_curacion(doc_id, cur)
    return {"borrado": nombre, "sha_corto": sha[:10]}


def listar_hitos(doc_id: str) -> dict[str, Any]:
    cur = git_store.leer_curacion(doc_id)
    salida = []
    for nombre, sha in git_store.listar_hitos(doc_id).items():
        e = git_store.log_limitado(doc_id, sha, 1)
        salida.append({"nombre": nombre, "descripcion": cur["descripciones"].get(nombre),
                       **(ramas._paso(e[0]) if e else {"sha_corto": sha[:10]})})
    orden = {n: git_store.contar(doc_id, sha) for n, sha in git_store.listar_hitos(doc_id).items()}
    salida.sort(key=lambda h: (h.get("fecha") or "", orden.get(h["nombre"], 0)), reverse=True)
    return {"hitos": salida}


def ocultar_pasos(doc_id: str, pasos: list[str], ocultar: bool) -> dict[str, Any]:
    shas = [git_store.resolver(doc_id, p) for p in pasos]
    with git_store.bloqueo(doc_id):
        cur = git_store.leer_curacion(doc_id)
        actuales = set(cur["ocultos"])
        actuales = actuales | set(shas) if ocultar else actuales - set(shas)
        cur["ocultos"] = sorted(actuales)
        git_store.guardar_curacion(doc_id, cur)
    return {"ocultos": len(cur["ocultos"])}


def pasos_curados(doc_id: str, rama: str | None, vista: str, limite: int) -> dict[str, Any]:
    """``vista`` = ``todos`` (with ``hito``/``oculto`` per step),
    ``visibles`` (hidden ones left out) or ``hitos`` (one entry per
    milestone on the branch + the tip, each with how many steps it groups)."""
    nombre = rama or git_store.rama_activa(doc_id)
    sha = git_store.tip_rama(doc_id, nombre)
    if sha is None:
        raise LookupError(f"rama {nombre!r} no encontrada")
    cur = git_store.leer_curacion(doc_id)
    ocultos = set(cur["ocultos"])
    hitos_por_sha: dict[str, list[str]] = {}
    for h, s in git_store.listar_hitos(doc_id).items():
        hitos_por_sha.setdefault(s, []).append(h)
    entradas = git_store.log_limitado(doc_id, sha, ramas.PASOS_MAX if vista == "hitos" else limite * 4)
    total = git_store.contar(doc_id, sha)

    def _e(x: dict[str, Any]) -> dict[str, Any]:
        return {**ramas._paso(x), "hito": (hitos_por_sha.get(x["sha"]) or [None])[0],
                "oculto": x["sha"] in ocultos}

    if vista == "todos":
        lista = [_e(x) for x in entradas[:limite]]
    elif vista == "visibles":
        lista = [_e(x) for x in entradas if x["sha"] not in ocultos][:limite]
    else:
        lista = []
        agrupa = 0
        actual: dict[str, Any] | None = None
        for i, x in enumerate(entradas):
            if i == 0 or x["sha"] in hitos_por_sha:
                if actual is not None:
                    actual["agrupa"] = agrupa
                    lista.append(actual)
                actual, agrupa = _e(x), 0
            agrupa += 1
        if actual is not None:
            actual["agrupa"] = agrupa + max(0, total - len(entradas))
            lista.append(actual)
        lista = lista[:limite]
    return {"rama": nombre, "vista": vista, "total": total, "pasos": lista}


# ------------------------------------------------------------ REST

def _traducir(fn, *args):
    try:
        return ramas._traducir(fn, *args)
    except scripts_runner.ScriptError as exc:  # pragma: no cover - reported as conflict normally
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class _FusionarBody(BaseModel):
    desde: str = Field(..., max_length=64)
    piezas: list[str] | None = None
    estrategia: str | None = None
    forzar: bool = False
    simular: bool = False


@router.post("/documentos/{doc_id}/ramas/fusionar", dependencies=[Depends(auth.requiere_token)])
def fusionar_ruta(doc_id: str, body: _FusionarBody) -> dict[str, Any]:
    """Merge ``desde`` (branch or step) into the active branch, or bring
    ``piezas`` from it. ``simular`` returns the plan/conflicts/checks and
    writes nothing. ``resultado``: fusionada | simulada | conflicto |
    conflicto_geometrico | ya_incluida."""
    ramas._doc_o_404(doc_id)
    with parametros.bloqueo(doc_id):
        ramas._doc_o_404(doc_id)
        return _traducir(fusionar, doc_id, body.desde, body.piezas, body.estrategia, body.forzar, body.simular)


class _HitoBody(BaseModel):
    nombre: str
    paso: str | None = Field(default=None, max_length=64)
    descripcion: str | None = Field(default=None, max_length=500)


class _OcultarBody(BaseModel):
    pasos: list[str] = Field(..., min_length=1, max_length=200)
    ocultar: bool = True


@router.get("/documentos/{doc_id}/hitos")
def obtener_hitos(doc_id: str) -> dict[str, Any]:
    ramas._doc_o_404(doc_id)
    return _traducir(listar_hitos, doc_id)


@router.post("/documentos/{doc_id}/hitos", dependencies=[Depends(auth.requiere_token)])
def crear_hito_ruta(doc_id: str, body: _HitoBody) -> dict[str, Any]:
    def _crear(d: str) -> dict[str, Any]:
        ramas.asegurar(d)
        return crear_hito(d, body.nombre, body.paso, body.descripcion)
    resultado = ramas._bajo_candado(doc_id, _crear)
    eventos.publicar("anotaciones_actualizadas", doc_id, documents._revisiones.get(doc_id), ["ramas"])
    return resultado


@router.delete("/documentos/{doc_id}/hitos/{nombre}", dependencies=[Depends(auth.requiere_token)])
def borrar_hito_ruta(doc_id: str, nombre: str) -> dict[str, Any]:
    resultado = ramas._bajo_candado(doc_id, borrar_hito, nombre)
    eventos.publicar("anotaciones_actualizadas", doc_id, documents._revisiones.get(doc_id), ["ramas"])
    return resultado


@router.post("/documentos/{doc_id}/pasos/ocultar", dependencies=[Depends(auth.requiere_token)])
def ocultar_ruta(doc_id: str, body: _OcultarBody) -> dict[str, Any]:
    resultado = ramas._bajo_candado(doc_id, ocultar_pasos, body.pasos, body.ocultar)
    eventos.publicar("anotaciones_actualizadas", doc_id, documents._revisiones.get(doc_id), ["ramas"])
    return resultado


@router.get("/documentos/{doc_id}/ramas/{rama}/pasos_curados")
def obtener_pasos_curados(doc_id: str, rama: str, vista: str = Query(default="hitos", pattern="^(todos|visibles|hitos)$"),
                          limite: int = Query(default=50, ge=1, le=ramas.PASOS_MAX)) -> dict[str, Any]:
    ramas._doc_o_404(doc_id)
    if not git_store.listar_ramas(doc_id):
        ramas._bajo_candado(doc_id, ramas.asegurar)
    return _traducir(pasos_curados, doc_id, rama, vista, limite)
