# Verificación del frontend — 2026-10-16

Ejecutada desde el navegador integrado sobre Forja local, con la sesión autorizada por el usuario. No se leyó ni copió su clave. Las acciones de aplicación se ejecutaron mediante controles visibles, no mediante peticiones externas ni acceso directo al estado JavaScript.

## Documentos

- Ensamble real `8a3fbf4395f244739a8ad684fdb632cd`: únicamente inspección y distancia.
- Fixture `5b97147e91e145de91593fd85a26f39c`, `Forja-prueba-frontend.stl`: caja cerrada 10 × 12 × 14 mm, importada desde el botón Abrir archivo. Conservada para reproducir las comprobaciones.

## Recorridos observados

| Recorrido | Resultado |
|---|---|
| Inspección del ducto real | 50.323 × 92.300 × 86.421 mm; cerrado; un cuerpo |
| Distancia ducto/carcasa | 0 mm; advertencia de contacto o interferencia, sin distinguirlos |
| Guardar objetivo e indicación con dirección | Persistieron al recargar y restaurar |
| Añadir condición de malla cerrada | Cumple en la comparación del candidato |
| Candidato de desplazamiento X=2 mm | Preparado; superposición azul visible; aplicación confirmada |
| Referencia obsoleta tras aplicación | Aviso de revisión visible; desaparece al restaurar el estado correspondiente |
| Orientaciones | Seis opciones; apoyos 120, 140 y 168 mm²; todas caben |
| Trabajo FDM | Encolado y resultado recuperado desde Actualizar resultado; límites de muestreo visibles |
| Exportación STL y 3MF | Manifiesto con pieza incluida y eventos de descarga reales para ambos formatos |
| Historial | Instantánea antes de candidato restaurada; intención conservada |
| Rol Referencia | Rechazó preparación con «Pieza protegida» |
| Captura de tres puntos | Detectó defecto de nombre en STL sin grupos; corregido y repetido |
| Región con imagen | Tres puntos y PNG de prueba guardados; miniatura visible |
| Inspección posterior a captura | Pieza correcta, dimensiones 10 × 12 × 14 mm |
| Corte Z=0 | Un contorno y sección visible |
| Ajuste de plano | RMS 0.0000 mm sobre tres puntos de la cara superior |
| Ajuste de círculo | Radio 3.044 mm sobre tres puntos; es ajuste matemático, no detección de agujero |
| Alineación | Captura destino y origen en igual orden; correspondencias idénticas, error máximo/RMS 0.000 mm |

## Corrección y alcance

`resolveDesignPiece` mapea una etiqueta no canónica solo cuando existe una única pieza. Para varias piezas exige coincidencia explícita. Prueba de regresión añadida; suite JavaScript: 65 aprobadas, cero fallos. `git diff --check` correcto.

Esta sesión cubre los recorridos anteriores, no todas las combinaciones: no se repitieron desde el frontend las booleanas, deformación regional, cancelación de trabajos, edición concurrente ni un flujo STEP completo. Esos contratos tienen pruebas automatizadas previas, que no sustituyen una verificación visual de cada combinación. La prueba no certifica encaje ni imprimibilidad física del ducto.

# Verificación del frontend — 2026-10-16 · indicaciones unificadas

Decisión: `docs/decisions/0019-unified-intents.md`. Alcance: el panel Indicaciones (antes Diseño) con notas, trazos, indicaciones, propuestas y preparación para imprimir.

## Entorno

- Instancia aislada de Forja con clave ficticia (`FORJA_TOKEN` de prueba). No se leyó, imprimió ni copió la clave real del servicio.
- Documento de prueba `5b97147e91e145de91593fd85a26f39c`. El ensamble real `8a3fbf4395f244739a8ad684fdb632cd` se cargó solo en lectura y no se modificó.
- Navegador headless por CDP (Chrome de Flatpak).

## Recorridos revisados

Recorridos registrados por la revisión de la sesión sobre las capturas listadas abajo. Esta fase no los repitió en el navegador; la tabla resume lo que la revisión cubrió:

| Recorrido | Qué se comprobó |
|---|---|
| Añadir nota → tarjeta | La marca crea una indicación con su texto; no hay doble entrada en Notas |
| Lista de indicaciones | Estados en español; filtro Abiertas/Todas |
| Interpretación del agente | «Lo que entendió el agente» aparece tras la respuesta por la ruta de agente |
| Propuesta enlazada | Resumen, comprobaciones en palabras sencillas, «Ver propuesta» sobre el modelo |
| Aplicar | Estado «cambio aplicado»; «Volver atrás» disponible |
| Deshacer | Restaura el snapshot previo; la indicación pasa a revertida, no a resuelta |
| Resolver | Solo la acción del usuario marca «resuelta» |
| Borradores | El texto escrito sobrevive a un evento en vivo y a re-render |
| Notas antiguas | Notas sin indicación visibles como marcas sueltas con «Convertir en indicación» |
| Ver en el modelo | Resalta las marcas de la indicación |
| Vista 360 | Comprobada en el modelo tras la indicación |

Capturas (scratchpad de la sesión):

- `/tmp/claude-1000/-home-elisaul77/a8936b9d-ace3-487b-b846-94a5b4e47cd6/scratchpad/01_inicial.png`
- `/tmp/claude-1000/-home-elisaul77/a8936b9d-ace3-487b-b846-94a5b4e47cd6/scratchpad/02_panel.png`
- `/tmp/claude-1000/-home-elisaul77/a8936b9d-ace3-487b-b846-94a5b4e47cd6/scratchpad/03_dialogo.png`
- `/tmp/claude-1000/-home-elisaul77/a8936b9d-ace3-487b-b846-94a5b4e47cd6/scratchpad/04_tarjeta.png`
- `/tmp/claude-1000/-home-elisaul77/a8936b9d-ace3-487b-b846-94a5b4e47cd6/scratchpad/05_lista.png`
- `/tmp/claude-1000/-home-elisaul77/a8936b9d-ace3-487b-b846-94a5b4e47cd6/scratchpad/06_entendida.png`
- `/tmp/claude-1000/-home-elisaul77/a8936b9d-ace3-487b-b846-94a5b4e47cd6/scratchpad/07_propuesta.png`
- `/tmp/claude-1000/-home-elisaul77/a8936b9d-ace3-487b-b846-94a5b4e47cd6/scratchpad/08_ver_propuesta.png`
- `/tmp/claude-1000/-home-elisaul77/a8936b9d-ace3-487b-b846-94a5b4e47cd6/scratchpad/09_aplicado.png`
- `/tmp/claude-1000/-home-elisaul77/a8936b9d-ace3-487b-b846-94a5b4e47cd6/scratchpad/10_ver_modelo.png`
- `/tmp/claude-1000/-home-elisaul77/a8936b9d-ace3-487b-b846-94a5b4e47cd6/scratchpad/11_360.png`
- `/tmp/claude-1000/-home-elisaul77/a8936b9d-ace3-487b-b846-94a5b4e47cd6/scratchpad/r01_orden.png`
- `/tmp/claude-1000/-home-elisaul77/a8936b9d-ace3-487b-b846-94a5b4e47cd6/scratchpad/r02_token.png`
- `/tmp/claude-1000/-home-elisaul77/a8936b9d-ace3-487b-b846-94a5b4e47cd6/scratchpad/r03_deshecho.png`
- `/tmp/claude-1000/-home-elisaul77/a8936b9d-ace3-487b-b846-94a5b4e47cd6/scratchpad/r04_ver_modelo.png`
- `/tmp/claude-1000/-home-elisaul77/a8936b9d-ace3-487b-b846-94a5b4e47cd6/scratchpad/r05_360.png`

## Pruebas automatizadas

- JavaScript (`node --test tests/web/*.test.mjs`): **80 aprobadas, 0 fallos**.
- Python relevante (`test_design_intents`, `test_design_mcp`, `test_design_workspace`, `test_enlace_orca`, `test_mcp_http`, `test_mcp_tools`, `test_notes`): **148 aprobadas, 48 omitidas** (las omitidas requieren servicios o credenciales reales).
- Suite completa: el conjunto de fallos coincide con el de la línea base `caf1d53`. Comparación contra la salida guardada de la ejecución previa; no se volvió a ejecutar completa en esta fase.
- `git diff --check`: correcto.

## Límites

Revisión visual y de comportamiento, no certificación de accesibilidad ni de compatibilidad con todos los navegadores. No hay prueba de extremo a extremo con un cliente de IA real: el agente se simuló por la ruta HTTP de agente con clave ficticia. Las respuestas del agente no se generan ni se envían desde Forja.
