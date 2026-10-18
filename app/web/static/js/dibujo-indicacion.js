// Forja — trazos de una indicación en curso (botón «Dibujar indicación»).
// Módulo puro, sin DOM: `notes.js` guarda aquí los trazos hasta «Terminar»
// y las pruebas web lo importan directamente.

/** Tope de trazos por indicación en el visor (el servidor admite 64 marcas por indicación). */
export const MAX_TRAZOS_INDICACION = 32;

const copiarPuntos = (puntos) => puntos.map((p) => [...p]);

/** Botones de la barra del visor que quedan marcados para un modo de `notes.js`. */
export function claseModo(modo) {
  return {
    nota: modo === "nota",
    pizarra: typeof modo === "string" && modo.startsWith("pizarra"),
  };
}

export class BorradorIndicacion {
  constructor() {
    this._trazos = [];
    // Pieza tocada por cada trazo (nombre del visor) o `null`, alineada con `_trazos`.
    this._piezas = [];
  }

  /** Número de trazos de la indicación en curso. */
  get cantidad() {
    return this._trazos.length;
  }

  /** Trazos en orden, copiados. */
  get trazos() {
    return this._trazos.map(copiarPuntos);
  }

  /** Pieza tocada por cada trazo, en orden (`null` si el trazo no tocó ninguna). */
  get piezas() {
    return [...this._piezas];
  }

  /** Añade un trazo (al menos 2 puntos) con la pieza que tocó, si tocó alguna.
   * Devuelve `false` si no se añadió. */
  añadirTrazo(puntos, pieza = null) {
    if (!Array.isArray(puntos) || puntos.length < 2) return false;
    if (this._trazos.length >= MAX_TRAZOS_INDICACION) return false;
    this._trazos.push(copiarPuntos(puntos));
    this._piezas.push(pieza || null);
    return true;
  }

  /** Quita el último trazo; devuelve ese trazo o `null` si no había ninguno. */
  deshacerTrazo() {
    this._piezas.pop();
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

/** Región de una indicación con varios trazos: la pieza que más trazos tocan (en empate, la
 * primera que aparece) y solo los puntos de esos trazos. Sin ningún trazo sobre una pieza
 * devuelve `{ pieza: null, puntos: [] }` y la indicación se crea sin región. */
export function resolverRegionIndicacion(trazos, piezas) {
  const cuenta = new Map();
  piezas.forEach((pieza) => {
    if (pieza) cuenta.set(pieza, (cuenta.get(pieza) ?? 0) + 1);
  });
  let pieza = null;
  let mejor = 0;
  for (const [nombre, veces] of cuenta) {
    if (veces > mejor) {
      pieza = nombre;
      mejor = veces;
    }
  }
  if (!pieza) return { pieza: null, puntos: [] };
  const puntos = trazos
    .filter((_, i) => piezas[i] === pieza)
    .flat(1)
    .map((p) => [...p]);
  return { pieza, puntos };
}
