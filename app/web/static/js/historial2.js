// Forja — Historial 2.0: grafo de pasos de todas las ramas con miniaturas,
// detalle por paso, historial por pieza y restaurar una pieza o todo.
// La lógica pura (carriles, filtros, fechas, chips) vive en
// `historial-grafo.js`; aquí solo DOM y peticiones.
import { obtenerTokenSesion } from "./api.js?v=11";
import {
  calcularCarriles, trazoArista, filtrarNodos, predicadoFiltros, fechaRelativa, fechaAbsoluta,
  chipsDeCambios, autor, coloresDeRamas, ramasPorPrimerPadre, PALETA_RAMAS, piezasTocadas, numeroEs, tituloPaso,
} from "./historial-grafo.js?v=4";
import { compararEstados, mallaDeEstado, crearRama, crearHito, activarRama } from "./ramas.js?v=3";

const enc = encodeURIComponent;
const ALTO_FILA = 84;
const ANCHO_CARRIL = 16;
const LIMITE = 150;

async function pedir(url, opciones = {}) {
  const resp = await fetch(url, opciones);
  if (resp.status === 401) sessionStorage.removeItem("fj_token");
  if (!resp.ok) {
    let detalle = `${resp.status}`;
    try { const j = await resp.json(); detalle = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* sin cuerpo */ }
    throw new Error(detalle);
  }
  return resp.json();
}

function conToken(cuerpo) {
  const token = obtenerTokenSesion().trim();
  if (!token) throw new Error("operación cancelada: falta el token de Forja");
  const headers = { "X-Forja-Token": token };
  if (cuerpo !== undefined) headers["Content-Type"] = "application/json";
  return { method: "POST", headers, body: cuerpo === undefined ? undefined : JSON.stringify(cuerpo) };
}

export const urlMiniatura = (id, sha, pieza = null) =>
  `/documentos/${enc(id)}/pasos/${enc(sha)}/miniatura.png${pieza ? `?pieza=${enc(pieza)}` : ""}`;

export class ForjaHistorial2Controlador {
  constructor(docId, { alCambiar, alComparar, piezaSeleccionada }) {
    this.docId = docId;
    this.datos = null;
    this.error = null;
    this.cargando = false;
    this.sucio = true;
    this.filtros = { texto: "", autor: "todos", soloHitos: false, rama: "" };
    this.pieza = null; // filtro por pieza activo
    this.piezaDescartada = null; // la seleccionada que el usuario quitó con ✕
    this.seleccionado = null; // sha_corto
    this.aviso = null; // {tipo: ok|error, texto}
    this._alCambiar = alCambiar;
    this._alComparar = alComparar;
    this._piezaSeleccionada = piezaSeleccionada;
    this._peticion = 0;
    this.alActualizar = null; // lo asigna la vista
  }

  invalidar() { this.sucio = true; }

  _notificar() { this.alActualizar?.(); }

  async cargar() {
    const n = ++this._peticion;
    this.cargando = true;
    this.error = null;
    this._notificar();
    try {
      const q = new URLSearchParams({ limite: String(LIMITE) });
      if (this.pieza) q.set("pieza", this.pieza);
      const datos = await pedir(`/documentos/${enc(this.docId)}/grafo?${q}`);
      if (n !== this._peticion) return;
      this.datos = datos;
      this.sucio = false;
      if (this.seleccionado && !datos.nodos.some(x => x.sha_corto === this.seleccionado)) this.seleccionado = null;
    } catch (err) {
      if (n !== this._peticion) return;
      this.error = err.message;
    } finally {
      if (n === this._peticion) { this.cargando = false; this._notificar(); }
    }
  }

  /** Llamado al cambiar la selección del visor. */
  sincronizarPieza() {
    const sel = this._piezaSeleccionada?.() ?? null;
    if (sel !== this.piezaDescartada) this.piezaDescartada = null;
    const objetivo = sel && sel !== this.piezaDescartada ? sel : null;
    if (objetivo && objetivo !== this.pieza) {
      this.pieza = objetivo;
      this.seleccionado = null;
      return this.cargar();
    }
    return null;
  }

  quitarPieza() {
    this.piezaDescartada = this.pieza;
    this.pieza = null;
    this.seleccionado = null;
    return this.cargar();
  }

  filtrarPorPieza(nombre) {
    this.pieza = nombre;
    this.piezaDescartada = null;
    this.seleccionado = null;
    return this.cargar();
  }

  nodosVisibles() {
    if (!this.datos) return [];
    const f = this.filtros;
    const activo = f.texto || f.autor !== "todos" || f.soloHitos || f.rama;
    return activo ? filtrarNodos(this.datos.nodos, predicadoFiltros(f)) : this.datos.nodos;
  }

  nodo(sha) { return this.datos?.nodos.find(x => x.sha_corto === sha) ?? null; }

  async _tras(registro, texto) {
    this.aviso = { tipo: "ok", texto };
    this.sucio = true;
    await this._alCambiar?.(registro);
    await this.cargar();
  }

  async restaurarPieza(pieza, sha) {
    const r = await pedir(`/documentos/${enc(this.docId)}/piezas/${enc(pieza)}/restaurar`, conToken({ desde: sha }));
    if (r.resultado === "sin_cambios") { this.aviso = { tipo: "info", texto: `«${pieza}» ya está como en ${sha}.` }; this._notificar(); return r; }
    if (r.resultado === "conflicto_geometrico") {
      const choques = (r.verificacion?.choques_nuevos || []).map(([a, b]) => `${a} ↔ ${b}`).join(", ");
      if (!window.confirm(`Restaurar «${pieza}» deja choques nuevos (${choques}). ¿Restaurar igualmente?`)) return r;
      const forzado = await pedir(`/documentos/${enc(this.docId)}/piezas/${enc(pieza)}/restaurar`, conToken({ desde: sha, forzar: true }));
      await this._tras(forzado, `«${pieza}» restaurada a ${sha} (con choques).`);
      return forzado;
    }
    if (!r.confirmada) throw new Error((r.conflictos || []).map(c => c.mensaje).join("; ") || r.resultado);
    await this._tras(r, `«${pieza}» restaurada a ${sha}.`);
    return r;
  }

  async restaurarTodo(sha) {
    const r = await pedir(`/documentos/${enc(this.docId)}/pasos/${enc(sha)}/restaurar`, conToken());
    if (r.resultado === "sin_cambios") { this.aviso = { tipo: "info", texto: "El documento ya está en ese estado." }; this._notificar(); return r; }
    await this._tras(r, `Documento restaurado a ${sha} (paso nuevo, nada se pierde).`);
    return r;
  }

  async crearRamaDesde(nombre, sha, cambiar) {
    await crearRama(this.docId, nombre, sha);
    if (cambiar) {
      const reg = await activarRama(this.docId, nombre);
      await this._tras(reg, `Rama «${nombre}» creada y activa.`);
    } else {
      this.aviso = { tipo: "ok", texto: `Rama «${nombre}» creada desde ${sha}.` };
      await this.cargar();
    }
  }

  async cambiarRama(nombre) {
    const reg = await activarRama(this.docId, nombre);
    const avisos = Array.isArray(reg.avisos) && reg.avisos.length ? ` ⚠ ${reg.avisos.join(" ")}` : "";
    await this._tras(reg, `Ahora estás en la rama «${nombre}».${avisos}`);
  }

  async marcarHito(nombre, sha) {
    await crearHito(this.docId, nombre, sha);
    this.aviso = { tipo: "ok", texto: `Hito «${nombre}» en ${sha}.` };
    await this.cargar();
  }

  /** Antes/después en el visor (dividida por defecto). */
  async comparar(a, b, etiquetas) {
    const [resumen, mallaA, mallaB] = await Promise.all([
      compararEstados(this.docId, a, b), mallaDeEstado(this.docId, a), mallaDeEstado(this.docId, b)]);
    await this._alComparar?.(resumen, mallaA, mallaB, { modo: "dividida", etiquetas });
  }
}

// ------------------------------------------------------------ vista

function el(etiqueta, clase = "", texto = null, attrs = {}) {
  const e = document.createElement(etiqueta);
  if (clase) e.className = clase;
  if (texto !== null) e.textContent = texto;
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    e.setAttribute(k, v === true ? "" : String(v));
  }
  return e;
}

function svg(etiqueta, attrs = {}) {
  const e = document.createElementNS("http://www.w3.org/2000/svg", etiqueta);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, String(v));
  return e;
}

function accion(texto, alClic, { clase = "fj-btn fj-btn--ghost", titulo = null, etiqueta = null } = {}) {
  const b = el("button", clase, texto, { type: "button", title: titulo, "aria-label": etiqueta });
  b.addEventListener("click", async ev => {
    ev.stopPropagation();
    b.disabled = true;
    b.setAttribute("aria-busy", "true");
    try { await alClic(); } catch (err) { mostrarError(b, err); } finally { b.disabled = false; b.removeAttribute("aria-busy"); }
  });
  return b;
}

let _vistaActual = null;
function mostrarError(_origen, err) {
  const ctl = _vistaActual?.ctl;
  if (ctl) { ctl.aviso = { tipo: "error", texto: `No se pudo completar: ${err.message}` }; ctl._notificar(); }
  else alert(`No se pudo completar: ${err.message}`);
}

function chip(c) {
  return el("span", `fj-h2-chip fj-h2-chip--${c.tipo}`, c.texto, { title: c.titulo });
}

function pastillaRama(nombre, colorIdx, activa) {
  const p = el("span", `fj-h2-pastilla${activa ? " is-activa" : ""}`, null, { title: activa ? `Rama activa: ${nombre}` : `Rama ${nombre}` });
  p.style.setProperty("--fj-h2-color", PALETA_RAMAS[colorIdx ?? 0]);
  p.append(el("span", "fj-h2-pastilla__punto", null, { "aria-hidden": "true" }), el("span", "fj-h2-pastilla__nombre", nombre));
  if (activa) p.append(el("span", "fj-h2-pastilla__marca", "activa", { "data-prioridad": 2 }));
  return p;
}

function pastillaHito(nombre) {
  return el("span", "fj-h2-pastilla fj-h2-pastilla--hito", `◆ ${nombre}`, { title: `Hito ${nombre}` });
}

function miniatura(src, clase, alt) {
  const caja = el("div", `${clase} is-cargando`);
  const img = el("img", "", null, { src, alt, loading: "lazy", decoding: "async", width: 256, height: 192 });
  img.addEventListener("load", () => caja.classList.remove("is-cargando"));
  img.addEventListener("error", () => { caja.classList.remove("is-cargando"); caja.classList.add("is-vacia"); img.remove(); caja.textContent = "sin vista"; });
  caja.append(img);
  return caja;
}

const desborda = e => e.scrollWidth > e.clientWidth + 1;

/** Ajusta cada tarjeta a su ancho real: los chips que no caben se agrupan en
 * «+N más» y la fila de metadatos oculta primero el sha y luego la fecha
 * (las pastillas de rama se acortan con elipsis). Nada queda cortado. */
function ajustarTarjetas(contenedor) {
  for (const fila of contenedor.querySelectorAll(".fj-h2__fila")) {
    const meta = fila.querySelector(".fj-h2__meta");
    if (meta) {
      const opcionales = [...meta.querySelectorAll("[data-prioridad]")].sort((a, b) => a.dataset.prioridad - b.dataset.prioridad);
      for (const o of opcionales) o.hidden = false;
      // Las pastillas solo se acortan como último recurso: primero se mide
      // con ellas a tamaño completo y se ocultan los opcionales.
      meta.classList.add("is-midiendo");
      for (const o of opcionales) { if (!desborda(meta)) break; o.hidden = true; }
      meta.classList.remove("is-midiendo");
    }
    const chips = fila.querySelector(".fj-h2__chips");
    if (!chips) continue;
    chips.querySelector(".fj-h2-chip--mas")?.remove();
    const todos = [...chips.children];
    for (const c of todos) c.hidden = false;
    if (!desborda(chips)) continue;
    const mas = el("span", "fj-h2-chip fj-h2-chip--mas", "");
    chips.append(mas);
    for (let k = todos.length - 1; k >= 0; k--) {
      todos[k].hidden = true;
      const ocultos = todos.slice(k);
      mas.textContent = `+${ocultos.length} más`;
      mas.title = ocultos.map(c => c.textContent).join(", ");
      if (!desborda(chips)) break;
    }
  }
}

class Vista {
  constructor(raiz) {
    this.raiz = raiz;
    this.ctl = null;
    this.raiz.classList.add("fj-h2");
    this.raiz.textContent = "";

    // Barra superior.
    const barra = el("header", "fj-h2__barra");
    const titulo = el("div", "fj-h2__titulo");
    titulo.append(el("h2", "", "Historial"), (this.contador = el("span", "fj-h2__contador", "")));
    const filtros = el("div", "fj-h2__filtros", null, { role: "group", "aria-label": "Filtros del historial" });
    this.busqueda = el("input", "fj-input fj-h2__buscar", null, { type: "search", placeholder: "Buscar…", title: "Buscar por mensaje o sha", "aria-label": "Buscar en el historial" });
    this.busqueda.addEventListener("input", () => { this.ctl.filtros.texto = this.busqueda.value; this.pintarLista(); });
    this.segmento = el("div", "fj-h2__segmento", null, { role: "radiogroup", "aria-label": "Autor" });
    for (const [valor, texto, tit] of [["todos", "Todos", "Todos los autores"], ["agente", "🤖", "Solo el agente"], ["humano", "👤", "Solo humanos"], ["forja", "⚙", "Solo Forja"]]) {
      const b = el("button", "fj-h2__seg", texto, { type: "button", role: "radio", "data-valor": valor, title: tit, "aria-label": tit });
      b.addEventListener("click", () => { this.ctl.filtros.autor = valor; this.pintarFiltros(); this.pintarLista(); });
      this.segmento.append(b);
    }
    this.hitos = el("button", "fj-h2__toggle", "◆ Hitos", { type: "button", "aria-pressed": "false", title: "Solo pasos con hito o punta de rama" });
    this.hitos.addEventListener("click", () => { this.ctl.filtros.soloHitos = !this.ctl.filtros.soloHitos; this.pintarFiltros(); this.pintarLista(); });
    this.selRama = el("select", "fj-input fj-h2__rama", null, { "aria-label": "Rama a mostrar" });
    this.selRama.addEventListener("change", () => { this.ctl.filtros.rama = this.selRama.value; this.pintarLista(); });
    filtros.append(this.busqueda, this.segmento, this.hitos, this.selRama);
    this.piezaFila = el("div", "fj-h2__pieza");
    barra.append(titulo, filtros, this.piezaFila);

    this.avisoEl = el("div", "fj-h2__aviso", null, { role: "status", "aria-live": "polite" });

    const cuerpo = el("div", "fj-h2__cuerpo");
    this.lista = el("div", "fj-h2__lista");
    this.detalle = el("aside", "fj-h2__detalle", null, { "aria-label": "Detalle del paso", "aria-live": "polite" });
    cuerpo.append(this.lista, this.detalle);

    // Cajón con la gestión clásica (ramas/fusión e instantáneas G1).
    this.cajon = el("div", "fj-h2__cajon");
    const extraRamas = el("details", "fj-h2__extra");
    extraRamas.append(el("summary", "", "Ramas y fusiones"));
    const extraInst = el("details", "fj-h2__extra");
    extraInst.append(el("summary", "", "Instantáneas «antes de…» (deshacer clásico)"));
    const panelRamas = document.getElementById("fj-panel-ramas");
    const panelInst = document.getElementById("fj-panel-instantaneas");
    if (panelRamas) { panelRamas.hidden = false; extraRamas.append(panelRamas); }
    if (panelInst) { panelInst.hidden = false; extraInst.append(panelInst); }
    this.cajon.append(extraRamas, extraInst);

    this.raiz.append(barra, this.avisoEl, cuerpo, this.cajon);
    this._tirador();
  }

  _tirador() {
    const panel = this.raiz.closest(".fj-panel-lateral");
    if (!panel || panel.querySelector(".fj-h2__tirador")) return;
    const t = el("div", "fj-h2__tirador", null, { role: "separator", "aria-orientation": "vertical", "aria-label": "Cambiar el ancho del historial", tabindex: 0 });
    const fijar = px => {
      const max = Math.max(480, Math.min(1100, window.innerWidth - 380));
      const v = Math.round(Math.min(max, Math.max(480, px)));
      panel.style.setProperty("--fj-h2-ancho", `${v}px`);
      t.setAttribute("aria-valuenow", String(v));
      try { localStorage.setItem("fj_h2_ancho", String(v)); } catch { /* sin almacenamiento */ }
    };
    const guardado = Number(localStorage.getItem("fj_h2_ancho"));
    if (guardado) fijar(guardado);
    t.addEventListener("pointerdown", ev => {
      ev.preventDefault();
      t.setPointerCapture(ev.pointerId);
      const mover = e => fijar(panel.getBoundingClientRect().right - e.clientX);
      const soltar = () => { t.removeEventListener("pointermove", mover); t.removeEventListener("pointerup", soltar); window.dispatchEvent(new Event("resize")); };
      t.addEventListener("pointermove", mover);
      t.addEventListener("pointerup", soltar);
    });
    t.addEventListener("keydown", ev => {
      const w = panel.getBoundingClientRect().width;
      if (ev.key === "ArrowLeft") { ev.preventDefault(); fijar(w + 40); }
      if (ev.key === "ArrowRight") { ev.preventDefault(); fijar(w - 40); }
    });
    panel.prepend(t);
  }

  mostrar(ctl) {
    if (this.ctl !== ctl) {
      if (this.ctl) this.ctl.alActualizar = null;
      this.ctl = ctl;
      ctl.alActualizar = () => { if (this.ctl === ctl) this.pintar(); };
      this.busqueda.value = ctl.filtros.texto;
    }
    _vistaActual = this;
    const promesa = ctl.sincronizarPieza();
    if (!promesa && (ctl.sucio || (!ctl.datos && !ctl.cargando && !ctl.error))) ctl.cargar();
    this.pintar();
  }

  pintar() {
    this.pintarFiltros();
    this.pintarAviso();
    this.pintarPieza();
    this.pintarLista();
  }

  pintarFiltros() {
    const ctl = this.ctl;
    const d = ctl.datos;
    this.contador.textContent = d ? `${d.total} paso${d.total === 1 ? "" : "s"} · ${d.ramas.length} rama${d.ramas.length === 1 ? "" : "s"}` : "";
    for (const b of this.segmento.children) {
      const on = b.dataset.valor === ctl.filtros.autor;
      b.classList.toggle("is-on", on);
      b.setAttribute("aria-checked", String(on));
    }
    this.hitos.classList.toggle("is-on", ctl.filtros.soloHitos);
    this.hitos.setAttribute("aria-pressed", String(ctl.filtros.soloHitos));
    const valor = ctl.filtros.rama;
    this.selRama.textContent = "";
    this.selRama.append(el("option", "", "Todas las ramas", { value: "" }));
    for (const r of d?.ramas || []) this.selRama.append(el("option", "", r.activa ? `${r.nombre} (activa)` : r.nombre, { value: r.nombre }));
    this.selRama.value = valor;
  }

  pintarAviso() {
    const a = this.ctl.aviso;
    this.avisoEl.textContent = "";
    this.avisoEl.hidden = !a;
    if (!a) return;
    this.avisoEl.className = `fj-h2__aviso fj-h2__aviso--${a.tipo}`;
    this.avisoEl.setAttribute("role", a.tipo === "error" ? "alert" : "status");
    const cerrar = el("button", "fj-h2__cerrar", "×", { type: "button", "aria-label": "Cerrar aviso" });
    cerrar.addEventListener("click", () => { this.ctl.aviso = null; this.pintarAviso(); });
    this.avisoEl.append(el("span", "", a.texto), cerrar);
  }

  pintarPieza() {
    const ctl = this.ctl;
    this.piezaFila.textContent = "";
    const sel = ctl._piezaSeleccionada?.() ?? null;
    if (ctl.pieza) {
      const c = el("div", "fj-h2__pieza-chip", null, { role: "group", "aria-label": `Filtrado por la pieza ${ctl.pieza}` });
      const quitar = el("button", "fj-h2__cerrar", "✕", { type: "button", "aria-label": `Quitar el filtro de la pieza ${ctl.pieza}` });
      quitar.addEventListener("click", () => ctl.quitarPieza());
      c.append(el("span", "fj-h2__pieza-etq", "Pieza:"), el("strong", "", ctl.pieza), quitar);
      this.piezaFila.append(c);
      const nodos = ctl.datos && ctl.datos.pieza === ctl.pieza ? ctl.datos.nodos : [];
      if (nodos.length) {
        const tira = el("div", "fj-h2__tira", null, { role: "list", "aria-label": `Versiones de ${ctl.pieza}` });
        for (const n of nodos.slice(0, 24)) {
          const quitada = (n.cambios?.quitadas || []).includes(ctl.pieza);
          const b = el("button", `fj-h2__tira-item${n.sha_corto === ctl.seleccionado ? " is-sel" : ""}`, null,
            { type: "button", role: "listitem", title: `${n.mensaje} · ${fechaAbsoluta(n.fecha)}` });
          if (quitada) b.append(el("div", "fj-h2__tira-img is-vacia", "quitada"));
          else b.append(miniatura(urlMiniatura(ctl.docId, n.sha_corto, ctl.pieza), "fj-h2__tira-img", `${ctl.pieza} en ${n.sha_corto}`));
          b.append(el("span", "", fechaRelativa(n.fecha)));
          b.addEventListener("click", () => this.seleccionar(n.sha_corto));
          tira.append(b);
        }
        this.piezaFila.append(tira);
      }
    } else if (sel) {
      const b = el("button", "fj-h2__sugerencia", null, { type: "button" });
      b.append(el("span", "", "Ver el historial de "), el("strong", "", sel));
      b.addEventListener("click", () => ctl.filtrarPorPieza(sel));
      this.piezaFila.append(b);
    } else {
      this.piezaFila.append(el("span", "fj-h2__pista", "Selecciona una pieza en el visor para ver solo su historia."));
    }
  }

  pintarLista() {
    const ctl = this.ctl;
    const lista = this.lista;
    const scroll = lista.scrollTop;
    lista.textContent = "";
    if (ctl.error && !ctl.datos) {
      const caja = el("div", "fj-h2__estado fj-h2__estado--error", null, { role: "alert" });
      caja.append(el("strong", "", "No se pudo cargar el historial"), el("p", "", ctl.error), accion("Reintentar", () => ctl.cargar(), { clase: "fj-btn" }));
      lista.append(caja);
      this.pintarDetalle();
      return;
    }
    if (!ctl.datos || (ctl.cargando && ctl.datos.pieza !== ctl.pieza)) {
      lista.setAttribute("aria-busy", "true");
      for (let i = 0; i < 6; i++) {
        const f = el("div", "fj-h2__esqueleto", null, { "aria-hidden": "true" });
        f.append(el("span", "fj-h2__esq-nodo"), el("span", "fj-h2__esq-mini"), el("span", "fj-h2__esq-lineas"));
        f.style.animationDelay = `${i * 80}ms`;
        lista.append(f);
      }
      lista.append(el("span", "fj-sr", "Cargando historial…"));
      this.pintarDetalle();
      return;
    }
    lista.removeAttribute("aria-busy");
    const nodos = ctl.nodosVisibles();
    if (!nodos.length) {
      const caja = el("div", "fj-h2__estado");
      const filtrado = ctl.pieza || ctl.filtros.texto || ctl.filtros.autor !== "todos" || ctl.filtros.soloHitos || ctl.filtros.rama;
      caja.append(el("div", "fj-h2__estado-icono", filtrado ? "⌕" : "◌", { "aria-hidden": "true" }),
        el("strong", "", filtrado ? "Ningún paso coincide" : "Aún no hay pasos"),
        el("p", "", ctl.pieza ? `Ningún paso tocó «${ctl.pieza}» con estos filtros.` : filtrado ? "Prueba con otros filtros." : "Cada cambio aceptado aparecerá aquí con su miniatura."));
      if (filtrado) caja.append(accion("Limpiar filtros", async () => {
        ctl.filtros = { texto: "", autor: "todos", soloHitos: false, rama: "" };
        this.busqueda.value = "";
        if (ctl.pieza) await ctl.quitarPieza(); else this.pintar();
      }, { clase: "fj-btn" }));
      lista.append(caja);
      this.pintarDetalle();
      return;
    }

    const datos = ctl.datos;
    const orden = datos.ramas.map(r => r.nombre);
    const colores = coloresDeRamas(datos.ramas);
    const activa = datos.activa;
    const { filas, aristas, carriles } = calcularCarriles(nodos);
    const anchoGrafo = Math.max(2, carriles) * ANCHO_CARRIL + 8;
    const alto = nodos.length * ALTO_FILA;
    const contenedor = el("div", "fj-h2__filas", null, { role: "listbox", "aria-label": "Pasos del historial", "aria-activedescendant": ctl.seleccionado ? `fj-h2-${ctl.seleccionado}` : null });
    contenedor.style.setProperty("--fj-h2-grafo", `${anchoGrafo}px`);
    contenedor.style.setProperty("--fj-h2-fila", `${ALTO_FILA}px`);

    const lienzo = svg("svg", { class: "fj-h2__grafo", width: anchoGrafo, height: alto, viewBox: `0 0 ${anchoGrafo} ${alto}`, "aria-hidden": "true" });
    const geo = { alto: ALTO_FILA, ancho: ANCHO_CARRIL, margen: ANCHO_CARRIL / 2 + 4 };
    const ramaDe = ramasPorPrimerPadre(nodos, orden);
    const colorNodo = nodos.map(n => colores[ramaDe[n.sha_corto]] ?? 0);
    const enActiva = nodos.map(n => (n.ramas || []).includes(activa));
    const capaAristas = svg("g", { class: "fj-h2__aristas" });
    const aristasDibujadas = [];
    for (const e of aristas) {
      const hijo = nodos[e.de];
      const padre = nodos[e.a];
      const idx = e.orden > 0 && padre ? colorNodo[e.a] : colorNodo[e.de];
      const camino = svg("path", { d: trazoArista(e, geo), class: `fj-h2__arista${e.orden > 0 ? " is-fusion" : ""}${e.fuera ? " is-fuera" : ""}${enActiva[e.de] && (!padre || enActiva[e.a]) ? " is-activa" : ""}` });
      camino.style.stroke = PALETA_RAMAS[idx];
      if (hijo) { capaAristas.append(camino); aristasDibujadas.push(e); }
    }
    lienzo.append(capaAristas);
    const capaNodos = svg("g", { class: "fj-h2__nodos" });
    filas.forEach((f, i) => {
      const n = nodos[i];
      const cx = geo.margen + f.carril * ANCHO_CARRIL;
      const cy = i * ALTO_FILA + ALTO_FILA / 2;
      const color = PALETA_RAMAS[colorNodo[i]];
      const clase = `fj-h2__nodo${enActiva[i] ? " is-activa" : ""}${n.sha_corto === ctl.seleccionado ? " is-sel" : ""}`;
      let forma;
      if ((n.hitos || []).length) {
        forma = svg("rect", { x: cx - 6, y: cy - 6, width: 12, height: 12, rx: 2, transform: `rotate(45 ${cx} ${cy})`, class: clase });
        forma.dataset.rot = `rotate(45 ${cx} ${cy})`;
      } else {
        forma = svg("circle", { cx, cy, r: n.fusion ? 7 : 5.5, class: clase + (n.fusion ? " is-fusion" : "") });
      }
      forma.style.setProperty("--fj-h2-color", color);
      forma.dataset.fila = i;
      capaNodos.append(forma);
      if ((n.puntas || []).includes(activa)) {
        const halo = svg("circle", { cx, cy, r: 10, class: "fj-h2__halo" });
        halo.dataset.fila = i;
        capaNodos.append(halo);
      }
    });
    lienzo.append(capaNodos);
    contenedor.append(lienzo);

    const ahora = Date.now();
    nodos.forEach((n, i) => {
      const fila = el("div", `fj-h2__fila${n.sha_corto === ctl.seleccionado ? " is-sel" : ""}${enActiva[i] ? "" : " is-otra"}`, null, {
        id: `fj-h2-${n.sha_corto}`, role: "option", tabindex: (ctl.seleccionado ? n.sha_corto === ctl.seleccionado : i === 0) ? 0 : -1,
        "aria-selected": String(n.sha_corto === ctl.seleccionado), "data-sha": n.sha_corto,
      });
      fila.style.setProperty("--fj-h2-color", PALETA_RAMAS[colorNodo[i]]);
      fila.style.animationDelay = `${Math.min(i, 12) * 18}ms`;
      fila.append(miniatura(urlMiniatura(ctl.docId, n.sha_corto, ctl.pieza && !(n.cambios?.quitadas || []).includes(ctl.pieza) ? ctl.pieza : null), "fj-h2__mini", ""));
      const cuerpo = el("div", "fj-h2__tarjeta");
      const cabeza = el("div", "fj-h2__cabeza");
      const a = autor(n.autor);
      cabeza.append(el("span", `fj-h2__autor fj-h2__autor--${n.autor}`, a.icono, { title: a.texto, "aria-label": a.texto }));
      cabeza.append(el("span", "fj-h2__mensaje", tituloPaso(n), { title: n.mensaje }));
      cuerpo.append(cabeza);
      const meta = el("div", "fj-h2__meta");
      for (const r of n.puntas || []) meta.append(pastillaRama(r, colores[r], r === activa));
      for (const h of n.hitos || []) meta.append(pastillaHito(h));
      if (n.fusion) meta.append(el("span", "fj-h2-chip fj-h2-chip--fusion", "fusión", { title: "Paso de fusión (dos padres)" }));
      if (n.desde) meta.append(el("span", "fj-h2-chip fj-h2-chip--neutro", `desde ${n.desde.slice(0, 7)}`, { title: `Trae piezas del paso ${n.desde}` }));
      // data-prioridad: lo primero que se oculta si no cabe (1 = sha,
      // 2 = marca «activa» de la pastilla, 3 = fecha).
      meta.append(el("time", "fj-h2__fecha", fechaRelativa(n.fecha, ahora), { datetime: n.fecha, title: fechaAbsoluta(n.fecha), "data-prioridad": 3 }));
      meta.append(el("code", "fj-h2__sha", n.sha_corto.slice(0, 7), { title: n.sha_corto, "data-prioridad": 1 }));
      cuerpo.append(meta);
      const chips = el("div", "fj-h2__chips");
      for (const c of chipsDeCambios(n.cambios, Infinity)) chips.append(chip(c));
      cuerpo.append(chips);
      fila.append(cuerpo);
      fila.addEventListener("click", () => this.seleccionar(n.sha_corto));
      fila.addEventListener("keydown", ev => this._tecla(ev, nodos, i));
      contenedor.append(fila);
    });
    if (datos.truncado && !ctl.pieza) {
      contenedor.append(el("div", "fj-h2__mas", `Se muestran los ${datos.nodos.length} pasos más recientes de ${datos.total}.`));
    }
    lista.append(contenedor);
    ajustarTarjetas(contenedor);
    this._observarAncho();
    lista.scrollTop = scroll;
    // Alinear el grafo con el centro REAL de cada tarjeta.
    const ys = [...contenedor.querySelectorAll(".fj-h2__fila")].map(f => f.offsetTop + f.offsetHeight / 2);
    if (ys.length === nodos.length) {
      const ultima = contenedor.querySelectorAll(".fj-h2__fila")[ys.length - 1];
      ys.push(ultima.offsetTop + ultima.offsetHeight + ALTO_FILA / 2);
      const altoReal = Math.max(alto, ys[ys.length - 1]);
      lienzo.setAttribute("height", altoReal);
      lienzo.setAttribute("viewBox", `0 0 ${anchoGrafo} ${altoReal}`);
      const geoReal = { ...geo, ys };
      capaAristas.querySelectorAll("path").forEach((p, k) => p.setAttribute("d", trazoArista(aristasDibujadas[k], geoReal)));
      capaNodos.querySelectorAll("[data-fila]").forEach(f => {
        const i = Number(f.dataset.fila);
        const dy = ys[i] - (i * ALTO_FILA + ALTO_FILA / 2);
        f.setAttribute("transform", `translate(0 ${dy})${f.dataset.rot ? " " + f.dataset.rot : ""}`);
      });
    }
    this.pintarDetalle();
  }

  /** Reajusta chips y metadatos cuando cambia el ancho real de la lista
   * (tirador, abrir/cerrar el detalle, ventana). */
  _observarAncho() {
    if (this._ro || typeof ResizeObserver === "undefined") return;
    let ancho = 0;
    this._ro = new ResizeObserver(([e]) => {
      const w = Math.round(e.contentRect.width);
      if (w === ancho) return;
      ancho = w;
      const c = this.lista.querySelector(".fj-h2__filas");
      if (c) ajustarTarjetas(c);
    });
    this._ro.observe(this.lista);
  }

  _tecla(ev, nodos, i) {
    let j = null;
    if (ev.key === "ArrowDown") j = Math.min(nodos.length - 1, i + 1);
    else if (ev.key === "ArrowUp") j = Math.max(0, i - 1);
    else if (ev.key === "Home") j = 0;
    else if (ev.key === "End") j = nodos.length - 1;
    else if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); this.seleccionar(nodos[i].sha_corto); return; }
    if (j === null) return;
    ev.preventDefault();
    const destino = this.lista.querySelector(`[data-sha="${nodos[j].sha_corto}"]`);
    if (destino) {
      for (const f of this.lista.querySelectorAll(".fj-h2__fila")) f.tabIndex = -1;
      destino.tabIndex = 0;
      destino.focus();
    }
  }

  seleccionar(sha) {
    this.ctl.seleccionado = this.ctl.seleccionado === sha ? null : sha;
    this.pintarPieza();
    for (const f of this.lista.querySelectorAll(".fj-h2__fila")) {
      const on = f.dataset.sha === this.ctl.seleccionado;
      f.classList.toggle("is-sel", on);
      f.setAttribute("aria-selected", String(on));
    }
    for (const nodo of this.lista.querySelectorAll(".fj-h2__nodo")) nodo.classList.remove("is-sel");
    const idx = [...this.lista.querySelectorAll(".fj-h2__fila")].findIndex(f => f.dataset.sha === this.ctl.seleccionado);
    if (idx >= 0) this.lista.querySelectorAll(".fj-h2__nodo")[idx]?.classList.add("is-sel");
    this.pintarDetalle();
    // Detalle apilado bajo la lista (panel estrecho): llevarlo a la vista.
    if (this.ctl.seleccionado && getComputedStyle(this.detalle.parentElement).display === "flex") this.detalle.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  pintarDetalle() {
    const ctl = this.ctl;
    const d = this.detalle;
    d.textContent = "";
    const n = ctl.seleccionado ? ctl.nodo(ctl.seleccionado) : null;
    d.classList.toggle("is-vacio", !n);
    if (!n) {
      const vacio = el("div", "fj-h2__detalle-vacio");
      vacio.append(el("div", "fj-h2__estado-icono", "⎇", { "aria-hidden": "true" }), el("strong", "", "Elige un paso"),
        el("p", "", "Verás qué piezas cambiaron, los parámetros, y podrás compararlo con el anterior o restaurarlo."));
      d.append(vacio);
      return;
    }
    const datos = ctl.datos;
    const colores = coloresDeRamas(datos.ramas);
    const a = autor(n.autor);

    const cab = el("div", "fj-h2__det-cab");
    cab.append(el("span", `fj-h2__autor fj-h2__autor--${n.autor} is-grande`, a.icono, { "aria-hidden": "true" }));
    const tit = el("div", "fj-h2__det-tit");
    tit.append(el("h3", "", tituloPaso(n), { title: n.mensaje }));
    const sub = el("div", "fj-h2__det-sub");
    sub.append(el("span", "fj-h2__det-autor", a.texto), el("span", "", "·", { "aria-hidden": "true" }),
      el("time", "", fechaRelativa(n.fecha), { datetime: n.fecha, title: fechaAbsoluta(n.fecha) }));
    tit.append(sub);
    const cerrar = el("button", "fj-h2__cerrar", "×", { type: "button", "aria-label": "Cerrar el detalle" });
    cerrar.addEventListener("click", () => this.seleccionar(n.sha_corto));
    cab.append(tit, cerrar);
    d.append(cab);

    const ids = el("div", "fj-h2__det-ids");
    ids.append(el("code", "fj-h2__sha", n.sha_corto, { title: "Identificador del paso" }));
    if (n.padres.length) {
      ids.append(el("span", "fj-h2__det-etq", n.padres.length > 1 ? "padres" : "padre"));
      for (const p of n.padres) {
        const b = el("button", "fj-h2__enlace", p.slice(0, 7), { type: "button", title: `Ir al paso ${p}` });
        b.addEventListener("click", () => { if (ctl.nodo(p)) { this.seleccionar(p); this.lista.querySelector(`[data-sha="${p}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" }); } });
        ids.append(b);
      }
    } else ids.append(el("span", "fj-h2__det-etq", "primer paso"));
    d.append(ids);

    const etiquetas = el("div", "fj-h2__meta");
    for (const r of n.ramas || []) etiquetas.append(pastillaRama(r, colores[r], r === datos.activa));
    for (const h of n.hitos || []) etiquetas.append(pastillaHito(h));
    d.append(etiquetas);

    if (n.mensaje && n.mensaje !== tituloPaso(n)) d.append(el("p", "fj-h2__det-msg", n.mensaje));
    d.append(miniatura(urlMiniatura(ctl.docId, n.sha_corto), "fj-h2__det-mini", `Vista del paso ${n.sha_corto}`));

    const acciones = el("div", "fj-h2__det-acciones");
    const padre = n.padres[0];
    if (padre) acciones.append(accion("◐ Antes / después", () => ctl.comparar(padre, n.sha_corto, ["Antes", "Después"]), { clase: "fj-btn fj-btn--primary", titulo: "Comparar con el paso anterior en el visor" }));
    acciones.append(accion("⇄ Comparar con el actual", () => ctl.comparar(n.sha_corto, datos.activa, [n.sha_corto.slice(0, 7), "Actual"]), { clase: "fj-btn", titulo: `Este paso frente a la punta de ${datos.activa}` }));
    d.append(acciones);

    const c = n.cambios || {};
    const tocadas = piezasTocadas(c);
    const sec = (titulo, cuenta) => {
      const s = el("section", "fj-h2__sec");
      const h = el("h4", "", titulo);
      if (cuenta !== undefined) h.append(el("span", "fj-h2__cuenta", String(cuenta)));
      s.append(h);
      d.append(s);
      return s;
    };
    const sPiezas = sec("Piezas tocadas", tocadas.length);
    if (!tocadas.length) sPiezas.append(el("p", "fj-h2__nada", c.error ? "No se pudo calcular el resumen." : "Ninguna pieza cambió en este paso."));
    const ul = el("ul", "fj-h2__piezas");
    const ESTADO = { "añadida": "añadida", cambiada: "cambiada", quitada: "quitada", material: "material" };
    for (const t of tocadas) {
      const li = el("li", `fj-h2__pieza-fila fj-h2__pieza-fila--${t.estado}`);
      li.append(el("span", "fj-h2__estado-punto", null, { "aria-hidden": "true" }), el("span", "fj-h2__pieza-nombre", t.nombre),
        el("span", "fj-h2__pieza-estado", ESTADO[t.estado] ?? t.estado));
      const filtro = el("button", "fj-h2__mini-btn", "⌕", { type: "button", title: `Historial de ${t.nombre}`, "aria-label": `Ver el historial de ${t.nombre}` });
      filtro.addEventListener("click", () => ctl.filtrarPorPieza(t.nombre));
      const textoRest = t.estado === "quitada" ? `Este paso quitó «${t.nombre}»: restaurarla aquí la quita del documento actual.` : `Dejar «${t.nombre}» como estaba en ${n.sha_corto.slice(0, 7)}; el resto no cambia.`;
      const rest = accion("↺ Restaurar", async () => {
        if (!window.confirm(`${textoRest}\n\nSe creará un paso nuevo («restaurar pieza ${t.nombre}»). ¿Continuar?`)) return;
        await ctl.restaurarPieza(t.nombre, n.sha_corto);
      }, { clase: "fj-h2__mini-btn fj-h2__mini-btn--texto", titulo: "Restaurar esta pieza a esta versión", etiqueta: `Restaurar ${t.nombre} a esta versión` });
      li.append(filtro, rest);
      ul.append(li);
    }
    if (tocadas.length) sPiezas.append(ul);
    if (c.volumen_pct !== null && c.volumen_pct !== undefined && !c.raiz) {
      sPiezas.append(el("p", "fj-h2__vol", `Volumen total ${c.volumen_delta >= 0 ? "+" : ""}${numeroEs(c.volumen_delta, 1)} mm³ (${c.volumen_pct >= 0 ? "+" : ""}${numeroEs(c.volumen_pct, 3)} %)`));
    }

    if ((c.parametros || []).length) {
      const sPar = sec(c.raiz ? "Parámetros iniciales" : "Parámetros", c.parametros.length);
      const tabla = el("table", "fj-h2__tabla");
      const thead = el("thead");
      const trh = el("tr");
      trh.append(el("th", "", "Nombre", { scope: "col" }), el("th", "", "Antes", { scope: "col" }), el("th", "", "", { scope: "col", "aria-label": "cambia a" }), el("th", "", "Después", { scope: "col" }));
      thead.append(trh);
      const tb = el("tbody");
      const fmt = v => (v === null || v === undefined ? "—" : typeof v === "number" ? numeroEs(v, 3) : typeof v === "string" ? v : JSON.stringify(v));
      for (const p of c.parametros) {
        const tr = el("tr");
        tr.append(el("th", "", p.nombre, { scope: "row" }), el("td", "fj-h2__antes", fmt(p.antes)), el("td", "fj-h2__flecha", "→", { "aria-hidden": "true" }), el("td", "fj-h2__despues", fmt(p.despues)));
        tb.append(tr);
      }
      tabla.append(thead, tb);
      sPar.append(tabla);
    }
    if ((c.materiales || []).length) {
      const sMat = sec("Materiales cambiados", c.materiales.length);
      sMat.append(el("p", "fj-h2__nada", c.materiales.join(", ")));
    }
    if (c.script_cambiado) sec("Script").append(el("p", "fj-h2__nada", "El texto del script cambió en este paso."));

    const sAcc = sec("Acciones");
    const grid = el("div", "fj-h2__det-grid");
    grid.append(accion("⟲ Restaurar todo el documento aquí", async () => {
      if (!window.confirm(`El documento volverá al estado de ${n.sha_corto.slice(0, 7)} como un paso NUEVO de «${datos.activa}» (lo actual queda en el historial). ¿Continuar?`)) return;
      await ctl.restaurarTodo(n.sha_corto);
    }, { clase: "fj-btn" }));
    grid.append(accion("⎇ Crear rama desde aquí", async () => {
      const nombre = window.prompt("Nombre de la rama nueva (letras, números, - y _):");
      if (!nombre) return;
      await ctl.crearRamaDesde(nombre.trim(), n.sha_corto, window.confirm(`¿Cambiar ahora a «${nombre.trim()}»?`));
    }, { clase: "fj-btn" }));
    if (!(n.hitos || []).length) grid.append(accion("◆ Marcar hito", async () => {
      const nombre = window.prompt("Nombre del hito (letras, números, - y _):");
      if (!nombre) return;
      await ctl.marcarHito(nombre.trim(), n.sha_corto);
    }, { clase: "fj-btn" }));
    for (const r of n.puntas || []) if (r !== datos.activa) grid.append(accion(`↪ Cambiar a «${r}»`, () => ctl.cambiarRama(r), { clase: "fj-btn" }));
    sAcc.append(grid);
  }
}

const _vistas = new WeakMap();

/** Pinta el historial del controlador en `raiz` (el contenedor del panel). */
export function renderizarHistorial2(ctl, raiz = document.getElementById("fj-panel-historial")) {
  if (!raiz) return;
  let vista = _vistas.get(raiz);
  if (!vista) { vista = new Vista(raiz); _vistas.set(raiz, vista); }
  vista.mostrar(ctl);
}
