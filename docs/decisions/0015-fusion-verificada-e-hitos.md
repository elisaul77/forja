# ADR 0015: Fusión verificada, traer pieza e hitos (Plan G · G4/G5)

Date: 2026-10-04
Status: Aceptado — amplía ADR-0014 (no lo sustituye)

## Context

G2/G3 dieron ramas (`refs/forja/ramas/`), pasos con el estado completo y
comparar por pieza. Faltaba volver a juntar trabajo: git fusiona STEP en
geometría rota (caddiff), así que la fusión debe hacerse sobre las fuentes
y verificarse sobre la geometría derivada.

## Decision

**Fusión completa** (`POST /documentos/{id}/ramas/fusionar {desde}`) de la
rama/paso B en la rama activa A, con el ancestro común O (`git merge-base`):

- Datos a 3 vías: parámetros (`meta.valores`) por clave; materiales por
  pieza (+ `declarado` como un todo); notas y trazos por id (`n`);
  ensamble por articulación (`id`) y valores, solo si las dos ramas
  comparten la pieza de reposo (`base_sha256`), si no, conflicto
  `ensamble`. `variables` y `timeout` como un todo.
- Script (texto de `_fuente/script.py`): igual o cambiado en un solo lado
  → ese; cambiado en los dos → `git merge-file -p --diff3` (sin repo, sin
  shell). Con conflicto de texto no se aplica nada: se devuelven las líneas
  con marcadores.
- Geometría derivada: si (script, valores, variables) fusionados coinciden
  con los de un lado, se toma su geometría tal cual; si no, se reconstruye
  en el sandbox (mismo camino que `parametros`). Sin script: geometría a 3
  vías por pieza con nombre (firma G3: volumen/bbox/nº de sólidos) y se
  compone un STEP nuevo con las piezas de cada lado.
- Conflicto = los dos lados cambiaron la misma clave/pieza de forma
  distinta. `estrategia` `nuestra`/`suya` los resuelve en bloque (también
  `--ours/--theirs` en el texto); por defecto `auto` = reportar.
- Un script por `ruta` cuyo texto fusionado difiere del de A pasa a
  guardarse como `codigo` (Forja nunca escribe fuera de sus datos), con
  aviso `script_a_codigo`.

**Traer pieza** (`piezas: [...]`): sustituye/añade/quita en A solo esas
piezas con la geometría de B (sólidos extraídos por `solidos_por_indice`,
recompuestos con `combinar_nombrados`) y sus materiales. Un documento con
script queda marcado `meta.geometria_editada = {desde, piezas}` (viaja en
`parametros.json` de las instantáneas): `GET /parametros` lo avisa y
`POST /parametros` responde 409 salvo `confirmar_script: true`; volver a
generar con el script (o `ejecutar_script`) borra la marca. No se edita el
código: no hay forma fiable de inventar ese cambio.

**Verificación obligatoria** antes de confirmar: validez del sólido y
`check_colisiones` sobre el resultado y los dos padres (caché por
contenido). Un choque entre un par de piezas que no chocaba en ningún padre,
o un sólido inválido cuando los padres eran válidos →
`conflicto_geometrico`, no se confirma salvo `forzar: true`. `check_fdm`
no se ejecuta (no es barato en documentos grandes).

**Confirmación**: misma maquinaria que `cambiar` (instantánea G1 «antes de
fusionar…», `_materializar`, análisis, `confirmar_revision`, evento SSE) y
todo o nada: ante cualquier fallo se reescriben los archivos del paso A y
la ref vuelve a A. Fusión = commit con **dos padres** (A, B) y trailer
`Forja-Fusion`; traer pieza = **un padre** y trailer `Forja-Desde` (no
incorpora la rama entera, así una fusión posterior sigue viendo el resto).
`simular: true` hace todo el cálculo (incluida la reconstrucción y las
verificaciones) y no escribe ni el documento ni el repo. Respuesta:
`{resultado: fusionada|simulada|conflicto|conflicto_geometrico|ya_incluida,
confirmada, modo, rama, desde, base, conflictos[], cambios[], avisos[],
verificacion}` (+ registro/revisión/`padres` al confirmar). Siempre 200;
400/404/409 solo para peticiones inválidas.

**Hitos (G5)**: `refs/forja/hitos/<nombre>` (mismas reglas de nombre que las
ramas) apuntando a un paso; descripciones y pasos ocultos en
`forja-curacion.json` dentro del repo. La historia nunca se reescribe.
`GET /ramas/{rama}/pasos_curados?vista=hitos|visibles|todos`: `hitos` da una
entrada por hito en la rama más la punta, cada una con `agrupa` (pasos que
resume). El endpoint `pasos` de G2 no cambia.

MCP: acciones nuevas en `rama` (`fusionar`, `traer_pieza`, `hito`, `hitos`)
con parámetros opcionales `piezas`, `estrategia`, `forzar`, `simular`; sin
herramientas nuevas.

## Consequences

- Fusiones de datos puras no tocan la geometría (misma revisión).
- Tras reconstruir no se re-resuelven las referencias de notas a caras
  (sí lo hace `parametros`); pueden quedar apuntando a índices viejos.
- Una fusión que reconstruye descarta piezas traídas antes (`geometria_editada`)
  con aviso.
- `git log` de una rama con fusiones incluye los pasos de la rama fusionada.
