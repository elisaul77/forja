// Forja — material/filamento por pieza (fdm-D). Funciones puras arriba
// (probadas con node), acceso HTTP abajo.
import { obtenerTokenSesion } from "./api.js?v=11";

const RE_COLOR = /^#[0-9A-Fa-f]{6}$/;

/** Mapa {pieza: "#RRGGBB"} solo de las piezas con color válido. */
export function coloresDePiezas(materiales) {
  const colores = {};
  for (const [nombre, entrada] of Object.entries(materiales ?? {})) {
    if (entrada && typeof entrada.color === "string" && RE_COLOR.test(entrada.color)) colores[nombre] = entrada.color.toUpperCase();
  }
  return colores;
}

/** Texto corto para el panel: "PETG negro · E2". */
export function etiquetaMaterial(entrada) {
  if (!entrada) return "Sin material";
  const partes = [];
  if (entrada.material) partes.push(entrada.material);
  if (entrada.extrusor) partes.push(`E${entrada.extrusor}`);
  return partes.join(" · ") || (entrada.color ? "Solo color" : "Sin material");
}

/** Entrada a enviar desde el formulario, o `null` (quitar) si quedó vacía.
 * Lanza Error con mensaje en español si algún campo es inválido. */
export function entradaDesdeFormulario({ material = "", color = "", extrusor = "", conColor = false }) {
  const salida = {};
  const texto = String(material).trim().replace(/\s+/g, " ");
  if (texto.length > 64) throw new Error("El material admite como máximo 64 caracteres");
  if (texto) salida.material = texto;
  if (conColor) {
    if (!RE_COLOR.test(color)) throw new Error("Color inválido (usar #RRGGBB)");
    salida.color = color.toUpperCase();
  }
  if (String(extrusor).trim() !== "") {
    const n = Number(extrusor);
    if (!Number.isInteger(n) || n < 1 || n > 16) throw new Error("El extrusor debe ser un entero de 1 a 16");
    salida.extrusor = n;
  }
  return Object.keys(salida).length ? salida : null;
}

export async function obtenerMateriales(id) {
  const resp = await fetch(`/documentos/${encodeURIComponent(id)}/materiales`);
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return resp.json();
}

/** Guarda {pieza: entrada|null}; devuelve {materiales, piezas}. */
export async function guardarMateriales(id, cambios) {
  const resp = await fetch(`/documentos/${encodeURIComponent(id)}/materiales`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Forja-Token": obtenerTokenSesion() },
    body: JSON.stringify({ materiales: cambios }),
  });
  if (resp.status === 401) sessionStorage.removeItem("fj_token");
  if (!resp.ok) {
    let detalle = resp.statusText;
    try { detalle = (await resp.json()).detail ?? detalle; } catch { /* no JSON */ }
    throw new Error(`${resp.status} ${typeof detalle === "string" ? detalle : JSON.stringify(detalle)}`);
  }
  return resp.json();
}
