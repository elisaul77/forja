// Forja — panel de parámetros de un script paramétrico (Fase 5C).
// Mismo endpoint que la herramienta MCP `parametros` (contrato Fase 5: la UI
// y el MCP comparten UN endpoint): GET para leer el esquema, POST (con token)
// para re-calcular. Cada cambio se agrupa 300 ms antes de enviarse; si llega
// otro cambio mientras el servidor re-calcula, se envía uno solo al terminar
// con los últimos valores.
import { aplicarParametros, obtenerParametros } from "./api.js?v=11";

const panelParametros = document.getElementById("fj-panel-parametros");
const ESPERA_MS = 300;

function formatoMs(ms) {
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${Math.round(ms)} ms`;
}

export class ForjaParametrosControlador {
  constructor(docId, alCambiar) {
    this.docId = docId;
    this.esquema = {};
    this.valores = {};
    // Llamado tras un re-cálculo exitoso (tabs.js recarga malla/ficha/notas);
    // devuelve los ms que tardó en bajar la malla.
    this._alCambiar = alCambiar;
    this._temporizador = null;
    this._enVuelo = false;
    this._pendiente = false;
    this.estado = "";
    this.error = false;
    this._estadoEl = null;
  }

  async cargarInicial() {
    const datos = await obtenerParametros(this.docId);
    this.esquema = datos.esquema ?? {};
    this.valores = { ...(datos.valores ?? {}) };
  }

  tieneParametros() {
    return Object.keys(this.esquema).length > 0;
  }

  ocupado() {
    return this._temporizador !== null || this._enVuelo || this._pendiente ||
      (panelParametros.contains(document.activeElement) && document.activeElement?.matches("input"));
  }

  destruir() {
    clearTimeout(this._temporizador);
    this._estadoEl = null;
  }

  programar(nombre, valor) {
    this.valores[nombre] = valor;
    clearTimeout(this._temporizador);
    this._temporizador = setTimeout(() => this._enviar(), ESPERA_MS);
  }

  _mostrarEstado(texto, error = false) {
    this.estado = texto;
    this.error = error;
    if (this._estadoEl) {
      this._estadoEl.textContent = texto;
      this._estadoEl.classList.toggle("is-error", error);
    }
  }

  async _enviar() {
    this._temporizador = null;
    if (this._enVuelo) {
      this._pendiente = true;
      return;
    }
    this._enVuelo = true;
    this._mostrarEstado("Recalculando…");
    const inicio = performance.now();
    try {
      const respuesta = await aplicarParametros(this.docId, { ...this.valores });
      const msMalla = await this._alCambiar(respuesta);
      const total = performance.now() - inicio;
      if (!this._pendiente) this.valores = { ...respuesta.valores };
      this._mostrarEstado(
        `Servidor ${formatoMs(respuesta.ms)} · malla ${formatoMs(msMalla ?? 0)} · total ${formatoMs(total)}`
      );
    } catch (err) {
      this._mostrarEstado(`No se pudo recalcular: ${err.message}`, true);
    } finally {
      this._enVuelo = false;
      if (this._pendiente) {
        this._pendiente = false;
        this._enviar();
      }
    }
  }
}

function crearCampo(controlador, nombre, def) {
  const fila = document.createElement("div");
  fila.className = "fj-param";

  const etiqueta = document.createElement("label");
  etiqueta.className = "fj-param__etiqueta";
  etiqueta.textContent = def.unidad ? `${nombre} (${def.unidad})` : nombre;
  if (def.desc) etiqueta.title = def.desc;
  fila.appendChild(etiqueta);

  const controles = document.createElement("div");
  controles.className = "fj-param__controles";

  const numero = document.createElement("input");
  numero.type = "number";
  numero.className = "fj-input fj-param__numero";
  numero.step = def.paso ?? "any";
  if (def.min !== undefined) numero.min = def.min;
  if (def.max !== undefined) numero.max = def.max;
  numero.value = controlador.valores[nombre] ?? def.valor;
  numero.setAttribute("aria-label", nombre);

  let deslizador = null;
  if (def.min !== undefined && def.max !== undefined) {
    deslizador = document.createElement("input");
    deslizador.type = "range";
    deslizador.className = "fj-param__deslizador";
    deslizador.min = def.min;
    deslizador.max = def.max;
    deslizador.step = def.paso ?? "any";
    deslizador.value = numero.value;
    deslizador.setAttribute("aria-label", `${nombre} (deslizador)`);
    deslizador.addEventListener("input", () => {
      numero.value = deslizador.value;
      controlador.programar(nombre, Number(deslizador.value));
    });
    controles.appendChild(deslizador);
  }

  numero.addEventListener("change", () => {
    const valor = Number(numero.value);
    const fueraDeRango =
      numero.value === "" ||
      !Number.isFinite(valor) ||
      (def.min !== undefined && valor < def.min) ||
      (def.max !== undefined && valor > def.max);
    numero.classList.toggle("is-error", fueraDeRango);
    if (fueraDeRango) {
      controlador._mostrarEstado(`'${nombre}' fuera de rango [${def.min ?? "−∞"}, ${def.max ?? "∞"}]`, true);
      return;
    }
    if (deslizador) deslizador.value = numero.value;
    controlador.programar(nombre, valor);
  });
  controles.appendChild(numero);
  fila.appendChild(controles);

  if (def.desc) {
    const ayuda = document.createElement("div");
    ayuda.className = "fj-param__desc";
    ayuda.textContent = def.desc;
    fila.appendChild(ayuda);
  }
  return fila;
}

export function renderizarPanelParametros(controlador) {
  panelParametros.textContent = "";
  controlador._estadoEl = null;
  if (!controlador.tieneParametros()) {
    const vacio = document.createElement("div");
    vacio.className = "fj-panel-lateral__vacio";
    vacio.textContent =
      "Este documento no declara PARAMETROS. Creá la pieza con un script que defina " +
      "PARAMETROS = {...} y construir(params) para ajustarla desde acá.";
    panelParametros.appendChild(vacio);
    return;
  }
  for (const [nombre, def] of Object.entries(controlador.esquema)) {
    panelParametros.appendChild(crearCampo(controlador, nombre, def));
  }
  const estado = document.createElement("div");
  estado.className = "fj-param__estado";
  estado.setAttribute("role", "status");
  panelParametros.appendChild(estado);
  controlador._estadoEl = estado;
  controlador._mostrarEstado(controlador.estado || "Mové un valor para recalcular la pieza.", controlador.error);
}
