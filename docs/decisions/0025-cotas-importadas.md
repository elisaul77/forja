# ADR 0025 — Cotas vivas de documentos importados (F23b): editar la medida reconocida

Date: 2026-10-17. Status: implemented in F23b (branch `phase-23b-cotas-mallas`). Extends ADR 0023 (cotas vivas de scripts, F23a) and ADR 0021 (descriptor de cota). Reuses ADR 0003 (versionado) and ADR 0022 (cursor de deshacer, sin cambios). Supersedes the F23a rule «documento importado = todo solo lectura» (ver «Desviaciones»).

## Decisión

Una cota del plano de un documento **sin script** (STEP importado o malla STL) es editable cuando una característica reconocida la produce. Tres operaciones, una por característica:

| Característica reconocida | Cota (`tipo`) | `operacion` | Qué hace |
|---|---|---|---|
| Grupo de cilindros cerrados de eje global (un agujero o `n` iguales) | `diametro` | `rellenar_y_taladrar` | Rellena cada agujero con un cilindro del diámetro viejo y retaladra el nuevo en el mismo eje y la misma profundidad. Edita el grupo entero y lo dice (`afectadas: n`). |
| Dos caras planas paralelas que se enfrentan a través de material | `distancia` (nuevo tipo, ver abajo) | `mover_cara` | Parte la pieza a media altura, desplaza la mitad superior y cierra el hueco con la sección extruida (estirar o comprimir). La traslación se resuelve por biseccion hasta que la distancia medida vuelve a la pedida (±0,01 mm). |
| Medida total por eje (bbox) | `tamano_total` | `escalar_eje` | Escala no uniforme de ese eje solo, con el mínimo fijo. Vale también para una malla STL (se escalan los vértices). |

Cualquier otra cota sale `solo_lectura: true` con `razon`:

- `posicion` de un agujero: «posición no editable: se edita el diámetro del agujero».
- `distancia` en un documento de varios sólidos: «varios sólidos: el estiramiento solo aplica a una pieza».
- Malla STL, cualquier cota que no sea medida total: «malla sin B-rep».
- Un agujero oblicuo, un chaflán o un redondeo no producen cota (F22 los omite o son arcos parciales), así que no hay nada que editar.

### `distancia`: tipo nuevo en el listado, no en el plano

F22 no dibuja distancias entre caras, así que `distancia` **no sale en el plano dibujado**. El listado de cotas de un documento importado añade un descriptor por cada par de caras paralelas reconocido (`app/cotas_geometria.py: parallel_face_descriptors`). Su `id` es `distancia:{eje}:{valor:.3f}:{lo:.3f}` (determinista por revisión). Es el único tipo que el listado añade a lo que F22 dibuja. Lo que no se dibuja no se anuncia como cota del plano: el listado lo marca como `origen: "reconocido"`, `parametro: null`.

## Reglas

- **Listado, no ejecución.** `GET /documentos/{id}/cotas` clasifica con `cotas.classify_imported` (pura) y añade `ancla` (coordenadas del modelo en mm) a cada cota editable: el borde del bbox para una medida total, el centro del primer agujero a media profundidad para un grupo, el punto medio del hueco para una distancia. La clasificación de scripts (ADR 0023) no cambia.
- **Una cota por edición.** La petición de un documento importado lleva exactamente un `{id: valor}`. Las ids cambian con la revisión que crea la edición; varias en una petición dan 422 «las cotas de un documento importado se editan una a una».
- **Validación del valor.** Número finito > 0; entre 0,1 y 10 veces la medida actual; igual a la actual → 422 «no hay cambio que guardar». Id desconocido 404; `solo_lectura` 422 con `razon`; `extra="forbid"` y revisión obligatoria (409 si no coincide), como en F23a.
- **Verificar antes de escribir.** Toda la geometría se calcula en memoria. El resultado se comprueba: válido; mismo número de sólidos; el volumen cambia lo que dice el análisis (agujero: π(r₀² − r₁²)·L por agujero; escalado: V·(f − 1); estirado: área de la sección · Δ), dentro del 1 %; el bbox solo se mueve en el eje editado (agujero: no se mueve); la distancia o el diámetro vuelven a medirse dentro de 0,01 mm. Un fallo es 422 `{error: true, mensaje}` y **nada se escribe**.
- **Pared.** Un agujero re-taladrado debe dejar al menos 0,1 mm de material alrededor (24 puntos de la circunferencia de radio `r + 0,1` a media profundidad, dentro del sólido propietario). Si no, 422 con `razon: "el agujero rompe la pared"`.
- **Escritura.** Solo después de verificar: el STEP (o el STL) nuevo y su sidecar `solidos.json` se escriben en temporales y se re-analizan; luego se toma el snapshot del estado anterior (ADR 0003: `restaurar` lo devuelve), se renombran los temporales sobre los reales, se actualiza el registro, la caché de mallas y de ramas, y se publica **una** revisión. Un fallo de E/S tras el snapshot restaura los bytes previos y responde 422.
- **Nombres de sólidos y notas.** Los sólidos conservan su nombre por posición (`solids.entradas_tras_edicion`); las notas con `referencia` se re-resuelven contra la forma nueva (`_reresolver_notas`, extraído de la ruta de script).
- **Propietario del sólido.** Un agujero se edita solo en el sólido cuya pared lo rodea; los demás sólidos del documento no se tocan.
- **Restricción de diseño.** Un documento con piezas protegidas en Diseño sigue el mismo guardián (`design_state.require_unprotected_legacy_edit`) que el camino de script: 409.

## Límites de seguridad (entrada de la operación)

La edición construye geometría a partir de un número de la petición, así que se acota:

- Valor: finito, positivo, entre 0,1× y 10× la medida actual (validación antes de cualquier geometría).
- Caras: un B-rep de más de `MAX_BREP_FACES` (5 000) caras no se edita; una malla de más de 1 000 000 de triángulos, tampoco.
- Agujeros por edición: como mucho 40 (`MAX_HOLES_PER_EDIT`).
- Presupuesto: cada operación booleana o sondeo de la edición gasta del `Presupuesto` de `app/checks/barrido.py` (200 unidades, 30 s). Agotado → 422 «presupuesto de cálculo agotado». Los fallos del núcleo se convierten en 422 con su mensaje, nunca se tragan.
- La biseccion tiene 40 pasos máximos y un rango acotado (la distancia tiene que quedar positiva).

## Por qué

- **Re-taladrar, no reconstruir.** Eli corrige una pieza impresa cambiando el número de la cota; reconstruir desde una malla o un B-rep importado pierde la geometría del usuario. Rellenar y volver a taladrar toca solo el agujero.
- **Estirar con mitad de la pieza.** Partir a media altura evita caras coincidentes entre los dos trozos: el corte cae dentro del material, y la sección es la de la pieza en ese plano (válida si la pieza es prismática entre las dos caras; si no, la verificación de volumen lo rechaza en vez de dejar una geometría dudosa).
- **Bisección con la traslación analítica como primer intento.** El primer intento ya es el exacto (D − H); la bisección corrige el residuo de medida si hace falta y acota el coste.
- **Verificar antes de escribir.** Una cota que no vuelve a su valor no deja revisión que deshacer a mano (la misma garantía que ADR 0023, aplicada a geometría).

## Desviaciones frente al plan

1. **«Click en el plano» no existe en la app.** El plano de F22 es una descarga (SVG/PDF/DXF); no hay vista de plano en el visor. El clic se hace sobre la etiqueta de la cota en el visor 3D, y el campo numérico de esa cota en el panel «Parámetros» recibe el foco. El editor numérico vive en el panel, no junto a la etiqueta.
2. **`distancia` nuevo en el listado** (ver arriba). El plano no la dibuja.
3. **`choques_nuevos` no implementado.** El plan lo pedía como dato informativo de colisiones tras la edición. No se calcula en esta fase.
4. **Malla con partes.** Un STL con sidecar de partes escala cada parte con el mismo origen y reescribe el sidecar (`mesh_parts.combine`), como `incorporar`. Un STL sin sidecar no gana uno.
5. **Biseccion con traslación analítica.** El plan describía la bisección como el mecanismo de resolución. La traslación exacta ya cumple el objetivo en la práctica; la bisección queda como corrector del residuo.

## Pendiente

- Cotas por eje de **posiciones** de agujeros (mover el centro): fuera de alcance, solo lectura.
- Agujeros oblicuos, chaflanes, redondeos, roscas, formas libres: solo lectura, con su razón.
- Distancias entre caras no paralelas o en documentos de varios sólidos: solo lectura.
- Verificación visual en navegador (headless Chrome, `docs/FRONTEND-VERIFICATION.md`): no ejecutada en esta fase; se hizo en la revisión. `?abrir=` cuelga el Chrome headless: el EventSource de `/eventos` no termina la carga, así que la captura se hace abriendo la app sin `?abrir=`.

## Pruebas

- `tests/test_cotas_importadas.py`: clasificación por operación (STEP y STL); re-taladro pasante, ciego y de grupo con volumen analítico; pared rota (422 con `razon`, nada escrito byte a byte); estirado y comprimido por distancia; escalado de un eje; `posicion` y valores fuera de límites (422, nada escrito); igual al actual; revisión obsoleta (409); varias cotas (422); verificación forzada fallida; presupuesto agotado; tope de caras; fallo de renombrado (rama y archivos vuelven a su estado previo); nombres de sólidos; concurrencia (una 200, una 409); MCP `parametros(cotas=...)` por el mismo endpoint; ancla.
- `tests/test_cotas_parametros.py`: los dos tests de «documento importado solo lectura» se actualizan al nuevo comportamiento (ver la desviación de F23a abajo).
- `tests/web/cotas.test.mjs`: texto de operación, validación geométrica (suelo 0,1×, cambio nulo), etiquetas del visor y guarda contra HTML.

## Desviación frente a F23a (documentada aquí)

F23a cerró la regla «documento importado = todo solo lectura» con la razón «no ligada a un parámetro del script». F23b la sustituye por la regla de características reconocidas. Los dos tests de F23a que la fijaban se actualizan: el documento importado sin script ya no tiene cotas solo lectura, y la malla STL edita su medida total. La cota sin característica reconocida sigue saliendo solo lectura con su razón.
