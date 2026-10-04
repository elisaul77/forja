// Forja — hand-off a OrcaSlicer: enlaces `orcaslicer://` y de descarga.
// Funciones puras (sin DOM) salvo `construirAnclas`, que recibe el `document`.
// El esquema de URL y la regla del slug replican el servidor
// (`documents._slug_descarga` y la ruta `/documentos/{id}/descarga/{slug}.{ext}`).
// Nunca se emite un "?" ni nada tras la extensión: Orca guarda como nombre de
// archivo todo lo que hay tras la última "/" de la URL decodificada.

const ID_RE = /^[A-Za-z0-9_-]+$/;
const EXTENSIONES = new Set(["3mf", "stl"]);
const ORIGEN_RE = /^https?:\/\/(?:[A-Za-z0-9.-]+|\[[0-9A-Fa-f:.]+\])(?::[0-9]{1,5})?$/;
const ESQUEMA_ORCA = "orcaslicer://open?file=";

// Equivale a `Path(str(nombre)).stem` de pathlib (POSIX, Python 3.12).
function stemPosix(nombre) {
  const partes = String(nombre).split("/").filter((p) => p !== "" && p !== ".");
  const base = partes.length ? partes[partes.length - 1] : "";
  const i = base.lastIndexOf(".");
  return i > 0 && i < base.length - 1 ? base.slice(0, i) : base;
}

export function slugDescarga(nombre) {
  const ascii = stemPosix(nombre).normalize("NFKD").replace(/[^\x00-\x7f]/g, "");
  const limpio = ascii.replace(/[^A-Za-z0-9_-]+/g, "_").replace(/^_+|_+$/g, "");
  return limpio.slice(0, 64).replace(/^_+|_+$/g, "") || "modelo";
}

function origenValido(origin) {
  return typeof origin === "string" && origin !== "null" && ORIGEN_RE.test(origin);
}

/** URL de descarga simple (`origin/documentos/id/descarga/slug.ext`) o null. */
export function buildDescargaUrl(origin, id, nombre, ext, pieza = null) {
  if (!origenValido(origin)) return null;
  if (typeof id !== "string" || !ID_RE.test(id)) return null;
  if (typeof ext !== "string" || !EXTENSIONES.has(ext)) return null;
  const tramo = pieza ? `${encodeURIComponent(pieza)}/` : "";
  return `${origin}/documentos/${id}/descarga/${tramo}${slugDescarga(nombre)}.${ext}`;
}

/** `orcaslicer://open?file=<URL codificada una sola vez>` o null. */
export function buildOrcaLink(origin, id, nombre, ext, pieza = null) {
  const inner = buildDescargaUrl(origin, id, nombre, ext, pieza);
  return inner === null ? null : ESQUEMA_ORCA + encodeURIComponent(inner);
}

export const AVISO_ORCA =
  "Si el navegador pregunta con qué aplicación abrir el enlace, elige OrcaSlicer y marca «recordar mi elección». " +
  "Orca descarga el archivo desde esta dirección. " +
  "Si Orca pregunta «Objeto multipieza detectado»: Sí = un solo objeto con piezas, No = objetos separados. " +
  "Si no se abre nada, usa «Descargar 3MF» y abre el archivo en Orca.";

/**
 * Crea los tres anclajes (Abrir en Orca, Descargar 3MF, Descargar STL) con el
 * `document` recibido. Devuelve [] si el id o el origen no son válidos.
 * `mostrarAviso(texto)` es opcional; el clic nunca llama a preventDefault.
 */
export function construirAnclas(doc, origin, ficha, mostrarAviso, obtenerPieza = null) {
  const id = ficha && ficha.id;
  const nombre = ficha && ficha.nombre;
  const orca = buildOrcaLink(origin, id, nombre, "3mf");
  const u3mf = buildDescargaUrl(origin, id, nombre, "3mf");
  const ustl = buildDescargaUrl(origin, id, nombre, "stl");
  if (orca === null || u3mf === null || ustl === null) return [];
  const crear = (texto, href, titulo) => {
    const a = doc.createElement("a");
    a.className = "fj-btn";
    a.textContent = texto;
    a.setAttribute("href", href);
    a.setAttribute("title", titulo);
    a.setAttribute("aria-label", titulo);
    return a;
  };
  const abrir = crear("Abrir en Orca", orca, "Abrir el modelo (3MF) en OrcaSlicer");
  abrir.addEventListener("click", () => {
    if (typeof mostrarAviso === "function") mostrarAviso(AVISO_ORCA);
  });
  const d3mf = crear("Descargar 3MF", u3mf, "Descargar el modelo como 3MF (milímetros, un objeto por sólido)");
  const dstl = crear("Descargar STL", ustl, "Descargar el modelo como STL");
  // With a piece selected, the three links target only that piece; with
  // nothing selected, the whole document. Refreshed right before use.
  const textos = new Map([[abrir, "Abrir en Orca"], [d3mf, "Descargar 3MF"], [dstl, "Descargar STL"]]);
  const refrescar = () => {
    const pieza = typeof obtenerPieza === "function" ? obtenerPieza() : null;
    abrir.setAttribute("href", buildOrcaLink(origin, id, nombre, "3mf", pieza) ?? orca);
    d3mf.setAttribute("href", buildDescargaUrl(origin, id, nombre, "3mf", pieza) ?? u3mf);
    dstl.setAttribute("href", buildDescargaUrl(origin, id, nombre, "stl", pieza) ?? ustl);
    for (const [a, t] of textos) a.textContent = pieza ? `${t}: ${pieza}` : t;
  };
  for (const a of textos.keys()) {
    for (const ev of ["pointerenter", "focus", "pointerdown", "keydown"]) a.addEventListener(ev, refrescar);
  }
  return Object.assign([abrir, d3mf, dstl], { refrescar });
}
