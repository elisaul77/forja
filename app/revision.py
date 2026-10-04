"""Content identity of a document (F11.1, ADR P03).

``revision`` is a short hash of what the viewer actually draws: the STEP
bytes with the two HEADER entities that change on every export
(``FILE_NAME`` carries a timestamp, ``FILE_DESCRIPTION`` the writer) blanked
out, followed by the geometric sidecars (``solidos.json``: per-solid names
and ranges the viewer splits the mesh by). Running the same script twice
therefore yields the same ``revision``; notes, the display name and
parameters without a geometric effect never enter the hash.

Pure functions over bytes: nothing here touches the file on disk (it is
never rewritten), the registry or the event hub.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

# Bumped if the normalisation ever changes, so old and new revisions never
# collide by accident.
_PREFIJO = b"forja-revision-1\0"

# One HEADER entity up to its terminating `;`, skipping over STEP string
# literals (`'...'`, with `''` as the escaped quote) so a `;` or `)` inside a
# string never ends the match early.
_RE_ENTIDAD = re.compile(
    rb"\b(FILE_NAME|FILE_DESCRIPTION)\s*\((?:'(?:[^']|'')*'|[^';])*\)\s*;",
    re.DOTALL,
)
_FIN_HEADER = re.compile(rb"\bENDSEC\s*;")


def normalizar_step(contenido: bytes) -> bytes:
    """Return ``contenido`` with ``FILE_NAME(...)``/``FILE_DESCRIPTION(...)``
    in the HEADER section replaced by empty entities. Bytes after the first
    ``ENDSEC;`` (the DATA section) are left exactly as they are; a file
    without a HEADER section comes back unchanged."""
    fin = _FIN_HEADER.search(contenido)
    if fin is None:
        return contenido
    cabecera = _RE_ENTIDAD.sub(lambda m: m.group(1) + b"();", contenido[: fin.start()])
    return cabecera + contenido[fin.start():]


def calcular(
    geometria: bytes, es_step: bool, sidecars: Iterable[tuple[str, bytes | None]] = ()
) -> str:
    """``revision`` of a document: blake2b-128 (hex) over the (normalised,
    if STEP) geometry bytes and each present sidecar, length-prefixed so
    concatenations cannot collide."""
    h = hashlib.blake2b(digest_size=16)
    h.update(_PREFIJO)
    cuerpo = normalizar_step(geometria) if es_step else geometria
    h.update(len(cuerpo).to_bytes(8, "big"))
    h.update(cuerpo)
    for nombre, datos in sidecars:
        if datos is None:
            continue
        clave = nombre.encode()
        h.update(len(clave).to_bytes(2, "big"))
        h.update(clave)
        h.update(len(datos).to_bytes(8, "big"))
        h.update(datos)
    return h.hexdigest()
