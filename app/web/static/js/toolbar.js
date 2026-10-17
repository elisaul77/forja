// Forja — lógica pura de la barra de herramientas (fase 24a): habilitación con
// motivo, líneas de tooltip, filtro de la paleta (Ctrl+K), atajos y reglas de
// cuándo ignorarlos. Sin DOM ni red: lo prueba tests/web/toolbar.test.mjs.
// El registro vive en toolbar-actions.js; el DOM en toolbar-ui.js.

// Combinaciones que el navegador usa antes que la página.
export const ATAJOS_RESERVADOS = ['Ctrl+W', 'Ctrl+T', 'Ctrl+N', 'Ctrl+R', 'Ctrl+L',
  'Ctrl+P', 'Ctrl+S', 'Ctrl+O', 'Ctrl+F', 'Ctrl+H', 'Ctrl+Q', 'Ctrl+D', 'Ctrl+U'];

const MOTIVO_SIN_DOCUMENTO = 'Abre un documento primero';
const MOTIVO_SIN_PIEZA = 'Selecciona una pieza primero';

const TECLAS_ESPECIALES = { Escape: 'Esc', Enter: 'Enter' };
const FORMATO_ATAJO = /^((Ctrl|Alt|Shift)\+)*([A-Z0-9]|\?|Esc|Enter)$/;

/**
 * Habilitación de una acción según el contexto.
 * `estado`: { documento: { formato: "step" | "stl" } | null, seleccion: string | null }
 */
export function estadoAccion(accion, estado = {}) {
  if (accion.estado === 'proximamente') {
    return { habilitada: false, motivo: `Próximamente · ${accion.fase}` };
  }
  const requiere = accion.requiere ?? [];
  const documento = estado.documento ?? null;
  if (requiere.length && !documento) return { habilitada: false, motivo: MOTIVO_SIN_DOCUMENTO };
  if (requiere.includes('seleccion') && !estado.seleccion) return { habilitada: false, motivo: MOTIVO_SIN_PIEZA };
  if (requiere.includes('b-rep') && documento.formato !== 'step') {
    return { habilitada: false, motivo: 'Solo para sólidos B-rep (este documento es malla)' };
  }
  if (requiere.includes('malla') && documento.formato !== 'stl') {
    return { habilitada: false, motivo: 'Solo para documentos de malla (STL)' };
  }
  return { habilitada: true, motivo: null };
}

/** Líneas del tooltip: «Etiqueta (atajo)», efecto y, si no está disponible, el motivo. */
export function tooltipLineas(accion, estado = {}) {
  const { habilitada, motivo } = estadoAccion(accion, estado);
  const cabeza = accion.atajo ? `${accion.etiqueta} (${accion.atajo})` : accion.etiqueta;
  const lineas = [cabeza];
  if (accion.tooltip) lineas.push(accion.tooltip);
  if (!habilitada) lineas.push(motivo);
  return lineas;
}

const normalizar = (texto) => String(texto).toLocaleLowerCase('es').normalize('NFD').replace(/\p{M}/gu, '');

/** Filtra acciones por palabras (sin tildes ni mayúsculas) sobre etiqueta, grupo y pestaña. */
export function filtrarPaleta(acciones, consulta) {
  const palabras = normalizar(consulta ?? '').split(/\s+/).filter(Boolean);
  if (palabras.length === 0) return [...acciones];
  return acciones.filter((accion) => {
    const texto = normalizar(`${accion.etiqueta} ${accion.grupo ?? ''} ${accion.pestana}`);
    return palabras.every((palabra) => texto.includes(palabra));
  });
}

/** Clave canónica del evento: «F», «Esc», «?», «Ctrl+K», «Ctrl+Shift+Z», «Alt+I». */
export function claveDesdeEvento(event) {
  let tecla = TECLAS_ESPECIALES[event.key] ?? event.key;
  if (/^[a-z]$/.test(tecla)) tecla = tecla.toUpperCase();
  const letra = /^[A-Z]$/.test(tecla);
  const modificadores = [];
  if (event.ctrlKey) modificadores.push('Ctrl');
  if (event.altKey) modificadores.push('Alt');
  if (event.shiftKey && letra) modificadores.push('Shift');
  return [...modificadores, tecla].join('+');
}

/** Acción cuyo atajo principal o alterno (p. ej. Ctrl+Y para rehacer) es `clave`. */
export function buscarPorAtajo(acciones, clave) {
  return acciones.find((accion) => accion.atajo === clave || accion.atajoAlterno === clave);
}

// ---------------- Deshacer y rehacer por revisiones (fase 24a, revisión 1) ----------------
// Cada paso es una restauración del servidor: el historial sigue siendo
// append-only y restaurar crea una revisión nueva. `pila` guarda las revisiones
// a las que se puede volver (la última es la inmediatamente anterior) y `rehacer`
// las dejadas por un deshacer. `cabeza` es la revisión que ve la barra.

export function nuevaPilaRevisiones() {
  return { cabeza: null, pila: [], rehacer: [] };
}

/**
 * `ids`: revisiones en el orden del servidor (la más reciente al final).
 * Si la cabeza cambió sin pasar por deshacer o rehacer, es una edición nueva: la
 * cabeza anterior queda como paso a deshacer y el rehacer se invalida.
 */
export function sincronizarPilaRevisiones(estado, ids) {
  if (ids.length === 0) {
    estado.cabeza = null;
    estado.pila = [];
    estado.rehacer = [];
    return estado;
  }
  const cabeza = ids[ids.length - 1];
  if (estado.cabeza === null) {
    estado.pila = ids.slice(0, -1);
    estado.rehacer = [];
  } else if (cabeza !== estado.cabeza) {
    estado.pila.push(estado.cabeza);
    estado.rehacer = [];
  }
  estado.cabeza = cabeza;
  return estado;
}

/** Revisión a la que deshacer (o null). Mueve la cabeza actual al rehacer. */
export function tomarDeshacer(estado) {
  if (estado.cabeza === null || estado.pila.length === 0) return null;
  const destino = estado.pila.pop();
  estado.rehacer.push(estado.cabeza);
  return destino;
}

/** Revisión a la que rehacer (o null). Mueve la cabeza actual a la pila. */
export function tomarRehacer(estado) {
  if (estado.cabeza === null || estado.rehacer.length === 0) return null;
  const destino = estado.rehacer.pop();
  estado.pila.push(estado.cabeza);
  return destino;
}

/** Tras una restauración: la cabeza pasa a ser la última revisión del servidor. */
export function fijarCabeza(estado, id) {
  estado.cabeza = id;
  return estado;
}

/** Si la restauración falla, el paso tomado vuelve a su pila. */
export function devolverPaso(estado, destino, sentido) {
  if (sentido === 'deshacer') {
    estado.rehacer.pop();
    estado.pila.push(destino);
  } else {
    estado.pila.pop();
    estado.rehacer.push(destino);
  }
  return estado;
}

/**
 * true si el atajo no debe ejecutarse: foco en campos o zonas editables, un
 * diálogo abierto, o una tecla suelta (letra o dígito) fuera del visor y la cinta.
 * `zonaVisor`: el foco está en el visor, la cinta o el cuerpo de la página.
 */
export function debeIgnorarAtajo(event, { target = {}, dialogoAbierto = false, zonaVisor = true } = {}) {
  if (dialogoAbierto) return true;
  if (['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName) || target.isContentEditable) return true;
  const teclaSuelta = /^[A-Za-z0-9]$/.test(event.key) && !event.ctrlKey && !event.altKey;
  if (teclaSuelta && !zonaVisor) return true;
  return false;
}

/** Problemas del registro (lista vacía = correcto). */
export function validarRegistro(acciones) {
  const problemas = [];
  const ids = new Set();
  const atajos = new Set();
  for (const accion of acciones) {
    if (ids.has(accion.id)) problemas.push(`id repetido: ${accion.id}`);
    ids.add(accion.id);
    if (accion.atajo) {
      if (atajos.has(accion.atajo)) problemas.push(`atajo repetido: ${accion.atajo}`);
      atajos.add(accion.atajo);
      if (!FORMATO_ATAJO.test(accion.atajo)) problemas.push(`formato de atajo: ${accion.atajo}`);
      if (ATAJOS_RESERVADOS.includes(accion.atajo)) problemas.push(`atajo reservado por el navegador: ${accion.atajo}`);
    }
    if (accion.estado === 'listo' && typeof accion.ejecutar !== 'function') problemas.push(`sin ejecutar: ${accion.id}`);
    if (accion.estado === 'proximamente' && (!accion.fase || accion.ejecutar)) problemas.push(`placeholder mal formado: ${accion.id}`);
  }
  return problemas;
}
