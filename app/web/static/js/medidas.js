// Forja — panel «Medidas» (fase 21): pares más cercanos del documento, en vivo.
// Sin imports: ni red ni DOM a nivel de módulo, para que las pruebas lo carguen
// en Node. El documento de render se pasa como argumento.
// Los nombres de sólido son datos no confiables: se pintan con textContent,
// nunca con innerHTML.

export const TOP_MEDIDAS = 20;

const ETIQUETAS_ESTADO = { choque: "Choque", contacto: "Contacto", holgura: "Holgura" };

export function etiquetaEstado(estado) {
  return ETIQUETAS_ESTADO[estado] ?? "Desconocido";
}

export function textoDistancia(mm) {
  return `${Number(mm).toFixed(3)} mm`;
}

/**
 * Una respuesta solo se acepta si es la última pedida y, cuando ya se conoce
 * la revisión vigente, si es de esa revisión. Una respuesta de una revisión
 * anterior se descarta, nunca se pinta.
 */
export function aceptarRespuesta({ seq, seqActual, revisionEsperada, revision }) {
  if (seq !== seqActual) return false;
  if (revisionEsperada && revision !== revisionEsperada) return false;
  return true;
}

/** Líneas de cabecera: recuento, cálculo incompleto y nombres inciertos. */
export function lineasResumen(datos) {
  const lineas = [`${datos.solidos} sólidos · ${datos.choques} choque(s)`];
  if (!datos.completo) {
    lineas.push(`Cálculo incompleto: se alcanzó el tope de ${datos.tope_calculos} comprobaciones.`);
  }
  if (datos.nombres_inciertos) lineas.push("Nombres de sólido inciertos.");
  return lineas;
}

function nodo(doc, etiqueta, clase, texto) {
  const el = doc.createElement(etiqueta);
  if (clase) el.className = clase;
  if (texto !== undefined) el.textContent = texto;
  return el;
}

function pintarFila(doc, par) {
  const fila = nodo(doc, "li", "fj-medidas__fila");
  fila.append(nodo(doc, "span", "fj-medidas__par", `${par.a} ↔ ${par.b}`));
  fila.append(nodo(doc, "span", `fj-medidas__estado fj-medidas__estado--${par.estado}`, etiquetaEstado(par.estado)));
  let mm = textoDistancia(par.distancia_mm);
  if (par.estado === "choque") mm += ` · ${Number(par.volumen_mm3).toFixed(1)} mm³`;
  fila.append(nodo(doc, "span", "fj-medidas__mm", mm));
  return fila;
}

/**
 * Pinta `estado` en `contenedor`:
 *   {tipo: "cargando"} | {tipo: "error", mensaje}
 *   | {tipo: "listo", datos, obsoleto, cargando}
 * Limpia el contenedor y lo reconstruye; el texto siempre va con textContent.
 */
export function renderizarMedidas(contenedor, estado, doc = globalThis.document) {
  const hijos = [nodo(doc, "h3", "fj-medidas__titulo", "Medidas")];
  contenedor.classList.toggle("is-obsoleto", estado.tipo === "listo" && estado.obsoleto);

  if (estado.tipo === "cargando") {
    hijos.push(nodo(doc, "p", "fj-medidas__aviso", "Calculando medidas…"));
  } else if (estado.tipo === "error") {
    hijos.push(nodo(doc, "p", "fj-medidas__aviso fj-medidas__aviso--error", estado.mensaje));
  } else {
    const { datos } = estado;
    if (estado.obsoleto) {
      hijos.push(nodo(doc, "p", "fj-medidas__aviso", estado.cargando ? "Actualizando…" : "Construyendo… (cifras anteriores)"));
    }
    for (const linea of lineasResumen(datos)) hijos.push(nodo(doc, "p", "fj-medidas__resumen", linea));
    if (datos.pares.length === 0) {
      hijos.push(nodo(doc, "p", "fj-medidas__aviso", "Ningún par de sólidos está a menos de la distancia de cálculo."));
    } else {
      const lista = nodo(doc, "ul", "fj-medidas__lista");
      for (const par of datos.pares) lista.append(pintarFila(doc, par));
      hijos.push(lista);
    }
    if (datos.ocultos > 0) {
      hijos.push(nodo(doc, "p", "fj-medidas__resumen", `+${datos.ocultos} pares más`));
    }
    const parametros = Object.entries(datos.parametros ?? {});
    if (parametros.length > 0) {
      hijos.push(nodo(doc, "h4", "fj-medidas__subtitulo", "Parámetros (solo lectura)"));
      const lista = nodo(doc, "ul", "fj-medidas__parametros");
      for (const [nombre, valor] of parametros) lista.append(nodo(doc, "li", null, `${nombre} = ${valor}`));
      hijos.push(lista);
    }
  }
  contenedor.replaceChildren(...hijos);
}

class MedidasError extends Error {
  constructor(status, mensaje) {
    super(mensaje);
    this.status = status;
  }
}

async function detalleDeError(resp) {
  try {
    const cuerpo = await resp.json();
    if (typeof cuerpo.detail === "string") return cuerpo.detail;
  } catch {
    // cuerpo no era JSON: se usa el texto de estado
  }
  return resp.statusText || "error";
}

/**
 * Controla el panel de un documento. `visible()` dice si el panel muestra
 * este documento ahora; `fetchImpl` y `doc` son inyectables para las pruebas.
 */
export class ForjaMedidasControlador {
  constructor({ docId, contenedor, visible = () => true, fetchImpl = (...args) => globalThis.fetch(...args),
    doc = globalThis.document, top = TOP_MEDIDAS }) {
    this.docId = docId;
    this.contenedor = contenedor;
    this.visible = visible;
    this.fetchImpl = fetchImpl;
    this.doc = doc;
    this.top = top;
    this.seq = 0;
    this.datos = null;
    this.error = null;
    this.cargando = false;
    this.obsoleto = false;
    this.revisionObjetivo = null;
  }

  /** Pinta lo que hay y pide de nuevo si la revisión conocida ya no es la de los datos. */
  mostrar(revision) {
    if (revision) this.revisionObjetivo = revision;
    this._pintar();
    if (!this.datos || this.datos.revision !== this.revisionObjetivo) void this.cargar();
  }

  /** Llega una revisión nueva (evento del servidor o edición local). */
  nuevaRevision(revision) {
    if (!revision || revision === this.revisionObjetivo) return;
    this.revisionObjetivo = revision;
    this.obsoleto = this.datos?.revision !== revision;
    if (this.visible()) {
      this._pintar();
      void this.cargar();
    }
  }

  /** Un script se está construyendo: las cifras actuales quedan en gris. */
  marcarConstruyendo() {
    this.obsoleto = true;
    this._pintar();
  }

  async cargar() {
    const seq = ++this.seq;
    this.cargando = true;
    this._pintar();
    try {
      const url = `/documentos/${encodeURIComponent(this.docId)}/medidas?top=${this.top}`;
      const resp = await this.fetchImpl(url, { headers: { Accept: "application/json" } });
      if (!resp.ok) throw new MedidasError(resp.status, await detalleDeError(resp));
      const datos = await resp.json();
      if (!aceptarRespuesta({ seq, seqActual: this.seq, revisionEsperada: this.revisionObjetivo, revision: datos.revision })) {
        return;
      }
      this.datos = datos;
      this.error = null;
      this.obsoleto = false;
    } catch (error) {
      if (seq !== this.seq) return;
      if (error instanceof MedidasError && error.status === 400) {
        this.error = "sin desglose de sólidos";
      } else {
        this.error = `No se pudieron calcular las medidas: ${error.message}`;
      }
    } finally {
      if (seq === this.seq) this.cargando = false;
    }
    this._pintar();
  }

  _estado() {
    if (this.error) return { tipo: "error", mensaje: this.error };
    if (!this.datos) return { tipo: "cargando" };
    return { tipo: "listo", datos: this.datos, obsoleto: this.obsoleto, cargando: this.cargando };
  }

  _pintar() {
    if (!this.visible()) return;
    renderizarMedidas(this.contenedor, this._estado(), this.doc);
  }
}
