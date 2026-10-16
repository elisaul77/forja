"""Immutable, bounded reference photos; referenced by hash from design intent."""

from __future__ import annotations
import hashlib
import io
import re
from PIL import Image, ImageOps, UnidentifiedImageError
import design_state

MAX_BYTES = 6 * 1024 * 1024


def directory(doc_id):
    design_state.path(doc_id)
    return design_state.ROOT / ".design-assets" / doc_id


def path(doc_id, key):
    if not re.fullmatch(r"[a-f0-9]{64}", key):
        raise ValueError("Referencia visual inválida")
    return directory(doc_id) / (key + ".png")


def store(doc_id, data):
    if len(data) > MAX_BYTES:
        raise ValueError("Foto demasiado grande (máximo 6 MB)")
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.width * source.height > 16000000:
                raise ValueError("Foto supera 16 megapíxeles")
            source.load()
            clean = ImageOps.exif_transpose(source).convert("RGB")
            clean.thumbnail((1600, 1600))
            output = io.BytesIO()
            clean.save(output, format="PNG")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("No se pudo leer una imagen válida") from exc
    png = output.getvalue()
    key = hashlib.sha256(png).hexdigest()
    root = directory(doc_id)
    root.mkdir(parents=True, exist_ok=True)
    destination = path(doc_id, key)
    if not destination.exists():
        if len(list(root.glob("*.png"))) >= 64:
            raise ValueError("Límite de 64 referencias visuales por documento")
        destination.write_bytes(png)
    return {
        "id": key,
        "width": clean.width,
        "height": clean.height,
        "url": f"/documentos/{doc_id}/design/images/{key}",
    }
