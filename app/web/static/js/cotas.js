// Forja — cotas vivas (F23a, ADR-0023; F23b, ADR-0025). Dentro del panel de
// parámetros: cada cota del plano que se puede editar (un PARAMETROS de script,
// o una característica reconocida en un documento importado) se edita con su
// número y muestra qué hará la edición; las demás muestran su razón. Mismo
// endpoint que la herramienta MCP `parametros(cotas=...)`. Todo texto va por textContent.
import { obtenerTokenSesion } from "./api.js?v=15";

const ETIQUETAS_TIPO = { tamano_total: "Tamaño", diametro: "Diámetro", posicion: "Posición" };
const ETIQUETAS_EJE = { x: "X", y: "Y", z: "Z" };
// Igual que app/cotas.py: sin `max` del parámetro, a lo sumo 10 veces la medida;
// en un documento importado (F23b) al menos una décima parte.
const FACTOR_TOPE_SIN_MAX = 10;
const FACTOR_MINIMO_GEOMETRICO = 0.1;
// Generación del último pintado: las lecturas y respuestas de una generación superada se descartan.
let generacion = 0;

/** Etiqueta en español de una cota del listado, p. ej. "Tamaño X" o "Diámetro (3 agujeros)". */
export function etiquetaCota(cota) {
  const base = ETIQUETAS_TIPO[cota.tipo] ?? "Cota";
  const eje = ETIQUETAS_EJE[cota.eje] ? ` ${ETIQUETAS_EJE[cota.eje]}` : "";
  const grupo = cota.cantidad > 1 ? ` (${cota.cantidad} agujeros)` : "";
  return `${base}${eje}${grupo}`;
}

/** Qué hará la edición de esta cota (F23b): texto en español, antes de aplicar. */
export function textoOperacion(cota, valor = cota.valor_mm) {
  const eje = typeof cota.eje === "string" ? cota.eje.toUpperCase() : "";
  switch (cota.operacion) {
    case "escalar_eje":
      return `Escala la pieza en ${eje} hasta ${valor} mm; los demás ejes no cambian.`;
    case "rellenar_y_taladrar": {
      const cuantos = cota.cantidad > 1 ? `los ${cota.cantidad} agujeros` : "el agujero";
      return `Rellena ${cuantos} y lo retaladra a Ø${valor} mm, en el mismo eje y la misma profundidad.`;
    }
    case "mover_cara":
      return `Separa o junta las dos caras hasta ${valor} mm: la mitad superior se desplaza y el hueco se rellena o se quita.`;
    case "parametro":
      return `Cambia el parámetro del script hasta ${valor} mm.`;
    default:
      return "";
  }
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
  if (cota.operacion !== "parametro" && valor < FACTOR_MINIMO_GEOMETRICO * cota.valor_mm) {
    return { ok: false, mensaje: `Mínimo ${FACTOR_MINIMO_GEOMETRICO} veces la medida actual` };
  }
  if (cota.operacion !== "parametro" && valor === cota.valor_mm) {
    return { ok: false, mensaje: "La cota ya mide ese valor" };
  }
  return { ok: true, valor };
}

/** Separación (mm del modelo) entre etiquetas de distancia que caen sobre el mismo punto. */
const DESFASE_ETIQUETA_MM = 8;
const EJE_INDICE = { x: 0, y: 1, z: 2 };

/**
 * Etiquetas del visor (F23b): `{id, texto, ancla}` de las cotas editables que traen ancla (mm del modelo).
 * Una distancia cuyo ancla ya está ocupada se desplaza a lo largo de su propio eje (de a
 * DESFASE_ETIQUETA_MM) para que las etiquetas X, Y y Z de una placa centrada no se apilen.
 */
export function etiquetasFlotantes(cotas) {
  const ocupadas = new Set();
  return cotas
    .filter((c) => c.operacion && !c.solo_lectura && Array.isArray(c.ancla) && c.ancla.length === 3)
    .map((c) => {
      const ancla = [...c.ancla];
      if (c.tipo === "distancia" && EJE_INDICE[c.eje] !== undefined) {
        const i = EJE_INDICE[c.eje];
        for (let k = 0; ocupadas.has(ancla.join(",")) && k < 10; k += 1) ancla[i] = c.ancla[i] + DESFASE_ETIQUETA_MM * (k + 1);
      }
      ocupadas.add(ancla.join(","));
      return { id: c.id, texto: `${etiquetaCota(c)} ${c.valor_mm} mm`, ancla };
    });
}

// Pulsar una etiqueta del visor enfoca el campo numérico de su fila (el editor de la cota).
if (typeof document !== "undefined") {
  document.addEventListener("forja:cota-pulsada", (evento) => {
    for (const campo of document.querySelectorAll("input[data-cota-id]")) {
      if (campo.dataset.cotaId === evento.detail?.id) {
        campo.scrollIntoView?.({ block: "nearest" });
        campo.focus();
        campo.select();
        return;
      }
    }
  });
}

/** Una respuesta sólo se pinta si sigue siendo la última pedida (las anteriores se descartan). */
export function esGeneracionVigente(generacionPedida, generacionActual) {
  return generacionPedida === generacionActual;
}

// Los rechazos de una edición (422 `{error, mensaje}`) traen `mensaje`; el resto, `detail`.
async function mensajeDeError(resp, porDefecto) {
  const cuerpo = await resp.json().catch(() => ({}));
  if (typeof cuerpo.mensaje === "string") return cuerpo.mensaje;
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
  if (cota.operacion && !cota.solo_lectura) {
    const numero = document.createElement("input");
    numero.type = "number";
    numero.className = "fj-input fj-param__numero";
    numero.step = "any";
    numero.value = String(cota.valor_mm);
    numero.setAttribute("aria-label", `${etiquetaCota(cota)} (mm)`);
    numero.dataset.cotaId = cota.id;
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
    const que = document.createElement("div");
    que.className = "fj-param__desc";
    que.textContent = textoOperacion(cota);
    controles.appendChild(que);
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
 * `alListar(etiquetas)` (opcional, F23b) recibe las etiquetas del visor de la lectura vigente.
 * Un 409 vuelve a leer el listado; una respuesta de una lectura ya superada se descarta.
 */
export async function renderizarCotas(contenedor, docId, alAplicar, alListar = null) {
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
  alListar?.(etiquetasFlotantes(datos.cotas));
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
