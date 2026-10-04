# ADR 0014: Git como backend del historial (sustituye a ADR-0006)

Date: 2026-10-04
Status: Aceptado — sustituye a ADR-0006

Origen: `plans/maestro/fases/G-git-para-diseno.md` (fase G1), con
`plans/maestro/fases/G0-resultados.md`.

## Context

ADR-0006 guardaba cada instantánea como copia completa de archivos en
`.historial/{doc}/{id}/`. Con documentos de 80 MB y 81 cambios eso llegó a
1,4 GB, sin autor, sin intención ni base para ramas/diff/fusión (Plan G).

## Decision

- Un repositorio git **bare** por documento: `{FORJA_DATA_DIR}/.repos/{doc}.git`.
  Cada instantánea = un commit cuyo árbol son exactamente los archivos de esa
  instantánea (mismas claves que ADR-0006: `{doc}.step|stl`, `notas.json`,
  `solidos.json`, `parametros.json`, `materiales.json`, `ensamble.json`,
  `ensamble_base.step`), más un subárbol opcional `_fuente/` que nunca vuelve
  por `leer_snapshot`.
- Solo *plumbing* (`hash-object`, `mktree`, `commit-tree`, `update-ref` con
  compare-and-swap, `cat-file`): sin árbol de trabajo ni índice, ningún nombre
  de archivo se convierte en ruta. Nombres validados (un solo componente, sin
  `.`/`..`/punto inicial/separadores/controles); id de documento por regex.
- `app/git_store.py` llama al binario sin shell, con timeout,
  `core.hooksPath=/dev/null`, `protocol.allow=never`, config de sistema/global
  ignorada, `safe.directory` = solo ese repo, `HOME` inexistente; candado por
  repo propio (no `parametros.bloqueo`, que no es reentrante y ya lo tienen
  los llamadores). `gc --auto` tras cada commit.
- Mensaje: la intención existente (`antes de parametros: alto=30`, `antes de
  restaurar X`…) + trailers `Forja-Snapshot` (id de 12 hex, el mismo contrato
  de siempre), `Forja-Fecha`, `Forja-Archivos`. Autor neutro: `agente` (MCP,
  cabecera `X-Forja-Origen: agente`), `humano` (navegador, `Sec-Fetch-Site`),
  `forja` (resto/migración). Nunca datos personales. El autor es una
  **etiqueta de origen** que declara el cliente (cabecera), no una prueba de
  identidad: cualquiera con acceso a la API puede enviar `agente` o `humano`.
- Contratos `leer_historial`/`restaurar` (REST y MCP) idénticos: mismos ids,
  `{id, fecha, mensaje}`, orden, bytes. Se añade `versioning.listar_commits`.
- Mientras exista un `.historial/` sin migrar se lee y lista primero (nada
  desaparece entre desplegar y migrar). `python -m migrar_historial`
  (dry-run por defecto; `--aplicar` como uid 1000) reescribe cada instantánea
  como commit con su id/fecha/mensaje, reaplica encima los commits nuevos,
  verifica byte a byte y solo entonces mueve la rama y renombra `.historial/`
  a `.historial.migrado/`; marcador `.repos/.migracion.json`; idempotente.

## Por qué la geometría sigue en git (desvío del plan)

G0 mostró reconstrucción determinista byte a byte, pero 4 de 5 scripts se
recuerdan por `ruta` y su texto no se guardaba: el ajedrez ya no reproduce su
STEP guardado. Sin texto de script en cada instantánea anterior, el STEP es
«imprescindible para restaurar». Se mantiene como blob; git comprime y
deltifica: 1,40 GB → 156 MB (f6be: 1,35 GB → 136 MB). Dejar de versionar la
geometría de documentos con script exige guardar el texto del script en el
commit *posterior* al cambio y una caché por hash de árbol: G2/G3.

## Consequences

- Imagen con `git` (Dockerfile). Sin dependencias Python nuevas.
- `revision` (F11.1) y la caché de mallas no cambian (siguen sobre los bytes).
- Instantáneas idénticas siguen siendo entradas distintas (commits con el
  mismo árbol).
- No se alcanza aún «< 50 MB»: queda para cuando la geometría sea derivada.
- `.historial.migrado/` (1,4 GB) queda hasta que Eli decida borrarlo.

## G2/G3 — ramas, pasos y comparar (2026-10-04)

- `refs/heads/main` sigue siendo la cadena de instantáneas «antes del
  cambio» (contratos de historial/restaurar intactos). Las ramas viven en
  `refs/forja/ramas/<nombre>` y cada commit es un **paso**: el estado
  COMPLETO tras un cambio aceptado (geometría, `meta.json`, notas, sólidos,
  materiales, ensamble y `_fuente/script.py` con el texto aunque venga de
  `ruta`), con trailers `Forja-Revision` y `Forja-Rama`. Rama activa en
  `forja-rama-activa` dentro del repo; `main` se crea perezosamente.
- El middleware registra el paso **antes** de enviar la respuesta de una
  petición mutante con estado < 400 (lo que `crear_snapshot` anotó).
- Cambiar de rama guarda antes lo no registrado en la rama que se deja y
  una instantánea G1 «antes de cambiar a la rama X»; escribe los archivos
  de forma atómica bajo el candado del documento y emite el evento SSE.
- Nombres de rama: `[A-Za-z0-9][A-Za-z0-9_-]{0,47}`; shas de fuera: 40 hex
  o abreviados de 7+ hex, nunca expresiones de revisión; `--end-of-options`.
- Comparar aplica la regla de G0 sobre `solidos.json` (caras solo si lo
  demás coincide, con caché por contenido) sin tomar el candado.
