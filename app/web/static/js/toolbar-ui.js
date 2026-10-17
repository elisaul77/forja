// Forja — barra de herramientas (fase 24a): cinta con pestañas, bloque Claude
// fijo a la derecha, tooltips propios, paleta de comandos (Ctrl+K) y hoja de
// atajos (?). Todo sale de ACCIONES (toolbar-actions.js) y la lógica de
// toolbar.js. Los textos se escriben solo con textContent.
import { PESTANAS, ACCIONES } from "./toolbar-actions.js";
import {
  estadoAccion, tooltipLineas, filtrarPaleta, claveDesdeEvento, debeIgnorarAtajo, buscarPorAtajo,
} from "./toolbar.js";
import { buildDescargaUrl, buildOrcaLink, slugDescarga, AVISO_ORCA } from "./orca.js";

const NS = "http://www.w3.org/2000/svg";
const SPRITE = "/static/icons/forja-icons.svg";
const RETRASO_TOOLTIP_MS = 350;

/**
 * Monta la barra sobre #fj-cinta. `tabs` es el TabManager de app.js.
 * Devuelve { actualizar, ejecutar } para quien necesite refrescarla o lanzar una acción.
 */
export function montarBarraHerramientas({ tabs, documento = document }) {
  const cinta = documento.getElementById("fj-cinta");
  if (!cinta) throw new Error("falta el contenedor #fj-cinta de la barra de herramientas");
  const feedback = documento.getElementById("fj-feedback");
  const app = documento.querySelector(".fj-app");
  const origen = window.location.origin;

  let pestanaActiva = "vista";
  let desarmarCara = null;
  let temporizadorTooltip = null;

  // ---------------- Estado y avisos ----------------
  const entradaActiva = () => tabs.tabs.get(tabs.activeId) ?? null;
  const requerirEntrada = () => {
    const entrada = entradaActiva();
    if (!entrada) throw new Error("no hay un documento abierto");
    return entrada;
  };
  const estadoActual = () => {
    const entrada = entradaActiva();
    return {
      documento: entrada ? { formato: entrada.ficha.formato } : null,
      seleccion: entrada?.viewer.selectedPiece ?? null,
    };
  };

  function mostrar(texto, esError = false) {
    if (!feedback) return;
    feedback.hidden = false;
    feedback.classList.toggle("is-error", esError);
    feedback.textContent = texto;
  }

  function abrirEnlace(url, nombreArchivo = null) {
    const enlace = documento.createElement("a");
    enlace.href = url;
    enlace.rel = "noopener";
    if (nombreArchivo) enlace.download = nombreArchivo;
    enlace.click();
  }

  // ---------------- Selección de cara («Mirar de frente») ----------------
  function armarCara(fn) {
    const visor = requerirEntrada().viewer;
    desarmarPick();
    const lienzo = visor.renderer.domElement;
    const alSoltar = (evento) => {
      if (evento.button !== 0) return;
      const golpe = visor.raycastearMalla(evento.clientX, evento.clientY);
      if (!golpe) {
        mostrar("No hay ninguna cara bajo el cursor. Haz clic sobre el modelo o pulsa Esc.");
        return;
      }
      desarmarPick();
      fn(golpe);
    };
    lienzo.addEventListener("pointerup", alSoltar);
    desarmarCara = () => lienzo.removeEventListener("pointerup", alSoltar);
    mostrar("Haz clic en una cara del modelo para mirarla de frente. Esc cancela.");
  }

  function desarmarPick() {
    if (desarmarCara) {
      desarmarCara();
      desarmarCara = null;
    }
  }

  // ---------------- Contexto de las acciones ----------------
  const contexto = {
    visor: () => requerirEntrada().viewer,
    entrada: () => entradaActiva(),
    pieza: () => entradaActiva()?.viewer.selectedPiece ?? null,
    enfoqueActivo: () => Boolean(app?.classList.contains("fj-app--enfoque")),
    clic(id) {
      const control = documento.getElementById(id);
      if (!control) throw new Error(`no existe el control ${id}`);
      control.click();
    },
    panel(seccion) {
      requerirEntrada();
      tabs.abrirPanel(seccion);
    },
    mostrar,
    exportar(ext) {
      const { ficha } = requerirEntrada();
      const url = buildDescargaUrl(origen, ficha.id, ficha.nombre, ext);
      if (!url) throw new Error(`formato de exportación no disponible: ${ext}`);
      abrirEnlace(url);
    },
    orca() {
      const { ficha } = requerirEntrada();
      const enlace = buildOrcaLink(origen, ficha.id, ficha.nombre, "3mf");
      if (!enlace) throw new Error("no se pudo construir el enlace de OrcaSlicer");
      abrirEnlace(enlace);
      mostrar(AVISO_ORCA);
    },
    captura() {
      const { ficha, viewer } = requerirEntrada();
      abrirEnlace(viewer.capturar(), `${slugDescarga(ficha.nombre)}-vista.png`);
    },
    propiedades() {
      const { ficha } = requerirEntrada();
      const volumen = Number(ficha.volumen).toLocaleString("es", { maximumFractionDigits: 2 });
      mostrar(`${ficha.nombre}: ${volumen} mm³ · ${ficha.solidos} sólido(s).`);
    },
    enfoque() {
      app?.classList.toggle("fj-app--enfoque");
    },
    elegirCara: (fn) => armarCara(fn),
    cancelar() {
      desarmarPick();
      entradaActiva()?.viewer.selectPiece(null);
    },
  };

  function ejecutarAccion(accion) {
    const { habilitada, motivo } = estadoAccion(accion, estadoActual());
    if (!habilitada) {
      mostrar(motivo);
      return;
    }
    try {
      accion.ejecutar(contexto);
    } catch (error) {
      mostrar(`No se pudo ejecutar «${accion.etiqueta}»: ${error.message}`, true);
    }
  }

  // ---------------- Tooltips propios ----------------
  const tooltip = documento.createElement("div");
  tooltip.id = "fj-tooltip";
  tooltip.className = "fj-tooltip";
  tooltip.setAttribute("role", "tooltip");
  tooltip.hidden = true;
  documento.body.append(tooltip);

  function mostrarTooltip(boton, accion) {
    clearTimeout(temporizadorTooltip);
    const estado = estadoActual();
    const { habilitada } = estadoAccion(accion, estado);
    const lineas = tooltipLineas(accion, estado);
    tooltip.replaceChildren();
    lineas.forEach((texto, indice) => {
      const linea = documento.createElement("div");
      const esMotivo = !habilitada && indice === lineas.length - 1;
      linea.className = indice === 0 ? "fj-tooltip__titulo" : esMotivo ? "fj-tooltip__motivo" : "fj-tooltip__texto";
      linea.textContent = texto;
      tooltip.append(linea);
    });
    const rect = boton.getBoundingClientRect();
    tooltip.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - 300))}px`;
    tooltip.style.top = `${rect.bottom + 6}px`;
    tooltip.hidden = false;
  }

  function programarTooltip(boton, accion) {
    clearTimeout(temporizadorTooltip);
    temporizadorTooltip = setTimeout(() => mostrarTooltip(boton, accion), RETRASO_TOOLTIP_MS);
  }

  function ocultarTooltip() {
    clearTimeout(temporizadorTooltip);
    tooltip.hidden = true;
  }

  // ---------------- Cinta ----------------
  function crearIcono(nombre) {
    const svg = documento.createElementNS(NS, "svg");
    svg.setAttribute("class", "fj-cinta__icono");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    const uso = documento.createElementNS(NS, "use");
    uso.setAttribute("href", `${SPRITE}#i-${nombre}`);
    svg.append(uso);
    return svg;
  }

  function crearBoton(accion, estado, { ia = false } = {}) {
    const boton = documento.createElement("button");
    boton.type = "button";
    boton.className = `fj-cinta__boton${ia ? " fj-cinta__boton--ia" : ""}`;
    boton.dataset.accion = accion.id;
    const { habilitada } = estadoAccion(accion, estado);
    boton.classList.toggle("is-disabled", !habilitada);
    boton.setAttribute("aria-disabled", String(!habilitada));
    if (accion.activo && entradaActiva()) {
      const activa = Boolean(accion.activo(contexto));
      boton.setAttribute("aria-pressed", String(activa));
      boton.classList.toggle("is-on", activa);
    }
    const texto = documento.createElement("span");
    texto.className = "fj-cinta__texto";
    texto.textContent = accion.etiqueta;
    boton.append(crearIcono(accion.icono), texto);
    boton.setAttribute("aria-describedby", "fj-tooltip");
    boton.addEventListener("click", () => ejecutarAccion(accion));
    boton.addEventListener("pointerenter", () => programarTooltip(boton, accion));
    boton.addEventListener("pointerleave", ocultarTooltip);
    boton.addEventListener("focus", () => mostrarTooltip(boton, accion));
    boton.addEventListener("blur", ocultarTooltip);
    return boton;
  }

  const fila = documento.createElement("div");
  fila.className = "fj-cinta__fila";
  const pestanasEl = documento.createElement("div");
  pestanasEl.className = "fj-cinta__pestanas";
  pestanasEl.setAttribute("role", "tablist");
  pestanasEl.setAttribute("aria-label", "Pestañas de herramientas");
  const claudeEl = documento.createElement("div");
  claudeEl.className = "fj-cinta__claude";
  claudeEl.setAttribute("role", "group");
  claudeEl.setAttribute("aria-label", "Claude");
  fila.append(pestanasEl, claudeEl);
  const gruposEl = documento.createElement("div");
  gruposEl.className = "fj-cinta__grupos";
  gruposEl.setAttribute("role", "toolbar");
  gruposEl.setAttribute("aria-label", "Herramientas de la pestaña");
  cinta.replaceChildren(fila, gruposEl);

  function renderizar() {
    const estado = estadoActual();
    const pestanas = PESTANAS.filter((p) => !p.cluster);
    pestanasEl.replaceChildren();
    for (const p of pestanas) {
      const activa = p.id === pestanaActiva;
      const boton = documento.createElement("button");
      boton.type = "button";
      boton.className = `fj-cinta__pestana${activa ? " is-active" : ""}`;
      boton.setAttribute("role", "tab");
      boton.setAttribute("aria-selected", String(activa));
      boton.tabIndex = activa ? 0 : -1;
      boton.textContent = p.etiqueta;
      boton.addEventListener("click", () => { pestanaActiva = p.id; renderizar(); });
      boton.addEventListener("keydown", (evento) => {
        const paso = evento.key === "ArrowRight" ? 1 : evento.key === "ArrowLeft" ? -1 : 0;
        if (!paso) return;
        const indice = (pestanas.findIndex((x) => x.id === pestanaActiva) + paso + pestanas.length) % pestanas.length;
        pestanaActiva = pestanas[indice].id;
        renderizar();
        pestanasEl.querySelector(`[data-pestana="${pestanaActiva}"]`)?.focus();
      });
      boton.dataset.pestana = p.id;
      pestanasEl.append(boton);
    }

    gruposEl.replaceChildren();
    const grupos = new Map();
    for (const accion of ACCIONES.filter((a) => a.pestana === pestanaActiva)) {
      if (!grupos.has(accion.grupo)) grupos.set(accion.grupo, []);
      grupos.get(accion.grupo).push(accion);
    }
    for (const [nombre, acciones] of grupos) {
      const bloque = documento.createElement("div");
      bloque.className = "fj-cinta__grupo";
      const botones = documento.createElement("div");
      botones.className = "fj-cinta__botones";
      botones.setAttribute("role", "group");
      botones.setAttribute("aria-label", nombre);
      for (const accion of acciones) botones.append(crearBoton(accion, estado));
      const titulo = documento.createElement("div");
      titulo.className = "fj-cinta__titulo";
      titulo.setAttribute("aria-hidden", "true");
      titulo.textContent = nombre;
      bloque.append(botones, titulo);
      gruposEl.append(bloque);
    }

    claudeEl.replaceChildren();
    for (const accion of ACCIONES.filter((a) => a.pestana === "claude")) {
      claudeEl.append(crearBoton(accion, estado, { ia: true }));
    }
  }

  // ---------------- Paleta de comandos (Ctrl+K) ----------------
  const paleta = documento.createElement("dialog");
  paleta.id = "fj-paleta";
  paleta.className = "fj-dialogo fj-paleta";
  paleta.setAttribute("aria-label", "Paleta de comandos");
  const busqueda = documento.createElement("input");
  busqueda.type = "search";
  busqueda.className = "fj-input fj-paleta__busqueda";
  busqueda.placeholder = "Buscar una acción… (Enter ejecuta la primera disponible)";
  busqueda.setAttribute("aria-label", "Buscar una acción");
  busqueda.autocomplete = "off";
  const lista = documento.createElement("div");
  lista.className = "fj-paleta__lista";
  lista.setAttribute("role", "listbox");
  paleta.append(busqueda, lista);
  documento.body.append(paleta);

  const etiquetaPestana = (id) => PESTANAS.find((p) => p.id === id)?.etiqueta ?? id;
  let foco = null;

  function renderPaleta() {
    const estado = estadoActual();
    const acciones = filtrarPaleta(ACCIONES, busqueda.value);
    lista.replaceChildren();
    if (acciones.length === 0) {
      const vacio = documento.createElement("div");
      vacio.className = "fj-paleta__vacio";
      vacio.textContent = "Ninguna acción coincide con la búsqueda.";
      lista.append(vacio);
      return;
    }
    for (const accion of acciones) {
      const { habilitada, motivo } = estadoAccion(accion, estado);
      const item = documento.createElement("button");
      item.type = "button";
      item.className = `fj-paleta__item${habilitada ? "" : " is-disabled"}`;
      item.setAttribute("role", "option");
      item.setAttribute("aria-disabled", String(!habilitada));
      item.dataset.accion = accion.id;
      const nombre = documento.createElement("span");
      nombre.className = "fj-paleta__nombre";
      nombre.textContent = accion.etiqueta;
      const meta = documento.createElement("span");
      meta.className = "fj-paleta__meta";
      meta.textContent = `${etiquetaPestana(accion.pestana)} · ${accion.grupo ?? ""}${accion.atajo ? ` · ${accion.atajo}` : ""}`;
      item.append(nombre, meta);
      if (!habilitada) {
        const aviso = documento.createElement("span");
        aviso.className = "fj-paleta__motivo";
        aviso.textContent = motivo;
        item.append(aviso);
      }
      item.addEventListener("click", () => {
        paleta.close();
        ejecutarAccion(accion);
      });
      lista.append(item);
    }
  }

  busqueda.addEventListener("input", renderPaleta);
  busqueda.addEventListener("keydown", (evento) => {
    if (evento.key === "Enter") {
      evento.preventDefault();
      lista.querySelector('[role="option"]:not([aria-disabled="true"])')?.click();
    } else if (evento.key === "ArrowDown") {
      evento.preventDefault();
      lista.querySelector('[role="option"]')?.focus();
    }
  });
  lista.addEventListener("keydown", (evento) => {
    if (evento.key !== "ArrowDown" && evento.key !== "ArrowUp") return;
    evento.preventDefault();
    const items = [...lista.querySelectorAll('[role="option"]')];
    const indice = items.indexOf(documento.activeElement);
    const siguiente = evento.key === "ArrowDown" ? indice + 1 : indice - 1;
    if (siguiente < 0) busqueda.focus();
    else items[siguiente]?.focus();
  });
  paleta.addEventListener("close", () => foco?.focus?.());

  function abrirPaleta() {
    foco = documento.activeElement;
    busqueda.value = "";
    renderPaleta();
    paleta.showModal();
    busqueda.focus();
  }

  // ---------------- Hoja de atajos (?) ----------------
  const atajos = documento.createElement("dialog");
  atajos.id = "fj-atajos";
  atajos.className = "fj-dialogo fj-atajos";
  atajos.setAttribute("aria-labelledby", "fj-atajos-titulo");
  const cabeceraAtajos = documento.createElement("div");
  cabeceraAtajos.className = "fj-atajos__cabecera";
  const tituloAtajos = documento.createElement("h3");
  tituloAtajos.id = "fj-atajos-titulo";
  tituloAtajos.textContent = "Atajos de teclado";
  const cerrarAtajos = documento.createElement("button");
  cerrarAtajos.type = "button";
  cerrarAtajos.className = "fj-btn fj-btn--ghost";
  cerrarAtajos.textContent = "Cerrar";
  cerrarAtajos.addEventListener("click", () => atajos.close());
  cabeceraAtajos.append(tituloAtajos, cerrarAtajos);
  const filas = documento.createElement("div");
  filas.className = "fj-atajos__filas";
  const filaAtajo = (tecla, texto) => {
    const fila = documento.createElement("div");
    fila.className = "fj-atajos__fila";
    const kbd = documento.createElement("kbd");
    kbd.textContent = tecla;
    const descripcion = documento.createElement("span");
    descripcion.textContent = texto;
    fila.append(kbd, descripcion);
    return fila;
  };
  filas.append(filaAtajo("?", "Mostrar esta ayuda"), filaAtajo("Ctrl+K", "Paleta de comandos"));
  for (const accion of ACCIONES.filter((a) => a.atajo)) filas.append(filaAtajo(accion.atajo, accion.etiqueta));
  atajos.append(cabeceraAtajos, filas);
  documento.body.append(atajos);

  function abrirAtajos() {
    foco = documento.activeElement;
    atajos.showModal();
    cerrarAtajos.focus();
  }
  atajos.addEventListener("close", () => foco?.focus?.());

  // ---------------- Atajos globales (un solo listener) ----------------
  documento.addEventListener("keydown", (evento) => {
    const dialogoAbierto = Boolean(documento.querySelector("dialog[open]"));
    if (evento.ctrlKey && !evento.altKey && !evento.shiftKey && evento.key.toLowerCase() === "k") {
      evento.preventDefault();
      if (!dialogoAbierto) abrirPaleta();
      return;
    }
    const activo = documento.activeElement;
    const zonaVisor = !activo || activo === documento.body || Boolean(activo.closest?.("#fj-main, #fj-cinta"));
    if (debeIgnorarAtajo(evento, { target: evento.target, dialogoAbierto, zonaVisor })) return;
    if (evento.key === "?") {
      evento.preventDefault();
      abrirAtajos();
      return;
    }
    const accion = buscarPorAtajo(ACCIONES, claveDesdeEvento(evento));
    if (!accion) return;
    evento.preventDefault();
    ejecutarAccion(accion);
  });

  tabs.onEstadoCambio = () => renderizar();
  renderizar();
  return { actualizar: renderizar, ejecutar: ejecutarAccion };
}
