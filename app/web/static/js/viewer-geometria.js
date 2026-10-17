// Forja — helpers puros del visor 3D (fase 24a, revisión 1): encuadre según el
// tipo de cámara y liberación de geometrías y materiales. Sin three.js, para
// que tests/web/viewer-geometria.test.mjs los pruebe con `node --test`.

const FOV_REFERENCIA = 45;
const MARGEN_ENCUADRE = 1.15;

const aspectoValido = (aspecto) => (Number.isFinite(aspecto) && aspecto > 0 ? aspecto : 1);

/**
 * Distancia de la cámara y altura del volumen visible para encuadrar una esfera
 * de radio `radio`. En ortográfica la altura es lo que se ve y la distancia solo
 * fija la posición: se calcula con el fov de referencia, nunca con `undefined`.
 */
export function calcularEncuadre({ ortogonal = false, fov, aspecto, radio }) {
  const r = Math.max(Number.isFinite(radio) ? radio : 1, 1);
  const a = aspectoValido(aspecto);
  const fovUsable = !ortogonal && Number.isFinite(fov) && fov > 0 ? fov : FOV_REFERENCIA;
  const vertical = (fovUsable / 2) * (Math.PI / 180);
  const horizontal = Math.atan(Math.tan(vertical) * a);
  // Medio alto de la ortográfica: el medio ancho (alto × aspecto) también debe cubrir la
  // esfera, así que con un visor más estrecho que cuadrado el alto crece en 1 / aspecto.
  return {
    distancia: (r / Math.sin(Math.min(vertical, horizontal))) * MARGEN_ENCUADRE,
    altura: (r * MARGEN_ENCUADRE) / Math.min(a, 1),
  };
}

/**
 * Medio alto del marco ortográfico al redimensionar. Se conserva el que dejó el último
 * encuadre (la distancia de la cámara no es esa altura); sin encuadre previo se deriva
 * de la distancia con el fov de referencia.
 */
export function alturaAlRedimensionar({ alturaGuardada, distancia }) {
  if (Number.isFinite(alturaGuardada) && alturaGuardada > 0) return alturaGuardada;
  const d = Number.isFinite(distancia) && distancia > 0 ? distancia : 1;
  return d * Math.tan((FOV_REFERENCIA / 2) * (Math.PI / 180));
}

/**
 * Marco de una cámara ortográfica. `reiniciarZoom` solo en el encuadre explícito
 * (Ajustar, vistas estándar): al redimensionar se conserva el zoom del usuario.
 */
export function aplicarMarcosOrtograficos(camara, { altura, aspecto, reiniciarZoom = false }) {
  const media = Math.max(Number.isFinite(altura) ? altura : 1, 0.01);
  const ancho = media * aspectoValido(aspecto);
  Object.assign(camara, { left: -ancho, right: ancho, top: media, bottom: -media });
  if (reiniciarZoom) camara.zoom = 1;
  camara.updateProjectionMatrix();
}

const liberarRecurso = (recurso) => {
  for (const unidad of [].concat(recurso ?? [])) unidad?.dispose?.();
};

/** Quita y libera las líneas de aristas vivas (hijos marcados con userData.esAristas). */
export function liberarAristas(objeto) {
  const aristas = objeto.children.filter((hijo) => hijo.userData?.esAristas);
  for (const hijo of aristas) {
    objeto.remove(hijo);
    liberarRecurso(hijo.geometry);
    liberarRecurso(hijo.material);
  }
  return aristas.length;
}

/** Libera una malla completa: sus aristas, su geometría y su material. */
export function liberarMalla(objeto) {
  if (!objeto) return;
  liberarAristas(objeto);
  liberarRecurso(objeto.geometry);
  liberarRecurso(objeto.material);
}
