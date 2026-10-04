# ADR-0012: revisión de geometría y eventos del visor

Fecha: 2026-10-03

## Contexto

El navegador mantenía la malla que había cargado al abrir un documento. Los cambios hechos por un agente solo aparecían después de recargar la página, perdiendo la cámara y la selección. Una revisión basada en la hora de modificación produciría avisos falsos cuando un script vuelve a exportar la misma forma.

## Decisión

Cada documento expone una `revision` calculada con BLAKE2b sobre el STEP o STL y el desglose geométrico. En STEP se normalizan `FILE_NAME` y `FILE_DESCRIPTION` del encabezado para que la hora de exportación no cambie la revisión. La revisión viaja en la ficha, la lista y `X-Forja-Revision` de `/malla`.

`GET /eventos` mantiene una conexión SSE asíncrona por página y envía `hola` con las revisiones actuales, seguida de avisos compactos `{id, revision, cambios}`. El servidor tiene un solo proceso; publica después de confirmar cada escritura. Cada cliente tiene una cola limitada; si se llena, recibe `resincronizar`. Hay un máximo de 32 conexiones. Al reconectar, el navegador compara revisiones y recupera la malla que le falte.

El visor actualiza la malla visible sin mover la cámara ni cerrar paneles. Un cambio que llega durante una edición se aplica al terminarla. Los documentos inactivos se actualizan al activarlos.

## Consecuencias

La conexión consume una tarea asíncrona, sin ocupar un hilo del grupo que atiende operaciones CAD. La publicación de eventos es auxiliar: un error del canal no revierte un documento ya guardado. Esta implementación depende de un único proceso de servidor; si se añaden varios workers hará falta un bus compartido. El navegador conserva los datos durante una desconexión y compara el estado al reconectar.
