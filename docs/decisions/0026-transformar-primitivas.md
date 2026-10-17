# ADR 0026 — Transformar, primitivas y booleanas con otra pieza desde la barra

Date: 2026-10-17. Status: implemented (F24b, branch `phase-24b-transformar-primitivas`). Numeración: 0022-0024 están tomados; 0025 queda reservada para F23b; 0026 es la siguiente libre.

## Contexto

La barra de herramientas (24a) dejó como «Próximamente · 24b» mover, las primitivas, las booleanas y «Pedir cambio con lo seleccionado». El pipeline de candidatos (ADR-0019) ya aplica `transform` y booleanas con caja o cilindro, pero no hay escala, alineación, apoyo sobre la cama, más formas de útil, ni booleanas contra otra pieza del documento.

## Decisión

1. **Mismo pipeline de candidatos.** Tres `kind` nuevos: `scale`, `align` y `drop_to_bed`. Cada operación pasa por `preparar_candidato` → comparación → aplicar. No hay herramienta MCP nueva (los tests fijan el conjunto).
2. **Más formas de útil.** `primitive` acepta `sphere`, `cone`, `torus`, `wedge` y `tube`, además de `box` y `cylinder`. Tamaños en mm, centrados en el origen; la tabla de qué significa cada dimensión vive en `design_operations._validar_util` y en la ayuda del formulario. Tope de 2000 mm por dimensión.
3. **Booleanas con cualquier cuerpo.** `other` nombra otra pieza del documento como cuerpo de la booleana. **La pieza auxiliar no cambia.** Unir dos piezas en un solo cuerpo exige borrar la auxiliar, y eso lo decide el usuario; el candidato lo indica en `limitations`.
4. **Alinear** traslada la pieza según cajas envolventes: `center`, `min` o `max` por eje (`axes`), o `stack` (apilar: la base sobre la cara superior de `other`, solo en Z). Al ser una traslación, las caras planas quedan coplanares (1e-6 en las pruebas).
5. **Apoyar sobre cama** gira la cara indicada por `normal` hacia −Z alrededor del centro de la caja y después baja la base a z = 0. Sin normal, solo baja.
6. **Escalar** por eje en torno al centro de la caja de la pieza: la pieza no se desplaza. Factor en [0.01, 100].
7. **Validación estricta** (`design_contracts.Operation`): `extra=forbid`, números finitos, límites. Los campos que solo aplican a un tipo (`factor`, `mode`, `axes`, `normal`, `other`) se comprueban **por valor**, no por los campos enviados: el MCP vuelca el modelo completo con `model_dump()`, así que `model_fields_set` no sirve.
8. **Escala en STEP.** `Shape.scale` de build123d da el volumen correcto en memoria, pero el STEP exportado conserva la geometría original: medido, 1680 mm³ en vez de 13440 mm³ en un candidato de escala ×2. La escala B-rep va por `kernel/b123d_kernel.escalar` (`BRepBuilderAPI_GTransform`, copia). Las formas B-rep de útil se construyen en `kernel/b123d_kernel.forma_primitiva`: solo el adaptador importa build123d u OCP (ADR-0001).
9. **Frontera con la interfaz.** Cada acción de Sólido abre «Preparar una alternativa manual» en Indicaciones ya ajustada a su tipo (`prepararOperacion`). Nada cambia la geometría sin un candidato comparado. «Pedir cambio con lo seleccionado» abre Indicaciones con `piece` = pieza seleccionada (`IntentCreate` ya lo admite).
10. **Las primitivas no crean piezas.** Solo entran como útiles de booleana (apoyo o recorte). Crear una pieza nueva cambia la lista de piezas y sus roles: queda fuera de esta fase.

## Seguridad

- Entradas numéricas validadas en el contrato (finitas, con límites, normal no nula, factor 0.01–100, dimensiones ≤ 2000 mm).
- `other` debe existir en el documento (422) y no puede ser la misma pieza (422 en el contrato).
- Una auxiliar de booleana en malla debe ser un volumen cerrado y orientado; si no, 422 y no se guarda nada.
- Los nombres de pieza y de indicación llegan al DOM con `textContent`.
- Sin dependencias nuevas.

## Límites conocidos

- Alinear usa la caja envolvente, no una cara que se elige en el visor. El selector de caras para alinear llega con 24c/24d.
- Apoyar sobre cama toma la normal de una entrada numérica. Elegir la cara con un clic llega después.
- Las primitivas en malla usan 64 secciones: volumen dentro del 2 % de la forma exacta. En B-rep son exactas.
- Una booleana con otra pieza deja ambas piezas. Para un solo cuerpo, hay que borrar la auxiliar.
- Una pieza escalada o alineada en STL pierde la referencia de sus notas, como cualquier candidato de malla (`referencia_perdida`).

## Consecuencias

- Registro de la barra: 105 acciones (71 listas, 34 «Próximamente»; antes 97 con 56 y 41).
- Hoja de atajos (`?`): sin cambios, 23 filas (24b no añade atajos).
- `GET /design/capabilities` lista `primitives` además de `operations`.
- Ninguna herramienta MCP nueva. `preparar_candidato` acepta los nuevos tipos por el mismo esquema.
