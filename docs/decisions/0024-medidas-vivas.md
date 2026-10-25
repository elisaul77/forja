# ADR 0024 — Medidas vivas: pares más cercanos por revisión

Date: 2026-10-17. Status: implemented (F21, branch `phase-21-medidas-vivas`). Reutiliza el pase de pares de `percibir` (ADR-0001 y Fase 5E); no cambia ningún contrato existente. Numeración: 0022 y 0023 están tomados; 0024 es la siguiente libre.

## Contexto

La cinta de Inspeccionar tiene el botón Medir (F21 en el plan). Eli quiere ver en el visor, con el documento abierto, los números de contacto, choque y holgura entre sólidos, y que se actualicen cuando el agente o un parámetro cambian la geometría. Esos números ya existen en `percibir` y `check_colisiones`: lo que falta es una vista que no los recalcule en el navegador (las cajas delimitadoras dan errores de varios milímetros) ni los recalcule en cada tick de un deslizador.

## Decisión

1. **Una sola fuente.** `app/medidas.py` llama a `percepcion._pares_cercanos_con_tope` (el mismo pase exacto de OCC, con el mismo margen `PROXIMIDAD_MM_POR_DEFECTO` y las mismas tolerancias de `checks/colisiones`). Los choques salen de `percepcion._detectar_choques`. Un número del panel es el que diría `percibir` para ese par; un test de paridad lo fija contra `percibir` (holgura de 3 mm) y `check_colisiones` (volumen del choque).
   - `_pares_cercanos_por_nombre` pasa a ser un envoltorio de `_pares_cercanos_con_tope` sin tope: la salida de `percibir` no cambia.
2. **Una revisión por respuesta.** Los números se calculan para una `revision` (la de `documents._revisiones`). La ruta la relee después del cálculo: si el documento cambió, responde 409 y no guarda nada en caché. La respuesta trae la misma `revision` en el cuerpo y en `X-Forja-Revision`. El cliente descarta cualquier respuesta que no sea la última pedida o que no sea de la revisión vigente (`aceptarRespuesta` en `medidas.js`).
3. **Caché por revisión.** Solo se guarda la última revisión de cada documento. Una lectura repetida de una revisión sin cambios no ejecuta OCC (`medidas.computos()` lo cuenta). `DELETE` del documento borra su entrada (`medidas.olvidar`).
4. **Límites.**
   - `TOPE_CALCULOS = 500` llamadas exactas de distancia por cálculo, después de la preselección por cajas. Si se agota, la respuesta lleva `completo: false` y el panel lo dice; no se inventan pares.
   - `top` (la única entrada) entre 1 y `TOP_MAX = 100`, por defecto 20; el resto se cuenta en `ocultos`. Fuera de rango, 422 de FastAPI antes de tocar la geometría.
   - Cada documento se mide bajo su propia cerradura `parametros.bloqueo` (la misma que usan las reconstrucciones de parámetros y las notas): el cálculo corre en el hilo del pool de FastAPI, no en el bucle de eventos.
5. **Vocabulario.** `estado` ∈ {`choque`, `contacto`, `holgura`}. `choque` si el volumen de intersección supera 0.5 mm³ (`TOLERANCIA_MM3_POR_DEFECTO`); `contacto` si la distancia es ≤ 0.05 mm (`TOLERANCIA_TOQUE_MM`); `holgura` en otro caso. Distancias en mm con 3 decimales. Los nombres de sólido son datos no confiables: el cliente los pinta con `textContent` (un test comprueba que no se usa `innerHTML`).
6. **Sin herramienta MCP nueva.** Los tests fijan el conjunto de herramientas; el agente sigue usando `percibir`.
7. **Sin evento nuevo.** El panel reutiliza `documento_actualizado` (con `revision`), `ensamble_actualizado` y `construyendo` de `/eventos` (ADR-0012). `construyendo` pone las cifras en gris hasta que llega la revisión nueva.

## Seguridad

- Lectura sin token, como `/percibir` y `/malla`: expone números derivados de la geometría, la misma exposición que ya tiene `/malla`. No expone rutas, ni el contenido del script, ni secretos.
- Ids: `notes._doc_id_valido` y pertenencia al registro antes de tocar ningún archivo o cerradura: un id inventado no crea entradas en `parametros._bloqueos` (el fallo de la fase 6.0).
- Entradas acotadas: el único parámetro tiene rango; el coste del cálculo tiene tope fijo; la caché guarda una entrada por documento.
- Los nombres de sólido llegan al DOM como texto.
- Sin dependencias nuevas.

## Consecuencias

- Las cifras del panel pueden ser parciales en ensamblajes grandes (`completo: false`): se dice explícitamente, nunca se muestran como completas.
- Las poses de un ensamblaje no cambian la revisión del documento, así que el panel no recalcula con una pose: mide la geometría guardada del documento. Medir una pose exige geometría posada y queda fuera de F21.
- La vista no reemplaza `check_colisiones` para decisiones de impresión: sigue siendo la misma medida del mismo kernel.
