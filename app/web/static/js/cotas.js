// Forja — cotas vivas (F23a, ADR-0023). Dentro del panel de parámetros de un
// script paramétrico: cada cota del plano que sale de un PARAMETROS se edita
// con su número; las demás muestran su razón. Mismo endpoint que la
// herramienta MCP `parametros(cotas=...)`. Todo texto va por textContent.
import { obtenerTokenSesion } from "./api.js?v=11";

const ETIQUETAS_TIPO = { tamano_total: "Tamaño", diametro: "Diámetro", posicion: "Posición" };
const ETIQUETAS_EJE = { x: "X", y: "Y", z: "Z" };
// Igual que app/cotas.py: sin `max` del parámetro, a lo sumo 10 veces la medida.
const FACTOR_TOPE_SIN_MAX = 10;
// Generación del último pintado: las lecturas y respuestas de una generación superada se descartan.
let generacion = 0;

/** Etiqueta en español de una cota del listado, p. ej. "Tamaño X" o "Diámetro (3 agujeros)". */
export function etiquetaCota(cota) {
  const base = ETIQUETAS_TIPO[cota.tipo] ?? "Cota";
  const eje = ETIQUETAS_EJE[cota.eje] ? ` ${ETIQUETAS_EJE[cota.eje]}` : "";
  const grupo = cota.cantidad > 1 ? ` (${cota.cantidad} agujeros)` : "";
  return `${base}${eje}${grupo}`;
}

/** Valida el texto de un campo antes de enviarlo: número finito > 0, dentro de min/max. */
export function validarValorCota(cota, texto) {
  const valor = typeof texto === "string" && texto.trim() !== "" ? Number(texto) : Number.NaN;
  if (!Number.isFinite(valor) || valor <= 0) return { ok: false, mensaje: "Introduce un número mayor que 0" };
  if (cota.min !== undefined && valor < cota.min) return { ok: false, mensaje: `Mínimo ${cota.min} mm` };
  if (cota.max !== undefined && valor > cota.max) return { ok: false, mensaje: `Máximo ${cota.max} mm` };
  if (cota.max === undefined && valor > FACTOR_TOPE_SIN_MAX * cota.valor_mm) {
    return { ok: false, mensaje: `Máximo ${FACTOR_TOPE_SIN_MAX} veces la medida actual` };
  }
  return { ok: true, valor };
}

/** Una respuesta sólo se pinta si sigue siendo la última pedida (las anteriores se descartan). */
export function esGeneracionVigente(generacionPedida, generacionActual) {
  return generacionPedida === generacionActual;
}

async function mensajeDeError(resp, porDefecto) {
  const cuerpo = await resp.json().catch(() => ({}));
  return typeof cuerpo.detail === "string" ? cuerpo.detail : porDefecto;
}

/** `{revision, cotas}` del documento (lectura token-free). */
export async function obtenerCotas(id) {
  const resp = await fetch(`/documentos/${id}/cotas`);
  if (!resp.ok) throw new Error(await mensajeDeError(resp, "no se pudieron leer las cotas"));
  return resp.json();
}

/** Edita cotas del listado `revision` ({id: valor}); 409 si el documento cambió entretanto. */
export async function aplicarCotas(id, revision, cambios) {
  const resp = await fetch(`/documentos/${id}/cotas/aplicar`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Forja-Token": obtenerTokenSesion(),
    },
    body: JSON.stringify({ revision, cotas: cambios }),
  });
  if (resp.status === 401) sessionStorage.removeItem("fj_token");
  if (!resp.ok) {
    const error = new Error(await mensajeDeError(resp, "no se pudo aplicar la cota"));
    error.estado = resp.status;
    throw error;
  }
  return resp.json();
}

// `alAplicar(cota, valor)` o, si el campo no valida, `alAplicar(null, null, mensaje)`.
function crearFila(cota, alAplicar) {
  const fila = document.createElement("div");
  fila.className = "fj-param";

  const etiqueta = document.createElement("label");
  etiqueta.className = "fj-param__etiqueta";
  etiqueta.textContent = etiquetaCota(cota);
  fila.appendChild(etiqueta);

  const controles = document.createElement("div");
  controles.className = "fj-param__controles";
  if (cota.operacion === "parametro") {
    const numero = document.createElement("input");
    numero.type = "number";
    numero.className = "fj-input fj-param__numero";
    numero.step = "any";
    numero.value = String(cota.valor_mm);
    numero.setAttribute("aria-label", `${etiquetaCota(cota)} (mm)`);
    const boton = document.createElement("button");
    boton.type = "button";
    boton.className = "fj-btn";
    boton.textContent = "Aplicar";
    boton.addEventListener("click", () => {
      const validacion = validarValorCota(cota, numero.value);
      numero.classList.toggle("is-error", !validacion.ok);
      if (!validacion.ok) {
        alAplicar(null, null, validacion.mensaje);
        return;
      }
      alAplicar(cota, validacion.valor);
    });
    controles.append(numero, boton);
  } else {
    const razon = document.createElement("div");
    razon.className = "fj-param__desc";
    razon.textContent = `Solo lectura: ${cota.razon ?? "sin motivo"}`;
    controles.appendChild(razon);
  }
  fila.appendChild(controles);
  return fila;
}

/**
 * Pinta las cotas del documento dentro de `contenedor`. `alAplicar(respuesta)`
 * se llama tras una edición correcta (el panel refresca valores y malla).
 * Un 409 vuelve a leer el listado; una respuesta de una lectura ya superada se descarta.
 */
export async function renderizarCotas(contenedor, docId, alAplicar) {
  generacion += 1;
  const generacionPropia = generacion;
  contenedor.textContent = "";
  const titulo = document.createElement("div");
  titulo.className = "fj-param__etiqueta";
  titulo.textContent = "Cotas del plano";
  contenedor.appendChild(titulo);
  const estado = document.createElement("div");
  estado.className = "fj-param__estado";
  estado.setAttribute("role", "status");
  contenedor.appendChild(estado);

  let datos;
  try {
    datos = await obtenerCotas(docId);
  } catch (err) {
    if (!esGeneracionVigente(generacionPropia, generacion)) return;
    estado.textContent = `No se pudieron leer las cotas: ${err.message}`;
    estado.classList.add("is-error");
    return;
  }
  if (!esGeneracionVigente(generacionPropia, generacion)) return;
  if (datos.cotas.length === 0) {
    estado.textContent = "Este documento no tiene cotas que dibujar.";
    return;
  }
  for (const cota of datos.cotas) {
    contenedor.appendChild(crearFila(cota, async (dimension, valor, mensaje) => {
      if (dimension === null) {
        estado.textContent = mensaje;
        estado.classList.add("is-error");
        return;
      }
      estado.classList.remove("is-error");
      estado.textContent = "Aplicando…";
      try {
        const respuesta = await aplicarCotas(docId, datos.revision, { [dimension.id]: valor });
        if (!esGeneracionVigente(generacionPropia, generacion)) return;
        await alAplicar(respuesta);
      } catch (err) {
        if (err.estado === 409) {
          estado.textContent = "El documento cambió; se recargan las cotas.";
          estado.classList.add("is-error");
          await renderizarCotas(contenedor, docId, alAplicar);
          return;
        }
        estado.textContent = `No se pudo aplicar: ${err.message}`;
        estado.classList.add("is-error");
      }
    }));
  }
  contenedor.appendChild(estado);
}
