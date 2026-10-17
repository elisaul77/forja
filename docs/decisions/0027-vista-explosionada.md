# ADR 0027 — Vista explosionada (F19): separar las piezas solo para mirar

Date: 2026-10-17. Status: implemented in F19 (branch `phase-19-explosionado`). Reuses ADR 0001 (núcleo B-rep), ADR 0010 (ensamblajes y su estado), ADR 0012 (eventos en vivo, sin cambios) and ADR 0020 (barrido, sin cambios). Numbering: 0026 is reserved for 24b.

## Decisión

Una vista explosionada es **solo de display**: desplaza cada pieza con nombre alejándola de su conjunto. No cambia la geometría guardada, el STEP, los sidecars, el historial, el registro ni la `revision`.

- **Agentes:** `captura(id, ..., explosion=0.0)` (MCP) y `GET /documentos/{id}/captura?explosion=`. Parámetro del endpoint que ya existe; **ninguna herramienta nueva** (los tests fijan el conjunto de herramientas).
- **Visor:** botón «Explosionar» en la barra de vista 3D con un deslizador «Separación» (0 a 100 %). La acción «Vista explosionada» de la cinta (`ens-explosionada`, grupo Vista) delega en ese botón.

## Reglas de desplazamiento

La función pura `app/explosion.py: desplazamientos(cajas, articulaciones, factor)` es el oráculo. `app/web/static/js/explosion.js` aplica el mismo algoritmo a las cajas de las mallas del visor. La tabla `tests/fixtures/explosion_offsets.json` fija la paridad: el test de Python y el de Node leen la misma tabla con tolerancia 1e-9.

- **Dirección.** Una pieza hija de una articulación `giro` o `deslizamiento` se aleja de su padre a lo largo del eje de la articulación. El signo es `-1` si el eje apunta hacia el padre, y `+1` en otro caso (producto punto 0 incluido). Las articulaciones `fijo` no dirigen nada. Las demás piezas se alejan del centro del conjunto. Una pieza cuyo centro coincide con el del conjunto va a `+Z`, sin NaN.
- **«Centro» = centro de la caja delimitadora.** Las entradas de `solidos` no traen centroide. Las piezas que comparten nombre (un sólido partido en fragmentos) se unen en una sola caja.
- **Distancia.** Cada pieza tiene una semiextensión `h` a lo largo de su dirección. Se ordenan por su proyección sobre esa dirección (empate por nombre). El acumulado `G_i = G_{i-1} + h_{i-1} + h_i` separa cada par consecutivo por la suma de sus semiextensiones. El desplazamiento es `factor · 0,5 · D · G_i / G_max`, con `D` la diagonal de la caja del conjunto. La pieza más alejada se mueve exactamente `factor · D / 2`.
- **Garantía.** La separación mínima por pares es una **meta de display**, no una garantía: si el conjunto tiene piezas muy grandes, dos piezas pueden seguir tocándose. El ADR no promete ausencia de interpenetración.
- **Determinismo.** El resultado no depende del orden de entrada. `factor = 0` devuelve todo a cero (sin `-0`).

## Validación y límites

- `factor` finito, número real (no booleano), entre 0 y 1. Fuera de rango, `NaN` o `inf` → 400 con «el factor de explosión debe ser un número entre 0 y 1». Es el 400 del endpoint, no el 422 por defecto de FastAPI, para que el mensaje sea en español.
- Solo documentos STEP con piezas con nombre. Con `explosion > 0` en STL → 400 «requiere un documento STEP con piezas con nombre». Con nombres inciertos → 400 (no se explosiona una pieza con nombre inventado).
- Cajas finitas y no invertidas; como máximo 500 piezas (`MAX_PIEZAS`). Articulaciones: se reutiliza la validación de `assemblies` (máximo 60, claves cerradas).
- Se reutilizan sin cambios `LIMITE_TRIANGULOS` (150 000) y `LIMITE_PNG_BYTES` (150 KB). El encuadre se ajusta solo, porque la escena crece con la separación.
- Sin rutas de archivo nuevas: no es un riesgo de seguridad nuevo.

## Compatibilidad

- `explosion=0` (por defecto) es **byte a byte** igual que la captura de antes: mismo PNG (SHA-256) y mismo `X-Forja-Captura`. El test de regresión está en `tests/test_captura_colisiones_exportar.py`.
- El cliente MCP envía el parámetro solo si es distinto de 0.
- Con `explosion > 0` el `X-Forja-Captura` añade ` - explosion 50 %` (ASCII, por la cabecera).

## Interacción con la animación y con el visor

- El visor escribe `mesh.position` de cada pieza; nunca toca `mesh.matrix`. La animación de ensamblajes (`animation.js`) fija `mesh.matrix` y `matrixAutoUpdate = false`, así que **la pose de la animación manda mientras dura**. Al volver al modo «cad» (`resetPose`) la explosión vuelve a verse.
- El botón no se activa fuera del modo «cad»: muestra «Sal del modo animación para explosionar las piezas» en la franja de estado.
- La explosión no persiste: no hace `PATCH` ni `POST`, y la recarga de la malla (`cargarMallaSTL`) la vuelve a aplicar con el factor vigente.
- Las articulaciones se leen en cada aplicación desde el estado del ensamblaje de la pestaña.

## Lo que no se hace (y por qué)

- **`explosion_auditada`** (la comprobación de interpenetración nueva con `pose_clearance.check_scene`) se omite. El plan la marca como opcional y el uso principal (ver piezas ocultas) no la necesita. Si se quiere, se añade como cabecera informativa en una fase posterior, sin cambiar la forma de la respuesta.
- **Persistir el estado** de la vista explosionada: se descarta. Es estado de visualización de la pestaña.
- **Cajas del visor frente a cajas del servidor.** El visor usa la caja de los triángulos de su malla; `captura` usa la caja OCC de `solidos.json`. Pueden diferir en la tolerancia de teselado. Por eso la paridad se prueba sobre la tabla común y no sobre dos renders.

## Consecuencias

- Cambios de caché (`?v=` único por módulo, verificado con grep): `explosion.js?v=1` (nuevo, solo en `viewer.js`), `viewer.js?v=design1-22` (en `tabs.js`), `tabs.js?v=52` (en `app.js`), `toolbar-actions.js?v=8` y `toolbar-ui.js?v=9` (en `toolbar-ui.js` y `app.js`), `app.js?v=55` (en `index.html`).
- `render.renderizar_png` acepta `desplazamientos` (copias, sin mutar mallas cacheadas).
- Es display: no hay test de geometría sobre la explosión, porque no cambia nada guardado (tests de `explosion=0` y de no modificar el documento).
