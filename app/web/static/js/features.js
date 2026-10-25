// Forja — rasgos de modificación (fase 24d, ADR-0029): filete, chaflán, vaciado,
// desfase de cara, matriz, simetría, dividir por plano y reparación de malla.
//
// Parte pura (sin DOM ni red): especificación, validación y construcción de la
// operación que recibe POST /design/candidates. Parte de formulario: elige la pieza,
// las aristas o caras (GET /design/referencias) y los valores, y crea un CANDIDATO
// que se revisa y aplica en «Indicaciones». Nunca muta el documento directamente.
// Los nombres de piezas vienen del usuario: solo se escriben con textContent.
import {
  obtenerDisenoDocumento, obtenerReferenciasDiseno, prepararCandidatoDiseno,
} from "./api.js?v=17";

const TOPE_MM = 1000;
const TOPE_CANTIDAD = 32;
const TOPE_REFERENCIAS = 64;
const EJES = ["x", "y", "z"];

export const RASGOS = {
  fillet: {
    titulo: "Filete", referencia: "arista", multiple: true,
    valor: { etiqueta: "Radio (mm)", defecto: 1, min: 0.01, max: TOPE_MM },
  },
  chamfer: {
    titulo: "Chaflán", referencia: "arista", multiple: true,
    valor: { etiqueta: "Longitud (mm)", defecto: 1, min: 0.01, max: TOPE_MM },
  },
  shell: {
    titulo: "Vaciar", referencia: "cara", multiple: true,
    valor: { etiqueta: "Espesor de pared (mm)", defecto: 2, min: 0.01, max: TOPE_MM },
  },
  push_pull: {
    titulo: "Desfasar cara", referencia: "cara", multiple: false,
    valor: { etiqueta: "Distancia (mm; negativa quita material)", defecto: 1, min: -TOPE_MM, max: TOPE_MM, noCero: true },
  },
  pattern: {
    titulo: "Matriz", referencia: null, multiple: false,
    valor: { etiqueta: "Separación (mm, solo lineal)", defecto: 10, min: 0.01, max: TOPE_MM },
  },
  mirror: { titulo: "Simetría", referencia: null, multiple: false },
  split: { titulo: "Dividir por plano", referencia: null, multiple: false },
  mesh_repair: { titulo: "Reparar malla", referencia: null, multiple: false },
};

const SUBTIPO = {
  LINE: "recta", CIRCLE: "circular", ELLIPSE: "elíptica", PLANE: "plana",
  CYLINDER: "cilíndrica", CONE: "cónica", SPHERE: "esférica", TORUS: "toroidal",
  BSPLINE: "de spline", BSPLINE_CURVE: "de spline",
};

const numeroEn = (valor, { etiqueta, min, max, noCero = false }) => {
  const texto = String(valor ?? "").trim().replace(",", ".");
  const n = Number(texto);
  if (!texto || !Number.isFinite(n)) throw new Error(`Introduce un número válido en ${etiqueta}`);
  if (n < min || n > max) throw new Error(`${etiqueta}: debe estar entre ${min} y ${max}`);
  if (noCero && n === 0) throw new Error(`${etiqueta}: no puede ser cero`);
  return n;
};

const entero = (valor, min, max, etiqueta) => {
  const n = numeroEn(valor, { etiqueta, min, max });
  if (!Number.isInteger(n)) throw new Error(`${etiqueta}: debe ser un número entero`);
  return n;
};

const ejeValido = (eje) => {
  if (!EJES.includes(eje)) throw new Error("Eje no válido");
  return eje;
};

const copiaHuella = (h) => ({
  tipo: h.tipo, subtipo: h.subtipo, centroide: [...h.centroide],
  direccion: [...h.direccion], medida: h.medida,
});

/**
 * Operación del contrato `Operation` (design_contracts.py) a partir del formulario.
 * Lanza Error con el motivo en español si algo no cuadra; nunca envía campos extra.
 */
export function construirOperacion(tipo, datos) {
  const spec = RASGOS[tipo];
  if (!spec) throw new Error(`Rasgo desconocido: ${tipo}`);
  if (!datos.pieza) throw new Error("Elige una pieza");
  const op = { kind: tipo, piece: datos.pieza };

  if (spec.referencia) {
    const huellas = datos.huellas ?? [];
    const nombre = spec.referencia === "arista" ? "arista" : "cara";
    if (!huellas.length) throw new Error(`Elige al menos una ${nombre}`);
    if (huellas.length > TOPE_REFERENCIAS) throw new Error(`Como máximo ${TOPE_REFERENCIAS} ${nombre}s`);
    if (!spec.multiple && huellas.length !== 1) throw new Error(`Elige exactamente una ${nombre}`);
    if (huellas.some((h) => h.tipo !== spec.referencia)) throw new Error(`Las referencias deben ser ${nombre}s`);
    op.huellas = huellas.map(copiaHuella);
  }
  if (spec.valor && tipo !== "pattern") op.valor_mm = numeroEn(datos.valor, spec.valor);

  if (tipo === "pattern") {
    op.patron = datos.patron === "circular" ? "circular" : "lineal";
    op.eje = ejeValido(datos.eje);
    op.cantidad = entero(datos.cantidad, 2, TOPE_CANTIDAD, "Cantidad de copias");
    if (op.patron === "lineal") {
      op.valor_mm = numeroEn(datos.valor, { etiqueta: "Separación (mm)", min: 0.01, max: TOPE_MM });
    } else {
      op.angulo_deg = numeroEn(datos.angulo, { etiqueta: "Ángulo total (grados)", min: 0.01, max: 360 });
    }
  }
  if (tipo === "mirror" || tipo === "split") {
    op.eje = ejeValido(datos.eje);
    op.plano_mm = numeroEn(datos.plano ?? 0, { etiqueta: "Posición del plano (mm)", min: -TOPE_MM, max: TOPE_MM });
  }
  return op;
}

/** Texto de una referencia: tipo, forma, medida y posición. */
export function etiquetaElemento(elemento) {
  const h = elemento.huella;
  const titulo = h.tipo === "arista" ? "Arista" : "Cara";
  const forma = SUBTIPO[h.subtipo] ?? String(h.subtipo ?? "").toLowerCase();
  const unidad = h.tipo === "arista" ? "mm" : "mm²";
  const medida = `${formatoMm(h.medida)} ${unidad}`;
  const [x, y, z] = h.centroide.map(formatoMm);
  return `${titulo} ${forma} · ${medida} · en (${x}, ${y}, ${z})`;
}

function formatoMm(valor) {
  return Number(valor).toLocaleString("es-ES", { maximumFractionDigits: 2 });
}

/** Piezas que el formulario ofrece: solo las de diseño, sin bloquear. */
export function piezasEditables(diseno) {
  const partes = diseno?.design?.parts ?? {};
  return (diseno?.pieces ?? [])
    .map((p) => p.name)
    .filter((nombre) => {
      const rol = partes[nombre] ?? {};
      return !rol.locked && (rol.role ?? "design") === "design";
    });
}

const API_FEATURES = { obtenerDisenoDocumento, obtenerReferenciasDiseno, prepararCandidatoDiseno };
const ID_FORMULARIO = "fj-rasgo";

/**
 * Abre el formulario del rasgo sobre la pieza elegida. Crear el candidato no
 * cambia la geometría: se revisa y aplica desde «Indicaciones».
 */
export async function abrirRasgo({
  documentoId, tipo, piezaInicial = null, documento = document,
  api = API_FEATURES, mostrar, panel,
}) {
  const spec = RASGOS[tipo];
  if (!spec) throw new Error(`Rasgo desconocido: ${tipo}`);
  const diseno = await api.obtenerDisenoDocumento(documentoId);
  const piezas = piezasEditables(diseno);
  if (!piezas.length) {
    mostrar("Este documento no tiene piezas de diseño editables.", true);
    return null;
  }
  const formulario = documento.createElement("section");
  formulario.id = ID_FORMULARIO;
  formulario.className = "fj-rasgo";
  formulario.setAttribute("role", "dialog");
  formulario.setAttribute("aria-label", spec.titulo);
  const cerrar = () => formulario.remove();

  const titulo = documento.createElement("h2");
  titulo.className = "fj-rasgo__titulo";
  titulo.textContent = spec.titulo;
  formulario.append(titulo);

  const campoPieza = campoSelect(documento, "Pieza", piezas.map((p) => [p, p]), piezaInicial && piezas.includes(piezaInicial) ? piezaInicial : piezas[0]);
  formulario.append(campoPieza.bloque);

  let referencias = [];
  let bloqueRef = null;
  const cargarReferencias = async () => {
    if (!spec.referencia) return;
    const pieza = campoPieza.control.value;
    const res = await api.obtenerReferenciasDiseno(documentoId, pieza, spec.referencia);
    referencias = res.elementos ?? [];
    if (bloqueRef) bloqueRef.remove();
    bloqueRef = documento.createElement("fieldset");
    bloqueRef.className = "fj-rasgo__refs";
    const leyenda = documento.createElement("legend");
    leyenda.textContent = spec.multiple ? `Elige ${spec.referencia}s` : `Elige una ${spec.referencia}`;
    bloqueRef.append(leyenda);
    referencias.forEach((el, i) => {
      const id = `fj-rasgo-ref-${i}`;
      const caja = documento.createElement("input");
      caja.type = spec.multiple ? "checkbox" : "radio";
      caja.name = "fj-rasgo-ref";
      caja.id = id;
      caja.value = String(i);
      const etiqueta = documento.createElement("label");
      etiqueta.htmlFor = id;
      etiqueta.textContent = etiquetaElemento(el);
      const fila = documento.createElement("div");
      fila.className = "fj-rasgo__fila";
      fila.append(caja, etiqueta);
      bloqueRef.append(fila);
    });
    if (!referencias.length) {
      const vacio = documento.createElement("p");
      vacio.className = "fj-rasgo__nota";
      vacio.textContent = `La pieza no tiene ${spec.referencia}s seleccionables.`;
      bloqueRef.append(vacio);
    }
    formulario.insertBefore(bloqueRef, campos);
  };
  const campos = documento.createElement("div");
  campos.className = "fj-rasgo__campos";
  formulario.append(campos);

  const controles = {};
  if (spec.valor) {
    controles.valor = campoTexto(documento, spec.valor.etiqueta, String(spec.valor.defecto));
    campos.append(controles.valor.bloque);
  }
  if (tipo === "pattern") {
    controles.patron = campoSelect(documento, "Tipo", [["lineal", "Lineal"], ["circular", "Circular"]], "lineal");
    controles.eje = campoSelect(documento, "Eje", EJES.map((e) => [e, e.toUpperCase()]), "x");
    controles.cantidad = campoTexto(documento, "Cantidad de copias (2 a 32)", "2");
    controles.angulo = campoTexto(documento, "Ángulo total (grados, circular)", "360");
    campos.append(controles.patron.bloque, controles.eje.bloque, controles.cantidad.bloque, controles.angulo.bloque);
  }
  if (tipo === "mirror" || tipo === "split") {
    controles.eje = campoSelect(documento, "Eje del plano normal", EJES.map((e) => [e, e.toUpperCase()]), "x");
    controles.plano = campoTexto(documento, "Posición del plano (mm)", "0");
    campos.append(controles.eje.bloque, controles.plano.bloque);
  }

  const acciones = documento.createElement("div");
  acciones.className = "fj-rasgo__acciones";
  const enviar = documento.createElement("button");
  enviar.type = "button";
  enviar.className = "fj-rasgo__enviar";
  enviar.textContent = "Preparar candidato";
  const salir = documento.createElement("button");
  salir.type = "button";
  salir.textContent = "Cerrar";
  salir.addEventListener("click", cerrar);
  acciones.append(enviar, salir);
  formulario.append(acciones);

  campoPieza.control.addEventListener("change", () => cargarReferencias().catch((e) => mostrar(e.detalle ?? e.message, true)));
  enviar.addEventListener("click", async () => {
    enviar.disabled = true;
    try {
      const seleccion = [...formulario.querySelectorAll('input[name="fj-rasgo-ref"]:checked')]
        .map((c) => referencias[Number(c.value)]?.huella)
        .filter(Boolean);
      const datos = {
        pieza: campoPieza.control.value,
        huellas: seleccion,
        valor: controles.valor?.control.value,
        patron: controles.patron?.control.value,
        eje: controles.eje?.control.value,
        cantidad: controles.cantidad?.control.value,
        angulo: controles.angulo?.control.value,
        plano: controles.plano?.control.value,
      };
      const operacion = construirOperacion(tipo, datos);
      await api.prepararCandidatoDiseno(documentoId, {
        revision: diseno.revision,
        state_revision: diseno.state_revision,
        request_id: globalThis.crypto.randomUUID(),
        operations: [operacion],
        summary: `${spec.titulo} en ${datos.pieza}`,
      });
      mostrar(`Candidato preparado: ${spec.titulo}. Revísalo y aplícalo en Indicaciones.`);
      cerrar();
      panel?.("design");
    } catch (error) {
      const detalle = error.status === 409
        ? "El documento cambió desde que abriste el formulario; ábrelo de nuevo."
        : error.detalle ?? error.message;
      mostrar(detalle, true);
    } finally {
      enviar.disabled = false;
    }
  });

  documento.body.append(formulario);
  await cargarReferencias();
  return formulario;
}

function campoTexto(documento, etiqueta, valor) {
  const bloque = documento.createElement("div");
  bloque.className = "fj-rasgo__campo";
  const id = `fj-rasgo-${Math.random().toString(36).slice(2, 8)}`;
  const label = documento.createElement("label");
  label.htmlFor = id;
  label.textContent = etiqueta;
  const control = documento.createElement("input");
  control.type = "text";
  control.inputMode = "decimal";
  control.id = id;
  control.value = valor;
  bloque.append(label, control);
  return { bloque, control };
}

function campoSelect(documento, etiqueta, opciones, valor) {
  const bloque = documento.createElement("div");
  bloque.className = "fj-rasgo__campo";
  const id = `fj-rasgo-${Math.random().toString(36).slice(2, 8)}`;
  const label = documento.createElement("label");
  label.htmlFor = id;
  label.textContent = etiqueta;
  const control = documento.createElement("select");
  control.id = id;
  for (const [valorOpcion, texto] of opciones) {
    const opcion = documento.createElement("option");
    opcion.value = valorOpcion;
    opcion.textContent = texto;
    control.append(opcion);
  }
  control.value = valor;
  bloque.append(label, control);
  return { bloque, control };
}
