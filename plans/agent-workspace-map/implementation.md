# Forja: espacio compartido humano/agente

Entrega integrada, sin subagentes. Preservar cambios locales existentes. Una ejecución integral al terminar; corregir y comprobar fallos si aparecen.

## Mapa de implementación

| Funcionalidad | Archivos nuevos | Integración existente |
|---|---|---|
| Objetivo, restricciones, roles, medidas y referencias por revisión | app/design_state.py | documents.py (snapshots/restaurar/borrar), ramas.py (captura/materialización) |
| Contratos tipados, consultas agrupadas, capacidades | app/design_contracts.py, app/design_routes.py | main.py, mcp_server/client.py, tools.py, server.py |
| Inspección regional, ajuste de planos/círculos STL, interfaces STEP, alineación por puntos, distancias | app/design_geometry.py | kernel/mesh.py, naming.py, incorporar.py reutilizados |
| Requisitos evaluables, comparación, preparación FDM y orientación | app/design_analysis.py | checks/fdm.py, documents.py |
| Candidatos, edición por pieza/booleana, protección, revisión e idempotencia | app/design_operations.py | ramas.py y geometría existente |
| Trabajos largos, progreso, cancelación, caché acotada | app/design_jobs.py | design_routes.py |
| Intención, anotaciones, roles, requisitos, análisis y candidatos visibles | app/web/static/js/design.js | tabs.js, index.html, forja-base.css, app.js |
| Exportación por rol y postura con manifiesto verificable | app/design_operations.py | export.py y descarga existente |
| Fotos de referencia asociadas a intención | app/design_assets.py | design_state.py, design_routes.py, design.js, MCP referencia_visual |
| Pruebas integrales y documentación | tests/test_design_workspace.py, tests/test_design_mcp.py, tests/web/design.test.mjs, docs/AGENT-WORKSPACE.md, docs/decisions/0018-agent-workspace.md | CHANGELOG.md |

## Contratos

- Estado de diseño versionado en sidecar `design.json`. Geometría y estado tienen revisiones separadas; las escrituras requieren ambas cuando corresponde.
- Coordenadas en mm y marco del documento. Referencias a región vinculadas a revisión; no reasignar selecciones obsoletas silenciosamente.
- Identidad de pieza persistente mientras se conserve nombre + firma geométrica; ante cambio, señalar referencia pendiente de revisar.
- Datos humanos son datos, nunca código. No ejecutar texto de anotaciones.
- Análisis diferencia exacto B-rep, malla y muestreo; datos insuficientes producen `no_comprobado`, nunca aprobación implícita.
- Operaciones preparan candidato separado; aplicar exige revisión vigente y validación. No sobrescribir referencias protegidas.
- Preservar scripting sandbox, notas, historial, ramas y selección actuales. No duplicar estos subsistemas.
- Planos/círculos en STL son ajustes explícitos sobre puntos seleccionados, no reconocimiento universal. Interfaces STEP son superficies analíticas, no garantía de agujero funcional.
- Cancelación cooperativa entre operaciones del kernel; no prometer interrumpir un cálculo nativo ya comenzado.
- Análisis FDM es heurístico y no sustituye laminado ni ensayo físico.

## Aceptación integral

Fixture STEP y STL: guardar intención/selecciones → inspeccionar → alinear → preparar cambio → detectar conflicto/restricción → aplicar → restaurar → exportar solo diseño. Revisar concurrencia, idempotencia, entradas no finitas, autenticación, límites, cancelación y persistencia. Ejecutar suite Python y JS y verificar UI real al final. No modificar geometría del ducto real para probar funciones.

## Verificación ejecutada (2026-10-15)

- Suite Python amplia: 623 aprobadas, 9 fallos corregidos, 3 omitidas. Se excluyeron pruebas que requieren credenciales reales y las suites de sandbox/hermeticidad.
- Comprobación posterior de módulos afectados e integración existente: **214 aprobadas, 3 omitidas**, sin fallos.
- Recorrido real MCP → HTTP con servidor aislado, credencial ficticia y documentos temporales: **1 aprobada** (contexto, guardar, consultar, candidato, aplicar y exportar con SHA-256).
- Suite JavaScript final: **64 aprobadas**, sin fallos.
- `git diff --check`: correcto. Los conteos de Python se solapan; no se suman como pruebas distintas.

- Revisión UI en navegador integrado: ensamble con tres piezas, panel Diseño y formulario de acceso verificados. Se corrigió la incompatibilidad del antiguo `window.prompt` usando `auth.js` asíncrono en el panel nuevo; tres pruebas cubren sesión, concurrencia y cancelación. Las consultas autenticadas de producción requieren que el usuario introduzca su clave; no se leyó ni utilizó esa credencial.
