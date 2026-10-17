# ADR 0022 — Deshacer y rehacer con cursor en el servidor

Date: 2026-10-17. Status: implemented (F24a2, branch `phase-24a2-deshacer`). Extends ADR 0014 (git como historial) and ADR 0003 (versionado); no reemplaza ninguno. Numeración: 0022 era libre en `feat/unified-intents`; si F23a (en paralelo) también toma 0022 al fusionar, renumerar uno de los dos antes del merge.

## Contexto

En la fase 24a el botón Deshacer quedó «Próximamente · 24a2». Una pila de deshacer en el cliente se desincroniza en cuanto el historial cambia por debajo (otra pestaña, el agente MCP, una restauración): repite pasos sobre una base distinta. Además, una restauración y un deshacer se guardaban igual en el servidor, así que no había forma de saber qué commits eran deshacer.

## Decisión

1. **Trailers de acción.** Los commits que escriben `POST .../deshacer`, `POST .../rehacer` y `POST .../restaurar` llevan `Forja-Accion: deshacer|rehacer|restaurar` y `Forja-Destino: <id>` (el id de 12 hex del paso que se restauró). Los commits anteriores no tienen trailer y cuentan como edición.
2. **Cursor sin estado propio.** `GET /documentos/{id}/deshacer` devuelve `{puede_deshacer, puede_rehacer, cabeza, destino_deshacer, destino_rehacer}`, calculado en `app/deshacer.py` a partir de los commits de `main` (función pura, sin disco). Reglas:
   - Cada edición y cada `restaurar` empujan su commit a la pila de deshacer y vacían la de rehacer.
   - Un `deshacer` saca el paso que revierte y empuja su propio commit a la pila de rehacer.
   - Un `rehacer` saca el paso de rehacer y lo devuelve a la pila de deshacer.
   - Así, deshacer repetido retrocede más allá de lo ya revertido, y rehacer solo funciona mientras los commits recientes son una cadena ininterrumpida de deshacer/rehacer.
   - El commit de creación («documento creado …») es la base, no un paso: su árbol ya es el estado inicial, y deshacerlo no cambiaría nada.
   - `cabeza` es el sha de la punta de `main`, leído una vez y usado para replayar el mismo historial.
3. **Escritura con precondición.** `POST .../deshacer` y `POST .../rehacer` reciben `{esperado: <cabeza>}` (`extra=forbid`, 422 si sobra algo). Bajo la misma cerradura (`parametros.bloqueo`) y la misma auth que `restaurar`:
   - `esperado` distinto de la cabeza actual → 409 «El historial cambió desde que se leyó…».
   - Sin paso en esa pila → 409 «No hay ninguna revisión anterior…» / «No hay nada que rehacer…».
   - Si no, restaura el `destino_*` como una restauración nueva (`_restaurar_documento_bloqueado` con `accion`). El historial nunca se reescribe.
   - La respuesta añade `accion`, `destino` y `cursor` (el estado nuevo).
4. **Cliente.** La barra (`toolbar-ui.js`) guarda el cursor por documento, lo relee al cambiar de pestaña, tras cada acción y cuando cambia el historial (`tabs.onHistorialCambio`). Envía la `cabeza` que vio. Un 409 muestra el mensaje del servidor y relee el cursor. Atajos: Ctrl+Z; Ctrl+Shift+Z y Ctrl+Y para rehacer.
5. **MCP.** Sin herramienta nueva (los tests fijan el conjunto). `leer_historial` sigue siendo solo lectura y `restaurar` no cambia su esquema. Deshacer y rehacer por MCP quedan fuera de esta fase: no se añadió acción alguna.

## Consecuencias

- El deshacer es fiable frente a cambios concurrentes: dos peticiones con la misma cabeza no se aplican las dos; la segunda recibe 409.
- Los commits antiguos (sin trailer) se tratan como edición: su historia sigue siendo deshacible y no hace falta migrar.
- El cursor ignora el historial legado (`.historial/`, ADR 0006) que no esté en git. Ese historial no se puede deshacer; sí se puede seguir restaurando como hasta ahora.
- Deshacer una restauración es posible; rehacerla, no (la restauración vacía la pila de rehacer).
- Cada deshacer o rehacer crea un commit: el historial crece igual que con cualquier cambio.

## Alternativas descartadas

- **Pila de deshacer en el cliente** (versión previa de 24a, revertida): se desincroniza con el historial.
- **Estado mutable en el servidor** (un puntero guardado): otro estado que puede divergir del historial; el cursor derivado no tiene esa falla.
- **Leer `Forja-Destino` para recorrer la pila**: la pila sale solo de los `accion`; `Forja-Destino` es informativo y el cursor no lo lee.
