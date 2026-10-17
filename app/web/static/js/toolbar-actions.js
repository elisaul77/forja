// Forja — registro único de la barra de herramientas (fase 24a).
// Datos puros: ni DOM ni red. La cinta, los tooltips, los atajos, la paleta
// (Ctrl+K) y la hoja de atajos (?) se generan desde esta lista.
//
// Contexto `c` que entrega toolbar-ui.js a cada `ejecutar(c)`:
//   c.visor()          visor 3D del documento activo (o null)
//   c.entrada()        pestaña activa: { acciones: {nota, pizarra, cupon}, animation } (o null)
//   c.pieza()          nombre de la pieza seleccionada (o null)
//   c.clic(id)         pulsa un control existente de la cabecera (#fj-btn-*)
//   c.panel(seccion)   abre un panel lateral del documento activo
//   c.mostrar(texto, esError)  mensaje en la franja de estado
//   c.exportar(ext)   descarga el documento activo (3mf | stl)
//   c.orca()           abre el documento activo en OrcaSlicer
//   c.captura()        descarga una captura PNG del visor
//   c.propiedades()    muestra volumen y sólidos del documento activo
//   c.enfoque()        alterna el modo de pantalla completa del visor
//   c.elegirCara(fn)  siguiente clic sobre el visor -> fn(golpe)
//   c.cancelar()       cancela la herramienta (picking, notas, pizarra) y la selección
//   c.operacion(p)    abre «Preparar una alternativa manual» (Indicaciones) ajustada a p = {tipo, primitiva?, otra?}
//   c.pedirCambio()   abre Indicaciones con la pieza seleccionada como referencia
//
// `activo(c)`: estado de un botón de conmutación (aria-pressed), solo con documento abierto.
// Estados: "listo" (ejecuta) | "proximamente" (visible y deshabilitada, con
// `fase`). `requiere`: "documento" | "seleccion" | "b-rep" | "malla".

export const PESTANAS = [
  { id: 'vista', etiqueta: 'Vista' },
  { id: 'croquis', etiqueta: 'Croquis' },
  { id: 'solido', etiqueta: 'Sólido' },
  { id: 'ensamble', etiqueta: 'Ensamble' },
  { id: 'inspeccionar', etiqueta: 'Inspeccionar' },
  { id: 'planos', etiqueta: 'Planos' },
  { id: 'imprimir', etiqueta: 'Imprimir' },
  { id: 'claude', etiqueta: 'Claude', cluster: true },
];

const DOC = ['documento'];
const PIEZA = ['documento', 'seleccion'];

const listo = (def) => ({ estado: 'listo', ...def });
const pronto = (fase, def) => ({ estado: 'proximamente', fase, ...def });

const MOTIVO_SIN_DESHACER = 'No hay ninguna revisión anterior a la que volver.';
const MOTIVO_SIN_REHACER = 'No hay nada que rehacer: una edición nueva borra el rehacer.';
// `historia` lo rellena toolbar-ui.js desde el cursor del servidor; `error` si no se pudo leer.
const cursorHistoria = (campo, sinPaso) => (estado) => {
  const historia = estado.historia;
  if (!historia) return 'Cargando el historial…';
  if (historia.error) return historia.error;
  return historia[campo] ? null : sinPaso;
};

export const ACCIONES = [
  // ---------------- Vista ----------------
  listo({ id: 'vista-abrir', etiqueta: 'Abrir archivo', pestana: 'vista', grupo: 'Archivo', icono: 'folder',
    tooltip: 'Sube un modelo STEP o STL desde tu equipo.', ejecutar: (c) => c.clic('fj-btn-abrir-archivo') }),
  listo({ id: 'vista-documentos', etiqueta: 'Documentos', pestana: 'vista', grupo: 'Archivo', icono: 'stack',
    tooltip: 'Abre la lista de documentos guardados en este servidor.', ejecutar: (c) => c.clic('fj-btn-documentos') }),
  listo({ id: 'vista-galeria', etiqueta: 'Galería', pestana: 'vista', grupo: 'Archivo', icono: 'grid',
    tooltip: 'Muestra la galería de modelos guardados.', ejecutar: (c) => c.clic('fj-btn-galeria') }),
  listo({ id: 'vista-perfil', etiqueta: 'Perfil de impresora', pestana: 'vista', grupo: 'Archivo', icono: 'printer',
    tooltip: 'Abre las tolerancias y medidas de tu impresora.', ejecutar: (c) => c.clic('fj-btn-perfil') }),
  listo({ id: 'vista-hito', etiqueta: 'Guardar versión (hito)', pestana: 'vista', grupo: 'Archivo', icono: 'flag',
    requiere: DOC, tooltip: 'Abre el historial para marcar un hito en la versión actual.',
    ejecutar: (c) => c.panel('historial') }),

  listo({ id: 'vista-ajustar', etiqueta: 'Ajustar a pantalla', pestana: 'vista', grupo: 'Cámara', icono: 'frame',
    atajo: 'F', requiere: DOC, tooltip: 'Encuadra todo el modelo en el visor.',
    ejecutar: (c) => c.visor().ajustarVista(c.visor().direccionHaciaCamara()) }),
  listo({ id: 'vista-frontal', etiqueta: 'Frontal', pestana: 'vista', grupo: 'Vistas estándar', icono: 'cube',
    atajo: '1', requiere: DOC, tooltip: 'Mira el modelo desde delante.', ejecutar: (c) => c.visor().setVistaEstandar('frontal') }),
  listo({ id: 'vista-posterior', etiqueta: 'Posterior', pestana: 'vista', grupo: 'Vistas estándar', icono: 'cube',
    atajo: '2', requiere: DOC, tooltip: 'Mira el modelo desde detrás.', ejecutar: (c) => c.visor().setVistaEstandar('posterior') }),
  listo({ id: 'vista-izquierda', etiqueta: 'Izquierda', pestana: 'vista', grupo: 'Vistas estándar', icono: 'cube',
    atajo: '3', requiere: DOC, tooltip: 'Mira el modelo desde la izquierda.', ejecutar: (c) => c.visor().setVistaEstandar('izquierda') }),
  listo({ id: 'vista-derecha', etiqueta: 'Derecha', pestana: 'vista', grupo: 'Vistas estándar', icono: 'cube',
    atajo: '4', requiere: DOC, tooltip: 'Mira el modelo desde la derecha.', ejecutar: (c) => c.visor().setVistaEstandar('derecha') }),
  listo({ id: 'vista-superior', etiqueta: 'Superior', pestana: 'vista', grupo: 'Vistas estándar', icono: 'cube',
    atajo: '5', requiere: DOC, tooltip: 'Mira el modelo desde arriba.', ejecutar: (c) => c.visor().setVistaEstandar('superior') }),
  listo({ id: 'vista-inferior', etiqueta: 'Inferior', pestana: 'vista', grupo: 'Vistas estándar', icono: 'cube',
    atajo: '6', requiere: DOC, tooltip: 'Mira el modelo desde abajo.', ejecutar: (c) => c.visor().setVistaEstandar('inferior') }),
  listo({ id: 'vista-isometrica', etiqueta: 'Isométrica', pestana: 'vista', grupo: 'Vistas estándar', icono: 'cube',
    atajo: '0', requiere: DOC, tooltip: 'Vista isométrica por defecto.', ejecutar: (c) => c.visor().setVistaEstandar('isometrica') }),
  listo({ id: 'vista-mirar-cara', etiqueta: 'Mirar de frente a una cara', pestana: 'vista', grupo: 'Cámara', icono: 'face',
    requiere: DOC, tooltip: 'Después, haz clic en una cara para mirarla de frente.',
    ejecutar: (c) => c.elegirCara((golpe) => { if (golpe?.normal) c.visor().mirarDeFrente(golpe.normal); }) }),
  listo({ id: 'vista-restaurar', etiqueta: 'Restaurar vista', pestana: 'vista', grupo: 'Cámara', icono: 'reset-cam',
    requiere: DOC, tooltip: 'Vuelve a la cámara que tenías antes de mirar de frente.', ejecutar: (c) => c.visor().restaurarVista() }),
  listo({ id: 'vista-ortografica', activo: (c) => c.visor().ortogonal, etiqueta: 'Ortográfica / perspectiva', pestana: 'vista', grupo: 'Cámara', icono: 'perspective',
    atajo: 'P', requiere: DOC, tooltip: 'Alterna entre proyección ortográfica y perspectiva.',
    ejecutar: (c) => c.visor().setOrtogonal(!c.visor().ortogonal) }),

  listo({ id: 'vista-alambre', activo: (c) => c.visor().wireframe, etiqueta: 'Alambre', pestana: 'vista', grupo: 'Estilo', icono: 'wire',
    atajo: 'W', requiere: DOC, tooltip: 'Muestra solo la malla de triángulos.', ejecutar: (c) => c.visor().setWireframe(!c.visor().wireframe) }),
  listo({ id: 'vista-aristas', activo: (c) => c.visor().aristas, etiqueta: 'Aristas vivas', pestana: 'vista', grupo: 'Estilo', icono: 'edges',
    requiere: DOC, tooltip: 'Marca las aristas con un ángulo mayor de 30°.', ejecutar: (c) => c.visor().setAristas(!c.visor().aristas) }),
  listo({ id: 'vista-rayos-x', activo: (c) => c.visor().rayosX, etiqueta: 'Rayos X', pestana: 'vista', grupo: 'Estilo', icono: 'ghost',
    requiere: DOC, tooltip: 'Vuelve el modelo translúcido para ver el interior.', ejecutar: (c) => c.visor().setRayosX(!c.visor().rayosX) }),
  listo({ id: 'vista-rejilla', activo: (c) => c.visor().grid.visible, etiqueta: 'Rejilla', pestana: 'vista', grupo: 'Estilo', icono: 'grid-lines',
    atajo: 'G', requiere: DOC, tooltip: 'Muestra u oculta la rejilla del suelo.', ejecutar: (c) => c.visor().setGridVisible(!c.visor().grid.visible) }),
  listo({ id: 'vista-ejes', activo: (c) => c.visor().ejes.visible, etiqueta: 'Ejes', pestana: 'vista', grupo: 'Estilo', icono: 'axes',
    requiere: DOC, tooltip: 'Muestra u oculta el triedro de ejes.', ejecutar: (c) => c.visor().setEjes(!c.visor().ejes.visible) }),
  listo({ id: 'vista-colores', etiqueta: 'Colores por pieza', pestana: 'vista', grupo: 'Estilo', icono: 'palette',
    requiere: DOC, tooltip: 'Abre el panel Piezas para cambiar el color de cada pieza.', ejecutar: (c) => c.panel('piezas') }),
  listo({ id: 'vista-materiales', etiqueta: 'Materiales', pestana: 'vista', grupo: 'Estilo', icono: 'swatch',
    requiere: DOC, tooltip: 'Abre el panel Piezas para asignar material y filamento.', ejecutar: (c) => c.panel('piezas') }),

  listo({ id: 'vista-plano-corte', activo: (c) => c.visor().corteActivo, etiqueta: 'Plano de corte', pestana: 'vista', grupo: 'Sección', icono: 'section',
    atajo: 'X', requiere: DOC, tooltip: 'Corta el modelo con un plano horizontal al centro; vuelve a pulsar para quitarlo.',
    ejecutar: (c) => {
      const v = c.visor();
      if (v.corteActivo) { v.desactivarCorte(); v.restaurarVista(); return; }
      v.activarCorte(v.centroEscena(), [0, 0, 1]);
    } }),

  listo({ id: 'vista-aislar', etiqueta: 'Aislar pieza', pestana: 'vista', grupo: 'Visibilidad', icono: 'eye',
    atajo: 'I', requiere: PIEZA, tooltip: 'Muestra solo la pieza seleccionada.', ejecutar: (c) => c.visor().isolatePiece(c.pieza()) }),
  listo({ id: 'vista-mostrar-todo', etiqueta: 'Mostrar todo', pestana: 'vista', grupo: 'Visibilidad', icono: 'eye',
    atajo: 'Alt+I', requiere: DOC, tooltip: 'Quita el aislamiento y muestra todas las piezas.', ejecutar: (c) => c.visor().showAllPieces() }),
  listo({ id: 'vista-ocultar', etiqueta: 'Ocultar pieza', pestana: 'vista', grupo: 'Visibilidad', icono: 'eye',
    atajo: 'H', requiere: PIEZA, tooltip: 'Oculta la pieza seleccionada sin borrarla.',
    ejecutar: (c) => c.visor().setPieceVisible(c.pieza(), false) }),
  listo({ id: 'vista-comparar', etiqueta: 'Comparar revisiones', pestana: 'vista', grupo: 'Comparar', icono: 'split',
    requiere: DOC, tooltip: 'Abre el historial para comparar dos revisiones superpuestas o divididas.',
    ejecutar: (c) => c.panel('historial') }),

  listo({ id: 'vista-captura', etiqueta: 'Captura de pantalla', pestana: 'vista', grupo: 'Salida', icono: 'camera',
    atajo: 'C', requiere: DOC, tooltip: 'Descarga una imagen PNG de la vista actual.', ejecutar: (c) => c.captura() }),
  listo({ id: 'vista-enfoque', activo: (c) => c.enfoqueActivo(), etiqueta: 'Pantalla completa', pestana: 'vista', grupo: 'Ventana', icono: 'expand',
    tooltip: 'Oculta los paneles para dejar solo el visor. Pulsa de nuevo para volver.', ejecutar: (c) => c.enfoque() }),
  pronto('F20', { id: 'vista-captura-enfocada', etiqueta: 'Captura enfocada', pestana: 'vista', grupo: 'Salida', icono: 'camera',
    tooltip: 'Captura enfocada en un punto del modelo.' }),

  // ---------------- Croquis (24c) ----------------
  pronto('24c', { id: 'croq-plano', etiqueta: 'Plano de croquis', pestana: 'croquis', grupo: 'Plano', icono: 'sketch',
    tooltip: 'Elige la cara o un plano base donde dibujar.' }),
  pronto('24c', { id: 'croq-linea', etiqueta: 'Línea', pestana: 'croquis', grupo: 'Dibujar', icono: 'sketch',
    tooltip: 'Dibuja una línea sobre el plano de croquis.' }),
  pronto('24c', { id: 'croq-rectangulo', etiqueta: 'Rectángulo', pestana: 'croquis', grupo: 'Dibujar', icono: 'sketch',
    tooltip: 'Dibuja un rectángulo sobre el plano de croquis.' }),
  pronto('24c', { id: 'croq-circulo', etiqueta: 'Círculo', pestana: 'croquis', grupo: 'Dibujar', icono: 'sketch',
    tooltip: 'Dibuja un círculo sobre el plano de croquis.' }),
  pronto('24c', { id: 'croq-cota', etiqueta: 'Cota de croquis', pestana: 'croquis', grupo: 'Restricciones', icono: 'sliders',
    tooltip: 'Fija una medida del croquis enlazada a un parámetro.' }),
  pronto('24c', { id: 'croq-extruir', etiqueta: 'Extruir', pestana: 'croquis', grupo: 'Convertir', icono: 'solid',
    tooltip: 'Convierte un perfil cerrado en un sólido.' }),
  pronto('24c', { id: 'croq-revolucion', etiqueta: 'Revolución', pestana: 'croquis', grupo: 'Convertir', icono: 'solid',
    tooltip: 'Gira un perfil cerrado alrededor de un eje.' }),

  // ---------------- Sólido (24b, 24d, 24e) ----------------
  // 24b (ADR-0026): cada acción abre «Preparar una alternativa manual» ya ajustada a su
  // tipo. Nunca cambia la geometría sola: el resultado es un candidato que se compara antes de aplicar.
  listo({ id: 'solido-mover', etiqueta: 'Mover / Rotar', pestana: 'solido', grupo: 'Transformar', icono: 'move',
    requiere: PIEZA, tooltip: 'Mueve o gira la pieza seleccionada; el resultado es un candidato que puedes revisar.',
    ejecutar: (c) => c.operacion({ tipo: 'transform' }) }),
  listo({ id: 'solido-escala', etiqueta: 'Escalar', pestana: 'solido', grupo: 'Transformar', icono: 'scale',
    requiere: PIEZA, tooltip: 'Escala la pieza por eje, en torno a su centro. Un factor 1 no cambia ese eje.',
    ejecutar: (c) => c.operacion({ tipo: 'scale' }) }),
  listo({ id: 'solido-alinear', etiqueta: 'Alinear', pestana: 'solido', grupo: 'Transformar', icono: 'align',
    requiere: PIEZA, tooltip: 'Alinea centros o caras con otra pieza, o la apila sobre ella.',
    ejecutar: (c) => c.operacion({ tipo: 'align' }) }),
  listo({ id: 'solido-apoyar', etiqueta: 'Apoyar sobre cama', pestana: 'solido', grupo: 'Transformar', icono: 'bed',
    requiere: PIEZA, tooltip: 'Baja la pieza hasta que su base quede en la cama (Z = 0). Puedes indicar la cara que apoya.',
    ejecutar: (c) => c.operacion({ tipo: 'drop_to_bed' }) }),
  listo({ id: 'solido-caja', etiqueta: 'Caja', pestana: 'solido', grupo: 'Crear', icono: 'box',
    requiere: PIEZA, tooltip: 'Añade una caja como apoyo (unión) a la pieza seleccionada.',
    ejecutar: (c) => c.operacion({ tipo: 'union', primitiva: 'box' }) }),
  listo({ id: 'solido-cilindro', etiqueta: 'Cilindro', pestana: 'solido', grupo: 'Crear', icono: 'cyl',
    requiere: PIEZA, tooltip: 'Añade un cilindro como apoyo (unión) a la pieza seleccionada.',
    ejecutar: (c) => c.operacion({ tipo: 'union', primitiva: 'cylinder' }) }),
  listo({ id: 'solido-esfera', etiqueta: 'Esfera', pestana: 'solido', grupo: 'Crear', icono: 'sphere',
    requiere: PIEZA, tooltip: 'Añade una esfera como apoyo (unión) a la pieza seleccionada.',
    ejecutar: (c) => c.operacion({ tipo: 'union', primitiva: 'sphere' }) }),
  listo({ id: 'solido-cono', etiqueta: 'Cono', pestana: 'solido', grupo: 'Crear', icono: 'cone',
    requiere: PIEZA, tooltip: 'Añade un cono (punta arriba) como apoyo (unión).',
    ejecutar: (c) => c.operacion({ tipo: 'union', primitiva: 'cone' }) }),
  listo({ id: 'solido-toro', etiqueta: 'Toro', pestana: 'solido', grupo: 'Crear', icono: 'torus',
    requiere: PIEZA, tooltip: 'Añade un toro como apoyo (unión).',
    ejecutar: (c) => c.operacion({ tipo: 'union', primitiva: 'torus' }) }),
  listo({ id: 'solido-cuna', etiqueta: 'Cuña', pestana: 'solido', grupo: 'Crear', icono: 'wedge',
    requiere: PIEZA, tooltip: 'Añade una cuña triangular como apoyo (unión).',
    ejecutar: (c) => c.operacion({ tipo: 'union', primitiva: 'wedge' }) }),
  listo({ id: 'solido-tubo', etiqueta: 'Tubo', pestana: 'solido', grupo: 'Crear', icono: 'tube',
    requiere: PIEZA, tooltip: 'Añade un tubo (anillo hueco) como apoyo (unión).',
    ejecutar: (c) => c.operacion({ tipo: 'union', primitiva: 'tube' }) }),
  listo({ id: 'solido-unir', etiqueta: 'Unir', pestana: 'solido', grupo: 'Booleanas', icono: 'boolean',
    requiere: PIEZA, tooltip: 'Une la pieza seleccionada con una primitiva o con otra pieza. La otra pieza no cambia.',
    ejecutar: (c) => c.operacion({ tipo: 'union' }) }),
  listo({ id: 'solido-restar', etiqueta: 'Restar', pestana: 'solido', grupo: 'Booleanas', icono: 'boolean',
    requiere: PIEZA, tooltip: 'Resta una primitiva o otra pieza de la pieza seleccionada. La otra pieza no cambia.',
    ejecutar: (c) => c.operacion({ tipo: 'subtract' }) }),
  listo({ id: 'solido-intersecar', etiqueta: 'Intersectar', pestana: 'solido', grupo: 'Booleanas', icono: 'boolean',
    requiere: PIEZA, tooltip: 'Conserva solo la parte común con una primitiva u otra pieza.',
    ejecutar: (c) => c.operacion({ tipo: 'intersect' }) }),
  pronto('24d', { id: 'solido-filete', etiqueta: 'Filete', pestana: 'solido', grupo: 'Modificar', icono: 'fillet',
    tooltip: 'Redondea las aristas seleccionadas.' }),
  pronto('24d', { id: 'solido-chaflan', etiqueta: 'Chaflán', pestana: 'solido', grupo: 'Modificar', icono: 'fillet',
    tooltip: 'Biselado recto en las aristas seleccionadas.' }),
  pronto('24d', { id: 'solido-simetria', etiqueta: 'Simetría', pestana: 'solido', grupo: 'Patrón', icono: 'mirror',
    tooltip: 'Refleja la pieza respecto a un plano.' }),
  pronto('24e', { id: 'solido-agujero-gota', etiqueta: 'Agujero en gota', pestana: 'solido', grupo: 'Orificios', icono: 'hole',
    tooltip: 'Taladro con forma de gota, imprimible en vertical.' }),
  pronto('24e', { id: 'solido-inserto', etiqueta: 'Alojamiento de inserto', pestana: 'solido', grupo: 'Orificios', icono: 'hole',
    tooltip: 'Hueco para un inserto roscado (M2 a M5).' }),

  // ---------------- Ensamble ----------------
  listo({ id: 'ens-articulacion', etiqueta: 'Articulación', pestana: 'ensamble', grupo: 'Articulaciones', icono: 'joint',
    tooltip: 'Abre el panel Ensamble para definir fija, giro o deslizamiento.', ejecutar: (c) => c.panel('ensamble') }),
  listo({ id: 'ens-pose', etiqueta: 'Pose y deslizadores', pestana: 'ensamble', grupo: 'Articulaciones', icono: 'sliders',
    tooltip: 'Abre el panel Ensamble para fijar la pose de cada articulación.', ejecutar: (c) => c.panel('ensamble') }),
  listo({ id: 'ens-reproducir', etiqueta: 'Reproducir movimiento', pestana: 'ensamble', grupo: 'Movimiento', icono: 'play',
    requiere: DOC, tooltip: 'Reproduce la animación del ensamble.',
    ejecutar: (c) => { const e = c.entrada(); if (!e) return; e.animation.play(); } }),
  listo({ id: 'ens-comprobar', etiqueta: 'Comprobar posición', pestana: 'ensamble', grupo: 'Movimiento', icono: 'check',
    tooltip: 'Abre el panel Ensamble para comprobar choques y contactos.', ejecutar: (c) => c.panel('ensamble') }),
  listo({ id: 'ens-biblioteca', etiqueta: 'Insertar pieza de biblioteca', pestana: 'ensamble', grupo: 'Piezas', icono: 'library',
    tooltip: 'Abre la galería para insertar una pieza guardada.', ejecutar: (c) => c.clic('fj-btn-galeria') }),
  pronto('F18', { id: 'ens-barrido', etiqueta: 'Barrido continuo', pestana: 'ensamble', grupo: 'Movimiento', icono: 'play',
    tooltip: 'Recorre una articulación y busca choques a lo largo del recorrido.' }),
  listo({ id: 'ens-explosionada', activo: (c) => c.entrada()?.acciones?.explosion?.getAttribute('aria-pressed') === 'true', etiqueta: 'Vista explosionada', pestana: 'ensamble', grupo: 'Vista', icono: 'split', requiere: ['documento', 'b-rep'], tooltip: 'Separa las piezas para ver cómo encajan.', ejecutar: (c) => c.entrada()?.acciones?.explosion?.click() }),
  pronto('24f', { id: 'ens-mate', etiqueta: 'Unir caras (mate)', pestana: 'ensamble', grupo: 'Restricciones', icono: 'joint',
    tooltip: 'Une dos caras: coincidentes, concéntricas o a distancia.' }),
  pronto('24f', { id: 'ens-holgura', etiqueta: 'Holgura entre piezas', pestana: 'ensamble', grupo: 'Piezas', icono: 'gap',
    tooltip: 'Mapa de calor de la holgura entre piezas.' }),
  pronto('24f', { id: 'ens-bom', etiqueta: 'Lista de piezas (BOM)', pestana: 'ensamble', grupo: 'Piezas', icono: 'bom',
    tooltip: 'Tabla con volumen, masa y filamento por pieza.' }),

  // ---------------- Inspeccionar ----------------
  listo({ id: 'inspec-seleccionar', etiqueta: 'Seleccionar', pestana: 'inspeccionar', grupo: 'Seleccionar', icono: 'cursor',
    atajo: 'S', tooltip: 'Vuelve al modo de selección: cancela la herramienta activa.', ejecutar: (c) => c.cancelar() }),
  listo({ id: 'inspec-cancelar', etiqueta: 'Cancelar herramienta', pestana: 'inspeccionar', grupo: 'Seleccionar', icono: 'cursor',
    atajo: 'Esc', tooltip: 'Cancela la herramienta activa y quita la selección.', ejecutar: (c) => c.cancelar() }),
  listo({ id: 'inspec-medir', etiqueta: 'Medir', pestana: 'inspeccionar', grupo: 'Medir', icono: 'ruler',
    atajo: 'M', requiere: DOC, tooltip: 'Muestra los pares de sólidos más cercanos (contacto, choque, holgura) en el panel Medidas.',
    ejecutar: (c) => c.panel('medidas') }),
  listo({ id: 'inspec-propiedades', etiqueta: 'Propiedades', pestana: 'inspeccionar', grupo: 'Análisis', icono: 'props',
    requiere: DOC, tooltip: 'Muestra volumen, dimensiones y sólidos del documento.',
    ejecutar: (c) => c.propiedades() }),
  pronto('24g', { id: 'inspec-curvatura', etiqueta: 'Curvatura y zonas finas', pestana: 'inspeccionar', grupo: 'Análisis', icono: 'spark',
    tooltip: 'Colorea las zonas de poca curvatura o paredes finas.' }),
  pronto('24g', { id: 'inspec-voladizos', etiqueta: 'Voladizos', pestana: 'inspeccionar', grupo: 'Análisis', icono: 'spark',
    tooltip: 'Mapa de voladizos que necesitarán soporte.' }),
  listo({ id: 'inspec-notas', etiqueta: 'Notas / pines', pestana: 'inspeccionar', grupo: 'Anotar', icono: 'pin',
    activo: (c) => c.entrada()?.notas?.modo === 'nota',
    atajo: 'N', requiere: DOC, tooltip: 'Coloca una nota en el punto del visor donde hagas clic.',
    ejecutar: (c) => c.entrada()?.acciones.nota.click() }),
  listo({ id: 'inspec-pizarra', etiqueta: 'Pizarra', pestana: 'inspeccionar', grupo: 'Anotar', icono: 'board',
    activo: (c) => String(c.entrada()?.notas?.modo ?? '').startsWith('pizarra'),
    requiere: DOC, tooltip: 'Dibuja trazos sobre un plano para indicar qué cambiar.',
    ejecutar: (c) => c.entrada()?.acciones.pizarra.click() }),
  listo({ id: 'inspec-parametros', etiqueta: 'Parámetros', pestana: 'inspeccionar', grupo: 'Diseño', icono: 'sliders',
    requiere: DOC, tooltip: 'Valores con nombre que controlan la pieza.', ejecutar: (c) => c.panel('parametros') }),
  // Cotas (F23a, ADR-0023): abre «Cotas del plano» dentro de Parámetros. El clic sobre
  // una cota del plano o del visor queda para F23b.
  listo({ id: 'inspec-cotas-vivas', etiqueta: 'Cotas', pestana: 'inspeccionar', grupo: 'Diseño', icono: 'sliders',
    requiere: DOC, tooltip: 'Abre las cotas del plano y edita las que siguen a un parámetro.',
    ejecutar: (c) => c.cotas() }),
  // Deshacer y rehacer (24a2, ADR-0022): el cursor llega de GET /documentos/{id}/deshacer
  // (toolbar-ui.js), así que el motivo de un botón deshabilitado sale de ahí.
  listo({ id: 'hist-deshacer', etiqueta: 'Deshacer', pestana: 'inspeccionar', grupo: 'Historia', icono: 'undo',
    atajo: 'Ctrl+Z', requiere: DOC, disponible: cursorHistoria('puede_deshacer', MOTIVO_SIN_DESHACER),
    tooltip: 'Vuelve a la revisión anterior. Crea una revisión nueva: el historial no se borra.',
    ejecutar: (c) => c.deshacer() }),
  listo({ id: 'hist-rehacer', etiqueta: 'Rehacer', pestana: 'inspeccionar', grupo: 'Historia', icono: 'redo',
    atajo: 'Ctrl+Shift+Z', atajoAlterno: 'Ctrl+Y', requiere: DOC,
    disponible: cursorHistoria('puede_rehacer', MOTIVO_SIN_REHACER),
    tooltip: 'Vuelve a la revisión que dejó Deshacer. Una edición nueva lo invalida.',
    ejecutar: (c) => c.rehacer() }),
  listo({ id: 'hist-historial', etiqueta: 'Historial', pestana: 'inspeccionar', grupo: 'Historia', icono: 'history',
    requiere: DOC, tooltip: 'Lista de pasos, hitos e instantáneas del documento.', ejecutar: (c) => c.panel('historial') }),
  listo({ id: 'hist-ramas', etiqueta: 'Ramas', pestana: 'inspeccionar', grupo: 'Historia', icono: 'branch',
    requiere: DOC, tooltip: 'Crea, cambia o compara ramas de diseño.', ejecutar: (c) => c.panel('ramas') }),
  listo({ id: 'hist-fusionar', etiqueta: 'Fusionar ramas', pestana: 'inspeccionar', grupo: 'Historia', icono: 'merge',
    requiere: DOC, tooltip: 'Abre las ramas para fusionar una en otra.', ejecutar: (c) => c.panel('ramas') }),

  // ---------------- Planos (F22) ----------------
  pronto('F22', { id: 'planos-generar', etiqueta: 'Generar plano', pestana: 'planos', grupo: 'Planos', icono: 'drawing',
    tooltip: 'Tres vistas y una isométrica con cotas.' }),
  pronto('F22', { id: 'planos-exportar', etiqueta: 'Exportar SVG / PDF / DXF', pestana: 'planos', grupo: 'Planos', icono: 'drawing',
    tooltip: 'Exporta el plano en SVG, PDF o DXF.' }),
  pronto('F22', { id: 'planos-cotas', etiqueta: 'Cotas automáticas', pestana: 'planos', grupo: 'Cotas', icono: 'sliders',
    tooltip: 'Coloca las cotas principales del plano automáticamente.' }),
  pronto('24j', { id: 'planos-detalle', etiqueta: 'Vista en corte y detalle', pestana: 'planos', grupo: 'Planos', icono: 'section',
    tooltip: 'Vista en corte o detalle ampliado de una zona.' }),
  pronto('24j', { id: 'planos-plantilla', etiqueta: 'Plantilla 1:1', pestana: 'planos', grupo: 'Plantillas', icono: 'print',
    tooltip: 'Plantilla a escala 1:1 para comparar con la pieza real.' }),

  // ---------------- Imprimir ----------------
  listo({ id: 'imp-cupon', etiqueta: 'Cupón de ajuste', pestana: 'imprimir', grupo: 'Verificar', icono: 'print',
    requiere: DOC, tooltip: 'Imprime solo las zonas de encaje para probar el ajuste.',
    ejecutar: (c) => c.entrada()?.acciones.cupon.click() }),
  listo({ id: 'imp-probeta', etiqueta: 'Probeta de tolerancias', pestana: 'imprimir', grupo: 'Verificar', icono: 'printer',
    tooltip: 'Abre el perfil de impresora para generar la probeta.', ejecutar: (c) => c.clic('fj-btn-perfil') }),
  listo({ id: 'imp-exportar-3mf', etiqueta: 'Exportar 3MF', pestana: 'imprimir', grupo: 'Salida', icono: 'export',
    atajo: 'Ctrl+E', requiere: DOC, tooltip: 'Descarga el modelo en 3MF con un objeto por pieza.', ejecutar: (c) => c.exportar('3mf') }),
  listo({ id: 'imp-exportar-stl', etiqueta: 'Exportar STL', pestana: 'imprimir', grupo: 'Salida', icono: 'export',
    requiere: DOC, tooltip: 'Descarga el modelo en STL.', ejecutar: (c) => c.exportar('stl') }),
  listo({ id: 'imp-abrir-orca', etiqueta: 'Abrir en OrcaSlicer', pestana: 'imprimir', grupo: 'Salida', icono: 'orca',
    requiere: DOC, tooltip: 'Abre el modelo en OrcaSlicer mediante su enlace.', ejecutar: (c) => c.orca() }),
  pronto('24h', { id: 'imp-orientar', etiqueta: 'Orientación recomendada', pestana: 'imprimir', grupo: 'Orientar', icono: 'orient',
    tooltip: 'Propone la orientación de impresión con una explicación.' }),
  pronto('24h', { id: 'imp-partir', etiqueta: 'Partir para la cama', pestana: 'imprimir', grupo: 'Orientar', icono: 'orient',
    tooltip: 'Parte la pieza en mitades con pasadores para caber en la cama.' }),
  pronto('24h', { id: 'imp-elefante', etiqueta: 'Chaflán de base', pestana: 'imprimir', grupo: 'Corregir', icono: 'fillet',
    tooltip: 'Chaflán en la base contra la pata de elefante.' }),
  pronto('24h', { id: 'imp-sacrificio', etiqueta: 'Puente de sacrificio', pestana: 'imprimir', grupo: 'Corregir', icono: 'bridge',
    tooltip: 'Añade un puente que se retira después de imprimir.' }),
  pronto('24i', { id: 'imp-estimar', etiqueta: 'Estimar tiempo y filamento', pestana: 'imprimir', grupo: 'Estimar', icono: 'cost',
    tooltip: 'Tiempo, filamento y coste estimados.' }),
  pronto('24i', { id: 'imp-tolerancias', etiqueta: 'Preajustes de ajuste', pestana: 'imprimir', grupo: 'Tolerancias', icono: 'sliders',
    tooltip: 'Holgado, deslizante, ajustado o prensado, por impresora y boquilla.' }),

  // ---------------- Claude (cluster fijo) ----------------
  listo({ id: 'claude-bandeja', etiqueta: 'Indicaciones', pestana: 'claude', grupo: 'Claude', icono: 'claude',
    requiere: DOC, tooltip: 'Abre la lista de indicaciones pendientes y resueltas.', ejecutar: (c) => c.panel('design') }),
  listo({ id: 'claude-referencia', etiqueta: 'Foto de referencia', pestana: 'claude', grupo: 'Claude', icono: 'camera',
    requiere: DOC, tooltip: 'Abre Indicaciones para adjuntar o ver fotos de referencia.', ejecutar: (c) => c.panel('design') }),
  listo({ id: 'claude-pasos', etiqueta: 'Pasos de Claude', pestana: 'claude', grupo: 'Claude', icono: 'undo',
    requiere: DOC, tooltip: 'Abre el historial para revisar los pasos aplicados. El filtro por autor llega después.',
    ejecutar: (c) => c.panel('historial') }),
  listo({ id: 'claude-cambio-seleccion', etiqueta: 'Pedir cambio con lo seleccionado', pestana: 'claude', grupo: 'Claude', icono: 'claude',
    requiere: PIEZA, tooltip: 'Describe un cambio para la pieza seleccionada; queda ligado a esa pieza en Indicaciones.',
    ejecutar: (c) => c.pedirCambio() }),
  pronto('24k', { id: 'claude-preguntar', etiqueta: 'Preguntar a Claude sobre esta pieza', pestana: 'claude', grupo: 'Claude', icono: 'claude',
    tooltip: 'Pregunta a Claude por la cara o pieza seleccionada.' }),
  pronto('24k', { id: 'claude-variantes', etiqueta: 'Proponer 3 variantes', pestana: 'claude', grupo: 'Claude', icono: 'claude',
    tooltip: 'Tres candidatos para comparar y elegir.' }),
  pronto('24k', { id: 'claude-revisar', etiqueta: 'Revisar como impresor', pestana: 'claude', grupo: 'Claude', icono: 'claude',
    tooltip: 'Informe de impresión con arreglos propuestos.' }),
];
