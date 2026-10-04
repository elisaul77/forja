// Forja — pines ("notas") y pizarra ("trazos") sobre un documento (Fase 4).
// Un `ForjaNotasControlador` por pestaña: guarda el modo de interacción
// (picking de nota / dibujo de pizarra) propio de esa pestaña, igual que
// `ForjaViewer` aísla cámara/selección por pestaña (ver `tabs.js`).
import {
  obtenerCaras,
  obtenerCarasTriangulos,
  listarNotas,
  crearNota,
  borrarNota,
  actualizarVisibilidadNota,
  crearTrazo,
  borrarTrazo,
  actualizarVisibilidadTrazo,
} from "./api.js?v=11";

// ---------------- Diálogos compartidos (uno solo puede estar abierto a la vez) ----------------

const dlgComentario = document.getElementById("fj-dialogo-comentario");
const dlgComentarioTitulo = document.getElementById("fj-dialogo-comentario-titulo");
const dlgComentarioTexto = document.getElementById("fj-dialogo-comentario-texto");
const dlgComentarioCancelar = document.getElementById("fj-dialogo-comentario-cancelar");

/** Muestra el diálogo de comentario; devuelve el texto (puede ser vacío) o `null` si se canceló. */
function pedirComentario(titulo) {
  return new Promise((resolve) => {
    dlgComentarioTitulo.textContent = titulo;
    dlgComentarioTexto.value = "";
    let resuelto = false;
    const cerrar = (valor) => {
      if (resuelto) return;
      resuelto = true;
      dlgComentario.removeEventListener("close", onClose);
      dlgComentarioCancelar.removeEventListener("click", onCancelar);
      resolve(valor);
    };
    const onClose = () => cerrar(dlgComentario.returnValue === "cancelar" ? null : dlgComentarioTexto.value.trim());
    const onCancelar = () => {
      dlgComentario.close("cancelar");
    };
    dlgComentario.addEventListener("close", onClose);
    dlgComentarioCancelar.addEventListener("click", onCancelar);
    dlgComentario.showModal();
  });
}

const dlgPizarra = document.getElementById("fj-dialogo-pizarra");
const pzModo = document.getElementById("fj-pizarra-modo");
const pzOffset = document.getElementById("fj-pizarra-offset");
const pzTipo = document.getElementById("fj-pizarra-tipo");
const pzComentario = document.getElementById("fj-pizarra-comentario");
const pzCancelar = document.getElementById("fj-pizarra-cancelar");

/** Muestra el diálogo de configuración de la pizarra; devuelve
 * `{modo, offset, tipo, comentario}` o `null` si se canceló. */
function pedirConfigPizarra() {
  return new Promise((resolve) => {
    pzComentario.value = "";
    let resuelto = false;
    const cerrar = (valor) => {
      if (resuelto) return;
      resuelto = true;
      dlgPizarra.removeEventListener("close", onClose);
      pzCancelar.removeEventListener("click", onCancelar);
      resolve(valor);
    };
    const onClose = () => {
      if (dlgPizarra.returnValue === "cancelar") return cerrar(null);
      cerrar({
        modo: pzModo.value,
        offset: Number(pzOffset.value) || 0,
        tipo: pzTipo.value,
        comentario: pzComentario.value.trim(),
      });
    };
    const onCancelar = () => dlgPizarra.close("cancelar");
    dlgPizarra.addEventListener("close", onClose);
    pzCancelar.addEventListener("click", onCancelar);
    dlgPizarra.showModal();
  });
}

// ---------------- Controlador por pestaña ----------------

export class ForjaNotasControlador {
  constructor(viewer, docId) {
    this.viewer = viewer;
    this.docId = docId;
    this.notas = [];
    this.trazos = [];
    this._caras = null; // null = aún no consultado; [] = documento STL sin caras
    this._carasTriangulos = null;
    this._carasEpoch = 0;
    this.modo = null; // null | "nota" | "pizarra-esperando-cara" | "pizarra-dibujando"
    this._configPizarraPendiente = null;
    this._trazoActual = null;

    this._alClicar = this._alClicar.bind(this);
    this._alBajarBoton = this._alBajarBoton.bind(this);
    this._alMoverMouse = this._alMoverMouse.bind(this);
    this._alSoltarBoton = this._alSoltarBoton.bind(this);

    const canvas = this.viewer.renderer.domElement;
    canvas.addEventListener("click", this._alClicar);
    canvas.addEventListener("mousedown", this._alBajarBoton);
    canvas.addEventListener("mousemove", this._alMoverMouse);
    canvas.addEventListener("mouseup", this._alSoltarBoton);
  }

  async cargarInicial() {
    await this.recargar();
  }

  destruir() {
    const canvas = this.viewer.renderer.domElement;
    canvas.removeEventListener("click", this._alClicar);
    canvas.removeEventListener("mousedown", this._alBajarBoton);
    canvas.removeEventListener("mousemove", this._alMoverMouse);
    canvas.removeEventListener("mouseup", this._alSoltarBoton);
  }

  invalidarCaras() {
    this._carasEpoch++;
    this._caras = null;
    this._carasTriangulos = null;
  }

  ocupado() {
    return dlgComentario.open || dlgPizarra.open || this.modo === "pizarra-dibujando" || this._trazoActual !== null;
  }

  async recargar() {
    const resumen = await listarNotas(this.docId, true);
    this.notas = resumen
      .filter((e) => e.tipo === "nota")
      .map((e) => ({ ...e, punto: e.referencia.punto }));
    this.trazos = resumen.filter((e) => e.tipo !== "nota");
    this.viewer.mostrarAnotaciones(this.notas, this.trazos);
  }

  async _asegurarCaras() {
    if (this._caras !== null) return;
    const epoch = this._carasEpoch;
    try {
      const caras = await obtenerCaras(this.docId);
      const triangulos = await obtenerCarasTriangulos(this.docId);
      if (epoch !== this._carasEpoch) return;
      this._caras = caras;
      this._carasTriangulos = triangulos;
    } catch {
      if (epoch !== this._carasEpoch) return;
      this._caras = [];
      this._carasTriangulos = null;
    }
  }

  _referenciaDesdeGolpeo(hit) {
    if (this._carasTriangulos && hit.indiceTriangulo < this._carasTriangulos.length) {
      const idxCara = this._carasTriangulos[hit.indiceTriangulo];
      const cara = this._caras[idxCara];
      if (cara) {
        return {
          tipo: "cara",
          id: cara.id,
          punto: hit.punto,
          huella: {
            tipo: "cara",
            subtipo: cara.tipo,
            centroide: cara.centroide,
            direccion: cara.normal,
            medida: cara.area,
          },
        };
      }
    }
    return { tipo: "punto", punto: hit.punto };
  }

  // ---------------- modo "📌 Nota" ----------------

  activarModoNota() {
    this.modo = this.modo === "nota" ? null : "nota";
    return this.modo === "nota";
  }

  async _alClicar(ev) {
    if (this.modo !== "nota" && this.modo !== "pizarra-esperando-cara") return;
    const hit = this.viewer.raycastearMalla(ev.clientX, ev.clientY);
    if (!hit) return;
    await this._asegurarCaras();

    if (this.modo === "pizarra-esperando-cara") {
      const referencia = this._referenciaDesdeGolpeo(hit);
      const normal =
        referencia.tipo === "cara" ? referencia.huella.direccion : this.viewer.direccionHaciaCamara();
      const offset = this._configPizarraPendiente.offset;
      const origen = [
        hit.punto[0] + normal[0] * offset,
        hit.punto[1] + normal[1] * offset,
        hit.punto[2] + normal[2] * offset,
      ];
      this._colocarPlanoYEmpezarDibujo(origen, normal, referencia.tipo === "cara" ? referencia : null);
      return;
    }

    const referencia = this._referenciaDesdeGolpeo(hit);
    const comentario = await pedirComentario("Nota para el asistente");
    if (comentario === null) return;
    await crearNota(this.docId, comentario, referencia);
    await this.recargar();
  }

  // ---------------- modo "✏ Pizarra" ----------------

  async activarModoPizarra() {
    if (
      this.modo === "pizarra-esperando-cara" ||
      this.modo === "pizarra-dibujando" ||
      this.modo === "pizarra-vista-pendiente"
    ) {
      this._salirDeModoPizarra();
      return false;
    }
    const config = await pedirConfigPizarra();
    if (config === null) return false;
    this._configPizarraPendiente = config;

    if (config.modo === "cara") {
      this.modo = "pizarra-esperando-cara";
      return true;
    }

    if (config.modo === "vista") {
      // The plane is placed at the first click, on the surface under the
      // pointer, so the drawing lands on what the user sees (not behind it).
      this.modo = "pizarra-vista-pendiente";
      this._controlesDeDibujo(true);
      return true;
    }

    if (config.modo === "xy" || config.modo === "xz" || config.modo === "yz") {
      this._empezarCorte(config.modo);
      return true;
    }

    const centro = this.viewer.centroEscena();
    let normal;
    if (config.modo === "vista") normal = this.viewer.direccionHaciaCamara();
    else if (config.modo === "xy") normal = [0, 0, 1];
    else if (config.modo === "xz") normal = [0, 1, 0];
    else normal = [1, 0, 0]; // yz

    const proyeccion = centro[0] * normal[0] + centro[1] * normal[1] + centro[2] * normal[2];
    const base = [
      centro[0] - normal[0] * proyeccion,
      centro[1] - normal[1] * proyeccion,
      centro[2] - normal[2] * proyeccion,
    ];
    const origen = [
      base[0] + normal[0] * config.offset,
      base[1] + normal[1] * config.offset,
      base[2] + normal[2] * config.offset,
    ];
    this._colocarPlanoYEmpezarDibujo(origen, normal, null);
    return true;
  }

  _colocarPlanoYEmpezarDibujo(origen, normal, referenciaCara) {
    this.viewer.mostrarPlanoPizarra(origen, normal);
    this._referenciaPizarra = referenciaCara;
    this.modo = "pizarra-dibujando";
    this._controlesDeDibujo(true);
  }

  _salirDeModoPizarra() {
    this._terminarCorte();
    this.viewer.ocultarPlanoPizarra();
    this._controlesDeDibujo(false);
    this.modo = null;
    this._trazoActual = null;
    this._configPizarraPendiente = null;
    this._referenciaPizarra = null;
  }

  /** Plano XY/XZ/YZ movible: vista de frente + corte en vivo; se dibuja sobre el corte. */
  _empezarCorte(modo) {
    const normal = modo === "xy" ? [0, 0, 1] : modo === "xz" ? [0, 1, 0] : [1, 0, 0];
    const [min, max] = this.viewer.rangoEscena(normal);
    const margen = (max - min) * 0.02 || 1;
    let pos = (min + max) / 2;
    const colocar = () => {
      const origen = normal.map((v) => v * pos);
      this.viewer.mostrarPlanoPizarra(origen, normal, Math.max((max - min) * 3, 200));
      this.viewer.activarCorte(origen, normal);
      etiqueta.textContent = `${modo.toUpperCase()} · ${"xyz"[normal.indexOf(1)]} = ${pos.toFixed(1)} mm`;
    };

    const panel = document.createElement("div");
    panel.className = "fj-panel-corte";
    panel.style.cssText =
      "position:absolute;left:50%;bottom:56px;transform:translateX(-50%);z-index:20;display:flex;gap:10px;" +
      "align-items:center;padding:8px 12px;border-radius:8px;background:var(--fj-raised,#26211d);" +
      "color:var(--fj-text,#ece6df);border:1px solid var(--fj-line,#3a332d);font:13px system-ui;max-width:92%";
    const etiqueta = document.createElement("span");
    const deslizador = document.createElement("input");
    Object.assign(deslizador, { type: "range", min: min - margen, max: max + margen, step: "any", value: pos });
    deslizador.style.width = "min(320px, 50vw)";
    deslizador.addEventListener("input", () => {
      pos = Number(deslizador.value);
      colocar();
    });
    const ayuda = document.createElement("span");
    ayuda.textContent = "Mueve el plano y dibuja sobre el corte";
    ayuda.style.opacity = "0.7";
    const cancelar = document.createElement("button");
    cancelar.type = "button";
    cancelar.textContent = "Cancelar";
    cancelar.addEventListener("click", () => this._salirDeModoPizarra());
    panel.append(etiqueta, deslizador, ayuda, cancelar);
    const contenedor = this.viewer.container;
    if (getComputedStyle(contenedor).position === "static") contenedor.style.position = "relative";
    contenedor.appendChild(panel);
    this._panelCorte = panel;

    this.viewer.mirarDeFrente(normal);
    colocar();
    this._referenciaPizarra = null;
    this.modo = "pizarra-dibujando";
    this._controlesDeDibujo(true);
  }

  _terminarCorte() {
    if (!this._panelCorte) return;
    this._panelCorte.remove();
    this._panelCorte = null;
    this.viewer.desactivarCorte();
    this.viewer.restaurarVista();
  }

  _planoVistaEnPuntero(ev) {
    const normal = this.viewer.direccionHaciaCamara();
    const hit = this.viewer.raycastearMalla(ev.clientX, ev.clientY);
    let base;
    if (hit) {
      base = hit.punto;
    } else {
      const c = this.viewer.centroEscena();
      const p = c[0] * normal[0] + c[1] * normal[1] + c[2] * normal[2];
      base = [c[0] - normal[0] * p, c[1] - normal[1] * p, c[2] - normal[2] * p];
    }
    // Always a hair in front of the surface so the stroke is never hidden.
    const offset = Math.max(this._configPizarraPendiente.offset || 0, 0.2);
    const origen = base.map((v, i) => v + normal[i] * offset);
    this.viewer.mostrarPlanoPizarra(origen, normal);
    this._referenciaPizarra = null;
    this.modo = "pizarra-dibujando";
  }

  /** While drawing: wheel zooms, right button pans, left button draws (no rotation). */
  _controlesDeDibujo(activo) {
    const c = this.viewer.controls;
    if (activo) {
      if (!this._botonesOriginales) this._botonesOriginales = { ...c.mouseButtons };
      c.enabled = true;
      c.mouseButtons = { LEFT: null, MIDDLE: this._botonesOriginales.MIDDLE, RIGHT: this._botonesOriginales.RIGHT };
      c.enableRotate = false;
    } else {
      if (this._botonesOriginales) c.mouseButtons = this._botonesOriginales;
      this._botonesOriginales = null;
      c.enableRotate = true;
      c.enabled = true;
    }
  }

  _alBajarBoton(ev) {
    if (this.modo === "pizarra-vista-pendiente" && ev.button === 0) this._planoVistaEnPuntero(ev);
    if (this.modo !== "pizarra-dibujando" || ev.button !== 0) return;
    const punto = this.viewer.proyectarEnPlanoPizarra(ev.clientX, ev.clientY);
    if (!punto) return;
    this._trazoActual = [punto];
    this.viewer.mostrarAnotaciones(this.notas, [
      ...this.trazos,
      { tipo: this._configPizarraPendiente.tipo, visible: true, puntos: this._trazoActual },
    ]);
  }

  _alMoverMouse(ev) {
    if (this.modo !== "pizarra-dibujando" || this._trazoActual === null) return;
    const punto = this.viewer.proyectarEnPlanoPizarra(ev.clientX, ev.clientY);
    if (!punto) return;
    this._trazoActual.push(punto);
    this.viewer.mostrarAnotaciones(this.notas, [
      ...this.trazos,
      { tipo: this._configPizarraPendiente.tipo, visible: true, puntos: this._trazoActual },
    ]);
  }

  async _alSoltarBoton(ev) {
    if (this.modo !== "pizarra-dibujando" || ev.button !== 0 || this._trazoActual === null) return;
    const puntos = this._trazoActual;
    this._trazoActual = null;
    if (puntos.length < 2) {
      this.viewer.mostrarAnotaciones(this.notas, this.trazos);
      return;
    }
    const { tipo, comentario } = this._configPizarraPendiente;
    const plano = this.viewer._planoPizarra;
    await crearTrazo(this.docId, {
      tipo,
      comentario,
      puntos,
      plano_origen: plano.origen,
      plano_normal: plano.normal,
      referencia: this._referenciaPizarra,
    });
    this._salirDeModoPizarra();
    await this.recargar();
  }

  // ---------------- panel lateral: notas ----------------

  async alternarVisibilidadNota(notaId, visible) {
    await actualizarVisibilidadNota(this.docId, notaId, visible);
    await this.recargar();
  }

  async eliminarNota(notaId) {
    await borrarNota(this.docId, notaId);
    await this.recargar();
  }

  async alternarVisibilidadTrazo(trazoId, visible) {
    await actualizarVisibilidadTrazo(this.docId, trazoId, visible);
    await this.recargar();
  }

  async eliminarTrazo(trazoId) {
    await borrarTrazo(this.docId, trazoId);
    await this.recargar();
  }
}

// ---------------- panel lateral compartido: render de la lista de notas ----------------

const panelNotas = document.getElementById("fj-panel-notas");

export function renderizarPanelNotas(controlador) {
  panelNotas.textContent = "";
  const items = [...controlador.notas, ...controlador.trazos];
  if (items.length === 0) {
    const vacio = document.createElement("div");
    vacio.className = "fj-panel-lateral__vacio";
    vacio.textContent = "Sin notas ni trazos todavía. Usa Añadir nota o Dibujar indicación en el visor.";
    panelNotas.appendChild(vacio);
    return;
  }
  for (const item of items) {
    const esNota = item.tipo === "nota";
    const fila = document.createElement("div");
    fila.className = "fj-item-nota";

    const cuerpo = document.createElement("div");
    cuerpo.className = "fj-item-nota__cuerpo";
    const comentario = document.createElement("div");
    comentario.className = "fj-item-nota__comentario";
    comentario.textContent = item.comentario || "(sin comentario)";
    cuerpo.appendChild(comentario);

    const meta = document.createElement("div");
    meta.className = "fj-item-nota__meta";
    meta.textContent = esNota ? "📌 nota" : `✏ ${item.tipo} · ${item.puntos_resumen.n_puntos} pts`;
    cuerpo.appendChild(meta);

    if (item.referencia_perdida) {
      const perdida = document.createElement("div");
      perdida.className = "fj-item-nota__perdida";
      perdida.textContent = "⚠ referencia perdida (la geometría cambió)";
      cuerpo.appendChild(perdida);
    }
    fila.appendChild(cuerpo);

    const acciones = document.createElement("div");
    acciones.className = "fj-item-nota__acciones";
    const btnVisible = document.createElement("button");
    btnVisible.type = "button";
    btnVisible.className = "fj-btn fj-btn--ghost";
    btnVisible.textContent = item.visible ? "Ocultar" : "Mostrar";
    btnVisible.addEventListener("click", () =>
      esNota
        ? controlador.alternarVisibilidadNota(item.n, !item.visible)
        : controlador.alternarVisibilidadTrazo(item.n, !item.visible),
    );
    const btnBorrar = document.createElement("button");
    btnBorrar.type = "button";
    btnBorrar.className = "fj-btn fj-btn--ghost";
    btnBorrar.textContent = "Borrar";
    btnBorrar.addEventListener("click", () =>
      esNota ? controlador.eliminarNota(item.n) : controlador.eliminarTrazo(item.n),
    );
    acciones.appendChild(btnVisible);
    acciones.appendChild(btnBorrar);
    fila.appendChild(acciones);

    panelNotas.appendChild(fila);
  }
}
