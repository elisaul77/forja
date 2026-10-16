# Diseño compartido en Forja

Abre un documento y entra en **Diseño**, en el panel lateral. El mismo estado y las mismas operaciones están disponibles para el usuario y el agente MCP.

## Para el usuario

1. Escribe el objetivo y guarda la intención.
2. Asigna **Diseño**, **Referencia** o **Espacio reservado** a cada pieza. Referencias y reservas quedan protegidas en operaciones de candidatos.
3. En **Señalar y explicar**, activa la captura y pulsa sobre el modelo. Añade una explicación y, si hace falta, una dirección XYZ o una foto de referencia (máximo 6 MB y 16 megapíxeles). Las notas y trazos existentes siguen disponibles en Notas y se incluyen en el contexto MCP.
4. Añade condiciones de malla cerrada, cuerpo único, holgura mínima, dimensión, conservación de geometría o revisión física pendiente.
5. En **Medir y comprobar**, inspecciona una pieza, mide separación, ajusta un plano/círculo sobre puntos, calcula alineación o visualiza un corte. Para alinear, captura primero destino, guárdalo, y después captura la pieza móvil en el mismo orden.
6. Prepara una alternativa. El candidato aparece en azul translúcido sobre el original. Revisa condiciones y aplica o descarta. El historial permite recuperar el estado anterior.
7. Guarda la postura de impresión por pieza. Compara seis orientaciones, ejecuta el análisis FDM y descarga solo piezas de diseño. La carcasa y demás referencias se excluyen.

Las consultas y acciones protegidas del panel solicitan la clave de Forja en un formulario integrado, compatible con el navegador de Codex. Se conserva solo durante la pestaña. Un cambio concurrente produce conflicto: recarga y revisa antes de volver a guardar. Las indicaciones antiguas permanecen visibles como obsoletas después de cambiar geometría.

## Para el agente MCP

| Herramienta | Contrato |
|---|---|
| `capacidades_diseno` | Métodos, límites y esquemas disponibles |
| `contexto_diseno` | Objetivo, roles, requisitos, firmas, notas, selecciones obsoletas y revisiones |
| `guardar_diseno` | Reemplazo validado del estado; conservar campos no editados; requiere ambas revisiones |
| `consultar_diseno` | Lote de hasta 16 consultas o trabajo en segundo plano |
| `preparar_candidato` | Transformar, añadir/quitar/intersectar primitiva o desplazar región STL; petición idempotente |
| `candidato_diseno` | Listar, aplicar o descartar; aplicación verificable y recuperable |
| `trabajo_diseno` | Estado, resultado o cancelación cooperativa |
| `referencia_visual` | Foto adjunta a una indicación, como imagen MCP |
| `exportar_diseno` | Manifiesto y descarga por roles y postura, con huella SHA-256 |

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
- Los trabajos sobreviven al cierre del panel, pero no al reinicio del servidor. La intención y los candidatos sí se guardan en disco.

## Archivos y mantenimiento

Mapa: `plans/agent-workspace-map/implementation.md`. Dependencias extraídas: 71 archivos, 1465 nodos, 3090 relaciones (extracción estructural, no inferencias semánticas). Los módulos `design_*` separan estado, geometría, evaluación, candidatos, trabajos y rutas. No se ejecuta código libre ni se interpreta el texto de anotaciones desde estas herramientas.
