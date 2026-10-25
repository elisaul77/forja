// Forja — galería de ejemplos: tarjetas de GET /ejemplos y apertura con
// POST /ejemplos/{id}/abrir (token, como el resto de rutas que crean o mutan).
import { obtenerTokenSesion } from "./api.js?v=17";

// Misma clave que api.js (no exportada allí): se limpia cuando el token no sirve.
const CLAVE_TOKEN_SESION = "fj_token";

async function errorDe(resp) {
  let detalle = resp.statusText;
  try {
    detalle = (await resp.json()).detail ?? detalle;
  } catch {
    // cuerpo no JSON: se conserva statusText
  }
  return new Error(`${resp.status} ${detalle}`);
}

export async function listarEjemplos() {
  const resp = await fetch("/ejemplos");
  if (!resp.ok) throw await errorDe(resp);
  return resp.json();
}

/** Crea el documento «Ejemplo — …» en el servidor y devuelve su ficha. */
export async function abrirEjemplo(id) {
  const token = obtenerTokenSesion().trim();
  if (!token) throw new Error("Operación cancelada: no se proporcionó la clave de edición");
  const resp = await fetch(`/ejemplos/${encodeURIComponent(id)}/abrir`, {
    method: "POST", headers: { "X-Forja-Token": token },
  });
  if (resp.status === 401) sessionStorage.removeItem(CLAVE_TOKEN_SESION);
  if (!resp.ok) throw await errorDe(resp);
  return resp.json();
}

/** Pinta una tarjeta por ejemplo en `contenedor`; `abrir(ejemplo)` al pulsar «Abrir». */
export function renderEjemplos(contenedor, ejemplos, abrir) {
  contenedor.replaceChildren();
  for (const ejemplo of ejemplos) {
    const tarjeta = document.createElement("article");
    tarjeta.className = "fj-ejemplo";
    const titulo = document.createElement("h4");
    titulo.textContent = ejemplo.titulo;
    const descripcion = document.createElement("p");
    descripcion.textContent = ejemplo.descripcion;
    const tecnicas = document.createElement("ul");
    tecnicas.className = "fj-ejemplo__tecnicas";
    tecnicas.setAttribute("aria-label", "Técnicas");
    for (const tecnica of ejemplo.tecnicas) {
      const etiqueta = document.createElement("li");
      etiqueta.textContent = tecnica;
      tecnicas.append(etiqueta);
    }
    const boton = document.createElement("button");
    boton.className = "fj-btn fj-btn--primary";
    boton.type = "button";
    boton.textContent = "Abrir";
    boton.setAttribute("aria-label", `Abrir ejemplo ${ejemplo.titulo}`);
    boton.onclick = () => abrir(ejemplo);
    tarjeta.append(titulo, descripcion, tecnicas, boton);
    contenedor.append(tarjeta);
  }
}

/** Carga el listado y lo pinta; si falla, lo dice en el propio contenedor. */
export async function cargarEjemplos(contenedor, abrir) {
  contenedor.replaceChildren();
  try {
    renderEjemplos(contenedor, await listarEjemplos(), abrir);
  } catch (error) {
    const aviso = document.createElement("p");
    aviso.className = "fj-ejemplos__aviso";
    aviso.textContent = `No se pudieron cargar los ejemplos: ${error.message}`;
    contenedor.append(aviso);
  }
}
