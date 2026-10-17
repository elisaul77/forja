# ADR 0021 — Planos técnicos: una hoja con vistas, cotas automáticas y descriptores

Date: 2026-10-17. Status: implemented (F22, branch `phase-22-planos`). Supersedes nothing; extends ADR 0016 (export estanco) and ADR 0011 (descarga sin token).

## Decision

Un documento se exporta como **una hoja** (`svg`, `pdf` o `dxf`) mediante la herramienta existente `exportar`, con formatos nuevos y parámetros opcionales. No hay herramienta MCP nueva. La hoja tiene cuatro vistas (frontal, superior, derecha e isométrica), líneas visibles y ocultas, cotas automáticas y un cajetín.

- **Proyección.** Solidos B-rep: `Shape.project_to_viewport` de build123d 0.13 (aristas exactas de OCC, líneas ocultas reales). Se pasa `look_at` = centro del bbox. Sin él, build123d centra en el centro de masas y desplaza la hoja por el desplazamiento del agujero (medido: 0,59 mm en la placa de prueba). build123d devuelve los puntos relativos a `look_at`: `construir` les suma `centro @ r` y `centro @ u` para llevarlos al marco absoluto de cada vista, el mismo en que miden las cotas y el bbox (regresión: pieza escalonada lejos del origen con un Ø6). Las aristas ocultas que coinciden con una visible (el contorno inferior de un sólido sobre el superior) se eliminan muestreando tres puntos de cada segmento contra las visibles, con tolerancia de 0,02 mm.
- **Mallas (STL).** Sin B-rep no hay aristas exactas: se dibujan las aristas de silueta (caras de frente frente a caras de espalda) y de rasgo (ángulo diedro > 30°) vistas desde el observador. No hay líneas ocultas ni agujeros. Las cotas son el bbox. La respuesta lleva `aviso`, y el cajetín lo dice. **No se rechaza** (decisión del usuario, 2026-10-16).
- **Cotas.** Tamaño total (bbox), diámetro y posición de agujeros. Un agujero es un cilindro **cerrado**: caras cilíndricas que suman una vuelta completa (2π, aunque OCC las parta en varias), en vacío (`BRepClass3d_SolidClassifier` sobre el eje, a la altura de la cara; material = pestaña). Un empalme cóncavo cubre una fracción de vuelta y nunca es agujero (regresión: R4 sin cota). Solo se miden los ejes paralelos a un eje global; el resto se cuenta en `omitidas` y nunca se estima. Los agujeros iguales en un mismo eje se agrupan (`cantidad`).
- **Escala y papel.** Lista fija `5:1, 2:1, 1:1, 1:2, 1:5, 1:10`; se elige la mayor que cabe en A4 (por defecto) o A3, con margen para cotas y cajetín. Una escala pedida que no cabe da 400.
- **Proyección.** Tercer ángulo por defecto (`tercer_angulo`), primer ángulo opcional (`primer_angulo`). Queda impresa en el cajetín.
- **Escritores.** Un único juego de primitivas (`trazo`, `texto`) en milímetros de papel; SVG y PDF escritos a mano (sin dependencia nueva; PDF = Helvetica base-14 con WinAnsiEncoding, un xref); DXF con `ezdxf` directamente (capas `VISIBLES`, `OCULTAS` con DASHED, `COTAS`, `TEXTO`; unidades mm).
- **Seguridad.** Opciones validadas contra conjuntos fijos antes de cualquier cálculo. El nombre de archivo es `{id_documento}.{formato}` dentro de `exportados`. Nombres de documento, piezas y claves de PARAMETROS se escapan en SVG (XML), PDF (paréntesis, barra, cp1252) y DXF (`%` sustituido). Los caracteres de control se sustituyen por espacio y el texto se trunca.
- **Solo lectura.** Sin entrada en el historial, sin snapshot, sin cambio en el documento ni en sus sidecars. El bloqueo del documento se mantiene solo mientras se leen las formas; la proyección corre fuera del lock.
- **Visor.** Tres anclas en la barra de exportación: «Plano» (PDF), «Plano SVG» y «Plano DXF», que descargan por `/documentos/{id}/descarga/<slug>.{ext}`. Esa ruta construye la hoja por defecto en memoria (nada escrito en disco), igual que STL y 3MF. La descarga lee el documento bajo su bloqueo, libera el bloqueo y recién entonces proyecta. Las tres descargas de plano responden con `X-Content-Type-Options: nosniff` y `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; sandbox`.

## Descriptor de cota (contrato con F23)

Cada cota es un dict con estas claves (F23 añade `operacion` según su tipo):

| clave | valor |
|---|---|
| `id` | determinista: `tamano_total:{eje}:{valor:.3f}`, `diametro:{eje}:{d:.3f}:n{cantidad}`, `posicion:{eje}:{valor:.3f}:{eje_agujero}{c1}_{c2}:d{d}` |
| `tipo` | `tamano_total` \| `diametro` \| `posicion` (`distancia` reservado, no emitido) |
| `valor_mm` | float redondeado a 3 decimales, en mm del modelo (nunca del papel) |
| `eje` | `x` \| `y` \| `z` |
| `vista` | `frontal` \| `superior` \| `derecha` (la vista donde se dibuja el eje o el círculo) |
| `origen` | `parametro` si el valor coincide (1e-6) con una única clave de PARAMETROS; si no, `reconocido` |
| `parametro` | la clave o `null` |
| `operacion` | `null` en F22 |
| `cantidad` | solo en `diametro`: número de agujeros del grupo |

Los ids no usan azar ni uuid: la misma geometría y la misma revisión dan los mismos ids. Un cambio de una sola cota cambia solo el id de esa cota.

Las cotas `posicion` miden desde la esquina mínima del bbox, en el eje indicado. Solo se reportan las cotas que la hoja dibuja: el cajetín «Cotas: N» coincide con el número de cotas dibujadas (los textos de cota) y con el descriptor. La cota de diámetro se produce una vez por grupo; cada agujero tiene sus dos cotas de posición.

## Respuesta compacta

`{ruta, tamano_bytes, vistas, escala, papel, proyeccion, cotas, parametros_ligados, omitidas}`, más `completo: false` si el tope de aristas (200 000) o el presupuesto de tiempo (20 s) obligó a omitir las ocultas de la isométrica, y `aviso` para mallas o agujeros omitidos. Las cotas van en línea solo con `detalle=true` (máximo 40, con `cotas_mas`). Nunca viajan los bytes del archivo.

## Rejected

- **reportlab / cairosvg / svglib** para el PDF y el SVG: son dependencias nuevas de runtime; un PDF de una página con texto Helvetica y trazos cabe en unas decenas de líneas y es verificable con un xref.
- **Refusar STL** (la propuesta original de la fase): el usuario pidió explícitamente que las mallas también den planos.
- **Ajuste de cilindros sobre mallas**: fuera de alcance de F22; los agujeros de mallas no se miden.
- **Escalas libres**: una lista fija mantiene la hoja previsible y el texto de escala honesto.

## Limits (stated, not hidden)

- Sin **secciones**: no se generan cortes (ni para B-rep ni para mallas). Las vistas son proyecciones.
- Mallas: sin líneas ocultas, sin agujeros, sin aristas de borde en mallas abiertas. Más de 100 000 triángulos se rechazan con 400 («malla demasiado grande para un plano»): la proyección se hace arista a arista en Python.
- Las cotas de tamaño van en la vista que posee el eje (x y z en frontal, y en superior). Si esa vista no se pide, la cota no se dibuja y tampoco figura en el descriptor ni en el cajetín.
- El centrado del texto y el anclaje a la derecha en PDF usan un ancho medio de 0,5 em por carácter (Helvetica sin métricas AFM). Es una aproximación visual; el SVG y el DXF usan el motor de su visor.
- `PARAMETROS` solo liga por valor único; dos claves con el mismo valor dejan la cota sin ligar.
- Los cilindros con eje no paralelo a un eje global se omiten y se cuentan.
- No se excluyen «piezas de referencia»: el registro de sólidos no tiene ese concepto fuera de Diseño.

## Consequences

- F23 consume `id`, `tipo`, `valor_mm`, `eje`, `vista`, `origen`, `parametro` y `operacion` tal cual; no hace falta recalcular geometría para listar cotas.
- `documents.py` solo recibe cableado (validación, carga bajo lock y despacho); la lógica vive en `app/planos.py`, y `app/export.py` solo escribe el archivo.
