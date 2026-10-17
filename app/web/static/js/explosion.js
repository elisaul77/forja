// Forja — vista explosionada (F19, ADR-0027). Desplazamientos de display por
// pieza. Espejo de app/explosion.py: mismo algoritmo y mismas validaciones; la
// tabla tests/fixtures/explosion_offsets.json fija la paridad. Sin three.js:
// solo lee `mesh.geometry.boundingBox` y escribe `mesh.position`.

export const FACTOR_MIN = 0;
export const FACTOR_MAX = 1;
export const MAX_PIEZAS = 500;
const EPS = 1e-9;
const TIPOS_CON_EJE = ['giro', 'deslizamiento'];

export function validarFactor(factor) {
  if (typeof factor !== 'number' || !Number.isFinite(factor) || factor < FACTOR_MIN || factor > FACTOR_MAX) {
    throw new Error('el factor de explosión debe ser un número entre 0 y 1');
  }
  return factor;
}

function validarCajas(cajas) {
  const nombres = Object.keys(cajas);
  if (nombres.length > MAX_PIEZAS) throw new Error(`demasiadas piezas para explosionar (${nombres.length} > ${MAX_PIEZAS})`);
  const validadas = {};
  for (const nombre of nombres) {
    const caja = cajas[nombre];
    if (!Array.isArray(caja) || caja.length !== 6) throw new Error(`caja inválida para la pieza ${nombre}`);
    for (const v of caja) {
      if (typeof v !== 'number' || !Number.isFinite(v)) throw new Error(`caja inválida para la pieza ${nombre}: se requiere un número finito`);
    }
    for (let k = 0; k < 3; k++) {
      if (caja[2 * k] > caja[2 * k + 1]) throw new Error(`caja invertida para la pieza ${nombre}`);
    }
    validadas[nombre] = caja.slice();
  }
  return validadas;
}

const centro = (c) => [(c[0] + c[1]) / 2, (c[2] + c[3]) / 2, (c[4] + c[5]) / 2];
const resta = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const punto = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];

function unitario(eje) {
  if (!Array.isArray(eje) || eje.length !== 3) throw new Error('el eje de articulación debe tener tres números');
  if (!eje.every((v) => typeof v === 'number' && Number.isFinite(v))) throw new Error('el eje de articulación debe ser finito');
  const norma = Math.sqrt(punto(eje, eje));
  if (norma < EPS) throw new Error('el eje de articulación no puede ser nulo');
  return [eje[0] / norma, eje[1] / norma, eje[2] / norma];
}

/** Desplazamiento `[dx, dy, dz]` en mm por pieza (ver app/explosion.py). */
export function desplazamientosExplosion(cajasEntrada, articulaciones, factorEntrada) {
  const factor = validarFactor(factorEntrada);
  const cajas = validarCajas(cajasEntrada);
  const nombres = Object.keys(cajas);
  if (nombres.length === 0) return {};

  const todas = nombres.map((n) => cajas[n]);
  const xmin = Math.min(...todas.map((c) => c[0]));
  const xmax = Math.max(...todas.map((c) => c[1]));
  const ymin = Math.min(...todas.map((c) => c[2]));
  const ymax = Math.max(...todas.map((c) => c[3]));
  const zmin = Math.min(...todas.map((c) => c[4]));
  const zmax = Math.max(...todas.map((c) => c[5]));
  const centroConjunto = [(xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2];
  const diagonal = Math.sqrt((xmax - xmin) ** 2 + (ymax - ymin) ** 2 + (zmax - zmin) ** 2);

  const centros = {};
  for (const n of nombres) centros[n] = centro(cajas[n]);

  const ejeDeHijo = {};
  for (const art of articulaciones ?? []) {
    if (TIPOS_CON_EJE.includes(art.tipo) && art.padre in cajas && art.hijo in cajas) {
      ejeDeHijo[art.hijo] = { padre: art.padre, eje: unitario(art.eje) };
    }
  }

  const direcciones = {};
  for (const n of nombres) {
    if (n in ejeDeHijo) {
      const { padre, eje } = ejeDeHijo[n];
      const signo = punto(eje, resta(centros[n], centros[padre])) < 0 ? -1 : 1;
      direcciones[n] = [eje[0] * signo, eje[1] * signo, eje[2] * signo];
    } else {
      const relativo = resta(centros[n], centroConjunto);
      const norma = Math.sqrt(punto(relativo, relativo));
      direcciones[n] = norma < EPS ? [0, 0, 1] : [relativo[0] / norma, relativo[1] / norma, relativo[2] / norma];
    }
  }

  const semi = {};
  const proyeccion = {};
  for (const n of nombres) {
    const d = direcciones[n];
    const c = cajas[n];
    semi[n] = [0, 1, 2].reduce((suma, k) => suma + Math.abs(d[k]) * (c[2 * k + 1] - c[2 * k]) / 2, 0);
    proyeccion[n] = punto(d, resta(centros[n], centroConjunto));
  }

  // Orden por proyección y, en empate, por nombre (igual que sorted() en Python).
  const orden = nombres.slice().sort((a, b) => (proyeccion[a] - proyeccion[b]) || (a < b ? -1 : a > b ? 1 : 0));
  const acumulado = [];
  orden.forEach((n, i) => {
    acumulado.push(i === 0 ? semi[n] : acumulado[i - 1] + semi[orden[i - 1]] + semi[n]);
  });

  const maximo = acumulado[acumulado.length - 1];
  const base = factor * 0.5 * diagonal;
  const resultado = {};
  orden.forEach((n, i) => {
    const t = maximo > EPS ? base * acumulado[i] / maximo : base * (i + 1) / orden.length;
    const d = direcciones[n];
    // `+ 0` convierte -0 en 0, como en Python.
    resultado[n] = [d[0] * t + 0, d[1] * t + 0, d[2] * t + 0];
  });
  return Object.fromEntries(Object.keys(resultado).sort().map((n) => [n, resultado[n]]));
}

/** Cajas de las piezas del visor (`mesh.geometry.boundingBox`, sin pose). */
export function cajasDePiezas(piezas) {
  const cajas = {};
  for (const [nombre, pieza] of piezas) {
    const caja = pieza.mesh.geometry.boundingBox;
    cajas[nombre] = [caja.min.x, caja.max.x, caja.min.y, caja.max.y, caja.min.z, caja.max.z];
  }
  return cajas;
}

/** Coloca cada malla de `piezas` (Map nombre -> {mesh}) en su desplazamiento.
 * Solo escribe `mesh.position`: la pose de la animación usa `mesh.matrix` y
 * manda mientras dura. Devuelve el mapa de desplazamientos aplicado. */
export function aplicarExplosion(piezas, factor, articulaciones) {
  // Una pieza sin triángulos tiene caja vacía (±Infinity): no entra en el cálculo y se queda en su sitio.
  const conCaja = new Map([...piezas].filter(([, pieza]) => cajaFinita(pieza.mesh.geometry.boundingBox)));
  const offsets = desplazamientosExplosion(cajasDePiezas(conCaja), articulaciones, factor);
  for (const [nombre, pieza] of piezas) {
    const [dx, dy, dz] = offsets[nombre] ?? [0, 0, 0];
    pieza.mesh.position.set(dx, dy, dz);
  }
  return offsets;
}

function cajaFinita(caja) {
  const valores = [caja.min.x, caja.max.x, caja.min.y, caja.max.y, caja.min.z, caja.max.z];
  return valores.every(Number.isFinite) && caja.min.x <= caja.max.x && caja.min.y <= caja.max.y && caja.min.z <= caja.max.z;
}
