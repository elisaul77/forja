"""Git as the history backend (Plan G · G1, ADR-0014).

One bare repository per document under ``{FORJA_DATA_DIR}/.repos/{doc_id}.git``.
Each history entry (what ADR-0006 called a snapshot) is one commit whose
tree holds exactly the files of that entry — the same ``dict[str, bytes]``
`app/versioning.py` has always moved around — plus, optionally, a
``_fuente/`` subtree with extra source blobs (e.g. the script text read
from its ``ruta``) that never come back through :func:`leer`.

Only git *plumbing* is used (``hash-object``, ``mktree``, ``commit-tree``,
``update-ref``, ``cat-file``): there is no work tree and no index, so a file
name can never be turned into a path on disk, and nothing in the repo is
ever checked out. The binary is called without a shell, with a timeout,
with hooks disabled (``core.hooksPath=/dev/null``), every transport
protocol forbidden (``protocol.allow=never``), system/global config
ignored, and ``safe.directory`` limited to the one repository in use.
Authors are neutral roles (``agente``/``humano``/``forja``), never people.

Commit message layout (parsed back by :func:`log`)::

    {mensaje}

    Forja-Snapshot: {id}
    Forja-Fecha: {fecha}
    Forja-Archivos: ["a.step", "notas.json"]
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

REPOS_DIR = Path(os.environ.get("FORJA_DATA_DIR", "/data/documentos")) / ".repos"

RAMA = "refs/heads/main"
AUTORES = ("agente", "humano", "forja")
FUENTE_DIR = "_fuente"
TIMEOUT_S = 120.0

_RE_DOC_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_RE_SHA = re.compile(r"^[0-9a-f]{40}$")
_RE_SHA_CORTO = re.compile(r"^[0-9a-f]{7,40}$")
# G2: branch names are one safe ref component (no `/`, `.`, `..`, `@{`,
# spaces, `.lock`...) — stricter than `git check-ref-format`.
_RE_RAMA = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,47}$")
REF_RAMAS = "refs/forja/ramas/"
RAMA_PRINCIPAL = "main"
_ARCHIVO_ACTIVA = "forja-rama-activa"
_SEPARADOR = "\n\nForja-Snapshot: "

_bloqueos: dict[str, threading.Lock] = {}
_bloqueo_global = threading.Lock()


class GitError(RuntimeError):
    """The git binary failed or timed out."""


def bloqueo(doc_id: str) -> threading.Lock:
    """Per-repository lock. Separate from `parametros.bloqueo` on purpose:
    that one is held by most callers already and is not reentrant."""
    with _bloqueo_global:
        return _bloqueos.setdefault(doc_id, threading.Lock())


def validar_doc_id(doc_id: str) -> str:
    if not isinstance(doc_id, str) or not _RE_DOC_ID.match(doc_id):
        raise ValueError(f"id de documento no valido para el historial: {doc_id!r}")
    return doc_id


def validar_nombre(nombre: str) -> str:
    """A history file name is one flat path component: no separators, no
    ``.``/``..``, no leading dot, no control characters."""
    if (
        not isinstance(nombre, str)
        or not nombre
        or len(nombre.encode()) > 255
        or nombre in (".", "..")
        or nombre.startswith(".")
        or nombre == FUENTE_DIR
        or any(c in nombre for c in "/\\")
        or any(ord(c) < 32 for c in nombre)
    ):
        raise ValueError(f"nombre de archivo fuera del repositorio: {nombre!r}")
    return nombre


def validar_sha(sha: str) -> str:
    """A full 40-hex commit id coming from outside (never a ref expression)."""
    if not isinstance(sha, str) or not _RE_SHA.match(sha):
        raise ValueError("sha no valido")
    return sha


def validar_rama(nombre: str) -> str:
    if not isinstance(nombre, str) or not _RE_RAMA.match(nombre) or nombre.endswith("-lock"):
        raise ValueError(f"nombre de rama no valido: {nombre!r} (letras, numeros, - y _; max 48)")
    return nombre


def ruta_repo(doc_id: str) -> Path:
    return REPOS_DIR / f"{validar_doc_id(doc_id)}.git"


def _entorno(autor: str | None = None, fecha_git: str | None = None) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": "/nonexistent",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/bin/false",
        "GIT_SSH_COMMAND": "/bin/false",
    }
    nombre = autor if autor in AUTORES else "forja"
    env.update({
        "GIT_AUTHOR_NAME": nombre, "GIT_AUTHOR_EMAIL": f"{nombre}@forja.local",
        "GIT_COMMITTER_NAME": "forja", "GIT_COMMITTER_EMAIL": "forja@forja.local",
    })
    if fecha_git:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = fecha_git
    return env


def _git(repo: Path, *args: str, entrada: bytes | None = None,
         autor: str | None = None, fecha_git: str | None = None,
         timeout: float = TIMEOUT_S, permitir_fallo: bool = False) -> bytes:
    cmd = [
        "git",
        "-c", "core.hooksPath=/dev/null",
        "-c", f"safe.directory={repo}",
        "-c", "protocol.allow=never",
        "-c", "core.fsmonitor=false",
        "-c", "gc.auto=256",
        "--git-dir", str(repo),
        *args,
    ]
    try:
        res = subprocess.run(
            cmd, input=entrada, capture_output=True, timeout=timeout,
            env=_entorno(autor, fecha_git), check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {args[0]} supero {timeout}s") from exc
    if res.returncode != 0 and not permitir_fallo:
        raise GitError(f"git {args[0]} fallo ({res.returncode}): {res.stderr.decode(errors='replace')[:300]}")
    return res.stdout if res.returncode == 0 else b""


def existe(doc_id: str) -> bool:
    return (ruta_repo(doc_id) / "HEAD").exists()


def inicializar(doc_id: str) -> Path:
    repo = ruta_repo(doc_id)
    if not (repo / "HEAD").exists():
        REPOS_DIR.mkdir(parents=True, exist_ok=True)
        _git(repo, "init", "--bare", "--quiet", str(repo))
        _git(repo, "symbolic-ref", "HEAD", RAMA)
    return repo


def cabeza(doc_id: str) -> str | None:
    if not existe(doc_id):
        return None
    sha = _git(ruta_repo(doc_id), "rev-parse", "-q", "--verify", RAMA, permitir_fallo=True).decode().strip()
    return sha or None


def _escribir_blob(repo: Path, contenido: bytes) -> str:
    return _git(repo, "hash-object", "-w", "--stdin", entrada=contenido).decode().strip()


def _escribir_arbol(repo: Path, entradas: list[tuple[str, str, str]]) -> str:
    """``entradas`` = ``[(tipo, sha, nombre)]`` (``tipo`` blob|tree); names
    are validated by the caller."""
    texto = "".join(f"{modo} {tipo} {sha}\t{nombre}\n" for modo, tipo, sha, nombre in
                    ((("040000" if t == "tree" else "100644"), t, s, n) for t, s, n in entradas))
    return _git(repo, "mktree", entrada=texto.encode()).decode().strip()


def construir_mensaje(mensaje: str, snapshot_id: str, fecha: str, archivos: list[str],
                      extra: dict[str, str] | None = None) -> str:
    texto = (f"{mensaje}{_SEPARADOR}{snapshot_id}\nForja-Fecha: {fecha}\n"
             f"Forja-Archivos: {json.dumps(sorted(archivos), ensure_ascii=False)}\n")
    for clave, valor in (extra or {}).items():
        texto += f"Forja-{clave}: {valor}\n"
    return texto


def _fecha_git(fecha: str) -> str:
    # "2026-10-04T12:00:00Z" -> git's ISO 8601 strict input
    return fecha.replace("Z", "+00:00")


def escribir_commit(doc_id: str, archivos: dict[str, bytes], mensaje: str, snapshot_id: str,
                    fecha: str, autor: str | None = None, fuente: dict[str, bytes] | None = None,
                    padre: str | None = "HEAD", extra: dict[str, str] | None = None,
                    padres_extra: list[str] | None = None) -> str:
    """Create the commit object (tree = exactly ``archivos`` + optional
    ``_fuente/``) on top of ``padre`` (``"HEAD"`` = current branch tip,
    ``None`` = root commit, else a sha) WITHOUT moving the branch. Returns
    the new commit sha. ``padres_extra`` (G4) adds more parents after
    ``padre`` (a merge commit). Caller holds :func:`bloqueo`."""
    repo = inicializar(doc_id)
    arbol = construir_arbol(doc_id, archivos, fuente)
    if padre == "HEAD":
        padre = cabeza(doc_id)
    args = ["commit-tree", arbol]
    if padre:
        args += ["-p", validar_sha(padre)]
    for otro in padres_extra or []:
        args += ["-p", validar_sha(otro)]
    texto = construir_mensaje(mensaje, snapshot_id, fecha, list(archivos), extra)
    return _git(repo, *args, entrada=texto.encode(), autor=autor,
                fecha_git=_fecha_git(fecha)).decode().strip()


def construir_arbol(doc_id: str, archivos: dict[str, bytes],
                    fuente: dict[str, bytes] | None = None) -> str:
    """Write the blobs and the tree (``archivos`` + optional ``_fuente/``);
    returns the tree sha. Same bytes -> same tree sha."""
    repo = inicializar(doc_id)
    entradas = [("blob", _escribir_blob(repo, contenido), validar_nombre(nombre))
                for nombre, contenido in sorted(archivos.items())]
    if fuente:
        sub = [("blob", _escribir_blob(repo, contenido), validar_nombre(nombre))
               for nombre, contenido in sorted(fuente.items())]
        entradas.append(("tree", _escribir_arbol(repo, sub), FUENTE_DIR))
    return _escribir_arbol(repo, entradas)


def arbol_de(doc_id: str, commit_sha: str) -> str:
    return _git(ruta_repo(doc_id), "rev-parse", "--verify", "--end-of-options",
                f"{validar_sha(commit_sha)}^{{tree}}").decode().strip()


def mover_rama(doc_id: str, nuevo: str, anterior: str | None) -> None:
    """Compare-and-swap the branch tip (empty old value = must not exist)."""
    validar_sha(nuevo)
    if anterior:
        validar_sha(anterior)
    _git(ruta_repo(doc_id), "update-ref", "--end-of-options", RAMA, nuevo, anterior or "0" * 40)


def commit(doc_id: str, archivos: dict[str, bytes], mensaje: str, snapshot_id: str, fecha: str,
           autor: str | None = None, fuente: dict[str, bytes] | None = None) -> str:
    """Append one history commit on the branch. Returns its sha."""
    with bloqueo(doc_id):
        anterior = cabeza(doc_id)
        nuevo = escribir_commit(doc_id, archivos, mensaje, snapshot_id, fecha, autor, fuente,
                                padre=anterior)
        mover_rama(doc_id, nuevo, anterior)
        _git(ruta_repo(doc_id), "gc", "--auto", "--quiet", permitir_fallo=True)
        return nuevo


def _parsear(sha: str, cuerpo: str, autor: str) -> dict[str, Any] | None:
    pos = cuerpo.rfind(_SEPARADOR)
    if pos < 0:
        return None
    mensaje, resto = cuerpo[:pos], cuerpo[pos + len(_SEPARADOR):]
    lineas = resto.split("\n")
    datos = {"sha": sha, "id": lineas[0].strip(), "mensaje": mensaje, "autor": autor}
    for linea in lineas[1:]:
        if linea.startswith("Forja-Fecha: "):
            datos["fecha"] = linea[len("Forja-Fecha: "):]
        elif linea.startswith("Forja-Revision: "):
            datos["revision"] = linea[len("Forja-Revision: "):].strip() or None
        elif linea.startswith("Forja-Archivos: "):
            try:
                datos["archivos"] = json.loads(linea[len("Forja-Archivos: "):])
            except json.JSONDecodeError:
                datos["archivos"] = []
    if "fecha" not in datos:
        return None
    return datos


def log(doc_id: str, desde: str | None = None) -> list[dict[str, Any]]:
    """Forja commits reachable from ``desde`` (default: branch tip), oldest
    first: ``[{sha, id, fecha, mensaje, autor, archivos}]``."""
    if desde:
        validar_sha(desde)
    if not existe(doc_id):
        return []
    tip = desde or cabeza(doc_id)
    if not tip:
        return []
    salida = _git(ruta_repo(doc_id), "log", "--reverse", "--format=%H%x00%an%x00%B%x1e",
                  "--end-of-options", tip)
    entradas = []
    for bloque in salida.decode("utf-8", errors="surrogateescape").split("\x1e"):
        bloque = bloque.lstrip("\n")
        if not bloque:
            continue
        sha, autor, cuerpo = bloque.split("\x00", 2)
        datos = _parsear(sha, cuerpo, autor)
        if datos is not None:
            entradas.append(datos)
    return entradas


def _leer_objeto(repo: Path, sha: str) -> bytes:
    return _git(repo, "cat-file", "blob", validar_sha(sha))


def leer(doc_id: str, commit_sha: str) -> dict[str, bytes]:
    """Top-level files of ``commit_sha`` (never ``_fuente/``)."""
    validar_sha(commit_sha)
    repo = ruta_repo(doc_id)
    listado = _git(repo, "ls-tree", "-z", "--end-of-options", commit_sha).decode("utf-8", errors="surrogateescape")
    archivos = {}
    for linea in listado.split("\x00"):
        if not linea:
            continue
        cabecera, nombre = linea.split("\t", 1)
        _modo, tipo, sha = cabecera.split()
        if tipo == "blob":
            archivos[nombre] = _leer_objeto(repo, sha)
    return archivos


def leer_fuente(doc_id: str, commit_sha: str) -> dict[str, bytes]:
    validar_sha(commit_sha)
    repo = ruta_repo(doc_id)
    salida = _git(repo, "ls-tree", "-z", "--end-of-options", f"{commit_sha}:{FUENTE_DIR}", permitir_fallo=True).decode()
    archivos = {}
    for linea in salida.split("\x00"):
        if linea:
            cabecera, nombre = linea.split("\t", 1)
            archivos[nombre] = _leer_objeto(repo, cabecera.split()[2])
    return archivos


# ------------------------------------------------------------ G2: ramas
#
# Branches live under ``refs/forja/ramas/<nombre>``, NOT ``refs/heads/``:
# ``refs/heads/main`` is (since G1) the chain of "before the change"
# snapshots that `leer_historial`/`restaurar` read, and it must stay intact.
# Each branch commit holds the COMPLETE state after an accepted change.

def ref_rama(nombre: str) -> str:
    return REF_RAMAS + validar_rama(nombre)


def tip_rama(doc_id: str, nombre: str) -> str | None:
    if not existe(doc_id):
        return None
    sha = _git(ruta_repo(doc_id), "rev-parse", "-q", "--verify", "--end-of-options",
               ref_rama(nombre), permitir_fallo=True).decode().strip()
    return sha or None


def listar_ramas(doc_id: str) -> dict[str, str]:
    """``{nombre: sha}`` of every branch (sorted by name)."""
    if not existe(doc_id):
        return {}
    salida = _git(ruta_repo(doc_id), "for-each-ref", "--format=%(refname)%00%(objectname)",
                  REF_RAMAS).decode()
    ramas = {}
    for linea in salida.splitlines():
        ref, _, sha = linea.partition("\x00")
        nombre = ref[len(REF_RAMAS):]
        if _RE_RAMA.match(nombre) and _RE_SHA.match(sha):
            ramas[nombre] = sha
    return dict(sorted(ramas.items()))


def mover_ref_rama(doc_id: str, nombre: str, nuevo: str | None, anterior: str | None) -> None:
    """Compare-and-swap a branch ref; ``nuevo=None`` deletes it (only if it
    still points at ``anterior``). Caller holds :func:`bloqueo`."""
    repo = ruta_repo(doc_id)
    ref = ref_rama(nombre)
    if nuevo is None:
        _git(repo, "update-ref", "-d", "--end-of-options", ref, validar_sha(anterior or ""))
    else:
        _git(repo, "update-ref", "--end-of-options", ref, validar_sha(nuevo),
             validar_sha(anterior) if anterior else "0" * 40)


def rama_activa(doc_id: str) -> str:
    try:
        nombre = (ruta_repo(doc_id) / _ARCHIVO_ACTIVA).read_text().strip()
        return validar_rama(nombre)
    except (OSError, ValueError):
        return RAMA_PRINCIPAL


def fijar_rama_activa(doc_id: str, nombre: str) -> None:
    destino = inicializar(doc_id) / _ARCHIVO_ACTIVA
    temporal = destino.with_name(destino.name + ".tmp")
    temporal.write_text(validar_rama(nombre) + "\n")
    os.replace(temporal, destino)


_ARCHIVO_DIVERGENTE = "forja-script-divergente"


def leer_script_divergente(doc_id: str) -> dict[str, str] | None:
    """``{ruta, sha256}`` recorded by a branch switch whose ``_fuente``
    script differs from the file at ``ruta`` (G2 fix-review), or ``None``."""
    try:
        datos = json.loads((ruta_repo(doc_id) / _ARCHIVO_DIVERGENTE).read_text())
    except (OSError, ValueError):
        return None
    if isinstance(datos, dict) and isinstance(datos.get("ruta"), str) and isinstance(datos.get("sha256"), str):
        return {"ruta": datos["ruta"], "sha256": datos["sha256"]}
    return None


def fijar_script_divergente(doc_id: str, ruta: str | None, sha256: str | None) -> None:
    """Record (both given) or clear (either ``None``) the divergence marker."""
    destino = ruta_repo(doc_id) / _ARCHIVO_DIVERGENTE
    if ruta is None or sha256 is None:
        destino.unlink(missing_ok=True)
        return
    destino = inicializar(doc_id) / _ARCHIVO_DIVERGENTE
    temporal = destino.with_name(destino.name + ".tmp")
    temporal.write_text(json.dumps({"ruta": ruta, "sha256": sha256}))
    os.replace(temporal, destino)


def resolver(doc_id: str, referencia: str) -> str:
    """Branch name, full sha or abbreviated sha (7+ hex) -> full commit sha.
    Raises ``ValueError`` for anything else (never passes free text to git
    as a revision expression) and ``LookupError`` if it does not exist."""
    if not isinstance(referencia, str):
        raise ValueError("referencia no valida")
    if _RE_SHA_CORTO.match(referencia):
        sha = _git(ruta_repo(doc_id), "rev-parse", "-q", "--verify", "--end-of-options",
                   f"{referencia}^{{commit}}", permitir_fallo=True).decode().strip()
        if _RE_SHA.match(sha):
            return sha
        if not _RE_RAMA.match(referencia):
            raise LookupError(f"paso {referencia!r} no encontrado")
    sha = tip_rama(doc_id, referencia)
    if sha is None:
        raise LookupError(f"rama o paso {referencia!r} no encontrado")
    return sha


def contar(doc_id: str, sha: str) -> int:
    return int(_git(ruta_repo(doc_id), "rev-list", "--count", "--end-of-options",
                    validar_sha(sha)).decode().strip() or 0)


def log_limitado(doc_id: str, sha: str, limite: int) -> list[dict[str, Any]]:
    """Newest-first Forja commits reachable from ``sha`` (at most ``limite``)."""
    salida = _git(ruta_repo(doc_id), "log", f"--max-count={int(limite)}",
                  "--format=%H%x00%an%x00%B%x1e", "--end-of-options", validar_sha(sha))
    entradas = []
    for bloque in salida.decode("utf-8", errors="surrogateescape").split("\x1e"):
        bloque = bloque.lstrip("\n")
        if not bloque:
            continue
        sha_c, autor, cuerpo = bloque.split("\x00", 2)
        datos = _parsear(sha_c, cuerpo, autor)
        if datos is not None:
            entradas.append(datos)
    return entradas


def recoger(doc_id: str) -> None:
    _git(ruta_repo(doc_id), "gc", "--auto", "--quiet", permitir_fallo=True)


def compactar(doc_id: str, agresivo: bool = False, timeout: float = 1800.0) -> None:
    with bloqueo(doc_id):
        args = ["gc", "--quiet", "--prune=now"] + (["--aggressive"] if agresivo else [])
        _git(ruta_repo(doc_id), *args, timeout=timeout)


def tamano(doc_id: str) -> int:
    repo = ruta_repo(doc_id)
    return sum(p.stat().st_size for p in repo.rglob("*") if p.is_file()) if repo.exists() else 0


def borrar(doc_id: str) -> None:
    repo = ruta_repo(doc_id)
    with bloqueo(doc_id):
        if repo.exists() and repo.resolve().parent == REPOS_DIR.resolve():
            shutil.rmtree(repo, ignore_errors=True)


# ------------------------------------------------------------ G4: fusion

def base_comun(doc_id: str, a: str, b: str) -> str | None:
    """Best common ancestor of two commits (``git merge-base``) or ``None``."""
    sha = _git(ruta_repo(doc_id), "merge-base", "--end-of-options", validar_sha(a), validar_sha(b),
               permitir_fallo=True).decode().strip()
    return sha if _RE_SHA.match(sha) else None


def fusionar_texto(base: bytes, nuestro: bytes, suyo: bytes,
                   estrategia: str | None = None) -> tuple[bytes, int]:
    """Three-way text merge with ``git merge-file -p`` (no repo needed).
    Returns ``(texto, conflictos)``; with conflicts the text carries the
    usual ``<<<<<<< / ||||||| / ======= / >>>>>>>`` markers. ``estrategia``
    ``nuestra``/``suya`` resolves every conflict hunk to that side."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="forja-merge-") as tmp:
        rutas = []
        for nombre, contenido in (("a", nuestro), ("o", base), ("b", suyo)):
            camino = Path(tmp) / nombre
            camino.write_bytes(contenido)
            rutas.append(str(camino))
        args = ["merge-file", "-p", "--diff3", "-L", "rama activa", "-L", "ancestro", "-L", "rama fusionada"]
        if estrategia == "nuestra":
            args.append("--ours")
        elif estrategia == "suya":
            args.append("--theirs")
        try:
            res = subprocess.run(["git", *args, *rutas], capture_output=True, timeout=TIMEOUT_S,
                                 env=_entorno(), check=False)
        except subprocess.TimeoutExpired as exc:
            raise GitError("git merge-file supero el tiempo") from exc
    if res.returncode < 0 or res.returncode > 127:
        raise GitError(f"git merge-file fallo: {res.stderr.decode(errors='replace')[:300]}")
    return res.stdout, res.returncode


# ------------------------------------------------------------ G5: hitos y curacion
#
# Milestones are refs ``refs/forja/hitos/<nombre>`` pointing at a step; the
# description, hidden steps and groups live in ``forja-curacion.json``
# inside the repo (metadata apart: history is never rewritten).

REF_HITOS = "refs/forja/hitos/"
_ARCHIVO_CURACION = "forja-curacion.json"


def ref_hito(nombre: str) -> str:
    return REF_HITOS + validar_rama(nombre)


def listar_hitos(doc_id: str) -> dict[str, str]:
    if not existe(doc_id):
        return {}
    salida = _git(ruta_repo(doc_id), "for-each-ref", "--format=%(refname)%00%(objectname)",
                  REF_HITOS).decode()
    hitos = {}
    for linea in salida.splitlines():
        ref, _, sha = linea.partition("\x00")
        nombre = ref[len(REF_HITOS):]
        if _RE_RAMA.match(nombre) and _RE_SHA.match(sha):
            hitos[nombre] = sha
    return dict(sorted(hitos.items()))


def mover_ref_hito(doc_id: str, nombre: str, nuevo: str | None, anterior: str | None) -> None:
    """Compare-and-swap a milestone ref; ``nuevo=None`` deletes it."""
    repo = ruta_repo(doc_id)
    ref = ref_hito(nombre)
    if nuevo is None:
        _git(repo, "update-ref", "-d", "--end-of-options", ref, validar_sha(anterior or ""))
    else:
        _git(repo, "update-ref", "--end-of-options", ref, validar_sha(nuevo),
             validar_sha(anterior) if anterior else "0" * 40)


def leer_curacion(doc_id: str) -> dict[str, Any]:
    """``{descripciones: {hito: texto}, ocultos: [sha]}`` (always both)."""
    try:
        datos = json.loads((ruta_repo(doc_id) / _ARCHIVO_CURACION).read_text())
    except (OSError, ValueError):
        datos = {}
    if not isinstance(datos, dict):
        datos = {}
    desc = datos.get("descripciones")
    ocultos = datos.get("ocultos")
    return {"descripciones": {k: str(v) for k, v in desc.items() if isinstance(k, str)} if isinstance(desc, dict) else {},
            "ocultos": [s for s in ocultos if isinstance(s, str) and _RE_SHA.match(s)] if isinstance(ocultos, list) else []}


def guardar_curacion(doc_id: str, datos: dict[str, Any]) -> None:
    destino = inicializar(doc_id) / _ARCHIVO_CURACION
    temporal = destino.with_name(destino.name + ".tmp")
    temporal.write_text(json.dumps(datos, ensure_ascii=False))
    os.replace(temporal, destino)
