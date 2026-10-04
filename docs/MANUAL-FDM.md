# 📘 Manual del ciclo FDM de Forja

Cuatro funciones pensadas para quien imprime en FDM: **perfil de tolerancias**, **cupones de prueba**, **arreglos FDM** y **material por pieza**.

**Antes de empezar:** abre **http://localhost:8710** y recarga con **Ctrl+Shift+R** para cargar la versión nueva. Las acciones que modifican algo (guardar un perfil, crear un cupón, cambiar un material) piden la **clave de edición** (el token). La primera vez la pegas y queda recordada mientras dure la sesión del navegador.

---

## 1️⃣ Perfil de tolerancias: que las piezas encajen en TU impresora

**Para qué sirve:** ninguna impresora imprime exacto. Los agujeros salen un poco más chicos y los ejes un poco más gruesos. El perfil mide eso una vez y compensa todos tus diseños automáticamente.

### Paso a paso
1. Pulsa **«Perfil de impresora»**, en la barra superior.
2. Pulsa **«Generar probeta»**. Se crea el documento **«Calibración de tolerancias»** (74×54×13 mm) con:
   - agujeros verticales de Ø3, Ø5 y Ø8;
   - agujeros horizontales de Ø3, Ø5 y Ø8, en un bloque aparte;
   - pines de Ø3, Ø5 y Ø8;
   - ranuras de 2, 3 y 5 mm;
   - una fila de agujeros de Ø5 con holguras de +0,1 a +0,5 mm, más un pin suelto de 5 mm para probar encajes.
3. Ábrela en Orca con **«Abrir en Orca»** e imprímela con tu perfil y filamento habituales. Tarda unos 30 minutos (estimado, sin laminar).
4. Mide con el calibrador cada agujero, pin y ranura. Cada elemento lleva su número grabado en relieve.
5. Vuelve a **«Perfil de impresora»** y anota:
   - las medidas reales en sus casillas;
   - qué holgura encajó **deslizante**: el pin entra y gira suave;
   - qué holgura encajó **a presión**: entra apretado y no se sale;
   - material, boquilla y altura de capa.
6. Pulsa **«Guardar y activar»**.

> Mientras no midas, se usa un perfil **semilla** para Ender-3 V3 SE con PLA: agujero −0,15 mm, eje +0,05 mm, deslizante 0,25 mm y presión 0,10 mm.

### Cómo lo usa el agente
Dentro de un script, en lugar de escribir medidas fijas:

```python
caja - Cylinder(agujero(3) / 2, 10)     # agujero de 3 mm que sale de 3 mm
Cylinder(eje(5) / 2, 20)                # eje de 5 mm real
ranura(3)                               # ancho a modelar para una ranura de 3 mm
ajuste("M3_pasante")                    # tornillo M3 que pasa libre
ajuste("M3_roscado")                    # M3 que rosca en el plástico
ajuste("eje_deslizante", 8)             # agujero para un eje de 8 que gira
ajuste("eje_presion", 8)                # agujero para un eje de 8 a presión
```

También están M2, M2.5, M4 y M5. Para agujeros acostados se usa `agujero(d, horizontal=True)`.

**Por REST:** `GET /perfiles`, `GET /perfiles/{nombre}`, `POST /perfiles` y `POST /perfiles/probeta` (las dos últimas con token).

---

## 2️⃣ Cupón de prueba: comprobar el encaje antes de imprimir horas

**Para qué sirve:** antes de imprimir un chasis de 6 horas, imprimes en minutos solo la parte donde dos piezas encajan.

### Paso a paso
1. Abre un documento con varias piezas, como un ensamble.
2. *(Recomendado)* Haz clic en la pieza que quieres probar para seleccionarla. Si no seleccionas ninguna, Forja busca todas las zonas de encaje del documento.
3. Pulsa **«Cupón de prueba»**. En ensambles grandes puede tardar hasta unos 2 minutos.
4. Se abre una pestaña nueva **«Cupón — <nombre>»** con los trozos recortados (`cupon_<pieza>`), apoyados y ordenados para una cama de 220×220.
5. Pulsa **«Abrir en Orca»**, imprime y prueba el encaje.
6. **Si encaja**, imprime la pieza completa. **Si no**, ajusta la holgura en el diseño o en el perfil, y repite.

### Qué detecta
Las piezas que se tocan o quedan a menos de **1 mm** entre sí. El recorte deja **4 mm** de margen alrededor de la zona. El documento original no se modifica.

### Avisos
- *«sin zonas de encaje»*: las piezas no se tocan, por ejemplo un tornillo y una tuerca puestos uno al lado del otro.
- *«no cabe en 220×220»*: el cupón es demasiado grande. Selecciona una pieza concreta en lugar de pedir todo el documento.

> 📌 Las piezas no se giran a propósito: un eje cortado y acostado sale ovalado, y eso falsearía la prueba de encaje.

**Por MCP:** el agente usa `cupon(id, pieza="L_Tuerca_rotula_1_v5")`. También acepta `holgura_max`, `margen` o una `caja` exacta en mm, y devuelve el enlace para Orca.
**Por REST:** `POST /documentos/{id}/cupon` (con token).

---

## 3️⃣ Arreglos FDM: piezas que se imprimen sin soportes ni defectos

**Para qué sirve:** son funciones que el agente usa dentro del script para que la geometría ya salga pensada para FDM. Usan tu perfil activo cuando aplica.

| Función | Qué resuelve | Ejemplo |
|---|---|---|
| `agujero_gota(d, largo, eje="X")` | Agujero **horizontal sin soporte**: techo en punta a 45°, compensado con tu perfil | `caja - agujero_gota(8, 120)` |
| `chaflan_base(pieza, 0.5)` | **Pata de elefante**: bisela la base apoyada en la cama. Si no puede, deja la pieza igual y avisa | `pieza = chaflan_base(pieza, 0.4)` |
| `puente_sacrificio(d, z)` | **Avellanados boca abajo**: una capa fina que tapa el agujero y luego se rompe con una broca | `pieza + puente_sacrificio(6, 3)` |
| `partir_para_cama(pieza, union=...)` | **Pieza más grande que la cama**: la corta en partes que caben y añade pasadores o cola de milano | `resultado = partir_para_cama(caja, nombre="caja")` |

### `partir_para_cama` en detalle

```python
resultado = partir_para_cama(caja_300mm, nombre="caja",
                             union="pasadores")   # o "cola_milano"
```

Devuelve `caja_parte1`, `caja_parte2`, … y `caja_pasador1`, `caja_pasador2`, …

- **Pasadores:** los agujeros se compensan con tu perfil. La holgura es a presión por defecto; usa `holgura="deslizante"` para que entren suaves. Los pasadores salen de pie al lado de las partes, listos para imprimir.
- **Cola de milano:** solo en cortes en X o Y. Si falla, usa pasadores.
- Prueba la rotación de 90° si así salen menos partes.

### `check_fdm` te sugiere la función
La respuesta de `check_fdm` incluye `sugerencias`. Si ve un agujero horizontal redondo, recomienda `agujero_gota`. Si la pieza no cabe en la cama, recomienda `partir_para_cama`.

> Los avisos de estas funciones quedan en la lista `AVISOS_FDM` dentro del script.
> `voladizos_a_45` no existe todavía: reescribir caras arbitrarias no es fiable.

---

## 4️⃣ Material por pieza: multicolor y multimaterial listo para Orca

**Para qué sirve:** cada pieza con nombre lleva su filamento, color y extrusor, y llega a Orca ya asignada.

### Desde el script

```python
resultado = {"cuerpo": ..., "tapa": ..., "junta": ...}

MATERIALES = {
    "cuerpo": {"material": "PLA", "color": "#F0F0F0", "extrusor": 1},
    "tapa":   "PETG negro",                          # forma corta: solo texto
    "junta":  {"material": "TPU", "color": "#202020", "extrusor": 2},
}
```

- `color` va en formato `#RRGGBB`, y `extrusor` es un número de 1 a 16.
- Un nombre que no corresponde a ninguna pieza se ignora y se avisa en `materiales_ignorados`.

### Desde el visor (sin tocar el script)
1. Abre el documento y pulsa **«Piezas»**.
2. Cada pieza muestra un punto con su color.
3. Selecciona una pieza. En su detalle aparecen **material**, **color** y **extrusor**.
4. Pulsa **«Guardar material»** para guardar, o **«Quitar»** para borrarlo.

El visor pinta cada pieza con su color.

### Qué pasa al exportar
- **«Abrir en Orca» / «Descargar 3MF»** incluyen el color y el extrusor de cada objeto. También al descargar una sola pieza.
- Los materiales quedan en el historial: al **restaurar** una versión vuelven los materiales que tenía.
- Si vuelves a ejecutar el script con la misma declaración, tus cambios manuales se conservan. Si cambias `MATERIALES` en el script, esa declaración manda.

**Por MCP:** el agente usa `parametros(id, materiales={"tapa": {"extrusor": 2}})` para cambiarlos sin reconstruir, y `resumen_documento` los muestra.
**Por REST:** `GET /documentos/{id}/materiales` y `POST /documentos/{id}/materiales` (con token).

> ⚠️ Pendiente de comprobar en OrcaSlicer que el extrusor asignado se respeta. Los colores viajan en el formato estándar 3MF (`basematerials`) y el extrusor en `Metadata/model_settings.config`.

---

## 🔁 Flujo recomendado para una pieza nueva

1. **Una vez:** imprime la probeta y guarda tu perfil.
2. Pídele la pieza al agente. Usará `agujero()`, `ajuste()` y las funciones FDM.
3. Mira el resultado en vivo y marca lo que quieras cambiar con la pizarra.
4. Genera un **cupón de prueba** de las zonas de encaje: imprime, prueba y ajusta.
5. Asigna los materiales si es multicolor.
6. **Abrir en Orca** e imprime la pieza completa.
