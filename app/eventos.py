"""In-memory event hub behind ``GET /eventos`` (F11.1, ADR P04).

The server pushes document changes to every open viewer over Server-Sent
Events. Each event carries only ``{id, revision, cambios?}`` — never text, names or
geometry — so the stream stays harmless even though the port is reachable
from the LAN without a token (P-2).

- ``publicar`` is safe from any thread: the sync routes run in Starlette's
  threadpool, so the event is handed to each subscriber's own event loop with
  ``loop.call_soon_threadsafe`` (never touching an ``asyncio.Queue`` from a
  foreign thread).
- Every subscriber has a bounded queue; on overflow it receives a
  ``resincronizar`` marker so the viewer reconciles from the current state.
- ``flujo`` is an async generator: an open SSE connection costs a coroutine,
  never a threadpool worker (T14). A comment line every ``LATIDO_S`` seconds
  keeps proxies from closing the idle connection and lets the server notice
  a gone client.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import AsyncIterator, Callable
from typing import Any

logger = logging.getLogger(__name__)

TIPOS = frozenset({
    "documento_creado",
    "documento_actualizado",
    "documento_eliminado",
    "construyendo",
    "construccion_fallida",
    "miniatura_lista",
    "construccion_terminada",
    "anotaciones_actualizadas",
    "ensamble_actualizado",
    "resincronizar",
})

LATIDO_S = 15.0
COLA_MAXIMA = 64
SUSCRIPTORES_MAXIMOS = 32
# Milliseconds the browser waits before reconnecting (SSE `retry:` field).
REINTENTO_MS = 3000


class _Suscriptor:
    __slots__ = ("loop", "cola")

    def __init__(self, loop: asyncio.AbstractEventLoop, maximo: int) -> None:
        self.loop = loop
        self.cola: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=maximo)

    def poner(self, evento: dict[str, Any]) -> None:
        """Runs on the subscriber's own loop: last-wins on overflow."""
        if self.cola.full():
            while not self.cola.empty():
                self.cola.get_nowait()
            self.cola.put_nowait({"tipo": "resincronizar", "id": "", "revision": None})
            return
        self.cola.put_nowait(evento)


class Hub:
    def __init__(self, cola_maxima: int = COLA_MAXIMA) -> None:
        self._cola_maxima = cola_maxima
        self._suscriptores: set[_Suscriptor] = set()
        self._lock = threading.Lock()

    def suscribir(self) -> _Suscriptor:
        sub = _Suscriptor(asyncio.get_running_loop(), self._cola_maxima)
        with self._lock:
            if len(self._suscriptores) >= SUSCRIPTORES_MAXIMOS:
                raise RuntimeError("demasiadas conexiones en vivo")
            self._suscriptores.add(sub)
        return sub

    def desuscribir(self, sub: _Suscriptor) -> None:
        with self._lock:
            self._suscriptores.discard(sub)

    def cantidad(self) -> int:
        with self._lock:
            return len(self._suscriptores)

    def publicar(self, tipo: str, doc_id: str, revision: str | None = None,
                cambios: list[str] | None = None) -> None:
        """Queue ``{tipo, id, revision, cambios?}`` for every subscriber. Never raises:
        a broken event must never fail the commit that produced it."""
        if tipo not in TIPOS:
            raise ValueError(f"tipo de evento desconocido: {tipo}")
        evento = {"tipo": tipo, "id": doc_id, "revision": revision}
        if cambios:
            evento["cambios"] = cambios
        with self._lock:
            suscriptores = list(self._suscriptores)
        for sub in suscriptores:
            try:
                sub.loop.call_soon_threadsafe(sub.poner, evento)
            except RuntimeError:  # loop already closed: the client is gone
                self.desuscribir(sub)

    async def flujo(self, latido_s: float | None = None,
                    estado_inicial: Callable[[], list[dict[str, str]]] | None = None,
                    suscriptor: _Suscriptor | None = None) -> AsyncIterator[str]:
        """SSE text for one client until it disconnects."""
        espera = LATIDO_S if latido_s is None else latido_s
        sub = suscriptor if suscriptor is not None else self.suscribir()
        try:
            yield f"retry: {REINTENTO_MS}\n: conectado\n\n"
            if estado_inicial is not None:
                yield "event: hola\ndata: " + json.dumps({"documentos": estado_inicial()}) + "\n\n"
            while True:
                try:
                    evento = await asyncio.wait_for(sub.cola.get(), timeout=espera)
                except asyncio.TimeoutError:
                    yield ": latido\n\n"
                    continue
                datos = json.dumps({k: v for k, v in evento.items() if k != "tipo"})
                yield f"event: {evento['tipo']}\ndata: {datos}\n\n"
        finally:
            self.desuscribir(sub)


hub = Hub()


def publicar(tipo: str, doc_id: str, revision: str | None = None,
            cambios: list[str] | None = None) -> None:
    """Module-level shortcut over the process-wide hub (never raises)."""
    try:
        hub.publicar(tipo, doc_id, revision, cambios)
    except Exception:  # noqa: BLE001 - events are best-effort by contract
        logger.exception("no se pudo publicar el evento %s", tipo)
