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
  obtenerDisenoDocumento,
} from "./api.js?v=17";
import {
  createIntent,
  sendIntentAction,
  designPiecesOf,
  resolveDesignPiece,
  subsamplePoints,
} from "./design.js?v=12";
import { BorradorIndicacion, MAX_TRAZOS_INDICACION, resolverRegionIndicacion } from "./dibujo-indicacion.js?v=3";

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
/** Rechazo definitivo del servidor (4xx): la petición no se guardó. */
function esRechazoDefinitivo(error) {
  return Number.isInteger(error?.status) && error.status >= 400 && error.status < 500;
}

/** Intervalo mínimo entre raycasts de un trazo en movimiento (ms). */
const INTERVALO_RAYCAST_MS = 50;

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
    // Pieza que toca el trazo en curso (nombre del visor) o null.
    this._piezaTrazoActual = null;
    this._ultimoRaycast = -Infinity;
    // Trazos de la indicación en curso ("Dibujar indicación"); nada se guarda hasta «Terminar».
    this._borrador = null;
    this._barra = null;
    // Indicación a la que se añade la próxima marca (botón "Añadir otra marca").
    this.targetIntent = null;
    // Indicación creada en esta sesión del modo: las marcas siguientes sin texto se suman a ella.
    this._intentSesion = null;
    // Lo llama tabs.js para refrescar el panel Indicaciones tras cada marca.
    this.alCambiarMarcas = null;
    // Lo llama tabs.js con el modo nuevo (null al salir) para sincronizar los botones.
    this.alCambiarModo = null;

    this._alClicar = this._alClicar.bind(this);
    this._alBajarBoton = this._alBajarBoton.bind(this);
    this._alMoverMouse = this._alMoverMouse.bind(this);
    this._alSoltarBoton = this._alSoltarBoton.bind(this);
    this._alTecla = this._alTecla.bind(this);

    const canvas = this.viewer.renderer.domElement;
    canvas.addEventListener("click", this._alClicar);
    canvas.addEventListener("mousedown", this._alBajarBoton);
    canvas.addEventListener("mousemove", this._alMoverMouse);
    canvas.addEventListener("mouseup", this._alSoltarBoton);
    // Captura: Ctrl+Z y Enter/Esc mientras se dibuja no deben llegar al historial ni a otros atajos.
    window.addEventListener("keydown", this._alTecla, true);
  }

  // Salir del modo de nota o de pizarra cancela también la indicación objetivo
  // (botón "Añadir otra marca"): la próxima marca ya no se asociaría.
  get modo() {
    return this._modo;
  }
  set modo(valor) {
    const anterior = this._modo;
    this._modo = valor;
    if (!valor) {
      this.targetIntent = null;
      this._intentSesion = null;
    }
    if (valor !== anterior) this.alCambiarModo?.(valor ?? null);
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
    window.removeEventListener("keydown", this._alTecla, true);
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
    await this._vincularMarcas([nota.n], comentario, {
      pieza: this._piezaDe(hit.pieceName),
      puntos: [hit.punto],
      tipo: "nota",
    }, destino);
  }

  /** Nombre canónico de la pieza del golpeo; `undefined` (se omite) si no es inequívoco. */
  _piezaDe(nombreVisor) {
    return this._piezaCanonica(nombreVisor || this.viewer.selectedPiece);
  }

  /** Nombre canónico de una pieza del visor, sin usar la selección; `undefined` si no hay
   * nombre o no es inequívoco. */
  _piezaCanonica(nombre) {
    if (!nombre) return undefined;
    const piezas = designPiecesOf(this.docId);
    // Panel aún sin cargar: el nombre del visor es el candidato; el servidor lo valida.
    if (!piezas.length) return nombre;
    return resolveDesignPiece(piezas, nombre) ?? undefined;
  }

  /** Pieza que hay bajo el puntero (nombre del visor), o `null` si no toca ninguna. */
  _piezaBajo(ev) {
    return this.viewer.raycastearMalla(ev.clientX, ev.clientY)?.pieceName ?? null;
  }

  /** Como `_piezaBajo`, pero para el movimiento del trazo: como mucho un raycast cada
   * INTERVALO_RAYCAST_MS, para no recorrer la malla en cada píxel. */
  _piezaEnMovimiento(ev) {
    const ahora = performance.now();
    if (ahora - this._ultimoRaycast < INTERVALO_RAYCAST_MS) return null;
    this._ultimoRaycast = ahora;
    return this._piezaBajo(ev);
  }

  /** Une marcas recién guardadas (uno o varios trazos) a una indicación: una nueva, o la objetivo
   * si el usuario pulsó "Añadir otra marca". Nunca falla en silencio: las marcas
   * ya están guardadas aunque la indicación no se cree. `tipo` ("nota" | "trazo") dice cómo
   * se crearon las marcas: solo los trazos de una indicación fallida se descartan. */
  async _vincularMarcas(marcas, texto, { pieza, puntos, tipo }, destino = null) {
    texto = (texto ?? "").trim();
    // Sin indicación objetivo ni texto (el diálogo ya se mostró): se suma a la
    // indicación creada en esta sesión; si no hay, la marca queda suelta.
    if (!destino && !texto) destino = this._intentSesion;
    if (!destino && !texto) {
      avisarFeedback("Marca guardada sin indicación; aparece en «Marcas sin indicación».");
      this.alCambiarMarcas?.();
      return;
    }
    let sinRegion = false;
    try {
      if (destino) {
        await sendIntentAction(this.docId, destino, { action: "attach", marks: marcas });
      } else {
        // Una región (points) solo viaja con su pieza: el servidor la rechaza sin ella.
        const cuerpo = {
          text: texto,
          marks: marcas,
          ...(pieza && puntos?.length ? { piece: pieza, points: subsamplePoints(puntos) } : {}),
        };
        let leido;
        try {
          leido = await createIntent(this.docId, cuerpo);
        } catch (error) {
          // Solo un rechazo de validación (422) reintenta, y sin región: el nombre del
          // visor no es canónico (STL antiguo) y nunca se guarda con otra pieza.
          if (!cuerpo.piece || error.status !== 422) throw error;
          delete cuerpo.piece;
          delete cuerpo.points;
          sinRegion = true;
          leido = await createIntent(this.docId, cuerpo);
        }
        const creada = (leido?.design?.intents ?? []).find((i) => (i.marks ?? []).some((m) => marcas.includes(m)));
        if (creada && this.modo) this._intentSesion = creada.id;
      }
      if (sinRegion) avisarFeedback("Indicación guardada sin región: el nombre de la pieza no coincide con el modelo.");
    } catch (error) {
      await this._fallaVinculo(marcas, error, { tipo, destino });
    }
    // «Añadir otra marca» sigue apuntando a la misma indicación hasta salir del modo o cancelar.
    this.alCambiarMarcas?.();
  }

  /** La indicación no quedó enlazada. Una nota suelta se conserva («Marcas sin indicación»).
   * Trazos: ante un rechazo definitivo (4xx) se descartan los recién guardados. Ante error de
   * servidor, red o tiempo agotado no se sabe si el servidor llegó a guardarla: se lee el
   * diseño y solo se borran las marcas que ninguna indicación referencia. Si el diseño no se
   * puede leer, no se borra nada y se avisa. */
  async _fallaVinculo(marcas, error, { tipo, destino }) {
    const motivo = error.message ?? error;
    if (tipo !== "trazo") {
      avisarFeedback(`La nota se guardó, pero no quedó enlazada a una indicación: ${motivo}. Aparece en «Marcas sin indicación».`);
      return;
    }
    if (esRechazoDefinitivo(error)) {
      const quien = destino ? "No se añadieron a la indicación" : "No se guardó la indicación";
      const fallidas = await this._descartarTrazos(marcas);
      avisarFeedback(
        fallidas.length
          ? `${quien}: ${motivo}. No se pudieron descartar estas marcas, que quedan sin indicación: ${fallidas.join(", ")}.`
          : `${quien}: ${motivo}. Los trazos se descartaron; vuelve a dibujarlos.`,
      );
      await this.recargar();
      return;
    }
    // Estado incierto: el servidor pudo guardar la indicación antes de fallar.
    let enIndicacion;
    try {
      const diseno = await obtenerDisenoDocumento(this.docId);
      enIndicacion = new Set((diseno.design?.intents ?? []).flatMap((i) => i.marks ?? []));
    } catch (errorLectura) {
      avisarFeedback(
        `No se sabe si la indicación quedó guardada (${motivo}; ${errorLectura.message ?? errorLectura}). ` +
          "No se borró ninguna marca: revisa «Marcas sin indicación» antes de volver a dibujar.",
      );
      return;
    }
    const sueltas = marcas.filter((m) => !enIndicacion.has(m));
    if (!sueltas.length) {
      avisarFeedback(`La indicación sí se guardó, a pesar del error (${motivo}); sus marcas están enlazadas.`);
    } else {
      const fallidas = await this._descartarTrazos(sueltas);
      avisarFeedback(
        `No se confirmó la indicación (${motivo}). ` +
          (fallidas.length
            ? `No se pudieron descartar estas marcas, que quedan sin indicación: ${fallidas.join(", ")}.`
            : "Los trazos sin indicación se descartaron; vuelve a dibujarlos."),
      );
    }
    await this.recargar();
  }

  /** Borra trazos recién guardados de una indicación fallida. Devuelve los que no se pudieron
   * borrar, con su motivo. */
  async _descartarTrazos(marcas) {
    const fallidas = [];
    for (const marca of marcas) {
      try {
        await borrarTrazo(this.docId, marca);
      } catch (error) {
        fallidas.push(`${marca} (${error.message ?? error})`);
      }
    }
    return fallidas;
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
    this._entrarEnDibujo();
    this._controlesDeDibujo(true);
  }

  _salirDeModoPizarra() {
    this._terminarCorte();
    this._quitarBarra();
    this.viewer.ocultarPlanoPizarra();
    this._controlesDeDibujo(false);
    this.modo = null;
    this._trazoActual = null;
    this._borrador = null;
    this._configPizarraPendiente = null;
    this._referenciaPizarra = null;
  }

  /** Entra en el modo de dibujo: la indicación empieza vacía y la barra queda visible
   * hasta «Terminar» o «Cancelar». Cada trazo suelto añade a la misma indicación. */
  _entrarEnDibujo() {
    this.modo = "pizarra-dibujando";
    this._borrador = new BorradorIndicacion();
    this._mostrarBarra();
  }

  /** Barra flotante de la indicación: contador, deshacer, terminar y cancelar. */
  _mostrarBarra() {
    this._quitarBarra();
    const contenedor = this.viewer.container;
    if (getComputedStyle(contenedor).position === "static") contenedor.style.position = "relative";
    const barra = document.createElement("div");
    barra.className = "fj-barra-indicacion";
    barra.setAttribute("role", "toolbar");
    barra.setAttribute("aria-label", "Indicación en curso");
    barra.style.cssText =
      "position:absolute;left:50%;top:12px;transform:translateX(-50%);z-index:20;display:flex;gap:8px;" +
      "align-items:center;flex-wrap:wrap;justify-content:center;padding:8px 12px;border-radius:8px;" +
      "background:var(--fj-raised,#26211d);color:var(--fj-text,#ece6df);border:1px solid var(--fj-line,#3a332d);" +
      "font:13px system-ui;max-width:92%";
    const contador = document.createElement("span");
    const crearBoton = (texto, alPulsar) => {
      const boton = document.createElement("button");
      boton.type = "button";
      boton.className = "fj-btn";
      boton.textContent = texto;
      boton.addEventListener("click", alPulsar);
      return boton;
    };
    const deshacer = crearBoton("Deshacer trazo", () => this._deshacerTrazo());
    const terminar = crearBoton("Terminar indicación", () => this._terminarIndicacion());
    const cancelar = crearBoton("Cancelar", () => this._cancelarIndicacion());
    const ayuda = document.createElement("span");
    ayuda.textContent = "Suelta el botón para añadir otro trazo · Enter termina · Esc cancela · Ctrl+Z deshace";
    ayuda.style.opacity = "0.7";
    barra.append(contador, deshacer, terminar, cancelar, ayuda);
    contenedor.appendChild(barra);
    this._barra = { barra, contador, deshacer, terminar };
    this._actualizarBarra();
  }

  _actualizarBarra() {
    if (!this._barra) return;
    const n = this._borrador?.cantidad ?? 0;
    this._barra.contador.textContent = n === 0 ? "Sin trazos" : n === 1 ? "1 trazo" : `${n} trazos`;
    this._barra.deshacer.disabled = n === 0;
    this._barra.terminar.disabled = n === 0;
  }

  _quitarBarra() {
    this._barra?.barra.remove();
    this._barra = null;
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
    this._entrarEnDibujo();
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
    this._entrarEnDibujo();
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
    this._piezaTrazoActual = this._piezaBajo(ev);
    this._pintar(this._trazoActual);
  }

  _alMoverMouse(ev) {
    if (this.modo !== "pizarra-dibujando" || this._trazoActual === null) return;
    const punto = this.viewer.proyectarEnPlanoPizarra(ev.clientX, ev.clientY);
    if (!punto) return;
    this._trazoActual.push(punto);
    // Pieza del trazo: la primera que toque mientras se dibuja.
    if (this._piezaTrazoActual === null) this._piezaTrazoActual = this._piezaEnMovimiento(ev);
    this._pintar(this._trazoActual);
  }

  /** Al soltar, el trazo pasa al borrador de la indicación y el modo sigue activo:
   * nada se guarda hasta «Terminar». */
  _alSoltarBoton(ev) {
    if (this.modo !== "pizarra-dibujando" || ev.button !== 0 || this._trazoActual === null) return;
    const puntos = this._trazoActual;
    const pieza = this._piezaTrazoActual;
    this._trazoActual = null;
    this._piezaTrazoActual = null;
    if (!this._borrador.añadirTrazo(puntos, pieza) && puntos.length >= 2) {
      avisarFeedback(`Una indicación admite hasta ${MAX_TRAZOS_INDICACION} trazos; termínala para empezar otra.`);
    }
    this._pintar(null);
    this._actualizarBarra();
  }

  /** Dibuja los trazos de la indicación en curso más el que se está dibujando. */
  _pintar(trazoActual) {
    const { tipo } = this._configPizarraPendiente;
    const enCurso = (this._borrador?.trazos ?? []).map((puntos) => ({ tipo, visible: true, puntos }));
    if (trazoActual) enCurso.push({ tipo, visible: true, puntos: trazoActual });
    this.viewer.mostrarAnotaciones(this.notas, [...this.trazos, ...enCurso]);
  }

  /** «Terminar indicación»: guarda cada trazo como marca y enlaza todas a una sola
   * indicación (la nueva, o la objetivo de «Añadir otra marca»). */
  async _terminarIndicacion() {
    if (this.modo !== "pizarra-dibujando" || !this._borrador) return;
    const trazos = this._borrador.finalizar();
    if (!trazos) {
      avisarFeedback("Dibuja al menos un trazo antes de terminar la indicación.");
      return;
    }
    const { tipo, comentario } = this._configPizarraPendiente;
    const plano = this.viewer._planoPizarra;
    const referencia = this._referenciaPizarra;
    // Antes de salir del modo de pizarra (que borra targetIntent) y antes de cualquier await.
    const destino = this.targetIntent;
    const piezasTrazos = this._borrador.piezas;
    this._salirDeModoPizarra();

    const marcas = [];
    try {
      for (const puntosTrazo of trazos) {
        const trazo = await crearTrazo(this.docId, {
          tipo,
          comentario,
          puntos: puntosTrazo,
          plano_origen: plano.origen,
          plano_normal: plano.normal,
          referencia,
        });
        marcas.push(trazo.n);
      }
    } catch (error) {
      // Los trazos ya guardados se enlazan igualmente; el aviso dice cuántos faltan.
      avisarFeedback(`Se guardaron ${marcas.length} de ${trazos.length} trazos: ${error.message ?? error}`);
      if (!marcas.length) {
        await this.recargar();
        return;
      }
    }
    // Región: la pieza más tocada, con los puntos solo de los trazos realmente guardados.
    const region = resolverRegionIndicacion(trazos.slice(0, marcas.length), piezasTrazos.slice(0, marcas.length));
    const pieza = this._piezaCanonica(region.pieza);
    const puntos = pieza ? region.puntos : [];
    await this.recargar();
    // El comentario del diálogo es el texto de la indicación; si está vacío se pregunta una vez.
    const texto = comentario || (await pedirComentario("¿Qué quieres decir con esta indicación?")) || "";
    await this._vincularMarcas(marcas, texto, { pieza, puntos, tipo: "trazo" }, destino);
  }

  /** «Cancelar» o Esc: descarta todos los trazos sin guardar nada. */
  _cancelarIndicacion() {
    if (this.modo !== "pizarra-dibujando") return;
    this._salirDeModoPizarra();
  }

  /** «Deshacer trazo» o Ctrl+Z: quita solo el último trazo. */
  _deshacerTrazo() {
    if (this.modo !== "pizarra-dibujando" || !this._borrador) return;
    if (!this._borrador.deshacerTrazo()) {
      avisarFeedback("No hay trazos que deshacer.");
      return;
    }
    this._pintar(null);
    this._actualizarBarra();
  }

  /** Teclado mientras se dibuja: Enter termina, Esc cancela, Ctrl+Z deshace el último trazo.
   * Enter y Ctrl+Z se dejan a los controles con foco (botones, campos de texto). */
  _alTecla(ev) {
    if (this.modo !== "pizarra-dibujando") return;
    const etiqueta = ev.target?.tagName;
    const escribiendo = ["INPUT", "TEXTAREA", "SELECT"].includes(etiqueta);
    const enControl = escribiendo || etiqueta === "BUTTON";
    const tecla = String(ev.key).toLowerCase();
    if (ev.key === "Escape") {
      // Esc dentro de un campo (p. ej. el deslizador del corte) es de ese campo: no descarta trazos.
      if (escribiendo) return;
      ev.preventDefault();
      ev.stopPropagation();
      this._cancelarIndicacion();
    } else if (ev.key === "Enter" && !enControl) {
      ev.preventDefault();
      ev.stopPropagation();
      void this._terminarIndicacion();
    } else if ((ev.ctrlKey || ev.metaKey) && !ev.shiftKey && !ev.altKey && tecla === "z" && !escribiendo) {
      ev.preventDefault();
      ev.stopPropagation();
      this._deshacerTrazo();
    }
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
