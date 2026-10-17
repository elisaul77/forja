# ADR 0023 — Cotas vivas (F23a): editar una cota de un script editando su parámetro

Date: 2026-10-17. Status: implemented in F23a, revisión 1 (branch `phase-23a-cotas-vivas`). F23b (documentos importados STEP/STL: clic en el plano y en el visor, cilindros, caras paralelas, escala por eje) queda para una fase aparte. Supersedes nothing; extends ADR 0021 (descriptor de cota) y ADR 0003 (versionado). No relacionado con ADR 0022 (cursor de deshacer).

## Decisión

Cada cota del plano (F22) que sale de una clave de `PARAMETROS` de un documento creado por script es un control vivo. Editarla traduce el valor a `{clave: valor}` y lo aplica por el mismo camino que `POST /documentos/{id}/parametros`, pero **verificado antes de escribir**: el script se ejecuta, la geometría resultante se mide en memoria y solo si cada cota editada vuelve al valor pedido (±0,01 mm) se confirma la escritura.

- **Listado.** `GET /documentos/{id}/cotas` (sin token, solo lectura) devuelve `{revision, cotas}`. Cada cota trae las claves del descriptor de F22 más `operacion`: `"parametro"` si es editable (con `min`/`max` del parámetro), o `solo_lectura: true` con `razon`. Razones: `"no ligada a un parametro del script"`, `"malla sin B-rep"` y `"ligada por valor: el script no lee PARAMS['clave']"`.
- **Edición.** `POST /documentos/{id}/cotas/aplicar` (con token, `extra="forbid"`) con `{revision, cotas: {id_cota: valor}}`. Éxito: el resumen de `parametros` más `editadas`: por cota, `valor_antes`, `pedido_mm`, `valor_despues`, `error_mm` y `no_sigue_al_parametro` cuando aplica.
- **Rechazo por no seguir.** 422 con cuerpo `{"error": true, "mensaje": "..."}` en español: nombra la cota y el parámetro y dice que el script no mueve esa cota con ese valor. Nada se escribe.
- **MCP.** Sin herramienta nueva. `parametros(id, cotas={...}, revision=None)` (mutuamente exclusiva con `valores`). El cliente MCP muestra el `mensaje` del 422 tal cual. Los ids y su `operacion` salen de `exportar(id, "svg", detalle=true)`.
- **Visor.** Sección «Cotas del plano» dentro del panel «Parámetros» (solo documentos con script). Campo numérico por cota editable, texto «Solo lectura: razón» en las demás. La acción «Cotas» de la barra abre ese panel y lleva el foco a la sección; sin PARAMETROS dice por qué en la franja de estado.

## Reglas

- **Revisión obligatoria.** La edición lleva la revisión en la que se leyeron los ids. Si no coincide, 409 y nada cambia. `parametros(cotas=...)` sin `revision` lee la vigente antes de escribir.
- **Atomicidad de la decisión.** La revisión, el listado, la verificación y la escritura van bajo `parametros.bloqueo(doc_id)`. Dos ediciones con la misma lectura: una 200, la otra 409.
- **Verificar antes de escribir.** El script corre a un fichero temporal de staging. Con la geometría importada en memoria se calcula el listado de cotas con los valores **nuevos** y se compara con el pedido (`cotas.tras_aplicar`). Si alguna cota editada no vuelve a su valor (`no_sigue_al_parametro`), `cotas.exigir_seguimiento` lanza `CotaNoSigue` antes de cualquier escritura.
- **Qué cambia en disco si se rechaza.** Nada: no hay snapshot, ni commit en la rama, ni `parametros.json`, `solidos.json`, notas, materiales o STEP tocados, ni `git_store`/rama modificados. El cursor de deshacer (ADR 0022) queda igual, porque solo lee el historial. Lo prueba `test_cota_que_no_sigue_al_parametro_se_informa` comparando bytes de los sidecars y del repositorio git antes y después.
- **Camino de fallo previo (ADR 0003), definido por caso.**
  - *Rechazo por no seguir:* no toma snapshot preventivo. Es el único caso que no deja rastro en el historial.
  - *Error del script o núcleo que no produce sólidos (422 `script invalido`), en el camino de cotas:* el snapshot preventivo se toma igual que en `/documentos/script` y `parametros`; se deja una entrada sin cambio de geometría. Lo fija `test_fallo_del_script_en_cotas_conserva_la_instantanea_preventiva` y `test_actualizar_documento_por_script_kernel_invalido_no_corrompe_estado`.
  - *Camino de `parametros` sin verificación:* sin cambios.
  - Implementación: `_actualizar_documento_desde_script(..., verificar=...)` difiere el snapshot hasta que la verificación pasa; sin `verificar`, el snapshot se toma antes de ejecutar el script, como siempre.
- **Una revisión por edición.** Solo si la verificación pasa y el script corre. Escritura y verificación usan el mismo script y la misma geometría.
- **Validación antes de escribir.** Id desconocido 404; solo lectura 422 con `razon`; valor no finito, no positivo o booleano 422; sin `max` del parámetro, más de 10 veces la medida actual 422; dos cotas de una misma clave con valores distintos 422; más de 40 cotas 422. Fuera de min/max 422 (lo dice `aplicar_valores`).
- **Vínculo por valor.** F22 liga una cota a una clave cuando el valor coincide. El listado añade una comprobación estática: si el script nunca lee `PARAMS["clave"]` (`cotas.claves_leidas`), la cota es de solo lectura «ligada por valor». Ver «Límites».
- **Malla.** Un STL no tiene script: todas sus cotas son de solo lectura con `razon: "malla sin B-rep"`.
- **Caché del listado.** `GET .../cotas` y la lectura previa de una edición se cachean por `(documento, revisión, entradas)`, donde las entradas son los valores de PARAMETROS, su esquema, las claves leídas, el nombre, las piezas y si es malla. La revisión cubre la geometría. Máximo 32 entradas; sale la más antigua. La verificación del staged build no se cachea (no tiene revisión).
- **Seguridad.** Los ids se buscan en el listado calculado por el servidor para esa revisión; nunca se evalúan ni se usan como ruta. Los valores son números finitos acotados. El nombre del documento se muestra con `textContent`.

## Límites

- **Vínculo por valor, comprobación estática.** `claves_leidas` busca literales `PARAMS["clave"]` o `PARAMS['clave']` en el código del script. No ejecuta nada (el sondeo perturbando la clave y midiendo habría costado una ejecución de script por clave en cada render del panel; se descartó). Dos casos:
  - Falso negativo: una clave leída por alias (`p = PARAMS; p["x"]`) o en un bucle no aparece; su cota sale de solo lectura. Es el lado conservador.
  - Falso positivo: el script lee la clave pero la geometría no depende de ella (caso `prof = PARAMS["prof"]` sin uso). La cota sale editable, y el intento de edición se rechaza por `no_sigue_al_parametro` sin escribir nada. Ya no hay escritura silenciosa errónea.
- **Coste.** Cada edición ejecuta el script una sola vez: la verificación usa la misma geometría que luego se escribe. El listado previo sale de la revisión cacheada (ver «Caché»). Un plano que falla al dibujarse en la verificación rechaza la edición con 400 sin escribir (antes la edición se escribía y luego fallaba el listado).

## Pendiente (F23b y sub-fases)

- **Clic sobre la cota en el plano o en el overlay 3D.** Sigue pendiente. Esta fase ofrece el editor en el panel de parámetros (y la acción «Cotas» de la barra que lo abre). Clicar la cota en el dibujo del plano o sobre el overlay del visor queda para una sub-fase posterior, junto con los documentos importados (F23b).

## Desviaciones frente al plan (F23a)

1. **Revisión 1: verificar antes de escribir, no revertir.** La primera versión escribía y luego informaba `no_sigue_al_parametro`, con revisión nueva. La revisión 1 cierra ese hueco: la verificación ocurre antes de cualquier escritura, así que no hace falta revertir ni `restaurar`.
2. **Un script que falla deja snapshot en el camino de cotas.** Es el comportamiento de ADR 0003 para cualquier edición por script; cambiarlo afectaría a `/documentos/script` y tiene pruebas propias. Solo el rechazo por no seguir queda sin rastro.
3. **Editor en el panel, no en el visor.** Ver «Pendiente».
4. **Vínculo por valor.** Ver «Límites». El caso de `_vinculo` en `app/planos.py` sigue siendo por valor (contrato de F22); la clasificación con el script se hace en `app/cotas.py`.

## Por qué

Eli corrige piezas de script cambiando el número de la cota en el plano, no buscando el parámetro. Reutilizar `parametros` evita un segundo camino de escritura: la validación, el snapshot y las restauraciones ya están probados. Verificar antes de escribir evita que una cota que no sigue deje una revisión que hay que deshacer a mano. La revisión en la lectura evita que una edición vieja pise un cambio ajeno.

## Pruebas

- `tests/test_cotas_parametros.py`: lógica pura (`app/cotas.py`, incluidas `claves_leidas` y `clasificar` con `leidas`), listado y clasificación, equivalencia con `parametros` (mismo bbox y volumen, una entrada de historial), solo lectura y «ligada por valor», fuera de rango, revisión obsoleta, id desconocido, campos extra, valores no finitos, restauración, **rechazo por no seguir** (sin revisión nueva, sin snapshot, sidecars, repositorio git y cursor de deshacer idénticos), **fallo del script con snapshot preventivo**, caché por revisión, dos ediciones simultáneas, documento sin script, malla STL, `exportar` detalle, y el ramal MCP en proceso (incluido el mensaje del rechazo).
- `tests/web/cotas.test.mjs`: etiquetas, validación del campo, descarte de respuestas viejas y guarda contra HTML.
- `tests/web/toolbar.test.mjs`: la acción «Cotas» está lista, exige documento y llama a `c.cotas()`.
