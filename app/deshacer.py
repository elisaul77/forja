"""Cursor de deshacer y rehacer (fase 24a2, ADR-0022).

Todo sale del historial de `main` (`git_store.log`): ningún estado mutable
extra. Cada commit guarda el estado que reemplazó, así que replayar la
historia de más antigua a más reciente reconstruye las dos pilas:

- Una edición o un `restaurar` empuja su commit a la pila de deshacer y
  vacía la de rehacer.
- Un `deshacer` saca el paso de deshacer que revirtió y empuja su propio
  commit (el estado que dejó) a la de rehacer.
- Un `rehacer` saca el paso de rehacer y lo devuelve a la de deshacer.

Por eso deshacer repetido retrocede más y rehacer solo sirve mientras los
commits más recientes sean una cadena ininterrumpida de deshacer/rehacer.
El commit de creación («documento creado …») es la base del documento y
nunca es un paso deshacible: su árbol ya es el estado inicial.

Funciones puras: sin disco, sin red, sin git.
"""
from __future__ import annotations

from typing import Any

ACCION_DESHACER = "deshacer"
ACCION_REHACER = "rehacer"
PREFIJO_CREACION = "documento creado"


def estado(entradas: list[dict[str, Any]], cabeza: str | None) -> dict[str, Any]:
    """Cursor de un historial. ``entradas`` van de la más antigua a la más
    reciente (`git_store.log`); cada una trae ``id``, ``mensaje`` y, si
    procede, ``accion`` (``deshacer``, ``rehacer`` o ``restaurar``).
    ``cabeza`` es el sha de la punta de la rama, lo que el cliente debe
    devolver como ``esperado``."""
    pila_deshacer: list[dict[str, Any]] = []
    pila_rehacer: list[dict[str, Any]] = []
    for entrada in entradas:
        accion = entrada.get("accion")
        if accion == ACCION_DESHACER:
            # Un deshacer solo se acepta con un paso debajo (lo comprueba la
            # ruta antes de escribir), así que el pop nunca lo deja vacío.
            if pila_deshacer:
                pila_deshacer.pop()
            pila_rehacer.append(entrada)
        elif accion == ACCION_REHACER:
            if pila_rehacer:
                pila_rehacer.pop()
            pila_deshacer.append(entrada)
        elif entrada["mensaje"].startswith(PREFIJO_CREACION):
            continue
        else:
            pila_deshacer.append(entrada)
            pila_rehacer = []
    return {
        "cabeza": cabeza,
        "puede_deshacer": bool(pila_deshacer),
        "puede_rehacer": bool(pila_rehacer),
        "destino_deshacer": pila_deshacer[-1]["id"] if pila_deshacer else None,
        "destino_rehacer": pila_rehacer[-1]["id"] if pila_rehacer else None,
    }
