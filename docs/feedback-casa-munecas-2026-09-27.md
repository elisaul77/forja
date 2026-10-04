## Feedback de uso real de Forja — sesión "casa de muñecas" (27-sep-2026)

Diseñé por MCP una casa de muñecas de 2 plantas con 20 muebles (≈225 primitivos,
21 sólidos). Script: ~/Documentos/3D/forja/casa-munecas/casa_v2.py.

**Consumo medido:** 33 llamadas al modelo, 33 k tokens de salida, 2,48 M releídos de
caché (~90 k por llamada). Lo caro es el NÚMERO DE LLAMADAS, no el tamaño del script.
Unas 12 llamadas fueron evitables y 7 de ellas se fueron en suplir cosas que Forja
todavía no tiene.

**Conclusión:** hoy Forja empata en tokens con FreeCAD `execute_python` y le gana en
robustez (headless, Docker, el diseño es un .py portable). Para que además sea más
barata hace falta lo siguiente, en orden de impacto:

### 1. `captura` real (hoy es un stub) — PRIORIDAD
Tuve que improvisar 2 caminos: Chrome headless sobre `?abrir=<id>` (sale pequeño y
gris, no se ve el interior) y un render en matplotlib dentro del contenedor.
Lo que funcionó:
- teselar cada sólido, juntar TODOS los triángulos en UNA sola Poly3DCollection
  (si es una colección por sólido, el orden de pintado sale mal y los muebles
  quedan tapados por la casa)
- sombreado |n·L|, un color distinto por sólido, fondo oscuro
Propuesta: `captura(id, azimut=45, elevacion=25, ancho=800, alto=800, colores_por_solido=True)`
que devuelva un PNG pequeño. Con 1 imagen sale mucho más barato que 4 llamadas
de workaround.

### 2. `check_colisiones(id, tolerancia_mm3=0.5)`
Hice esta comprobación a mano: volumen de intersección entre cada par de sólidos,
más detectar piezas que quedan partidas en varios sólidos. Encontró 6 choques y
2 tiradores flotando que a simple vista no se veían.
Que devuelva SOLO los pares con problema, por ejemplo:
`[{a:"sofa", b:"alfombra", vol:125.6}]` y `[{pieza:"mesita", solidos:2}]`.

### 3. Sólidos con nombre
Que el script pueda asignar `resultado = {"casa": ..., "silla_1": ..., ...}`
(un dict) y Forja conserve los nombres. Así colisiones, capturas y exportación
hablan de "silla_1" y no de "sólido 14".

### 4. `resumen_documento` cuando hay más de un sólido
Si solidos > 1, añadir por sólido: nombre, bbox y volumen (compacto, con tope de
N filas). Me salió "solidos: 2" cuando esperaba 1 y tuve que abrir el STEP por
`docker exec` para descubrir que era una astilla de 800 mm³.

### 5. `ejecutar_script` desde un archivo y con variables
- `ejecutar_script(ruta="/data/fuentes/.../casa_v2.py", variables={"MODO": "muebles"})`
  para no pegar 200 líneas en cada iteración: hoy pegar el código cuesta tokens
  de salida cada vez.
- Exponer en el MCP el `documento_id` que la API HTTP ya acepta, para
  actualizar un documento en su sitio en vez de crear uno nuevo cada vez.

### 6. `exportar` por sólido
`exportar(id, "stl", por_solido=True)` → un STL por sólido nombrado, sin
duplicados: los muebles repetidos (4 sillas) salen una sola vez. Hoy lo hice
con un script aparte.

### 7. Robustez del MCP
El contenedor se reinició a mitad de la sesión y el MCP (stdio vía
`docker exec -i`) murió sin volver. Seguí por HTTP
(`POST /documentos/script` con `X-Forja-Token`). Propuestas:
- documentar esa vía de respaldo en el SKILL.md
- valorar un transporte HTTP/SSE para que el MCP sobreviva a reinicios

### 8. Errores
Hoy solo devuelve la última línea del traceback. Añadir el número de línea del
script donde falló (sigue siendo compacto y ahorra una iteración).

### Aviso para el SKILL.md (propio de build123d, no es un bug de Forja)
`extrude(Plane.YZ * perfil, amount=L)` extruye hacia −X. Usar `dir=(1,0,0)`.
Me costó una ejecución fallida.
