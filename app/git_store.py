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


def construir_mensaje(mensaje: str, snapshot_id: str, fecha: str, archivos: list[str]) -> str:
    return (f"{mensaje}{_SEPARADOR}{snapshot_id}\nForja-Fecha: {fecha}\n"
            f"Forja-Archivos: {json.dumps(sorted(archivos), ensure_ascii=False)}\n")


def _fecha_git(fecha: str) -> str:
    # "2026-10-04T12:00:00Z" -> git's ISO 8601 strict input
    return fecha.replace("Z", "+00:00")


def escribir_commit(doc_id: str, archivos: dict[str, bytes], mensaje: str, snapshot_id: str,
                    fecha: str, autor: str | None = None, fuente: dict[str, bytes] | None = None,
                    padre: str | None = "HEAD") -> str:
    """Create the commit object (tree = exactly ``archivos`` + optional
    ``_fuente/``) on top of ``padre`` (``"HEAD"`` = current branch tip,
    ``None`` = root commit, else a sha) WITHOUT moving the branch. Returns
    the new commit sha. Caller holds :func:`bloqueo`."""
    repo = inicializar(doc_id)
    entradas = [("blob", _escribir_blob(repo, contenido), validar_nombre(nombre))
                for nombre, contenido in sorted(archivos.items())]
    if fuente:
        sub = [("blob", _escribir_blob(repo, contenido), validar_nombre(nombre))
               for nombre, contenido in sorted(fuente.items())]
        entradas.append(("tree", _escribir_arbol(repo, sub), FUENTE_DIR))
    arbol = _escribir_arbol(repo, entradas)
    if padre == "HEAD":
        padre = cabeza(doc_id)
    args = ["commit-tree", arbol]
    if padre:
        args += ["-p", padre]
    texto = construir_mensaje(mensaje, snapshot_id, fecha, list(archivos))
    return _git(repo, *args, entrada=texto.encode(), autor=autor,
                fecha_git=_fecha_git(fecha)).decode().strip()


def mover_rama(doc_id: str, nuevo: str, anterior: str | None) -> None:
    """Compare-and-swap the branch tip (empty old value = must not exist)."""
    if not _RE_SHA.match(nuevo):
        raise ValueError("sha no valido")
    _git(ruta_repo(doc_id), "update-ref", RAMA, nuevo, anterior or "0" * 40)


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
    if not existe(doc_id):
        return []
    tip = desde or cabeza(doc_id)
    if not tip:
        return []
    salida = _git(ruta_repo(doc_id), "log", "--reverse", "--format=%H%x00%an%x00%B%x1e", tip)
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
    return _git(repo, "cat-file", "blob", sha)


def leer(doc_id: str, commit_sha: str) -> dict[str, bytes]:
    """Top-level files of ``commit_sha`` (never ``_fuente/``)."""
    if not _RE_SHA.match(commit_sha):
        raise ValueError("sha no valido")
    repo = ruta_repo(doc_id)
    listado = _git(repo, "ls-tree", "-z", commit_sha).decode("utf-8", errors="surrogateescape")
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
    if not _RE_SHA.match(commit_sha):
        raise ValueError("sha no valido")
    repo = ruta_repo(doc_id)
    salida = _git(repo, "ls-tree", "-z", f"{commit_sha}:{FUENTE_DIR}", permitir_fallo=True).decode()
    archivos = {}
    for linea in salida.split("\x00"):
        if linea:
            cabecera, nombre = linea.split("\t", 1)
            archivos[nombre] = _leer_objeto(repo, cabecera.split()[2])
    return archivos


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
