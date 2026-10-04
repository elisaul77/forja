// Forja — panel «Ramas» (Plan G · G2) y modo «Comparar» (G3).
// Las mutaciones llevan el token de sesión, igual que restaurar.
import { obtenerTokenSesion } from "./api.js?v=11";

const panelRamas = document.getElementById("fj-panel-ramas");
const enc = encodeURIComponent;

async function pedir(url, opciones = {}) {
  const resp = await fetch(url, opciones);
  if (resp.status === 401) sessionStorage.removeItem("fj_token");
  if (!resp.ok) {
    let detalle = `${resp.status}`;
    try { const j = await resp.json(); detalle = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* sin cuerpo */ }
    throw new Error(detalle);
  }
  return resp;
}

function conToken(metodo, cuerpo) {
  const headers = { "X-Forja-Token": obtenerTokenSesion() };
  if (cuerpo !== undefined) headers["Content-Type"] = "application/json";
  return { method: metodo, headers, body: cuerpo === undefined ? undefined : JSON.stringify(cuerpo) };
}

export async function listarRamas(id) { return (await pedir(`/documentos/${enc(id)}/ramas`)).json(); }
export async function pasosDeRama(id, rama) { return (await pedir(`/documentos/${enc(id)}/ramas/${enc(rama)}/pasos?limite=50`)).json(); }
export async function crearRama(id, nombre, desde = null) { return (await pedir(`/documentos/${enc(id)}/ramas`, conToken("POST", { nombre, desde }))).json(); }
export async function activarRama(id, rama) { return (await pedir(`/documentos/${enc(id)}/ramas/${enc(rama)}/activar`, conToken("POST"))).json(); }
export async function borrarRama(id, rama) { return (await pedir(`/documentos/${enc(id)}/ramas/${enc(rama)}`, conToken("DELETE"))).json(); }
export async function compararEstados(id, a, b) { return (await pedir(`/documentos/${enc(id)}/comparar?a=${enc(a)}&b=${enc(b)}`)).json(); }
export async function mallaDeEstado(id, ref) { return (await pedir(`/documentos/${enc(id)}/comparar/malla?ref=${enc(ref)}`)).arrayBuffer(); }
// G4/G5
export async function fusionarRama(id, cuerpo) { return (await pedir(`/documentos/${enc(id)}/ramas/fusionar`, conToken("POST", cuerpo))).json(); }
export async function listarHitos(id) { return (await pedir(`/documentos/${enc(id)}/hitos`)).json(); }
export async function crearHito(id, nombre, paso, descripcion = null) { return (await pedir(`/documentos/${enc(id)}/hitos`, conToken("POST", { nombre, paso, descripcion }))).json(); }
export async function pasosCurados(id, rama, vista) { return (await pedir(`/documentos/${enc(id)}/ramas/${enc(rama)}/pasos_curados?vista=${enc(vista)}&limite=50`)).json(); }

const TEXTO_RESULTADO = {
  fusionada: "Fusión confirmada",
  simulada: "Vista previa: sin conflictos, lista para confirmar",
  conflicto: "Hay conflictos: no se puede confirmar",
  conflicto_geometrico: "Conflicto geométrico: aparecen choques nuevos",
  ya_incluida: "Esa rama ya está incluida en la activa",
};

export class ForjaRamasControlador {
  constructor(docId, { alCambiar, alComparar }) {
    this.docId = docId;
    this.datos = { activa: "main", ramas: [] };
    this.pasos = [];
    this.vista = null; // rama cuyos pasos se listan (por defecto la activa)
    this.comparando = null;
    this.avisos = []; // p. ej. «script_divergente: ...» tras cambiar de rama
    this._alCambiar = alCambiar;
    this._alComparar = alComparar;
    this.fusion = null; // {desde, piezas, plan} mientras se previsualiza una fusión
    this.eligiendo = null; // {desde, piezas: {nombre: estado}} al traer piezas
    this.soloHitos = false;
  }

  async _cargarPasos() {
    this.pasos = this.soloHitos
      ? (await pasosCurados(this.docId, this.vista, "hitos")).pasos
      : (await pasosDeRama(this.docId, this.vista)).pasos;
  }

  async cargarInicial() {
    this.datos = await listarRamas(this.docId);
    if (!this.vista || !this.datos.ramas.some(r => r.nombre === this.vista)) this.vista = this.datos.activa;
    await this._cargarPasos();
  }

  async verRama(nombre) { this.vista = nombre; await this._cargarPasos(); }

  async alternarHitos() { this.soloHitos = !this.soloHitos; await this._cargarPasos(); }

  async ponerHito(nombre, paso) { await crearHito(this.docId, nombre, paso); await this._cargarPasos(); }

  async elegirPiezas(desde) {
    const r = await compararEstados(this.docId, this.datos.activa, desde);
    this.eligiendo = { desde, piezas: r.piezas };
    this.fusion = null;
  }

  async previsualizarFusion(desde, piezas = null, estrategia = null) {
    const plan = await fusionarRama(this.docId, { desde, piezas, estrategia, simular: true });
    this.fusion = { desde, piezas, estrategia, plan };
    this.eligiendo = null;
    await this.comparar(this.datos.activa, desde);
  }

  async confirmarFusion(forzar = false) {
    const { desde, piezas, estrategia } = this.fusion;
    const plan = await fusionarRama(this.docId, { desde, piezas, estrategia, forzar });
    if (!plan.confirmada) { this.fusion = { ...this.fusion, plan }; return; }
    this.fusion = null;
    this.avisos = Array.isArray(plan.avisos) ? plan.avisos : [];
    await this.cargarInicial();
    await this._alCambiar(plan);
  }

  async crear(nombre, desde = null) { await crearRama(this.docId, nombre, desde); await this.cargarInicial(); }

  async cambiar(nombre) {
    const registro = await activarRama(this.docId, nombre);
    this.avisos = Array.isArray(registro.avisos) ? registro.avisos : [];
    this.vista = nombre;
    await this.cargarInicial();
    await this._alCambiar(registro);
  }

  async borrar(nombre) { await borrarRama(this.docId, nombre); await this.cargarInicial(); }

  async comparar(a, b) {
    const resumen = await compararEstados(this.docId, a, b);
    const [mallaA, mallaB] = await Promise.all([mallaDeEstado(this.docId, a), mallaDeEstado(this.docId, b)]);
    this.comparando = { a, b, resumen };
    await this._alComparar(resumen, mallaA, mallaB);
  }
}

function boton(texto, alClic, clase = "fj-btn fj-btn--ghost") {
  const b = document.createElement("button");
  b.type = "button"; b.className = clase; b.textContent = texto;
  b.addEventListener("click", async () => {
    b.disabled = true;
    try { await alClic(); } catch (err) { alert(`No se pudo completar: ${err.message}`); } finally { b.disabled = false; }
  });
  return b;
}

function fmt(n, d = 2) { return n === null || n === undefined ? "—" : Number(n).toFixed(d); }

export function textoResumen(r) {
  const c = r.resumen;
  const partes = [`+${c["añadida"]} añadidas`, `−${c.quitada} quitadas`, `${c.cambiada} cambiadas`, `${c.igual} iguales`];
  const vol = `Δvolumen ${r.volumen.delta >= 0 ? "+" : ""}${fmt(r.volumen.delta)} mm³` + (r.volumen.pct === null ? "" : ` (${r.volumen.pct >= 0 ? "+" : ""}${fmt(r.volumen.pct, 3)} %)`);
  const lineas = [r.sin_cambios ? "Sin cambios" : partes.join(" · "), vol];
  if (r.bbox.delta) lineas.push(`Δbbox ${r.bbox.delta.map(v => fmt(v, 3)).join(", ")} mm`);
  for (const p of r.parametros) lineas.push(`${p.nombre}: ${p.antes ?? "—"} → ${p.despues ?? "—"}`);
  for (const m of r.materiales) lineas.push(`material ${m.pieza}: ${JSON.stringify(m.antes)} → ${JSON.stringify(m.despues)}`);
  if (r.script_cambiado) lineas.push("el script cambió");
  return lineas;
}

export function renderizarPanelRamas(ctl) {
  panelRamas.textContent = "";
  const { activa, ramas } = ctl.datos;

  for (const texto of ctl.avisos) {
    const aviso = document.createElement("div");
    aviso.className = "fj-ramas__aviso";
    aviso.setAttribute("role", "alert");
    aviso.style.cssText = "border:1px solid #c98a00;background:#3a2c00;color:#ffd97a;padding:6px 8px;margin-bottom:6px;border-radius:4px;font-size:12px";
    aviso.textContent = `⚠ ${texto}`;
    panelRamas.append(aviso);
  }

  const cabecera = document.createElement("div");
  cabecera.className = "fj-ramas__cabecera";
  const etiqueta = document.createElement("label");
  etiqueta.textContent = "Rama activa ";
  const selector = document.createElement("select");
  selector.className = "fj-input";
  for (const r of ramas) {
    const op = document.createElement("option");
    op.value = r.nombre; op.textContent = `${r.nombre} (${r.pasos})`; op.selected = r.nombre === activa;
    selector.appendChild(op);
  }
  selector.addEventListener("change", async () => {
    selector.disabled = true;
    try { await ctl.cambiar(selector.value); } catch (err) { alert(`No se pudo cambiar de rama: ${err.message}`); }
    finally { selector.disabled = false; renderizarPanelRamas(ctl); }
  });
  etiqueta.appendChild(selector);
  cabecera.appendChild(etiqueta);
  cabecera.appendChild(boton("Nueva rama", async () => {
    const nombre = window.prompt("Nombre de la rama nueva (letras, números, - y _):");
    if (!nombre) return;
    await ctl.crear(nombre.trim());
    if (window.confirm(`¿Cambiar ahora a «${nombre.trim()}»?`)) await ctl.cambiar(nombre.trim());
    renderizarPanelRamas(ctl);
  }, "fj-btn"));
  panelRamas.appendChild(cabecera);

  const lista = document.createElement("div");
  lista.className = "fj-ramas__lista";
  for (const r of ramas) {
    const fila = document.createElement("div");
    fila.className = "fj-item-historial";
    const cuerpo = document.createElement("div");
    cuerpo.className = "fj-item-historial__cuerpo";
    const titulo = document.createElement("div");
    titulo.className = "fj-item-nota__comentario";
    titulo.textContent = `${r.nombre}${r.activa ? " · activa" : ""}`;
    const meta = document.createElement("div");
    meta.className = "fj-item-historial__meta";
    meta.textContent = r.ultimo ? `${r.pasos} pasos · ${r.ultimo.fecha} · ${r.ultimo.autor}` : `${r.pasos} pasos`;
    cuerpo.append(titulo, meta);
    const acciones = document.createElement("div");
    acciones.className = "fj-item-historial__acciones";
    acciones.appendChild(boton("Pasos", async () => { await ctl.verRama(r.nombre); renderizarPanelRamas(ctl); }));
    if (!r.activa) {
      acciones.appendChild(boton("Cambiar", async () => { await ctl.cambiar(r.nombre); renderizarPanelRamas(ctl); }));
      acciones.appendChild(boton("Comparar", async () => { await ctl.comparar(activa, r.nombre); }));
      acciones.appendChild(boton("Fusionar en esta rama", async () => { await ctl.previsualizarFusion(r.nombre); renderizarPanelRamas(ctl); }));
      acciones.appendChild(boton("Traer pieza…", async () => { await ctl.elegirPiezas(r.nombre); renderizarPanelRamas(ctl); }));
      if (r.nombre !== "main") acciones.appendChild(boton("Borrar", async () => {
        if (!window.confirm(`¿Borrar la rama «${r.nombre}»?`)) return;
        await ctl.borrar(r.nombre); renderizarPanelRamas(ctl);
      }));
    }
    fila.append(cuerpo, acciones);
    lista.appendChild(fila);
  }
  panelRamas.appendChild(lista);
  if (ctl.eligiendo) renderizarEleccion(ctl);
  if (ctl.fusion) renderizarFusion(ctl);

  const vistaHitos = document.createElement("label");
  vistaHitos.className = "fj-ramas__subtitulo";
  const casilla = document.createElement("input");
  casilla.type = "checkbox"; casilla.checked = ctl.soloHitos;
  casilla.addEventListener("change", async () => { await ctl.alternarHitos(); renderizarPanelRamas(ctl); });
  vistaHitos.append(casilla, " Solo hitos");
  panelRamas.appendChild(vistaHitos);

  const subtitulo = document.createElement("div");
  subtitulo.className = "fj-ramas__subtitulo";
  subtitulo.textContent = `Pasos de «${ctl.vista}» (más reciente primero; sin miniaturas todavía)`;
  panelRamas.appendChild(subtitulo);
  if (!ctl.pasos.length) {
    const vacio = document.createElement("div");
    vacio.className = "fj-panel-lateral__vacio";
    vacio.textContent = "Sin pasos todavía.";
    panelRamas.appendChild(vacio);
  }
  ctl.pasos.forEach((p, i) => {
    const fila = document.createElement("div");
    fila.className = "fj-item-historial";
    const cuerpo = document.createElement("div");
    cuerpo.className = "fj-item-historial__cuerpo";
    const mensaje = document.createElement("div");
    mensaje.className = "fj-item-nota__comentario";
    mensaje.textContent = p.hito ? `◆ ${p.hito} — ${p.mensaje}` : p.mensaje;
    const meta = document.createElement("div");
    meta.className = "fj-item-historial__meta";
    meta.textContent = `${p.fecha} · ${p.autor} · ${p.sha_corto}` + (p.agrupa > 1 ? ` · agrupa ${p.agrupa} pasos` : "");
    cuerpo.append(mensaje, meta);
    const acciones = document.createElement("div");
    acciones.className = "fj-item-historial__acciones";
    if (i > 0) acciones.appendChild(boton("Comparar con el último", async () => { await ctl.comparar(p.sha_corto, ctl.pasos[0].sha_corto); }));
    acciones.appendChild(boton("Rama desde aquí", async () => {
      const nombre = window.prompt("Nombre de la rama que parte de este paso:");
      if (!nombre) return;
      await ctl.crear(nombre.trim(), p.sha_corto); renderizarPanelRamas(ctl);
    }));
    if (!p.hito) acciones.appendChild(boton("Hito…", async () => {
      const nombre = window.prompt("Nombre del hito (letras, números, - y _):");
      if (!nombre) return;
      await ctl.ponerHito(nombre.trim(), p.sha_corto); renderizarPanelRamas(ctl);
    }));
    fila.append(cuerpo, acciones);
    panelRamas.appendChild(fila);
  });
}

const ESTADO_PIEZA = { igual: "igual", cambiada: "cambiada", "añadida": "añadida en la otra rama", quitada: "no existe en la otra rama" };

function caja(titulo) {
  const div = document.createElement("div");
  div.className = "fj-ramas__fusion";
  div.style.cssText = "border:1px solid #556;border-radius:4px;padding:6px 8px;margin:6px 0;font-size:12px";
  const h = document.createElement("div");
  h.className = "fj-item-nota__comentario";
  h.textContent = titulo;
  div.appendChild(h);
  return div;
}

function linea(texto, estilo = "") {
  const d = document.createElement("div");
  d.textContent = texto;
  if (estilo) d.style.cssText = estilo;
  return d;
}

function renderizarEleccion(ctl) {
  const { desde, piezas } = ctl.eligiendo;
  const div = caja(`Traer piezas de «${desde}» a «${ctl.datos.activa}»`);
  const marcadas = new Set();
  for (const [nombre, estado] of Object.entries(piezas)) {
    if (nombre === "documento") continue;
    const fila = document.createElement("label");
    fila.style.display = "block";
    const c = document.createElement("input");
    c.type = "checkbox"; c.disabled = estado === "igual";
    c.addEventListener("change", () => { if (c.checked) marcadas.add(nombre); else marcadas.delete(nombre); });
    fila.append(c, ` ${nombre} — ${ESTADO_PIEZA[estado] ?? estado}`);
    div.appendChild(fila);
  }
  const acciones = document.createElement("div");
  acciones.appendChild(boton("Vista previa", async () => {
    if (!marcadas.size) { alert("Marca al menos una pieza."); return; }
    await ctl.previsualizarFusion(desde, [...marcadas]); renderizarPanelRamas(ctl);
  }, "fj-btn"));
  acciones.appendChild(boton("Cancelar", async () => { ctl.eligiendo = null; renderizarPanelRamas(ctl); }));
  div.appendChild(acciones);
  panelRamas.appendChild(div);
}

function renderizarFusion(ctl) {
  const { desde, piezas, plan } = ctl.fusion;
  const que = piezas ? `Traer ${piezas.join(", ")} de «${desde}»` : `Fusionar «${desde}» en «${ctl.datos.activa}»`;
  const div = caja(que);
  const ok = plan.resultado === "simulada";
  div.appendChild(linea(TEXTO_RESULTADO[plan.resultado] ?? plan.resultado, `font-weight:600;color:${ok ? "#7fd18b" : "#ffb35c"}`));
  for (const c of plan.cambios || []) div.appendChild(linea(`• ${c}`));
  for (const c of plan.conflictos || []) {
    div.appendChild(linea(`✗ ${c.mensaje}`, "color:#ff8a80"));
    if (Array.isArray(c.lineas)) {
      const pre = document.createElement("pre");
      pre.style.cssText = "max-height:160px;overflow:auto;font-size:11px;background:#111;padding:4px";
      pre.textContent = c.lineas.join("\n");
      div.appendChild(pre);
    }
  }
  const v = plan.verificacion;
  if (v && v.comprobada) {
    div.appendChild(linea(`Verificación: sólido ${v.valido ? "válido" : "NO válido"} · ${v.choques.length} choques (activa ${v.padres.a}, otra ${v.padres.b})`));
    for (const [a, b] of v.choques_nuevos) div.appendChild(linea(`✗ choque nuevo: ${a} ↔ ${b}`, "color:#ff8a80"));
  }
  for (const a of plan.avisos || []) div.appendChild(linea(`⚠ ${a}`, "color:#ffd97a"));
  const acciones = document.createElement("div");
  if (ok) acciones.appendChild(boton("Confirmar", async () => { await ctl.confirmarFusion(false); renderizarPanelRamas(ctl); }, "fj-btn"));
  if (plan.resultado === "conflicto_geometrico") acciones.appendChild(boton("Confirmar de todos modos", async () => {
    if (!window.confirm("La fusión deja choques nuevos entre piezas. ¿Confirmar igualmente?")) return;
    await ctl.confirmarFusion(true); renderizarPanelRamas(ctl);
  }, "fj-btn"));
  if (plan.resultado === "conflicto" && !piezas) {
    acciones.appendChild(boton("Quedarme con lo mío", async () => { await ctl.previsualizarFusion(desde, null, "nuestra"); renderizarPanelRamas(ctl); }));
    acciones.appendChild(boton("Tomar lo de la otra", async () => { await ctl.previsualizarFusion(desde, null, "suya"); renderizarPanelRamas(ctl); }));
  }
  acciones.appendChild(boton("Cancelar", async () => { ctl.fusion = null; renderizarPanelRamas(ctl); }));
  div.appendChild(acciones);
  panelRamas.appendChild(div);
}
