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
} from "./api.js?v=13";
import {
  createIntent,
  sendIntentAction,
  designPiecesOf,
  resolveDesignPiece,
  subsamplePoints,
} from "./design.js?v=10";

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

/** Aviso en la línea de estado del visor (mismo elemento que usan las herramientas). */
function avisarFeedback(texto) {
  const feedback = document.getElementById("fj-feedback");
  if (feedback) {
    feedback.hidden = !texto;
    feedback.textContent = texto;
  }
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
    // Indicación a la que se añade la próxima marca (botón "Añadir otra marca").
    this.targetIntent = null;
    // Indicación creada en esta sesión del modo: las marcas siguientes sin texto se suman a ella.
    this._intentSesion = null;
    // Lo llama tabs.js para refrescar el panel Indicaciones tras cada marca.
    this.alCambiarMarcas = null;

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

  // Salir del modo de nota o de pizarra cancela también la indicación objetivo
  // (botón "Añadir otra marca"): la próxima marca ya no se asociaría.
  get modo() {
    return this._modo;
  }
  set modo(valor) {
    this._modo = valor;
    if (!valor) {
      this.targetIntent = null;
      this._intentSesion = null;
    }
  }

  async cargarInicial() {
    await this.recargar();
  }

  /** Sale de cualquier modo de nota o pizarra (lo usa la barra al cancelar).
   * Un trazo a medias se descarta; los diálogos abiertos no se tocan. */
  cancelarModos() {
    if (this.modo && this.modo.startsWith("pizarra")) this._salirDeModoPizarra();
    this._configPizarraPendiente = null;
    this._trazoActual = null;
    this.modo = null;
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
    if (!hit || this._ocupadoNota) return;
    const feedback = document.getElementById("fj-feedback");
    const avisar = (texto) => { if (feedback) { feedback.hidden = !texto; feedback.textContent = texto; } };
    this._ocupadoNota = true;
    try {
      if (this._caras === null) avisar("Preparando las caras del modelo para anclar la nota…");
      await this._asegurarCaras();
      avisar("");
      await this._colocarEnGolpeo(hit, avisar);
    } finally {
      this._ocupadoNota = false;
    }
  }

  async _colocarEnGolpeo(hit, avisar) {
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
    const comentario = await pedirComentario("¿Qué quieres cambiar aquí?");
    if (comentario === null) {
      this.targetIntent = null;
      return;
    }
    const destino = this.targetIntent;
    avisar("Guardando nota…");
    let nota;
    try {
      nota = await crearNota(this.docId, comentario, referencia);
      avisar("");
    } catch (error) {
      avisar(`No se pudo guardar la nota: ${error.message ?? error}`);
      return;
    }
    await this.recargar();
    await this._vincularMarca(nota.n, comentario, {
      pieza: this._piezaDe(hit.pieceName),
      puntos: [hit.punto],
    }, destino);
  }

  /** Nombre canónico de la pieza del golpeo; `undefined` (se omite) si no es inequívoco. */
  _piezaDe(nombreVisor) {
    const nombre = nombreVisor || this.viewer.selectedPiece;
    const piezas = designPiecesOf(this.docId);
    // Panel aún sin cargar: el nombre del visor es el candidato; el servidor lo valida.
    if (!piezas.length) return nombre || undefined;
    return resolveDesignPiece(piezas, nombre) ?? undefined;
  }

  /** Une una marca recién guardada a una indicación: una nueva, o la objetivo
   * si el usuario pulsó "Añadir otra marca". Nunca falla en silencio: la
   * marca ya está guardada aunque la indicación no se cree. */
  async _vincularMarca(n, texto, { pieza, puntos }, destino = null) {
    texto = (texto ?? "").trim();
    // Sin indicación objetivo ni texto (el diálogo ya se mostró): se suma a la
    // indicación creada en esta sesión; si no hay, la marca queda suelta.
    if (!destino && !texto) destino = this._intentSesion;
    if (!destino && !texto) {
      avisarFeedback("Marca guardada sin indicación; aparece en «Marcas sin indicación».");
      this.alCambiarMarcas?.();
      return;
    }
    try {
      if (destino) {
        await sendIntentAction(this.docId, destino, { action: "attach", marks: [n] });
      } else {
        const cuerpo = {
          text: texto,
          marks: [n],
          ...(pieza ? { piece: pieza } : {}),
          ...(puntos?.length ? { points: subsamplePoints(puntos) } : {}),
        };
        let leido;
        try {
          leido = await createIntent(this.docId, cuerpo);
        } catch (error) {
          if (!cuerpo.piece) throw error;
          // Nombre del visor no canónico (STL antiguo): se guarda sin pieza, nunca con otra.
          delete cuerpo.piece;
          leido = await createIntent(this.docId, cuerpo);
        }
        const creada = (leido?.design?.intents ?? []).find((i) => (i.marks ?? []).includes(n));
        if (creada && this.modo) this._intentSesion = creada.id;
      }
    } catch (error) {
      avisarFeedback(`La marca se guardó, pero la indicación no: ${error.message ?? error}`);
    }
    // «Añadir otra marca» sigue apuntando a la misma indicación hasta salir del modo o cancelar.
    this.alCambiarMarcas?.();
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
    // Antes de salir del modo de pizarra (que borra targetIntent).
    const destino = this.targetIntent;
    this._trazoActual = null;
    if (puntos.length < 2) {
      this.viewer.mostrarAnotaciones(this.notas, this.trazos);
      return;
    }
    const { tipo, comentario } = this._configPizarraPendiente;
    const plano = this.viewer._planoPizarra;
    const trazo = await crearTrazo(this.docId, {
      tipo,
      comentario,
      puntos,
      plano_origen: plano.origen,
      plano_normal: plano.normal,
      referencia: this._referenciaPizarra,
    });
    this._salirDeModoPizarra();
    await this.recargar();
    // El comentario del diálogo es el texto de la indicación; si está vacío se pregunta una vez.
    const texto = comentario || (await pedirComentario("¿Qué quieres decir con este trazo?")) || "";
    await this._vincularMarca(trazo.n, texto, {
      pieza: this._piezaDe(this.viewer.selectedPiece),
      puntos,
    }, destino);
  }

  // ---------------- panel lateral: notas ----------------

  async alternarVisibilidadNota(notaId, visible) {
    await actualizarVisibilidadNota(this.docId, notaId, visible);
    await this.recargar();
    this.alCambiarMarcas?.();
  }

  async eliminarNota(notaId) {
    await borrarNota(this.docId, notaId);
    await this.recargar();
    this.alCambiarMarcas?.();
  }

  async alternarVisibilidadTrazo(trazoId, visible) {
    await actualizarVisibilidadTrazo(this.docId, trazoId, visible);
    await this.recargar();
    this.alCambiarMarcas?.();
  }

  async eliminarTrazo(trazoId) {
    await borrarTrazo(this.docId, trazoId);
    await this.recargar();
    this.alCambiarMarcas?.();
  }
}
