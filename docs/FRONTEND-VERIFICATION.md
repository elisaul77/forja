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
