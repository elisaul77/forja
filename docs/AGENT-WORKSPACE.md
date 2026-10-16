# Diseño compartido en Forja

Abre un documento y entra en **Indicaciones**, en el panel lateral. Es la lista única donde se explica lo que quieres cambiar: notas, trazos, propuestas y preparación para imprimir. El mismo estado y las mismas operaciones están disponibles para el usuario y el agente MCP. Decisión de diseño en `docs/decisions/0019-unified-intents.md`.

## Para el usuario

1. Escribe el objetivo del diseño en la cabecera del panel y guárdalo.
2. Explica lo que quieres: marca en el modelo con **Añadir nota** o con el modo de trazo del visor, o escribe sin marcar con **Escribir sin marcar**. Cada marca se convierte en una indicación con su texto; no hace falta escribir lo mismo en Notas y en Diseño. Puedes adjuntar fotos de referencia (máximo 6 MB y 16 megapíxeles por foto). Las notas antiguas sin indicación aparecen como marcas sueltas; pulsa **Convertir en indicación** para unirlas a la lista.
3. Revisa lo que se entendió. Cada indicación muestra la lectura del agente, su pregunta si la tiene y los mensajes que has dejado. Responde con **Pedir un ajuste** si hace falta corregir.
4. Ve la propuesta. Cuando el agente prepara un cambio, aparece con su resumen y sus comprobaciones en palabras sencillas. **Ver propuesta** la muestra sobre el modelo; **Aceptar** la aplica, y **Descartar** la retira.
5. Acepta o vuelve atrás. Tras aplicar, **Volver atrás** restaura el estado anterior mediante el historial. Solo tú marcas la indicación como resuelta con **Sí, está resuelto**, o la reabres con **No, sigue mal**.
6. Asigna **Diseño**, **Referencia** o **Espacio reservado** a cada pieza. Referencias y reservas quedan protegidas en operaciones de candidatos.
7. Añade condiciones de malla cerrada, cuerpo único, holgura mínima, dimensión, conservación de geometría o revisión física pendiente.
8. En **Herramientas avanzadas** (plegado) están las herramientas técnicas: medir y comprobar, planos y círculos, alineación, cortes, candidato manual y pose. Para alinear, captura primero destino, guárdalo, y después captura la pieza móvil en el mismo orden.
9. En **Preparar para imprimir** eliges por pieza si se imprime o es solo referencia, buscas una mejor orientación, revisas si se puede imprimir y descargas 3MF o STL. La carcasa y demás referencias se excluyen de la descarga.

Estados de una indicación, derivados por el servidor: pendiente, entendida, con pregunta, propuesta disponible, cambio aplicado, revertida (el documento cambió después de aplicar), resuelta o descartada. Una propuesta obsoleta muestra «La pieza cambió; vuelve a preparar» en lugar de un fallo. Ninguna operación marca una indicación como resuelta: solo lo hace el usuario.

Forja no envía mensajes a ninguna IA. Lo que guardas queda disponible para el agente cuando lee Forja con `contexto_diseno` y tú le pides revisarlo. El panel lo indica en un aviso visible; no hay botones de «enviar a la IA».

Explicar, responder, adjuntar marcas o fotos, y marcar como resuelta o descartada no pide clave. Preparar, aplicar, restaurar, cambiar la geometría o las piezas protegidas sí la pide, y el aviso indica el motivo. Las consultas protegidas solicitan la clave en un formulario integrado, compatible con el navegador de Codex. Se conserva solo durante la pestaña. Un cambio concurrente produce conflicto: recarga y revisa antes de volver a guardar. Las indicaciones antiguas permanecen visibles como obsoletas después de cambiar geometría.

## Para el agente MCP

| Herramienta | Contrato |
|---|---|
| `capacidades_diseno` | Métodos, límites y esquemas disponibles |
| `contexto_diseno` | Objetivo, roles, requisitos, firmas, revisiones e `intents_view`: texto, estado derivado (`attention`), marcas con detalle (`marks_detail`), `missing_marks`, `stale` y `loose_marks` (notas sin indicación) |
| `responder_indicacion` | Registra tu interpretación, una pregunta o un mensaje del agente en una indicación (`id`, `indicacion`, opcionales `interpretacion`, `pregunta`, `mensaje`). Requiere clave; no cambia la geometría |
| `guardar_diseno` | Reemplazo validado del estado; conservar campos no editados; requiere ambas revisiones |
| `consultar_diseno` | Lote de hasta 16 consultas o trabajo en segundo plano |
| `preparar_candidato` | Transformar, añadir/quitar/intersectar primitiva o desplazar región STL; petición idempotente. Opcionales `indicacion` (vincula el candidato a una indicación) y `resumen` (explicación humana, máximo 500) |
| `candidato_diseno` | Listar, aplicar o descartar; aplicación verificable y recuperable mediante `before_snapshot` |
| `trabajo_diseno` | Estado, resultado o cancelación cooperativa |
| `referencia_visual` | Foto adjunta a una indicación, como imagen MCP |
| `exportar_diseno` | Manifiesto y descarga por roles y postura, con huella SHA-256 |

Flujo del agente: `contexto_diseno` → `responder_indicacion` con interpretación o pregunta → `preparar_candidato` vinculado a la indicación con un `resumen` → esperar a que el usuario acepte, pida un ajuste o descarte. El agente no marca nada como resuelto y no debe asumir que el usuario ha leído la propuesta hasta que lo confirme. Un candidato preparado no se invalida por respuestas, adjuntos ni lecturas del agente; sí se invalida por cambios de geometría, roles, requisitos u objetivo.

Consultas: `inspect`, `fit_plane`, `fit_circle`, `align`, `clearance`, `section`, `evaluate`, `fdm`, `orientations`, `compare`. Todas usan milímetros en coordenadas del documento. `align` calcula una matriz rígida sin deformar; úsala en una operación `transform.matrix`. `transform` también admite rotación XYZ en grados alrededor de `origin`, seguida de `translation`.

Las booleanas usan `box` o `cylinder`; `center` posiciona la herramienta. `size` de caja es XYZ; en cilindro X es diámetro y Z altura. `deform_region` desplaza vértices dentro de `radius_mm` respecto a `center`, con caída radial; la región exterior permanece inmóvil. No garantiza ausencia de autointersecciones.

## Interpretación de resultados

- `pass`: el requisito específico pasó el método declarado.
- `fail`: evidencia de incumplimiento.
- `not_checked`: no hay evidencia suficiente o requiere comprobación humana.
- Ninguno de estos resultados implica aprobación física general.
- Distancia FCL usa la triangulación; no equivale a distancia analítica exacta STEP.
- Malla abierta: contención y volumen no son fiables.
- FDM usa muestreo de paredes y análisis de caras, no trayectorias de un laminador.
- Los trabajos sobreviven al cierre del panel, pero no al reinicio del servidor. La intención, las respuestas y los candidatos sí se guardan en disco.
- El estado «entendida» o «propuesta» no certifica que el agente haya entendido bien. Es una lectura registrada; la confirmación la da el usuario.
- Deshacer restaura el documento completo al instante anterior a aplicar. No deshace ediciones posteriores de forma individual.

## Archivos y mantenimiento

Mapa: `plans/agent-workspace-map/implementation.md`. Dependencias extraídas: 71 archivos, 1465 nodos, 3090 relaciones (extracción estructural, no inferencias semánticas). Los módulos `design_*` separan estado, geometría, evaluación, candidatos, trabajos y rutas. No se ejecuta código libre ni se interpreta el texto de anotaciones desde estas herramientas.
