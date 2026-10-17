# ADR 0028 — Captura enfocada (F20) y captura del primer choque

Date: 2026-10-17. Status: implemented in F20 (branch `phase-20-captura-foco`). Extends ADR 0020 (barrido, sin cambios) and ADR 0027 (captura, sin cambios en el render por defecto). Numbering: 0026 is reserved for 24b.

## Decisión

Dos modos de imagen, ambos de solo lectura, ambos opt-in:

1. **Captura enfocada.** `captura(id, foco=[x,y,z], radio=mm)` (MCP) y `GET /documentos/{id}/captura?foco=x,y,z&radio=` recortan la misma proyección de la cámara alrededor de un punto: el encuadre es un cuadrado de lado `2 × radio` centrado en el punto proyectado. No hay cámara nueva ni vista nueva.
2. **Captura del primer choque.** `ensamble(id, barrido=..., captura_en_choque=True)` (MCP) añade **una** imagen de la pose del primer choque confirmado del barrido: solo el par que choca, encuadrado en su punto de contacto. Nunca una imagen por contacto. Sin choque confirmado no hay imagen y la respuesta lo dice (`imagenes: 0`, `motivo`).

Ninguna herramienta nueva: `captura` y `ensamble` ganan parámetros (los tests fijan el conjunto de herramientas).

### Reglas

- **`foco` y `radio` van juntos.** `foco` son tres coordenadas finitas, con |v| ≤ 1 000 000 mm. `radio` es finito y está entre 0,5 mm y 5 veces la diagonal de lo dibujado. Fuera de eso, 400 (HTTP) o `{error, mensaje}` (MCP) en español.
- **Foco fuera de la geometría.** Si el encuadre no ve ninguna pieza, se dibuja el encuadre general y la leyenda dice `foco fuera de la geometria: encuadre general`. Nunca una imagen vacía.
- **Sin `foco`, el render es byte a byte el de antes** (regresión por hash en `tests/test_captura_foco.py` y en las pruebas de F19).
- **Punto del choque.** Es el punto medio de los dos puntos más cercanos del par en la pose (`checks/distancia.distancia_y_puntos`, OCC). Para un par que se solapa la distancia es 0 y esos puntos aproximan la zona de contacto; el encuadre sigue siendo el par.
- **Radio del choque.** Por defecto, medio diagonal del par dibujado, con mínimo 0,5 mm. Se ajusta al par, no a la escena entera.
- **Vista del choque.** Sin `azimut`/`elevacion`, la captura prueba ocho vistas (cuatro cuadrantes a elevación 25° y 25° negativo) y toma la primera donde se ven las dos piezas (`render.piezas_visibles`, un marcador de color puro por pieza). Con la vista de siempre (45/25) una pieza puede quedar tapada por la otra (bisagra contra pared: el brazo queda detrás). Si ninguna vista las muestra a las dos, sale la vista 45/25 y la leyenda dice «una pieza queda tapada en las vistas probadas».
- **Colores.** Los del `captura` completo (índice sobre TODAS las piezas en su orden), no un índice nuevo del par.
- **Tope de triángulos y de PNG.** Los de `captura` (150 000 triángulos con reintento a teselación 3 veces más gruesa, y 150 KB de PNG). Sobre el tope tras el reintento: error en español, nunca degradación en silencio.
- **Nada se persiste.** La pose del choque se arma en memoria desde las formas de reposo (`pose_shapes_from_rest`). El STEP, el historial, el registro y la `revision` no cambian (hash en pruebas).

## Diseño del choque: dos llamadas, sin base64 en JSON

`captura-choque` es un `GET` separado, no un campo de `POST .../barrido`. El cliente MCP hace `POST barrido` (veredicto JSON) y luego `GET captura-choque?articulacion=&valor=&a=&b=` con el `valor` y el `par` de `primer_choque`. Así el PNG nunca viaja codificado en base64 dentro de una respuesta JSON. La pose se recalcula en el servidor con los mismos `valor` y `par`; el punto de foco sale del servidor, no de un valor que mande el cliente.

- El primer choque se toma en el orden de las articulaciones pedidas (`barrido` = "todas" en el orden de definición).
- Las dos llamadas no son atómicas. Si el documento cambia entre ellas, la imagen corresponde a la pose del `valor` pedido sobre el documento actual. Esto es una limitación conocida, no un error silencioso.
- Un fallo de la captura queda en `captura_error`; el veredicto del barrido se conserva.

## Lo que no prueba

- La imagen no demuestra que el choque sea el único: solo muestra el primer contacto del barrido.
- El punto de foco de un par que se solapa es una aproximación de la zona de contacto.
- La captura enfocada en el visor (botón «Captura enfocada» de la cinta) **no** está implementada en F20. La acción sigue `proximamente` con fase F20 hasta que haya un flujo de selección de punto en el visor; ver la nota de la cinta en CHANGELOG.

## Rechazado

- **Campo `png` en base64 dentro de `POST /barrido`.** Infla la respuesta JSON del veredicto por una imagen, y mezcla dos contratos (dato numérico y binario).
- **Nueva herramienta `captura_choque`.** Los tests fijan el conjunto de herramientas, y el modo cabe en `ensamble`.
- **Una imagen por contacto.** Ruido: el valor de la captura está en mostrar el único punto que importa, una vez.
- **Cámara propia para el foco.** Un encuadre distinto de la vista de siempre obligaría a explicar dos proyecciones; el recorte de la misma cámara es el mismo punto de vista, más cerca.
