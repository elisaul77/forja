import { renderDesign, designHit, disposeDesign, refreshDesign, panelSectionFor } from "./design.js?v=10";
// Forja — gestor de pestañas: cada pestaña posee su propio contenedor de
// visor y su propia instancia de ForjaViewer (cámara/selección independientes
// por pestaña, ver ADR-0002 / plan Fase 2).
import { ForjaViewer } from "./viewer.js?v=design1-22";
import { ForjaNotasControlador } from "./notes.js?v=26";
import { ForjaHistorialControlador, renderizarPanelHistorial } from "./historial.js?v=4";
import { ForjaHistorial2Controlador, renderizarHistorial2 } from "./historial2.js?v=5";
import { ForjaParametrosControlador, renderizarPanelParametros } from "./parametros.js?v=15";
import { obtenerDocumento, obtenerMallaConRevision, obtenerTokenSesion } from "./api.js?v=13";
import { crearBotonCupon } from "./cupon.js?v=4";
import { ForjaEnsambleControlador, renderizarPanelEnsamble } from "./ensamble.js?v=3";
import { AnimationView } from "./animation.js?v=7";
import { renderPiecesPanel } from "./pieces.js?v=10";
import { coloresDePiezas, guardarMateriales, obtenerMateriales } from "./materiales.js?v=2";
import { construirAnclas } from "./orca.js?v=4";
import { createRefreshScheduler } from "./live-core.js?v=11";
import { ForjaRamasControlador, renderizarPanelRamas, textoResumen } from "./ramas.js?v=4";
import { ForjaMedidasControlador } from "./medidas.js?v=1";

function formatoVolumen(mm3) {
  return `${mm3.toFixed(2)} mm³`;
}

function formatoBbox(bbox) {
  // Orden del backend: (xmin, xmax, ymin, ymax, zmin, zmax)
  const [xmin, xmax, ymin, ymax, zmin, zmax] = bbox;
  const f = (n) => n.toFixed(1);
  return `${f(xmax - xmin)} × ${f(ymax - ymin)} × ${f(zmax - zmin)} mm`;
}

export class TabManager {
  constructor({ tabsEl, mainEl, vacioEl, statusEls }) {
    this.tabsEl = tabsEl;
    this.mainEl = mainEl;
    this.vacioEl = vacioEl;
    this.statusEls = statusEls;
    this.tabs = new Map(); // docId -> { boton, contenedor, viewer, ficha, notas, historial }
    this.activeId = null;
    // Lo asigna app.js: abre (o activa) la pestaña de un documento por id.
    this.abrirDocumento = null;
    // Barra de herramientas (24a): avisa cuando cambian pestaña activa o selección.
    this.onEstadoCambio = null;
    // Cursor de deshacer (24a2): avisa cuando el historial de un documento cambió.
    this.onHistorialCambio = null;

    // Panel lateral (Fase 4), compartido entre pestañas: muestra siempre
    // los datos de la pestaña activa.
    this.panel = document.getElementById("fj-panel-lateral");
    this.panelSeccionHistorial = document.getElementById("fj-panel-historial");
    this.panelSeccionRamas = document.getElementById("fj-panel-ramas");
    this.panelSeccionParametros = document.getElementById("fj-panel-parametros");
    this.panelSeccionEnsamble = document.getElementById("fj-panel-ensamble");
    this.panelSeccionPiezas = document.getElementById("fj-panel-piezas");
    this.panelSeccionMedidas = document.getElementById("fj-panel-medidas");
    this.panelSeccionDesign = document.getElementById("fj-panel-design");
    // Indicaciones (antes «Diseño») es la lista única: notas, trazos y propuestas.
    this.panelActivo = "design";
    document.getElementById("fj-panel-lateral-cerrar").addEventListener("click", () => {
      this.panel.hidden = true;
      this.panelReturnFocus?.focus();
    });
    this.panel.addEventListener("keydown", event => {
      if (event.key === "Escape") {
        this.panel.hidden = true;
        this.panelReturnFocus?.focus();
      }
    });
    for (const boton of document.querySelectorAll(".fj-panel-lateral__tab")) {
      boton.addEventListener("click", () => this.abrirPanel(boton.dataset.panel));
    }
  }

  tieneAbierto(docId) {
    return this.tabs.has(docId);
  }

  _mostrarSeccionPanel(seccion) {
    this.panelActivo = seccion;
    this.panelSeccionDesign.hidden = seccion !== "design";
    this.panelSeccionHistorial.hidden = seccion !== "historial";
    // Historial 2.0: «Ramas» vive en el cajón de la vista Historial.
    this.panel.classList.toggle("fj-panel-lateral--historial", seccion === "historial");
    this.panelSeccionParametros.hidden = seccion !== "parametros";
    this.panelSeccionEnsamble.hidden = seccion !== "ensamble";
    this.panelSeccionMedidas.hidden = seccion !== "medidas";
    this.panelSeccionPiezas.hidden = seccion !== "piezas";
    for (const boton of document.querySelectorAll(".fj-panel-lateral__tab")) {
      boton.classList.toggle("is-active", boton.dataset.panel === seccion);
      boton.setAttribute("aria-pressed", String(boton.dataset.panel === seccion));
    }
  }

  abrirPanel(seccion) {
    if (!this.activeId) return;
    seccion = panelSectionFor(seccion);
    if (this.panel.hidden) this.panelReturnFocus = document.activeElement;
    this.panel.hidden = false;
    this._mostrarSeccionPanel(seccion);
    this._renderizarPanelPestanaActiva();
    this.panel.querySelector(`[data-panel="${seccion}"]`)?.focus();
  }

  _renderizarPanelPestanaActiva() {
    const entrada = this.tabs.get(this.activeId);
    if (!entrada) return;
    if (this.panelActivo === "design") renderDesign(entrada, this.panelSeccionDesign);
    renderizarPanelHistorial(entrada.historial);
    if (this.panelActivo === "historial") {
      renderizarHistorial2(entrada.h2);
      renderizarPanelRamas(entrada.ramas);
    }
    renderizarPanelParametros(entrada.parametros);
    if (this.panelActivo === "ensamble") renderizarPanelEnsamble(entrada.ensamble);
    if (this.panelActivo === "piezas") renderPiecesPanel(entrada.viewer, entrada.ficha.nombre, entrada.materiales);
    if (this.panelActivo === "medidas") entrada.medidas.mostrar(entrada.revision);
  }

  /** Abre (o reactiva si ya existe) una pestaña para el documento dado. */
  async abrirTab(ficha, mallaArrayBuffer, revision = ficha.revision) {
    if (this.tabs.has(ficha.id)) {
      this.activar(ficha.id);
      return;
    }

    const boton = document.createElement("div");
    boton.className = "fj-tab";
    boton.setAttribute("role", "tab");
    boton.title = ficha.nombre;
    boton.addEventListener("keydown", event => {
      if (event.target !== boton) return;
      if (["Enter", " "].includes(event.key)) {
        event.preventDefault(); this.activar(ficha.id);
      }
      if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
        event.preventDefault();
        const ids = [...this.tabs.keys()];
        let index = ids.indexOf(ficha.id);
        if (event.key === "Home") index = 0;
        else if (event.key === "End") index = ids.length - 1;
        else index = (index + (event.key === "ArrowRight" ? 1 : -1) + ids.length) % ids.length;
        this.activar(ids[index]); this.tabs.get(ids[index]).boton.focus();
      }
    });
    boton.innerHTML = `<span class="fj-tab__nombre"></span><button type="button" class="fj-tab__cerrar" aria-label="Cerrar">×</button>`;
    boton.querySelector(".fj-tab__nombre").textContent = ficha.nombre;
    boton.querySelector(".fj-tab__cerrar").setAttribute("aria-label", `Cerrar ${ficha.nombre}`);
    boton.addEventListener("click", (ev) => {
      if (ev.target.closest(".fj-tab__cerrar")) return;
      this.activar(ficha.id);
    });
    boton.querySelector(".fj-tab__cerrar").addEventListener("click", (ev) => {
      ev.stopPropagation();
      this.cerrar(ficha.id);
    });
    this.tabsEl.appendChild(boton);

    const contenedor = document.createElement("div");
    contenedor.className = "fj-viewport";
    this.mainEl.appendChild(contenedor);

    const viewer = new ForjaViewer(contenedor);
    viewer.cargarMallaSTL(mallaArrayBuffer);
    viewer.onDesignHit = hit => { if(this.activeId === ficha.id && this.panelActivo === "design") designHit(this.tabs.get(ficha.id), hit); };

    const notas = new ForjaNotasControlador(viewer, ficha.id);
    notas.alCambiarMarcas = () => this._refrescarIndicaciones(ficha.id);
    viewer.canSelectPiece = () => !notas.modo && animation.mode === "cad";
    viewer.onPieceSelection = () => {
      if (this.activeId === ficha.id && !this.panel.hidden && this.panelActivo === "piezas") {
        const actual = this.tabs.get(ficha.id);
        renderPiecesPanel(viewer, actual.ficha.nombre, actual.materiales);
      }
      if (this.activeId === ficha.id && !this.panel.hidden && this.panelActivo === "historial") {
        renderizarHistorial2(this.tabs.get(ficha.id).h2);
      }
    };
    const historial = new ForjaHistorialControlador(ficha.id, async (registroActualizado) => {
      await this._alRestaurar(ficha.id, registroActualizado);
    });
    const ramas = new ForjaRamasControlador(ficha.id, {
      alCambiar: async (registro) => { this._salirComparacion(ficha.id); await this._alRestaurar(ficha.id, registro); },
      alComparar: async (resumen, mallaA, mallaB) => this._mostrarComparacion(ficha.id, resumen, mallaA, mallaB),
    });
    const h2 = new ForjaHistorial2Controlador(ficha.id, {
      alCambiar: async (registro) => { this._salirComparacion(ficha.id); await this._alRestaurar(ficha.id, registro); },
      alComparar: async (resumen, mallaA, mallaB, opciones) => this._mostrarComparacion(ficha.id, resumen, mallaA, mallaB, opciones),
      piezaSeleccionada: () => viewer.selectedPiece,
    });
    const parametros = new ForjaParametrosControlador(ficha.id, async (registroActualizado) => {
      await this._alCambiarParametros(ficha.id, registroActualizado);
    });
    // F23b (ADR-0025): the editable dimensions of the document are labels on the 3D model.
    parametros.alListarCotas = (etiquetas) => viewer.mostrarEtiquetasCotas(etiquetas);
    const ensamble = new ForjaEnsambleControlador(ficha.id, async (registroActualizado) => {
      await this._alCambiarParametros(ficha.id, registroActualizado);
    }, () => {
      const current = this.tabs.get(ficha.id);
      if (current) current.animation.setAssembly(ensamble.data, ensamble.revision, current.revision);
    });

    const toolbar = document.createElement("div");
    toolbar.className = "fj-viewport__toolbar";
    toolbar.setAttribute("role", "group");
    toolbar.setAttribute("aria-label", "Herramientas del documento");
    const btnNota = document.createElement("button");
    btnNota.type = "button";
    btnNota.className = "fj-btn";
    btnNota.textContent = "Añadir nota";
    btnNota.addEventListener("click", () => {
      btnNota.classList.toggle("is-active", notas.activarModoNota());
      btnPizarra.classList.remove("is-active");
    });
    const btnPizarra = document.createElement("button");
    btnPizarra.type = "button";
    btnPizarra.className = "fj-btn";
    btnPizarra.textContent = "Dibujar indicación";
    btnPizarra.addEventListener("click", async () => {
      btnNota.classList.remove("is-active");
      const activo = await notas.activarModoPizarra();
      btnPizarra.classList.toggle("is-active", activo);
    });
    const btnPanel = document.createElement("button");
    btnPanel.type = "button";
    btnPanel.className = "fj-btn";
    btnPanel.textContent = "Indicaciones";
    btnPanel.addEventListener("click", () => this.abrirPanel("design"));
    const btnParametros = document.createElement("button");
    btnParametros.type = "button";
    btnParametros.className = "fj-btn";
    btnParametros.textContent = "Parámetros";
    btnParametros.hidden = true; // solo si el script declara PARAMETROS
    btnParametros.addEventListener("click", () => this.abrirPanel("parametros"));
    const btnEnsamble = document.createElement("button");
    btnEnsamble.type = "button";
    btnEnsamble.className = "fj-btn";
    btnEnsamble.textContent = "Ensamble";
    btnEnsamble.addEventListener("click", () => this.abrirPanel("ensamble"));
    const btnPieces = document.createElement("button");
    btnPieces.type = "button";
    btnPieces.className = "fj-btn";
    btnPieces.textContent = "Piezas";
    btnPieces.addEventListener("click", () => this.abrirPanel("piezas"));
    toolbar.append(btnPieces, btnNota, btnPizarra, btnPanel, btnParametros, btnEnsamble);
    const anclasOrca = construirAnclas(document, window.location.origin, ficha, (texto) => {
      const feedback = document.getElementById("fj-feedback");
      if (!feedback) return;
      feedback.hidden = false;
      feedback.classList.remove("is-error");
      feedback.textContent = texto;
    }, () => viewer.selectedPiece);
    toolbar.append(...anclasOrca);
    const btnCupon = crearBotonCupon(document, {
      id: ficha.id,
      origin: window.location.origin,
      obtenerPieza: () => viewer.selectedPiece,
      obtenerToken: obtenerTokenSesion,
      abrirDocumento: (id) => this.abrirDocumento?.(id),
      mostrar: (texto, esError, enlace) => {
        const feedback = document.getElementById("fj-feedback");
        if (!feedback) return;
        feedback.hidden = false;
        feedback.classList.toggle("is-error", Boolean(esError));
        feedback.textContent = texto;
        if (enlace) {
          const a = document.createElement("a");
          a.className = "fj-btn";
          a.href = enlace.href;
          a.textContent = enlace.texto;
          feedback.append(" ", a);
        }
      },
    });
    toolbar.append(btnCupon);
    viewer.onSeleccion = () => { anclasOrca.refrescar?.(); this.onEstadoCambio?.(); };
    contenedor.appendChild(toolbar);
    const animation = new AnimationView(contenedor, viewer, toolbar, () => {
      if (notas.ocupado()) {
        const feedback = document.getElementById("fj-feedback");
        feedback.hidden = false;
        feedback.textContent = "Termina la nota o el trazo antes de abrir la animación.";
        return false;
      }
      this._salirComparacion(ficha.id);
      notas.modo = null;
      btnNota.classList.remove("is-active");
      btnPizarra.classList.remove("is-active");
      viewer.ocultarPlanoPizarra();
      return true;
    }, ficha.id);

    const viewControls = document.createElement("div");
    viewControls.className = "fj-view-controls";
    viewControls.setAttribute("role", "group");
    viewControls.setAttribute("aria-label", "Controles de vista 3D");
    const viewLabel = document.createElement("span");
    viewLabel.className = "fj-view-controls__label";
    viewLabel.textContent = "VISTA 3D";
    viewControls.append(viewLabel);
    // Etiqueta larga + corta: con poco ancho (panel abierto) la barra usa la
    // corta para caber en una fila; el nombre completo queda en title/aria.
    const rotular = (button, largo, corto) => {
      const l = document.createElement("span"); l.className = "fj-view-controls__largo"; l.textContent = largo;
      const c = document.createElement("span"); c.className = "fj-view-controls__corto"; c.textContent = corto; c.setAttribute("aria-hidden", "true");
      button.append(l, c); button.title = largo; button.setAttribute("aria-label", largo);
    };
    for (const [label, corto, direction] of [["Isométrica", "Iso", [1, -1, .75]], ["Superior", "Sup", [0, 0, 1]], ["Frontal", "Fren", [0, -1, 0]], ["Lateral", "Lat", [1, 0, 0]]]) {
      const button = document.createElement("button");
      button.type = "button"; button.className = "fj-btn"; rotular(button, label, corto);
      button.addEventListener("click", () => viewer.ajustarVista(direction));
      viewControls.append(button);
    }
    const fit = document.createElement("button");
    fit.type = "button"; fit.className = "fj-btn"; rotular(fit, "Encuadrar", "⤢");
    fit.addEventListener("click", () => viewer.ajustarVista(viewer.direccionHaciaCamara()));
    viewControls.append(fit);
    for (const [label, corto, initial, action] of [["Cuadrícula", "▦", true, enabled => viewer.setGridVisible(enabled)], ["Malla", "◇", false, enabled => viewer.setWireframe(enabled)]]) {
      const toggle = document.createElement("button");
      toggle.type = "button"; toggle.className = "fj-btn"; rotular(toggle, label, corto);
      toggle.setAttribute("aria-pressed", String(initial));
      toggle.addEventListener("click", () => {
        const enabled = toggle.getAttribute("aria-pressed") !== "true";
        toggle.setAttribute("aria-pressed", String(enabled)); action(enabled);
      });
      viewControls.append(toggle);
    }
    // F19 (ADR-0027): vista explosionada de display. Solo STEP (piezas con nombre).
    // El deslizador (0-100 %) aparece al activar el botón. No llama a la API.
    const explosion = document.createElement("button");
    explosion.type = "button"; explosion.className = "fj-btn"; rotular(explosion, "Explosionar", "Expl");
    explosion.setAttribute("aria-pressed", "false");
    explosion.hidden = ficha.formato !== "step";
    const separacion = document.createElement("input");
    separacion.type = "range"; separacion.min = "0"; separacion.max = "100"; separacion.step = "1"; separacion.value = "50";
    separacion.className = "fj-explosion__rango"; separacion.hidden = true;
    separacion.setAttribute("aria-label", "Separación de la vista explosionada");
    const porcentaje = document.createElement("output");
    porcentaje.className = "fj-explosion__valor"; porcentaje.hidden = true; porcentaje.textContent = "50 %";
    const articulacionesVigentes = () => this.tabs.get(ficha.id)?.ensamble?.data?.articulaciones ?? [];
    explosion.addEventListener("click", () => {
      if (animation.mode !== "cad") {
        const feedback = document.getElementById("fj-feedback");
        feedback.hidden = false;
        feedback.textContent = "Sal del modo animación para explosionar las piezas.";
        return;
      }
      const activa = explosion.getAttribute("aria-pressed") !== "true";
      explosion.setAttribute("aria-pressed", String(activa));
      separacion.hidden = porcentaje.hidden = !activa;
      viewer.setExplosion(activa ? Number(separacion.value) / 100 : 0, articulacionesVigentes);
    });
    separacion.addEventListener("input", () => {
      porcentaje.textContent = `${separacion.value} %`;
      if (explosion.getAttribute("aria-pressed") === "true") viewer.setExplosion(Number(separacion.value) / 100, articulacionesVigentes);
    });
    viewControls.append(explosion, separacion, porcentaje);
    contenedor.append(viewControls);
    const help = document.createElement("div");
    help.className = "fj-view-help";
    help.textContent = "Arrastrar: orbitar · Rueda: acercar · Botón derecho: desplazar";
    contenedor.append(help);
    const notice = document.createElement("div");
    notice.className = "fj-live-notice";
    notice.textContent = "El modelo cambió; se actualizará al terminar";
    notice.hidden = true;
    contenedor.append(notice);
    // G3: banda del modo Comparar (resumen numérico + volver).
    const banda = document.createElement("div");
    banda.className = "fj-comparar";
    banda.hidden = true;
    contenedor.append(banda);

    // Historial 2.0: divisor arrastrable de la vista antes/después.
    const divisor = document.createElement("div");
    divisor.className = "fj-divisor";
    divisor.hidden = true;
    divisor.tabIndex = 0;
    divisor.setAttribute("role", "slider");
    divisor.setAttribute("aria-label", "Deslizador antes / después");
    divisor.setAttribute("aria-valuemin", "0");
    divisor.setAttribute("aria-valuemax", "100");
    divisor.innerHTML = '<span class="fj-divisor__etq fj-divisor__etq--a"></span><span class="fj-divisor__asa" aria-hidden="true">⟷</span><span class="fj-divisor__etq fj-divisor__etq--b"></span>';
    const moverDivisor = fraccion => {
      const f = Math.min(0.98, Math.max(0.02, fraccion));
      divisor.style.left = `${f * 100}%`;
      divisor.setAttribute("aria-valuenow", String(Math.round(f * 100)));
      viewer.fijarDivision(f);
    };
    divisor._mover = moverDivisor;
    divisor.addEventListener("pointerdown", ev => {
      ev.preventDefault();
      divisor.setPointerCapture(ev.pointerId);
      const mover = e => { const r = contenedor.getBoundingClientRect(); moverDivisor((e.clientX - r.left) / r.width); };
      const soltar = () => { divisor.removeEventListener("pointermove", mover); divisor.removeEventListener("pointerup", soltar); };
      divisor.addEventListener("pointermove", mover);
      divisor.addEventListener("pointerup", soltar);
    });
    divisor.addEventListener("keydown", ev => {
      const actual = Number(divisor.getAttribute("aria-valuenow") || 50) / 100;
      if (ev.key === "ArrowLeft") { ev.preventDefault(); moverDivisor(actual - 0.05); }
      if (ev.key === "ArrowRight") { ev.preventDefault(); moverDivisor(actual + 0.05); }
    });
    contenedor.append(divisor);

    const medidas = new ForjaMedidasControlador({
      docId: ficha.id,
      contenedor: this.panelSeccionMedidas,
      visible: () => this.activeId === ficha.id && !this.panel.hidden && this.panelActivo === "medidas",
    });
    const entry = { boton, contenedor, viewer, ficha, notas, historial, ramas, h2, divisor, banda, parametros, ensamble, animation, btnParametros,
      medidas, revision, pendingPanels: new Set(), panelLoading: false, localRefresh: false, notice };
    // Controles que la barra de herramientas reutiliza (no se duplican).
    entry.acciones = { nota: btnNota, pizarra: btnPizarra, cupon: btnCupon, explosion };
    // Acciones del panel Indicaciones: mismos modos que los botones del visor.
    entry.designActions = {
      pointMode: () => { if (notas.modo !== "nota") btnNota.click(); },
      drawMode: () => btnPizarra.click(),
    };
    // Tras deshacer una propuesta (restaurar), recarga la pestaña entera.
    entry.alRestaurar = registro => this._alRestaurar(ficha.id, registro);
    // Indicaciones: «Ver propuesta» usa la misma vista antes/después que el historial.
    entry.compararPropuesta = async (mallaPropuesta, estados, { lineas, alSalir } = {}) => {
      const mallaActual = (await obtenerMallaConRevision(ficha.id)).buffer;
      this._mostrarComparacion(ficha.id, { piezas: estados }, mallaActual, mallaPropuesta, {
        modo: "dividida", etiquetas: ["Ahora", "Propuesta"], titulo: "Ahora → Propuesta", lineas, alSalir,
      });
    };
    entry.salirComparacion = () => this._salirComparacion(ficha.id);
    entry.materiales = {
      materiales: {},
      guardar: async cambios => {
        const respuesta = await guardarMateriales(ficha.id, cambios);
        this._aplicarMateriales(entry, respuesta.materiales);
      },
    };
    boton.dataset.revision = revision ?? "";
    entry.scheduler = createRefreshScheduler({
      canRun: () => this.activeId === ficha.id && !document.hidden && !entry.localRefresh && !notas.ocupado() && !parametros.ocupado(),
      fetchMesh: async signal => {
        const mesh = await obtenerMallaConRevision(ficha.id, { signal });
        const record = await obtenerDocumento(ficha.id);
        return { ...mesh, ficha: record };
      },
      applyMesh: async result => {
        if (result.ficha.revision && result.revision !== result.ficha.revision) {
          entry.scheduler.notify(result.ficha.revision);
          return false;
        }
        await this._aplicarMalla(ficha.id, result.ficha, result);
      },
      onPending: pending => {
        boton.classList.toggle("is-stale", pending);
        boton.title = pending ? `${entry.ficha.nombre} · actualización pendiente` : entry.ficha.nombre;
        notice.hidden = !(pending && notas.ocupado());
      },
      onError: error => {
        if (String(error.message).startsWith("404")) this.documentoEliminado(ficha.id);
      },
    });
    entry.scheduler.setShown(revision);
    this.tabs.set(ficha.id, entry);
    try {
      await Promise.all([notas.cargarInicial(), historial.cargarInicial(), ramas.cargarInicial().catch(() => {}), parametros.cargarInicial(), ensamble.cargarInicial(), this._cargarMateriales(entry)]);
    } catch (error) {
      this.cerrar(ficha.id);
      throw error;
    }
    btnParametros.hidden = !parametros.tieneParametros();
    animation.setAssembly(ensamble.data, ensamble.revision, entry.revision);
    this.activar(ficha.id);
    viewer.ajustarVista();
  }

  activar(docId) {
    const entrada = this.tabs.get(docId);
    if (!entrada) return;
    if (this.activeId !== docId) this.tabs.get(this.activeId)?.animation.pause();

    for (const [id, t] of this.tabs) {
      const activa = id === docId;
      t.boton.classList.toggle("is-active", activa);
      t.boton.setAttribute("aria-selected", String(activa));
      t.boton.tabIndex = activa ? 0 : -1;
      t.contenedor.classList.toggle("is-active", activa);
    }
    this.activeId = docId;
    this.vacioEl.hidden = true;
    // El contenedor pasa de display:none a visible: forzar reajuste y
    // renderizado inmediato (no depender solo del bucle de animación).
    entrada.viewer.resize();
    this._actualizarStatusBar(entrada.ficha);
    if (!this.panel.hidden) this._renderizarPanelPestanaActiva();
    entrada.scheduler.flush(true);
    this.onEstadoCambio?.();
  }

  /** Callback del panel de historial tras un `restaurar` exitoso: recarga
   * la malla/ficha/notas de la pestaña con el estado ya restaurado. */
  async _alRestaurar(docId, registroActualizado) {
    const entrada = this.tabs.get(docId);
    if (!entrada) return;
    entrada.localRefresh = true;
    try {
      await entrada.scheduler.cancelAndWait();
      const malla = await obtenerMallaConRevision(docId);
      if (this.tabs.get(docId) !== entrada) return;
      entrada.ficha = registroActualizado;
      entrada.boton.querySelector(".fj-tab__nombre").textContent = registroActualizado.nombre;
      entrada.animation.pause();
      entrada.animation.resetPose();
      entrada.viewer.cargarMallaSTL(malla.buffer);
      this._setRevision(entrada, malla.revision);
      entrada.notas.invalidarCaras();
      await Promise.all([entrada.notas.recargar(), entrada.historial.cargarInicial(), entrada.ramas.cargarInicial().catch(() => {}), entrada.h2.invalidar(), entrada.parametros.cargarInicial(), entrada.ensamble.cargarInicial(), this._cargarMateriales(entrada)]);
      entrada.btnParametros.hidden = !entrada.parametros.tieneParametros();
      entrada.animation.setAssembly(entrada.ensamble.data, entrada.ensamble.revision, entrada.revision);
      if (docId === this.activeId) {
        this._actualizarStatusBar(entrada.ficha);
        this._renderizarPanelPestanaActiva();
      }
    } finally {
      entrada.localRefresh = false;
      entrada.scheduler.flush(true);
      this.onHistorialCambio?.(docId);
    }
  }

  /** Recarga la pestaña tras deshacer o rehacer desde la barra (24a2): `registro` es la
   * respuesta del servidor, con el mismo significado que en `restaurar`. */
  recargarTrasRevision(docId, registro) {
    return this._alRestaurar(docId, registro);
  }

  /** Tras crear o cambiar una marca: refresca Indicaciones si está a la vista. */
  _refrescarIndicaciones(docId) {
    const entry = this.tabs.get(docId);
    if (!entry || this.activeId !== docId || this.panel.hidden || this.panelActivo !== "design") return;
    refreshDesign(entry);
  }

  /** Callback del panel de parámetros tras un re-cálculo exitoso: nueva
   * malla (sin re-encuadrar la cámara), ficha, notas re-resueltas e
   * historial. Devuelve los ms que tardó bajar la malla. */
  async _alCambiarParametros(docId, registroActualizado) {
    const entrada = this.tabs.get(docId);
    if (!entrada) return 0;
    const inicio = performance.now();
    entrada.localRefresh = true;
    try {
      await entrada.scheduler.cancelAndWait();
      const malla = await obtenerMallaConRevision(docId);
      if (this.tabs.get(docId) !== entrada) return 0;
      entrada.animation.pause();
      entrada.animation.resetPose();
      entrada.viewer.cargarMallaSTL(malla.buffer, { conservarVista: true });
      this._setRevision(entrada, malla.revision);
      entrada.notas.invalidarCaras();
      const msMalla = performance.now() - inicio;
      entrada.ficha = registroActualizado;
      await Promise.all([entrada.notas.recargar(), entrada.historial.cargarInicial(), entrada.ramas.cargarInicial().catch(() => {}), entrada.h2.invalidar(), entrada.ensamble.cargarInicial(), this._cargarMateriales(entrada)]);
      entrada.animation.setAssembly(entrada.ensamble.data, entrada.ensamble.revision, entrada.revision);
      if (docId === this.activeId) {
        this._actualizarStatusBar(entrada.ficha);
        if (!this.panel.hidden && this.panelActivo !== "parametros") this._renderizarPanelPestanaActiva();
      }
      return msMalla;
    } finally {
      entrada.localRefresh = false;
      entrada.scheduler.flush(true);
    }
  }

  /** G3: modo Comparar sobre el visor de la pestaña. */
  _mostrarComparacion(docId, resumen, mallaA, mallaB, opciones = {}) {
    const entry = this.tabs.get(docId);
    if (!entry) return;
    entry.animation.setMode("cad");
    const dividida = opciones.modo === "dividida";
    const [etqA, etqB] = opciones.etiquetas ?? [resumen.a.sha_corto.slice(0, 7), resumen.b.sha_corto.slice(0, 7)];
    entry.alSalirComparacion = opciones.alSalir ?? null;
    if (dividida) {
      entry.viewer.mostrarComparacionDividida(mallaA, mallaB, resumen.piezas);
      entry.divisor.querySelector(".fj-divisor__etq--a").textContent = `◀ ${etqA}`;
      entry.divisor.querySelector(".fj-divisor__etq--b").textContent = `${etqB} ▶`;
      entry.divisor.hidden = false;
      entry.divisor._mover(0.5);
    } else {
      entry.viewer.mostrarComparacion(mallaA, mallaB, resumen.piezas);
      entry.divisor.hidden = true;
    }
    entry.banda.textContent = "";
    entry.banda.classList.toggle("is-dividida", dividida);
    const titulo = document.createElement("strong");
    titulo.textContent = opciones.titulo ?? `Comparar ${resumen.a.sha_corto.slice(0, 7)} → ${resumen.b.sha_corto.slice(0, 7)}`;
    const modos = document.createElement("div");
    modos.className = "fj-comparar__modos";
    modos.setAttribute("role", "group");
    modos.setAttribute("aria-label", "Modo de comparación");
    for (const [modo, texto] of [["dividida", "Dividida"], ["superpuesta", "Superpuesta"]]) {
      const b = document.createElement("button");
      b.type = "button"; b.className = "fj-btn fj-btn--ghost"; b.textContent = texto;
      b.setAttribute("aria-pressed", String((modo === "dividida") === dividida));
      b.addEventListener("click", () => this._mostrarComparacion(docId, resumen, mallaA, mallaB, { ...opciones, modo }));
      modos.append(b);
    }
    const leyenda = document.createElement("div");
    leyenda.className = "fj-comparar__leyenda";
    leyenda.textContent = dividida ? "Arrastra el divisor · verde: añadida · rojo: quitada · ámbar: cambiada · gris: igual" : "verde: añadida · rojo: quitada · ámbar: cambiada · gris: igual";
    const lista = document.createElement("ul");
    for (const linea of opciones.lineas ?? textoResumen(resumen)) {
      const li = document.createElement("li"); li.textContent = linea; lista.appendChild(li);
    }
    const volver = document.createElement("button");
    volver.type = "button"; volver.className = "fj-btn"; volver.textContent = "Volver";
    volver.addEventListener("click", () => this._salirComparacion(docId));
    entry.banda.append(titulo, modos, leyenda, lista, volver);
    entry.banda.hidden = false;
  }

  _salirComparacion(docId) {
    const entry = this.tabs.get(docId);
    if (!entry) return;
    entry.viewer.salirComparacion();
    entry.banda.hidden = true;
    entry.divisor.hidden = true;
    entry.ramas.comparando = null;
    const alSalir = entry.alSalirComparacion;
    entry.alSalirComparacion = null;
    alSalir?.();
  }

  /** fdm-D: materiales por pieza (colores en el visor + panel Piezas).
   * Un fallo de red deja los anteriores: nunca rompe la pestaña. */
  async _cargarMateriales(entry) {
    try {
      const respuesta = await obtenerMateriales(entry.ficha.id);
      this._aplicarMateriales(entry, respuesta.materiales);
    } catch { /* se reintenta con el próximo cambio */ }
  }

  _aplicarMateriales(entry, materiales) {
    if (this.tabs.get(entry.ficha.id) !== entry) return;
    entry.materiales.materiales = materiales ?? {};
    entry.viewer.setPieceColors(coloresDePiezas(entry.materiales.materiales));
    if (this.activeId === entry.ficha.id && !this.panel.hidden && this.panelActivo === "piezas") {
      renderPiecesPanel(entry.viewer, entry.ficha.nombre, entry.materiales);
    }
  }

  _setRevision(entry, revision) {
    entry.revision = revision;
    entry.boton.dataset.revision = revision ?? "";
    entry.scheduler.setShown(revision);
  }

  async _aplicarMalla(docId, ficha, mesh) {
    const entry = this.tabs.get(docId);
    if (!entry) return;
    this._salirComparacion(docId);
    entry.animation.pause();
    entry.animation.resetPose();
    entry.viewer.cargarMallaSTL(mesh.buffer, { conservarVista: true });
    entry.notas.invalidarCaras();
    entry.ficha = ficha;
    this._setRevision(entry, mesh.revision);
    await Promise.all([entry.notas.recargar(), entry.historial.cargarInicial(), entry.ramas.cargarInicial().catch(() => {}), entry.h2.invalidar(), entry.ensamble.cargarInicial(), this._cargarMateriales(entry)]);
    entry.animation.setAssembly(entry.ensamble.data, entry.ensamble.revision, entry.revision);
    if (!entry.parametros.ocupado()) await entry.parametros.cargarInicial();
    if (this.tabs.get(docId) !== entry) return;
    entry.btnParametros.hidden = !entry.parametros.tieneParametros();
    if (this.activeId === docId) {
      this._actualizarStatusBar(entry.ficha);
      if (!this.panel.hidden && !(this.panelActivo === "parametros" && entry.parametros.ocupado())) this._renderizarPanelPestanaActiva();
    }
  }

  /** Un script de `docId` se está construyendo: el panel Medidas muestra sus cifras en gris. */
  medidasConstruyendo(docId) {
    this.tabs.get(docId)?.medidas.marcarConstruyendo();
  }

  aplicarCambioExterno(docId, cambios = [], revision = null) {
    const entry = this.tabs.get(docId);
    if (!entry) return;
    entry.medidas.nuevaRevision(revision);
    if (cambios.includes("geometria") && (!revision || revision !== entry.revision)) {
      entry.scheduler.notify(revision);
    }
    for (const tag of cambios) if (tag !== "geometria") entry.pendingPanels.add(tag);
    if (cambios.includes("historial") || cambios.includes("geometria")) this.onHistorialCambio?.(docId);
    void this._actualizarPaneles(docId);
  }

  async _actualizarPaneles(docId) {
    const entry = this.tabs.get(docId);
    if (!entry || entry.panelLoading || entry.pendingPanels.size === 0) return;
    const tasks = [];
    for (const tag of [...entry.pendingPanels]) {
      if ((tag === "notas" && entry.notas.ocupado()) || (tag === "parametros" && entry.parametros.ocupado())) continue;
      entry.pendingPanels.delete(tag);
      if (tag === "notas") tasks.push(entry.notas.recargar());
      if (tag === "historial") tasks.push(entry.historial.cargarInicial(), entry.ramas.cargarInicial().catch(() => {}), entry.h2.invalidar());
      if (tag === "ensamble") tasks.push(entry.ensamble.cargarInicial().then(() => {
        if (this.tabs.get(docId) === entry) entry.animation.setAssembly(entry.ensamble.data, entry.ensamble.revision, entry.revision);
      }));
      if (tag === "ramas") tasks.push(entry.ramas.cargarInicial(), entry.h2.invalidar());
      if (tag === "parametros") tasks.push(entry.parametros.cargarInicial());
      if (tag === "design" && this.activeId === docId && this.panelActivo === "design") renderDesign(entry, this.panelSeccionDesign);
      if (tag === "materiales") tasks.push(this._cargarMateriales(entry));
    }
    if (!tasks.length) return;
    entry.panelLoading = true;
    await Promise.allSettled(tasks);
    entry.panelLoading = false;
    if (this.activeId === docId && !this.panel.hidden && this.tabs.get(docId) === entry &&
        !(this.panelActivo === "parametros" && entry.parametros.ocupado())) this._renderizarPanelPestanaActiva();
    if (entry.pendingPanels.size) void this._actualizarPaneles(docId);
  }

  reconciliar(documentos) {
    const known = new Map(documentos.filter(doc => doc && typeof doc.id === "string").map(doc => [doc.id, doc.revision]));
    for (const [id, entry] of this.tabs) {
      if (!known.has(id)) this.documentoEliminado(id);
      else this.aplicarCambioExterno(id,
        known.get(id) === entry.revision ? ["notas", "historial", "parametros", "ensamble", "materiales", "ramas"]
          : ["geometria", "notas", "historial", "parametros", "ensamble", "materiales", "ramas"], known.get(id));
    }
  }

  documentoEliminado(docId) {
    const name = this.tabs.get(docId)?.ficha.nombre;
    if (!name) return;
    this.cerrar(docId);
    const feedback = document.getElementById("fj-feedback");
    feedback.hidden = false;
    feedback.textContent = `El documento «${name}» fue eliminado.`;
  }

  reanudarPendientes() {
    for (const [id, entry] of this.tabs) {
      entry.notice.hidden = !(entry.scheduler.pending && entry.notas.ocupado());
      entry.scheduler.flush();
      void this._actualizarPaneles(id);
    }
  }

  depurar() {
    return [...this.tabs].map(([id, entry]) => ({ id, revision: entry.revision,
      camara: entry.viewer.camera.position.toArray(), objetivo: entry.viewer.controls.target.toArray(),
      seleccion: entry.viewer.selectedPiece, pendiente: entry.scheduler.pending }));
  }

  cerrar(docId) {
    const entrada = this.tabs.get(docId);
    if (!entrada) return;
    const hadFocus = entrada.boton.contains(document.activeElement);

    entrada.scheduler.dispose();

    entrada.notas.destruir();
    entrada.parametros.destruir();
    entrada.animation.dispose();
    entrada.viewer.dispose();
    entrada.contenedor.remove();
    entrada.boton.remove();
    disposeDesign(docId);
    this.tabs.delete(docId);
    this.onEstadoCambio?.();
    if (hadFocus && this.activeId !== docId) this.tabs.get(this.activeId)?.boton.focus();

    if (this.activeId === docId) {
      this.activeId = null;
      const restante = this.tabs.keys().next().value;
      if (restante !== undefined) {
        this.activar(restante);
        if (hadFocus) this.tabs.get(restante).boton.focus();
      } else {
        this.vacioEl.hidden = false;
        this.panel.hidden = true;
        this._actualizarStatusBar(null);
        if (hadFocus) document.getElementById("fj-empty-open").focus();
      }
    }
  }

  _actualizarStatusBar(ficha) {
    const s = this.statusEls;
    if (!ficha) {
      s.nombre.textContent = "—";
      s.volumen.textContent = "—";
      s.bbox.textContent = "—";
      s.solidos.textContent = "—";
      s.valido.textContent = "—";
      s.valido.className = "fj-badge";
      return;
    }
    s.nombre.textContent = ficha.nombre;
    s.nombre.title = ficha.nombre;
    s.volumen.textContent = formatoVolumen(ficha.volumen);
    s.bbox.textContent = formatoBbox(ficha.bbox);
    s.solidos.textContent = String(ficha.solidos);
    s.valido.textContent = ficha.valido ? "válido" : "inválido";
    s.valido.className = `fj-badge ${ficha.valido ? "fj-badge--run" : "fj-badge--err"}`;
  }
}
