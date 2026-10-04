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

export class ForjaRamasControlador {
  constructor(docId, { alCambiar, alComparar }) {
    this.docId = docId;
    this.datos = { activa: "main", ramas: [] };
    this.pasos = [];
    this.vista = null; // rama cuyos pasos se listan (por defecto la activa)
    this.comparando = null;
    this._alCambiar = alCambiar;
    this._alComparar = alComparar;
  }

  async cargarInicial() {
    this.datos = await listarRamas(this.docId);
    if (!this.vista || !this.datos.ramas.some(r => r.nombre === this.vista)) this.vista = this.datos.activa;
    this.pasos = (await pasosDeRama(this.docId, this.vista)).pasos;
  }

  async verRama(nombre) { this.vista = nombre; this.pasos = (await pasosDeRama(this.docId, nombre)).pasos; }

  async crear(nombre, desde = null) { await crearRama(this.docId, nombre, desde); await this.cargarInicial(); }

  async cambiar(nombre) {
    const registro = await activarRama(this.docId, nombre);
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
      if (r.nombre !== "main") acciones.appendChild(boton("Borrar", async () => {
        if (!window.confirm(`¿Borrar la rama «${r.nombre}»?`)) return;
        await ctl.borrar(r.nombre); renderizarPanelRamas(ctl);
      }));
    }
    fila.append(cuerpo, acciones);
    lista.appendChild(fila);
  }
  panelRamas.appendChild(lista);

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
    mensaje.textContent = p.mensaje;
    const meta = document.createElement("div");
    meta.className = "fj-item-historial__meta";
    meta.textContent = `${p.fecha} · ${p.autor} · ${p.sha_corto}`;
    cuerpo.append(mensaje, meta);
    const acciones = document.createElement("div");
    acciones.className = "fj-item-historial__acciones";
    if (i > 0) acciones.appendChild(boton("Comparar con el último", async () => { await ctl.comparar(p.sha_corto, ctl.pasos[0].sha_corto); }));
    acciones.appendChild(boton("Rama desde aquí", async () => {
      const nombre = window.prompt("Nombre de la rama que parte de este paso:");
      if (!nombre) return;
      await ctl.crear(nombre.trim(), p.sha_corto); renderizarPanelRamas(ctl);
    }));
    fila.append(cuerpo, acciones);
    panelRamas.appendChild(fila);
  });
}
