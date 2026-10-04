"""MCP tool implementations for Forja (Phase 3).

Every function here is a thin wrapper over `mcp_server.client` (which talks
to the FastAPI backend over loopback HTTP — see `mcp_server/__init__.py`).
Each one returns a small dict: ids, counts, numbers, booleans, short Spanish
messages — never raw vertices/faces. Images only ever come back through
`captura`, and only as an explicit MCP image content block.

Tool set here MUST match the tool table in
`~/.claude/skills/forja/SKILL.md` — no drift (Phase 3 contract).
"""
from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import Image

from mcp_server import client


def estado() -> dict[str, Any]:
    """Estado de Forja: versiones del kernel geometrico instaladas y numero
    de documentos abiertos actualmente."""
    return client.estado()


def listar_documentos() -> list[dict[str, Any]]:
    """Lista compacta de documentos abiertos: id, nombre, volumen y numero
    de solidos de cada uno (sin bbox ni malla)."""
    return client.listar_documentos()


def abrir_archivo(ruta: str) -> dict[str, Any]:
    """Abre un archivo STEP o STL como un nuevo documento de Forja.

    `ruta` debe estar dentro de una raiz permitida: `/data/documentos`
    (almacenamiento de Forja) o `/data/fuentes` (fuentes externas montadas
    en solo lectura). Cualquier otra ruta, o un intento de traversal
    (`..`), se rechaza. Devuelve {"id", "nombre"}; usa `resumen_documento`
    para ver bbox/volumen/solidos.
    """
    return client.abrir_archivo(ruta)


def resumen_documento(
    id: str, tope_solidos: int = 30, umbral_astilla_mm3: float = 1.0
) -> dict[str, Any]:
    """Resumen compacto de un documento: nombre, bbox (mm), volumen (mm3),
    numero de solidos y si la geometria es valida. Nunca incluye vertices
    ni caras crudas.

    Cuando el documento tiene mas de un solido, agrega
    `solidos_detalle: {lista, mas, astillas}`: `lista` es
    `[{nombre, bbox, vol}]` (nombres del script si `resultado` fue un dict,
    o `solido_N` en caso contrario), con tope `tope_solidos` (30 por
    defecto, "+K mas" via el campo `mas`); `astillas` lista los nombres de
    solidos sospechosos de ser un defecto (nunca por ser chicos frente a
    OTRO solido del documento — una silla es chica al lado de una casa y
    eso no es un defecto): (1) un nombre que produjo mas de un solido
    desconectado — se marca cada fragmento salvo el mas grande de ese mismo
    nombre (la pieza se "partio"); (2) volumen menor a `umbral_astilla_mm3`
    mm3 (1.0 por defecto); o (3) el solido es delgado Y ademas casi hueco
    dentro de su propia caja delimitadora (una lamina solida como un
    estante de 2 mm o una alfombra nunca cae aca, solo un residuo casi nulo
    de una operacion booleana).

    `materiales` (solo si alguna pieza tiene uno, fdm-D):
    `{pieza: {material?, color?, extrusor?}}`; cambiarlos con
    `parametros(id, materiales={...})`.
    """
    return client.resumen_documento(id, tope_solidos=tope_solidos, umbral_astilla_mm3=umbral_astilla_mm3)


def ejecutar_script(
    codigo: str | None = None,
    ruta: str | None = None,
    timeout: float = 30.0,
    nombre: str | None = None,
    variables: dict[str, Any] | None = None,
    documento_id: str | None = None,
    actualizar_si_existe: bool = True,
) -> dict[str, Any]:
    """Ejecuta codigo Python build123d (en mm) y crea un documento nuevo a
    partir de la figura resultante, o actualiza uno existente en su sitio.

    Usar exactamente uno de `codigo` (el script completo, en texto) o
    `ruta` (un archivo `.py` ya en disco, dentro de `/data/documentos` o
    `/data/fuentes` — solo lectura; evita pegar el script entero en cada
    iteracion). `variables` (dict JSON opcional) se inyecta como variables
    globales en el script antes de ejecutarlo (p.ej. `{"MODO": "muebles"}`),
    sin tener que tocar el texto del script para cambiar un parametro.
    `documento_id` (opcional): en vez de crear un documento nuevo, actualiza
    ese documento existente en su sitio (mismo id) — re-resuelve notas/
    trazos contra la geometria nueva (Fase 4). PARA MODIFICAR una pieza
    pasa SIEMPRE su `documento_id`: conserva historial y vista en vivo.
    Si no lo pasas pero `nombre` coincide con UN documento existente, se
    actualiza ese (`actualizado_por_nombre: true`); `actualizar_si_existe=
    false` fuerza un documento nuevo.

    El script debe asignar la figura final a una variable llamada
    `resultado`: un `Shape`/`Part`/`Solid` de build123d, O un dict
    `{"nombre": Shape, ...}` (Fase 5A, solidos con nombre) — asi
    `resumen_documento`/futuras `captura`/`check_colisiones`/`exportar`
    hablan de "silla_1" en vez de "solido 14". Los nombres solo admiten
    letras/numeros/`_`/`-`/tildes/enye, unicos, maximo 64 caracteres.

    Se ejecuta en un subproceso aislado con limite de tiempo (`timeout`
    segundos, 30 por defecto) para que un script que se cuelgue nunca
    bloquee el servidor. Prefiere UN solo `ejecutar_script` con la pieza
    completa en vez de muchas llamadas granulares (ahorra tokens ~13x). Si
    falla, devuelve `{"error": true, "mensaje": "<ultima linea del
    traceback>"[, "linea": N, "codigo_linea": "..."]}` — `linea`/
    `codigo_linea` (numero y texto de la linea del script que fallo) solo
    estan presentes cuando el error viene de una excepcion dentro del
    script mismo, nunca un traceback completo.

    Perfil de impresora (FDM): todo script recibe el perfil de tolerancias
    activo como global `PERFIL` (dict; `variables={"PERFIL": "<nombre>"}`
    elige otro perfil guardado) y estas funciones, que devuelven la medida
    A MODELAR ya compensada para que la pieza impresa mida lo nominal:
    `agujero(d)` (agujero vertical; `agujero(d, horizontal=True)` para uno
    horizontal), `eje(d)` (pin/eje), `ranura(w)` (ancho de ranura) y
    `ajuste(nombre, d=None)` con `"M3_pasante"`, `"M3_roscado"` (tambien M2,
    M2.5, M4, M5), y `"eje_deslizante"`/`"eje_presion"` (con `d` = diametro
    del eje devuelve el agujero a modelar; sin `d`, la holgura en mm). Ej.:
    `Cylinder(agujero(3) / 2, 10)`. Perfiles: `GET /perfiles`.

    Arreglos FDM (tambien inyectados, con el mismo `PERFIL`):
    `agujero_gota(d, largo, eje="X"|"Y", centro=(x,y,z))` solido a RESTAR:
    agujero horizontal con techo en punta a 45 grados (sin soporte), ya
    compensado; `chaflan_base(pieza, alto=0.5)` chaflan en las aristas de
    la cara apoyada (pata de elefante; si falla devuelve la pieza igual y
    deja un aviso en `AVISOS_FDM`); `puente_sacrificio(d, z, centro=(x,y))`
    disco de 1 capa a SUMAR sobre un contrataladro impreso boca abajo (se
    perfora luego); `partir_para_cama(pieza, cama=(220,220,250),
    union="pasadores"|"cola_milano", nombre="pieza")` parte con planos lo
    que no cabe y devuelve `{"<nombre>_parte1": ..., "<nombre>_pasador1":
    ...}` listo para `resultado` (agujeros compensados con holgura
    `eje_presion`; pasadores de pie al lado). Ej.:
    `resultado = partir_para_cama(caja - agujero_gota(8, 320), nombre="caja")`.
    """
    return client.ejecutar_script(
        codigo=codigo,
        ruta=ruta,
        timeout=timeout,
        nombre=nombre,
        variables=variables,
        documento_id=documento_id,
        actualizar_si_existe=actualizar_si_existe,
    )


def exportar(id: str, formato: str, por_solido: bool = False) -> dict[str, Any]:
    """Exporta un documento a `stl`, `step` o `3mf` dentro de
    `/data/documentos/exportados` y devuelve {"ruta", "tamano_bytes"} (nunca
    los bytes del archivo). `step` solo esta disponible para documentos que
    ya tienen un solido B-rep (importados o creados como STEP); un
    documento importado como malla STL no puede exportarse a `step`.
    `3mf` (Fase 5C) genera UN solo paquete con un objeto con nombre por
    cada solido con nombre (lo que OrcaSlicer quiere), tanto con
    `por_solido=false` como `true`; responde
    `{ruta, tamano_bytes, objetos: [nombres][, objetos_mas]}`.

    `por_solido=true` (Fase 5B, solo documentos STEP con solidos con
    nombre; para `stl`/`step`): exporta UN archivo por pieza nombrada en vez de un unico
    archivo para todo el documento. Piezas geometricamente identicas (p.ej.
    4 sillas iguales del mismo script) se exportan una sola vez; la
    respuesta es `{directorio, archivos: {nombre_archivo: [nombres_de_solido, ...]}}` —
    un archivo mapeado a varios nombres significa que esa geometria se
    repite. Una pieza cuyo nombre produjo varios solidos desconectados
    (ver `solidos_detalle.astillas` en `resumen_documento`) se exporta
    igual como un solo archivo (sus fragmentos se unen primero).

    Con `stl`/`3mf` y `por_solido=false` la respuesta trae ademas
    `descarga` (ruta relativa sin token), `url_descarga` y `enlace_orca`
    (`orcaslicer://open?file=<url codificada>`). Para abrirlo en OrcaSlicer,
    desde la shell del HOST: `xdg-open '<enlace_orca>'` (Forja no lanza nada).
    La base es `FORJA_PUBLIC_URL` (por defecto `http://localhost:8710`; solo
    `http(s)://host[:puerto]`, si no se usa el valor por defecto): Orca
    descarga el archivo desde esa direccion, asi que solo sirve si Orca
    corre donde esa direccion llega a Forja. Ausentes para `step` y `por_solido`.
    """
    return client.exportar(id, formato, por_solido=por_solido)


def check_colisiones(id: str, tolerancia_mm3: float = 0.5) -> dict[str, Any]:
    """Revisa un documento STEP con solidos nombrados en busca de choques,
    piezas partidas y piezas flotantes (Fase 5B). Solo devuelve problemas:
    `{choques: [{a, b, vol}], partidas: [{pieza, solidos}], flotantes:
    [pieza], ok}` — listas vacias y `ok: true` significa que no se encontro
    nada. `choques` son pares de solidos DE DISTINTO nombre cuyo volumen de
    interseccion supera `tolerancia_mm3` (0.5 por defecto; nunca compara los
    fragmentos de una MISMA pieza partida entre si, eso es lo que reporta
    `partidas`). `flotantes` son solidos que no tocan (ni estan a menos de
    ~0.05 mm de) ningun otro solido del documento; no se calcula si el
    documento tiene un solo solido. Las listas largas se recortan con un
    campo `_mas` (p.ej. `choques_mas`); fallos puntuales del kernel booleano
    en un par especifico se reportan en `errores` sin abortar el resto.
    Solo disponible para documentos STEP con `solidos.json` (no malla STL);
    en otro caso devuelve `{error: true, mensaje}`.
    """
    return client.check_colisiones(id, tolerancia_mm3=tolerancia_mm3)


def cupon(
    id: str,
    pieza: str | None = None,
    holgura_max: float | None = None,
    margen: float | None = None,
    caja: dict[str, list[float]] | None = None,
) -> dict[str, Any]:
    """Cupon de prueba FDM: crea un documento NUEVO «Cupón — <nombre>» con
    solo las zonas donde las piezas encajan, para imprimirlas en minutos
    antes de la pieza larga. El documento original no se toca.

    Sin `caja`: busca parejas de solidos de distinto nombre que se tocan o
    estan a menos de `holgura_max` mm (1 por defecto; con `pieza`, solo las
    parejas de esa pieza), recorta cada una con su caja de interes mas
    `margen` mm (4 por defecto) y las deja sobre la cama (z=0, separadas en
    X, orientacion original) como `cupon_<pieza>` (`_zN` si hay varias
    zonas). Con `caja` = `{min: [x, y, z], max: [x, y, z]}` (mm) recorta
    con esa caja todas las piezas (o solo `pieza`).
    Devuelve `{id_nuevo, nombre, piezas: [{nombre, de, bbox}], zonas:
    [{piezas, pares: [{a, b, dist}], caja}], descarga, url_descarga,
    enlace_orca}`; sin zonas de encaje → `{error: true, mensaje}`.
    """
    return client.cupon(id, pieza=pieza, holgura_max=holgura_max, margen=margen, caja=caja)


def percibir(
    id: str,
    capas: str = "contactos",
    cortes_z: list[float] | None = None,
    eje: str = "z",
    resolucion: int = 40,
    solidos: list[str] | None = None,
    proximidad_mm: float = 5.0,
) -> dict[str, Any]:
    """Percepcion espacial como TEXTO compacto (Fase 5E) -- relaciones
    medidas (que toca a que, a cuanto), no una imagen para interpretar.
    Devuelve `{texto, tokens_aprox[, nombres_inciertos]}`. Usar ESTO
    primero, antes de `captura`: una imagen es ambigua en profundidad/
    oclusion/milimetros (un render real de `casa_v2.py` mostro una mesa de
    centro como apoyada sobre la alfombra cuando en realidad flotaba a 0.4
    mm -- `percibir` lo mide exacto).

    `capas` (string separado por comas, `"contactos"` por defecto) admite
    `contactos`, `cortes`, `huecos`:

    - `contactos`: por cada solido CON NOMBRE, su relacion de apoyo (que
      hay debajo y el hueco `h` en mm -- `h=0.0` significa que realmente
      toca; el solido base/suelo de la escena aparece como `base` en vez de
      buscarle apoyo), vecinos laterales a `proximidad_mm` mm o menos
      (5.0 por defecto), piezas `SIN APOYO` (nada debajo dentro de esa
      distancia -- se reporta igual lo mas cercano en cualquier direccion),
      y penetraciones reales (`CHOQUE`, reusando `check_colisiones`).
      Ejemplo real (`casa_v2.py`):
      ```
      casa  base
      sofa  sobre casa  h=0.0
      mesa_centro  sobre alfombra  h=0.4 (NO toca)
      ```
    - `cortes`: una rejilla ASCII de ocupacion por cada valor en `cortes_z`,
      un corte perpendicular a `eje` (`x`|`y`|`z`, `z` por defecto: corte
      horizontal, "nivel de agua"); ancho `resolucion` celdas (10-100, 40
      por defecto), alto segun el aspecto de la escena (tope 30 filas).
      Una letra por solido presente en ese corte (leyenda incluida), `.`
      vacio, `#` donde se superponen dos o mas.
    - `huecos`: no implementado en esta fase (queda en el backlog).

    Limites: `cortes_z` admite como mucho 6 valores y `proximidad_mm` como
    mucho 50; fuera de eso el backend responde 422 y la herramienta
    devuelve `{error: true, mensaje}`.

    `solidos` (lista opcional de nombres) restringe el analisis a ese
    subconjunto, igual que en `captura`. Solo disponible para documentos
    STEP con `solidos.json` (no malla STL); en otro caso devuelve
    `{error: true, mensaje}`.
    """
    return client.percibir(
        id,
        capas=capas,
        cortes_z=cortes_z,
        eje=eje,
        resolucion=resolucion,
        solidos=solidos,
        proximidad_mm=proximidad_mm,
    )


def parametros(
    id: str, valores: dict[str, Any] | None = None, materiales: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Parametros de un documento creado con un script parametrico (Fase 5C):
    iterar cambiando NUMEROS, sin volver a mandar el script.

    - `valores=None` (lectura): `{esquema, valores}` — `esquema` es
      `{nombre: {valor, min?, max?, paso?, unidad, desc?}}` tal como lo
      declaro el `PARAMETROS` del script; ambos `{}` si el documento no
      declara parametros.
    - `valores={"alto": 40, ...}` (aplicar): re-ejecuta el script guardado
      con esos valores (los no mencionados conservan el valor actual) y
      actualiza el documento EN SU SITIO (mismo id; snapshot previo en el
      historial, solidos con nombre y notas se conservan). Valida min/max
      antes de ejecutar nada. Devuelve el resumen compacto + `valores` +
      `ms` (tiempo del re-calculo en el servidor), o `{error: true,
      mensaje}` (fuera de rango, nombre desconocido, documento sin script).

    El script declara, a nivel de modulo, un dict LITERAL
    `PARAMETROS = {"alto": {"valor": 30, "min": 10, "max": 80, "paso": 1,
    "unidad": "mm", "desc": "..."}, "ancho": 20}` (forma corta = solo el
    valor) y lee los valores con `def construir(params): ... return figura`
    (preferido; Forja la llama si el script no asigna `resultado`) o con el
    dict global `PARAMS` que Forja inyecta antes de ejecutar.

    `materiales` (opcional, fdm-D): cambia el filamento de piezas con
    nombre SIN re-ejecutar el script: `{"tapa": "PETG negro", "junta":
    {"material": "TPU", "color": "#202020", "extrusor": 2}, "base": null}`
    (`null` quita la asignacion; color #RRGGBB; extrusor 1..16). Se puede
    combinar con `valores` o ir solo; la respuesta incluye `materiales`
    vigentes. Un script tambien puede declararlos con un dict literal
    `MATERIALES = {...}` a nivel de modulo. El 3MF descargado/abierto en
    Orca lleva el extrusor de cada pieza y su color.
    """
    return client.parametros(id, valores, materiales)


def check_fdm(
    id: str, boquilla: float = 0.4, cama: str = "220x220x250", angulo_max: float = 45.0
) -> dict[str, Any]:
    """Revision de imprimibilidad FDM (Fase 5C) ANTES de laminar, por solido
    con nombre, cada pieza tal como se imprimiria sola (su orientacion
    actual, apoyada en su propio Z minimo). Solo reporta problemas:
    `{ok, boquilla, cama, angulo_max, problemas: {pieza: {...}}[, problemas_mas]}`
    con, por pieza, solo las claves que fallan:

    - `cama`: no cabe en `cama` ("XxYxZ" mm) -> `{dims, sugerencia}`
      ("rotar 90 en Z", "tumbar ...", o que no cabe de ninguna forma).
    - `voladizo`: area (mm2) de caras hacia abajo mas inclinadas que
      `angulo_max` grados desde la vertical (sin contar la cara apoyada en
      la cama; los puentes cuentan) -> `{mm2, peor: {mm2, bbox, ang}}`.
    - `pared_fina`: espesor local < 2 x `boquilla` (muestreo por rayos)
      -> `{min_mm, bbox, muestras}`; aristas en filo tambien aparecen.
    - `base`: area de contacto de la primera capa < 10 mm2 -> `{contacto_mm2}`.
    - `diminuto`: alguna dimension < `boquilla` -> `{dim_min_mm}`.

    Si hay arreglo directo, `sugerencias: [{pieza, funcion, motivo}]`
    nombra la funcion del script: `agujero_gota` (agujero redondo
    horizontal) o `partir_para_cama` (no cabe en ninguna orientacion).

    Solo documentos STEP con solidos con nombre; si no, `{error, mensaje}`.
    """
    return client.check_fdm(id, boquilla=boquilla, cama=cama, angulo_max=angulo_max)


def leer_notas(id: str, detalle: bool = False) -> Any:
    """Lista compacta de notas (pines) y trazos (pizarra) de un documento.

    Cada entrada: `{n, tipo, comentario, referencia, referencia_perdida,
    visible, plano, puntos_resumen}`. `tipo` es `"nota"` para un pin o
    `"quitar"/"anadir"/"medida"/"comentario"` para un trazo de pizarra.
    `referencia` es `{tipo: "cara"|"arista"|"punto", id, punto}` (nunca
    incluye la huella geometrica interna). `referencia_perdida: true`
    significa que, tras una reconstruccion, ya no se encontro una cara/
    arista geometricamente equivalente (Fase 4, nombrado estable) — la nota
    nunca se borra sola, solo se marca. `puntos_resumen` da `n_puntos` y el
    `bbox` del trazo/punto sin mandar la polilinea completa; usa
    `detalle=true` solo si de verdad necesitas los puntos completos (cuesta
    mas tokens).
    """
    return client.leer_notas(id, detalle=detalle)


def crear_nota(id: str, comentario: str, referencia: dict[str, Any]) -> dict[str, Any]:
    """Crea una nota (pin) sobre un documento.

    `referencia` debe ser `{"tipo": "cara"|"arista"|"punto", "punto": [x,y,z]}`;
    para `"cara"`/`"arista"` conviene ademas `"id"` y `"huella"` (sacados de
    `GET /documentos/{id}/caras` vía el visor web) para que la nota
    sobreviva a una reconstruccion futura — una referencia `"punto"` es un
    punto libre en el espacio (mm) y no necesita huella. Devuelve la nota
    creada, o `{"error": true, "mensaje": "..."}` si el documento no existe
    o la referencia/comentario no pasan la validacion (limite de longitud).
    """
    return client.crear_nota(id, comentario, referencia)


def borrar_nota(id: str, nota_id: str) -> dict[str, Any]:
    """Borra una nota (pin) por su `n`. Devuelve `{"ok": true}` o
    `{"error": true, "mensaje": "..."}` si no existia."""
    return client.borrar_nota(id, nota_id)


def leer_historial(id: str) -> Any:
    """Historial compacto de versiones de un documento: `[{id, fecha,
    mensaje}]`, uno por cada cambio aceptado (creacion, actualizacion por
    script, alta/baja de notas o trazos — Fase 4). Nunca incluye el
    contenido de los archivos; usa `restaurar` para volver a un snapshot."""
    return client.leer_historial(id)


def restaurar(id: str, snapshot: str) -> dict[str, Any]:
    """Restaura un documento (archivo + notas) a un snapshot anterior de su
    `leer_historial`. El estado ACTUAL tambien queda guardado como un nuevo
    snapshot antes de restaurar, asi que restaurar nunca es un camino sin
    vuelta atras. Devuelve el resumen actualizado del documento."""
    return client.restaurar(id, snapshot)


def rama(id: str, accion: str, nombre: str | None = None, desde: str | None = None,
         a: str | None = None) -> Any:
    """Ramas y pasos de un diseño (Plan G). Cada cambio aceptado deja un
    «paso» con el estado COMPLETO (geometria, script con su texto,
    parametros, materiales, notas, ensamble) en la rama activa.

    accion:
    - `listar`: `{activa, ramas: [{nombre, activa, pasos, ultimo}]}`.
    - `crear`: rama `nombre` desde el estado actual o desde `desde`
      (rama o sha_corto de un paso). No cambia de rama.
    - `cambiar`: materializa la rama `nombre` en el documento (el estado
      actual queda guardado; se deshace con `restaurar` o volviendo).
    - `renombrar`: `nombre` -> `a` (main no). `borrar`: `nombre` (ni la
      activa ni main).
    - `pasos`: `[{sha_corto, fecha, autor, mensaje, revision}]` de la rama
      `nombre` (por defecto la activa), mas reciente primero.
    - `comparar`: diff de `desde` (A) contra `a` (B, por defecto la rama
      activa); A/B = rama o sha_corto. Devuelve piezas
      {nombre: añadida|quitada|cambiada|igual}, volumen {a,b,delta,pct},
      bbox delta, parametros y materiales cambiados (antes/despues).
    Nombres de rama: letras, numeros, - y _ (max 48)."""
    return client.rama(id, accion, nombre, desde, a)


def captura(
    id: str,
    azimut: float = 45.0,
    elevacion: float = 25.0,
    ancho: int = 512,
    alto: int = 512,
    colores_por_solido: bool = True,
    solidos: list[str] | None = None,
) -> list[Any] | dict[str, Any]:
    """Renderiza una imagen PNG (Fase 5B, reemplaza el stub de la Fase 3) del
    documento: un z-buffer por pixel en numpy (sin GPU/OpenGL, sin
    matplotlib) dentro del contenedor, un color estable por SOLIDO CON
    NOMBRE, sombreado por normal, fondo oscuro. `ancho`/`alto` entre 100 y
    2000 px (512x512 por defecto). No pidas `captura` como primer paso:
    usa `percibir` primero (Fase 5E) -- es mucho mas barato en tokens y da
    numeros exactos en vez de una imagen ambigua.

    `solidos` (lista opcional de nombres) renderiza solo esos solidos — la
    forma barata de "ver el interior" de un ensamblaje: omitir el nombre de
    la carcasa/casa para ver los muebles de adentro sin exportar nada.

    Devuelve una imagen (bloque de contenido MCP), o
    `{error: true, mensaje}` si el documento no existe, no tiene desglose de
    solidos, o algun nombre en `solidos` no existe en el documento.
    No pidas captura salvo que de verdad necesites ver la geometria: una
    imagen sigue costando muchos mas tokens que un resumen numerico.
    """
    resultado = client.captura(
        id,
        azimut=azimut,
        elevacion=elevacion,
        ancho=ancho,
        alto=alto,
        colores_por_solido=colores_por_solido,
        solidos=solidos,
    )
    if "error" in resultado:
        return resultado
    return [Image(data=resultado["png"], format="png"), resultado["caption"]]


# ---------------------------------------------------------------- Phase 6:
# assemblies and the read-only local-service bridges.


def ensamble(
    id: str,
    articulaciones: list[dict[str, Any]] | None = None,
    valores: dict[str, float] | None = None,
    quitar: bool = False,
) -> dict[str, Any]:
    """Ensamblaje de un documento (Fase 6): definirlo, posarlo, leerlo o
    quitarlo. UN solo modo por llamada, como `parametros(id, valores=None)`;
    dos modos juntos devuelven `{error: true, mensaje}` sin tocar el
    documento.

    - Sin argumentos (leer): `{id, piezas, articulaciones, valores,
      obsoleto}`. `piezas` son los solidos CON NOMBRE del documento;
      `obsoleto: true` avisa que la geometria cambio desde que se definio el
      ensamble (posar fallaria; redefine las articulaciones).
    - `articulaciones=[{...}]` (definir): de 1 a 60 articulaciones `{id,
      tipo: "fijo"|"giro"|"deslizamiento", padre, hijo, origen, eje,
      limites: [min, max], valor}`. `padre` y `hijo` deben ser solidos con
      nombre DISTINTOS del mismo documento, sin ciclos ni dos padres para el
      mismo hijo. `origen` (mm) y `eje` viven en el sistema de reposo del
      STEP inmutable del documento: el hijo gira (tipo `giro`, grados) o se
      desliza (tipo `deslizamiento`, mm) alrededor de `origen`/`eje`, y luego
      hereda el movimiento de sus ancestros. El valor inicial DEBE ser cero
      (definir no mueve la geometria; despues se posa con `valores`).
      Opcional por lado: `referencia_padre`/`referencia_hijo` `{tipo:
      "cara"|"arista", id, punto[, huella]}` para re-anclar la articulacion
      si la pieza se reconstruye (la `huella` se valida y nunca se devuelve).
      Devuelve la misma lectura; queda un snapshot previo en el historial.
    - `valores={"articulacion": numero}` (posar): aplica una pose ABSOLUTA en
      su sitio (mismo id, snapshot previo, notas re-resueltas; los valores no
      mencionados conservan el actual) y devuelve el resumen del documento +
      `valores` + `ms`. Fuera de los `limites` de una articulacion -> 422.
    - `quitar=True` (quitar): elimina las articulaciones CONSERVANDO la
      posicion actual (la geometria no se mueve) y devuelve la lectura vacia.

    Los tres modos de escritura piden el token. Fallos -> `{error: true,
    mensaje}` con el detalle en espanol del backend: `409 ensamble en formato
    anterior; quitar las articulaciones y redefinirlas` (un estado guardado
    por un Forja anterior a la Fase 6.3: quitar y redefinir es la salida),
    `409` si la geometria cambio, `422` si la definicion o la pose no pasan
    la validacion.
    """
    return client.ensamble(id, articulaciones=articulaciones, valores=valores, quitar=quitar)


def suspension(
    subida_mm: float | None = None,
    cabeceo_deg: float | None = None,
    articulacion_deg: float | None = None,
    crudo: bool = False,
) -> dict[str, Any]:
    """Suspension del mostertruck (Fase 6) por el puente de SOLO LECTURA de
    Forja (`suspension-sim`): largo y compresion de cada amortiguador cuando
    la caja sube/baja con la rueda (`subida_mm`), cabecea sobre el eje de
    ruedas (`cabeceo_deg`) o articula (`articulacion_deg`). Un numero por
    caso, en mm/grados; sin argumentos consulta una subida de 4 mm (el caso
    que verifica el signo). Los anclajes y la geometria vigentes los toma el
    backend de la config del simulador: nada se persiste ahi (`/pose` es
    calculo puro, y el puente solo admite rutas de lectura).

    Devuelve la forma compacta para actuar:
    `{signo_correcto_al_subir?, limites: [L_min, L_max], marcha: {lado:
    {L, compresion}}, subida_rueda|cabeceo_sobre_eje_ruedas|articulacion:
    [{valor, lado: {L, compresion}}], fuera_de_carrera: [{caso, valor, lado,
    L}], ms}`. `signo_correcto_al_subir` solo aparece si el simulador lo da:
    si falta, la clave se OMITE, nunca se inventa un `false` — `false`
    significa "el signo esta mal" y la ausencia "nadie lo midio", y un agente
    actua distinto ante cada una. `marcha` es el largo de cada amortiguador en
    reposo (la fila
    de valor 0, que por eso no se repite en cada caso); los casos consultados
    traen solo los valores pedidos y un caso omitido no devuelve filas ni
    avisos (nunca el barrido por defecto del simulador);
    `fuera_de_carrera` lista UNICAMENTE las
    combinaciones que se salen de carrera, y lo decide el `L` medido frente a
    `limites` (`L < L_min` o `L > L_max`), no la bandera del simulador: una
    fila cuyo `L` se sale de `limites` NUNCA se reporta como dentro, tenga o
    no `dentro` (la bandera `dentro` solo puede anadir una fila mas, nunca
    esconder una). Si queda vacia, todas las poses consultadas estan dentro
    de carrera. `crudo=true` devuelve la tabla completa del
    simulador (una fila por valor, con `dentro` y `compresion` por lado) mas
    `limites` y `ms`.

    Si el puente esta desactivado, el simulador no responde, o contesta algo
    que no es la tabla documentada — una forma rara, un texto donde va un
    numero, o un numero no finito (`Infinity`/`NaN`) — devuelve `{error:
    true, mensaje}` con el motivo (nunca una excepcion, nunca numeros
    inventados): un no finito que llega EN PROCESO se rechaza con `respuesta
    inesperada del puente de suspension (/pose)`, y si el simulador lo manda
    en vivo el backend ya no alcanza a serializarlo y el motivo es su propio
    500. `puentes()` lo dice de antemano sin gastar la llamada.
    """
    return client.suspension(
        subida_mm=subida_mm,
        cabeceo_deg=cabeceo_deg,
        articulacion_deg=articulacion_deg,
        crudo=crudo,
    )


def puentes() -> dict[str, Any]:
    """Estado de los tres puentes de Forja hacia servicios locales (Fase 6):
    `{suspension: {habilitado, disponible}, blender: {…}, kybercore: {…}}`,
    con `mensaje` cuando un servicio habilitado no responde.

    `habilitado` = el puente esta encendido por configuracion
    (`FORJA_<SERVICIO>_ENABLED`; solo `suspension` viene encendido por
    defecto); `disponible` = ademas contesta ahora mismo. Llamalo antes de
    `suspension()` para no gastar una consulta condenada: un puente
    desactivado o caido sale como `disponible: false`, nunca como excepcion.
    """
    return client.puentes()
