# ADR 0023 — Cotas vivas (F23a): editar una cota de un script editando su parámetro

Date: 2026-10-17. Status: implemented in F23a (branch `phase-23a-cotas-vivas`). F23b (documentos importados STEP/STL: cilindros, caras paralelas, escala por eje) queda para una fase aparte. Supersedes nothing; extends ADR 0021 (descriptor de cota) and ADR 0003 (versionado).

## Decisión

Cada cota del plano (F22) que sale de una clave de `PARAMETROS` de un documento creado por script es un control vivo. Editarla traduce el valor a `{clave: valor}` y lo aplica por el mismo camino que `POST /documentos/{id}/parametros`: validación min/max, snapshot previo, una revisión nueva, documento actualizado en su sitio.

- **Listado.** `GET /documentos/{id}/cotas` (sin token, solo lectura) devuelve `{revision, cotas}`. Cada cota trae las claves del descriptor de F22 más `operacion`: `"parametro"` si es editable (con `min`/`max` del parámetro), o `solo_lectura: true` con `razon`. Las razones son `"no ligada a un parametro del script"` y `"malla sin B-rep"`.
- **Edición.** `POST /documentos/{id}/cotas/aplicar` (con token, `extra="forbid"`) con `{revision, cotas: {id_cota: valor}}`. Responde el resumen de `parametros` más `editadas`: por cota, `valor_antes`, `valor_despues`, `error_mm` y `no_sigue_al_parametro` cuando aplica.
- **MCP.** Sin herramienta nueva. `parametros(id, cotas={...}, revision=None)` (mutuamente exclusiva con `valores`). Los ids y su `operacion` salen de `exportar(id, "svg", detalle=true)`, que ahora trae además `revision` y `operacion` en `cotas_detalle`.
- **Visor.** Sección «Cotas del plano» dentro del panel «Parámetros» (solo documentos con script). Campo numérico por cota editable, texto «Solo lectura: razón» en las demás.

## Reglas

- **Revisión obligatoria.** La edición lleva la revisión en la que se leyeron los ids. Si no coincide, 409 y nada cambia. `parametros(cotas=...)` sin `revision` lee la vigente antes de escribir.
- **Atomicidad de la decisión.** La revisión, el listado y la escritura van bajo `parametros.bloqueo(doc_id)` (lock reentrante no hace falta: `aplicar_parametros` se partió en un cuerpo sin lock, `_aplicar_parametros_bloqueado`). Dos ediciones con la misma lectura: una 200, la otra 409.
- **Validación antes de escribir.** Id desconocido 404; solo lectura 422 con `razon`; valor no finito, no positivo o booleano 422; sin `max` del parámetro, más de 10 veces la medida actual 422; dos cotas de una misma clave con valores distintos 422; más de 40 cotas 422. Fuera de min/max 422 (lo dice `aplicar_valores`). Nada de esto deja snapshot ni revisión.
- **Una revisión por edición.** Se cumple cuando el script corre. Ver «Desviaciones».
- **Solo PARAMETROS.** La cota es editable solo si su valor coincide con una única clave declarada (vínculo de F22, por valor). Todo lo demás es de solo lectura; no se adivina.
- **Malla.** Un STL no tiene script: todas sus cotas son de solo lectura con `razon: "malla sin B-rep"`.
- **Seguridad.** Los ids se buscan en el listado calculado por el servidor para esa revisión; nunca se evalúan ni se usan como ruta. Los valores son números finitos acotados. El nombre del documento se muestra con `textContent`.

## Desviaciones frente al plan (F23a)

1. **Post-verificación informa, no revierte.** El plan pedía re-medir y revertir sin revisión si el error supera 0,01 mm. Eso exige saber el resultado antes de escribir, y `_actualizar_documento_desde_script` escribe el STEP al terminar el script. Por eso la edición se aplica y el informe marca `no_sigue_al_parametro: true` si la cota no se movió; el usuario puede `restaurar` el snapshot «antes de parametros». Esta es la regla de 23b (revertir) pendiente.
2. **Un script que falla deja snapshot.** El camino de `parametros` toma el snapshot antes de ejecutar el script. Un error del script (422) deja, por tanto, una entrada en el historial sin cambio de geometría. Es comportamiento previo, sin cambios en esta fase.
3. **Editor en el panel, no en el visor.** El plan pedía clic sobre la cota en el plano o en el overlay 3D. Esta fase deja el editor en el panel de parámetros. El clic en el overlay queda para una fase posterior.
4. **Vínculo por valor.** F22 liga una cota a una clave cuando el valor coincide. Si una cota coincide con un parámetro por casualidad, aparece editable; `no_sigue_al_parametro` lo muestra en el primer intento.

## Por qué

Eli corrige piezas de script cambiando el número de la cota en el plano, no buscando el parámetro. Reutilizar `parametros` evita un segundo camino de escritura: la validación, el snapshot y las restauraciones ya están probados. La revisión en la lectura evita que una edición vieja pise un cambio ajeno.

## Pruebas

`tests/test_cotas_parametros.py` (30): lógica pura (`app/cotas.py`), listado y clasificación, equivalencia con `parametros` (mismo bbox y volumen, una entrada de historial), solo lectura, fuera de rango, revisión obsoleta, id desconocido, campos extra, valores no finitos, restauración, cota que no sigue al parámetro, dos ediciones simultáneas, documento sin script, malla STL, `exportar` detalle y el ramal MCP en proceso. `tests/web/cotas.test.mjs` (7): etiquetas, validación del campo, descarte de respuestas viejas y guarda contra HTML.
