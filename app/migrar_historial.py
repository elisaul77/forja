"""Migrate the ADR-0006 snapshot store (`.historial/`) into per-document git
repositories (Plan G · G1, ADR-0014).

    docker compose exec -T -u 1000 forja python -m migrar_historial            # dry-run
    docker compose exec -T -u 1000 forja python -m migrar_historial --aplicar  # migrate

Run it as uid 1000 (the server's user): as root the new repos would be
root-owned and the server could not commit to them (`--aplicar` refuses).

Per document, in manifest order (chronological), each snapshot becomes one
commit with the same id, date and message (author ``forja``: ADR-0006 never
recorded who). Commits made by the new code before the migration ran are
replayed on top, so none is lost. The branch only moves after EVERY
snapshot of that document reads back byte for byte from git (files and
``{id, fecha, mensaje}``); ``.historial/`` is renamed to
``.historial.migrado/`` only after every document verified. Any failure
leaves `.historial/` and the branch exactly as they were (new objects stay
unreferenced and are pruned by the next gc). Idempotent: a document whose
branch already holds all its snapshot ids is skipped; a marker
``.repos/.migracion.json`` records the run.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import git_store
import versioning

MARCADOR = ".migracion.json"


def _documentos() -> list[str]:
    if not versioning.HISTORIAL_DIR.is_dir():
        return []
    ids = []
    for d in sorted(versioning.HISTORIAL_DIR.iterdir()):
        if d.is_dir() and (d / "manifest.json").exists():
            git_store.validar_doc_id(d.name)
            ids.append(d.name)
    return ids


def _tamano_dir(ruta: Path) -> int:
    return sum(p.stat().st_size for p in ruta.rglob("*") if p.is_file()) if ruta.exists() else 0


def _verificar(doc_id: str, manifest: list[dict[str, Any]], tip: str) -> None:
    por_id = {e["id"]: e for e in git_store.log(doc_id, desde=tip)}
    orden = [e["id"] for e in git_store.log(doc_id, desde=tip)]
    if orden[: len(manifest)] != [e["id"] for e in manifest]:
        raise RuntimeError(f"{doc_id}: orden de instantaneas distinto tras migrar")
    for e in manifest:
        g = por_id[e["id"]]
        if (g["fecha"], g["mensaje"]) != (e["fecha"], e["mensaje"]):
            raise RuntimeError(f"{doc_id}/{e['id']}: fecha o mensaje distintos")
        if git_store.leer(doc_id, g["sha"]) != versioning.leer_snapshot_legado(doc_id, e["id"]):
            raise RuntimeError(f"{doc_id}/{e['id']}: bytes distintos")


def migrar_documento(doc_id: str, aplicar: bool) -> dict[str, Any]:
    manifest = versioning._leer_manifest(doc_id)
    informe: dict[str, Any] = {
        "doc": doc_id, "instantaneas": len(manifest),
        "bytes_historial": _tamano_dir(versioning.HISTORIAL_DIR / doc_id),
    }
    with git_store.bloqueo(doc_id):
        existentes = git_store.log(doc_id)
        ids_git = [e["id"] for e in existentes]
        ids_legado = [e["id"] for e in manifest]
        if ids_git[: len(ids_legado)] == ids_legado:
            informe["accion"] = "ya migrado"
            return informe
        if set(ids_legado) & set(ids_git):
            raise RuntimeError(f"{doc_id}: historial git parcial e inconsistente; revisar a mano")
        # Read everything first: a missing/unreadable file aborts before any write.
        contenidos = [versioning.leer_snapshot_legado(doc_id, e["id"]) for e in manifest]
        for c in contenidos:
            for nombre in c:
                git_store.validar_nombre(nombre)
        informe["accion"] = "migrar" + (f" (+{len(existentes)} commits nuevos encima)" if existentes else "")
        if not aplicar:
            return informe
        anterior = git_store.cabeza(doc_id)
        tip = None
        for e, archivos in zip(manifest, contenidos):
            tip = git_store.escribir_commit(doc_id, archivos, e["mensaje"], e["id"], e["fecha"],
                                            "forja", padre=tip)
        for e in existentes:  # replay commits made after the deploy
            tip = git_store.escribir_commit(
                doc_id, git_store.leer(doc_id, e["sha"]), e["mensaje"], e["id"], e["fecha"],
                e["autor"], git_store.leer_fuente(doc_id, e["sha"]) or None, padre=tip)
        if tip is None:
            informe["accion"] = "vacio"
            return informe
        _verificar(doc_id, manifest, tip)
        # Compare-and-swap: if the live server committed meanwhile (another
        # process, so our lock does not cover it), this fails and the
        # document is left untouched; just run the migration again.
        git_store.mover_rama(doc_id, tip, anterior)
    git_store.compactar(doc_id)
    informe["bytes_git"] = git_store.tamano(doc_id)
    return informe


def migrar(aplicar: bool = False) -> dict[str, Any]:
    docs = _documentos()
    resumen: dict[str, Any] = {
        "modo": "aplicar" if aplicar else "dry-run",
        "bytes_historial": _tamano_dir(versioning.HISTORIAL_DIR),
        "documentos": [],
    }
    for doc_id in docs:
        resumen["documentos"].append(migrar_documento(doc_id, aplicar))
    if aplicar and docs:
        # Second, independent pass: everything verified before moving anything.
        for doc_id in docs:
            manifest = versioning._leer_manifest(doc_id)
            _verificar(doc_id, manifest, git_store.cabeza(doc_id))
        destino = versioning.HISTORIAL_DIR.with_name(".historial.migrado")
        if destino.exists():
            raise RuntimeError(f"{destino} ya existe; no se sobrescribe")
        git_store.REPOS_DIR.mkdir(parents=True, exist_ok=True)
        resumen["fecha"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        resumen["bytes_repos"] = _tamano_dir(git_store.REPOS_DIR)
        (git_store.REPOS_DIR / MARCADOR).write_text(json.dumps(resumen, ensure_ascii=False, indent=1))
        versioning.HISTORIAL_DIR.rename(destino)
        resumen["movido_a"] = str(destino)
    return resumen


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--aplicar", action="store_true", help="migrar de verdad (sin esto: dry-run)")
    args = p.parse_args(argv)
    if args.aplicar and os.geteuid() == 0:
        print("no ejecutar --aplicar como root: usar `-u 1000`", file=sys.stderr)
        return 2
    print(json.dumps(migrar(args.aplicar), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
