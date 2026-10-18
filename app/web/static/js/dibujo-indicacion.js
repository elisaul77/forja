// Forja — trazos de una indicación en curso (botón «Dibujar indicación»).
// Módulo puro, sin DOM: `notes.js` guarda aquí los trazos hasta «Terminar»
// y las pruebas web lo importan directamente.

/** Tope de trazos por indicación en el visor (el servidor admite 64 marcas por indicación). */
export const MAX_TRAZOS_INDICACION = 32;

const copiarPuntos = (puntos) => puntos.map((p) => [...p]);

export class BorradorIndicacion {
  constructor() {
    this._trazos = [];
  }

  /** Número de trazos de la indicación en curso. */
  get cantidad() {
    return this._trazos.length;
  }

  /** Trazos en orden, copiados. */
  get trazos() {
    return this._trazos.map(copiarPuntos);
  }

  /** Añade un trazo (al menos 2 puntos). Devuelve `false` si no se añadió. */
  añadirTrazo(puntos) {
    if (!Array.isArray(puntos) || puntos.length < 2) return false;
    if (this._trazos.length >= MAX_TRAZOS_INDICACION) return false;
    this._trazos.push(copiarPuntos(puntos));
    return true;
  }

  /** Quita el último trazo; devuelve ese trazo o `null` si no había ninguno. */
  deshacerTrazo() {
    return this._trazos.pop() ?? null;
  }

  /** Todos los puntos de todos los trazos, en orden (región de la indicación). */
  puntos() {
    return this._trazos.flat(1);
  }

  /** Trazos a enviar al terminar, o `null` si la indicación está vacía. */
  finalizar() {
    return this._trazos.length ? this.trazos : null;
  }
}
