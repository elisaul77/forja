// Forja — panel "Indicaciones" (antes "Diseño"): una sola lista para notas,
// trazos, fotos, propuestas del agente y la preparación para imprimir.
// Nada de lo que aquí se guarda se envía a una IA: queda disponible para el
// agente cuando el usuario le pide revisar Forja (contexto_diseno).
import { requestSessionToken, forgetSessionToken } from "./auth.js";
import { restaurarDocumento } from "./api.js?v=15";

const controllers = new Map();
const draftsByDocument = new Map();
const POLL_INTERVAL_MS = 2000;
const MAX_INTENT_POINTS = 256;
const MAX_PHOTOS = 6;
const MAX_PHOTO_BYTES = 6 * 1024 * 1024;
const PHOTO_TYPES = "image/png,image/jpeg,image/webp";
const DEFAULT_NOZZLE = "0.4";
const DEFAULT_BED = [220, 220, 250];
// Formas del útil y modos de alinear (24b, ADR-0026). Los tamaños se explican en la ayuda.
const PRIMITIVAS = [
  ["box", "Caja"],
  ["cylinder", "Cilindro"],
  ["sphere", "Esfera"],
  ["cone", "Cono"],
  ["torus", "Toro"],
  ["wedge", "Cuña"],
  ["tube", "Tubo"],
];
const AYUDA_FORMAS =
  "Caja: X, Y, Z · Cilindro: diámetro, reservado, altura · Esfera: diámetro (lo demás se ignora) · Cono: diámetro de la base, reservado, altura · Toro: diámetro exterior, diámetro del tubo · Cuña: X, Y, Z · Tubo: diámetro exterior, diámetro interior, altura.";
const MODOS_ALINEAR = [
  ["center", "Centros"],
  ["min", "Caras mínimas"],
  ["max", "Caras máximas"],
  ["stack", "Apilar sobre la cara superior (Z)"],
];
// Pendiente por documento desde la barra de herramientas: {operacion, pieza}.
const pendientes = new Map();
const HONEST_BANNER =
  "Forja guarda tus indicaciones para el agente. No se envían solas: el agente las lee cuando le pidas en tu chat que revise Forja.";

const labels = {
  design: "Diseño",
  reference: "Referencia",
  reserved: "Espacio reservado",
};
const printRoleLabels = {
  design: "Se imprime",
  reference: "Solo referencia",
  reserved: "Espacio reservado",
};
const statusLabels = {
  pass: "Cumple",
  fail: "No cumple",
  not_checked: "Sin comprobar",
  prepared: "Preparado",
  applied: "Aplicado",
  reverted: "Deshecho",
};
const attentionTexts = {
  waiting_agent: "Esperando al agente",
  question: "El agente pregunta",
  understood: "Entendida · sin propuesta aún",
  proposal: "Propuesta lista para revisar",
  applied: "Cambio aplicado · ¿lo resuelve?",
  reverted: "Cambio deshecho",
  resolved: "Resuelta",
  dismissed: "Descartada",
};

// ---------------- Funciones puras (probadas en tests/web) ----------------

/** Estado por pieza para colorear la vista antes/después de una propuesta.
 * La vista previa es una malla única («documento»): se pinta como cambiada. */
export function proposalStates(candidate) {
  const d = candidate?.difference ?? {};
  const states = { documento: "cambiada" };
  for (const c of d.changed ?? []) states[c.piece ?? c] = "cambiada";
  for (const a of d.added ?? []) states[a.piece ?? a] = "añadida";
  for (const r of d.removed ?? []) states[r.piece ?? r] = "quitada";
  return states;
}
/** Líneas breves para la banda de comparación de una propuesta. */
export function proposalLines(candidate) {
  const lines = [];
  if (candidate?.summary) lines.push(candidate.summary);
  for (const c of candidate?.difference?.changed ?? []) {
    const b = c.before?.dimensions_mm, a = c.after?.dimensions_mm;
    if (b && a) lines.push(`${c.piece}: ${b.map((v) => +v.toFixed(2)).join("×")} → ${a.map((v) => +v.toFixed(2)).join("×")} mm`);
  }
  return lines;
}

// The viewer uses a synthetic label for legacy ungrouped STL documents.
// Resolve it only when there is one unambiguous design part.
/** Operación de candidato desde «Preparar una alternativa manual». Envía solo los
 * campos que usa cada tipo: el servidor rechaza los que no aplican (extra=forbid). */
export function construirOperacion(v) {
  const base = { kind: v.tipo, piece: v.pieza };
  if (v.tipo === "transform")
    return { ...base, translation: vector(v.translation), rotation_deg: vector(v.rotation), origin: vector(v.center) };
  if (v.tipo === "scale") return { ...base, factor: vector(v.factor) };
  if (v.tipo === "drop_to_bed") return { ...base, normal: vector(v.normal) };
  if (v.tipo === "align") {
    const operacion = { ...base, other: piezaAuxiliar(v.otra), mode: v.modo };
    if (v.modo !== "stack") operacion.axes = ejesAlinear(v.ejes);
    return operacion;
  }
  if (v.tipo === "deform_region")
    return { ...base, translation: vector(v.translation), center: vector(v.center), radius_mm: Number(v.radius) };
  if (v.cuerpo === "piece") return { ...base, other: piezaAuxiliar(v.otra) };
  return {
    ...base,
    primitive: v.primitiva,
    size: vector(v.size),
    center: vector(v.center),
    rotation_deg: vector(v.rotation),
  };
}
function piezaAuxiliar(nombre) {
  if (!nombre) throw new Error("Elige la otra pieza del documento.");
  return nombre;
}
function ejesAlinear(texto) {
  const ejes = String(texto).toLowerCase().split(/[;,\s]+/).filter(Boolean);
  if (!ejes.length || ejes.some((e) => !["x", "y", "z"].includes(e)))
    throw new Error("Los ejes para alinear deben ser x, y o z.");
  return [...new Set(ejes)];
}

/** Abre «Preparar una alternativa manual» con `preset` = {tipo, primitiva?}. Se aplica al
 * siguiente render del panel (lo llama toolbar-ui.js tras abrir Indicaciones). */
export function prepararOperacion(entry, preset) {
  const id = entry.ficha.id;
  pendientes.set(id, { ...(pendientes.get(id) ?? {}), operacion: preset });
  const c = controllers.get(id);
  if (!c) return;
  c.openDetails.add("advanced");
  c.openDetails.add("alternative");
  if (c.data) c.render();
}
/** Pide una indicación ligada a la pieza seleccionada («Pedir cambio con lo seleccionado»). */
export function pedirCambioPieza(entry, pieza) {
  const id = entry.ficha.id;
  pendientes.set(id, { ...(pendientes.get(id) ?? {}), pieza: pieza ?? null });
  const c = controllers.get(id);
  if (c?.data) c.render();
}

export function resolveDesignPiece(pieces, name) {
  if (pieces.some((piece) => piece.name === name)) return name;
  return pieces.length === 1 ? pieces[0].name : null;
}
export function vector(text) {
  const v = text
    .trim()
    .split(/[;,\s]+/)
    .map(Number);
  if (
    v.length !== 3 ||
    v.some((x) => !Number.isFinite(x) || Math.abs(x) > 100000)
  )
    throw new Error(
      "Introduce tres números finitos separados por comas (máximo ±100000).",
    );
  return v;
}

/** Texto plano y breve para la atención de una indicación. Un servidor
 * antiguo (sin `attention`) se interpreta desde los campos del propio intent. */
// Un valor desconocido (servidor más nuevo) cae en una etiqueta neutra.
export function attentionLabel(attention) {
  return attentionTexts[attention] ?? "Estado sin clasificar";
}
/** Mensajes del registro sin el duplicado de la interpretación del agente. */
export function visibleMessages(intent) {
  const interpretation = String(intent?.interpretation ?? "").trim();
  return (intent?.messages ?? []).filter(
    (m) => !(m.author === "agent" && interpretation && String(m.text ?? "").trim() === interpretation),
  );
}
export function attentionOf(intent, view) {
  if (view?.attention && attentionTexts[view.attention]) return view.attention;
  if (intent?.status === "dismissed") return "dismissed";
  if (intent?.status === "resolved") return "resolved";
  if (intent?.question) return "question";
  if (intent?.interpretation) return "understood";
  return "waiting_agent";
}
export function isOpenAttention(attention) {
  return attention !== "resolved" && attention !== "dismissed";
}
export function pluralize(count, singular, plural) {
  return `${count} ${count === 1 ? singular : plural}`;
}
export function shortText(text, max = 40) {
  const clean = String(text ?? "").trim();
  return clean.length > max ? `${clean.slice(0, max - 1)}…` : clean;
}
/** Resumen en palabras de las comprobaciones de una propuesta. */
export function checkSummary(checks) {
  const failed = checks?.failed ?? 0;
  const pending = checks?.not_checked ?? 0;
  const passed = checks?.passed ?? 0;
  const parts = [];
  if (failed)
    parts.push(
      pluralize(
        failed,
        "comprobación no se cumple",
        "comprobaciones no se cumplen",
      ),
    );
  if (pending) parts.push(`${pending} sin comprobar`);
  if (parts.length) return parts.join(" · ");
  return passed
    ? "Todas las comprobaciones pasan"
    : "Sin comprobaciones definidas";
}
/** Una propuesta preparada sobre otra revisión o estado no debe mostrar cifras viejas. */
export function isProposalStale(candidate, current) {
  if (!candidate || !current || candidate.state !== "prepared") return false;
  // El servidor decide la obsolescencia (constraints_revision); el cliente
  // solo compara revisiones si el campo `stale` no viene (servidor antiguo).
  if (typeof candidate.stale === "boolean") return candidate.stale;
  const revisionDiffers =
    candidate.revision != null && candidate.revision !== current.revision;
  const stateDiffers =
    candidate.state_revision != null &&
    candidate.state_revision !== current.state_revision;
  return revisionDiffers || stateDiffers;
}
const isPoint = (p) =>
  Array.isArray(p) && p.length === 3 && p.every((v) => Number.isFinite(v));
/** Puntos de una nota (`referencia.punto`) o de un trazo (`puntos`), copiados. */
export function markPoints(mark) {
  if (Array.isArray(mark?.puntos))
    return mark.puntos.filter(isPoint).map((p) => [...p]);
  return isPoint(mark?.referencia?.punto) ? [[...mark.referencia.punto]] : [];
}
export function collectMarkPoints(marks) {
  return (marks ?? []).flatMap(markPoints);
}
/** Como mucho `max` puntos, repartidos desde el primero hasta el último. */
export function subsamplePoints(points, max = MAX_INTENT_POINTS) {
  const list = (points ?? []).filter(isPoint);
  if (list.length <= max) return list.map((p) => [...p]);
  if (max < 2) return [[...list[0]]];
  const out = [];
  for (let i = 0; i < max; i++)
    out.push([...list[Math.round((i * (list.length - 1)) / (max - 1))]]);
  return out;
}
/** Marcas (notas/trazos) que ninguna indicación enlaza todavía. */
export function looseMarks(marks, intents) {
  const linked = new Set((intents ?? []).flatMap((i) => i.marks ?? []));
  return (marks ?? []).filter((m) => !linked.has(m.n));
}
/** `notas` y `trazos` de la pestaña tienen rutas distintas en la UI: la
 * pestaña «Notas» y el botón «Notas e historial» abren este mismo panel. */
export function panelSectionFor(section) {
  if (section === "notas") return "design";
  if (section === "ramas") return "historial";
  return section;
}
/** Borradores por documento. Un valor vacío guardado sigue siendo un borrador. */
export function createDraftStore(initial = {}) {
  const values = new Map(Object.entries(initial));
  return {
    read(key, fallback = "") {
      return values.has(key) ? values.get(key) : fallback;
    },
    write(key, value) {
      values.set(key, value ?? "");
    },
    forget(key) {
      values.delete(key);
    },
    has(key) {
      return values.has(key);
    },
  };
}
function draftsFor(docId) {
  let store = draftsByDocument.get(docId);
  if (!store) {
    store = createDraftStore();
    draftsByDocument.set(docId, store);
  }
  return store;
}

// ---------------- Llamadas HTTP ----------------

function el(tag, text, attrs = {}) {
  const n = document.createElement(tag);
  if (text != null) n.textContent = text;
  Object.assign(n, attrs);
  return n;
}
/** `token` por defecto solo en métodos que no son GET (ADR-0005). Las
 * indicaciones del usuario pasan `{ token: false }`: explicar algo no cambia
 * la geometría ni el estado protegido. */
async function request(path, method = "GET", body, { token = method !== "GET", reason = "" } = {}) {
  const headers = {};
  if (body) headers["Content-Type"] = "application/json";
  if (token) {
    const value = await requestSessionToken(reason || "Esta acción necesita la clave de Forja");
    if (!value) throw new Error("Edición cancelada");
    headers["X-Forja-Token"] = value;
  }
  const response = await fetch(path, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    if (response.status === 401) forgetSessionToken();
    let data;
    try {
      data = await response.json();
    } catch {}
    // El estado HTTP viaja con el error: quien llama distingue un rechazo (4xx) de un fallo.
    const error = new Error(
      typeof data?.detail === "string"
        ? data.detail
        : `No se pudo completar (${response.status})`,
    );
    error.status = response.status;
    throw error;
  }
  return response.json();
}
const docPath = (docId) => `/documentos/${encodeURIComponent(docId)}/design`;

/** Crea una indicación: `{text, marks?, piece?, points?, images?}`. Devuelve el read() completo. */
export function createIntent(docId, body) {
  return request(`${docPath(docId)}/intents`, "POST", body, { token: false });
}
/** Acción del usuario sobre una indicación: reply | attach | resolve | reopen | dismiss | edit. */
export function sendIntentAction(docId, intentId, body) {
  return request(
    `${docPath(docId)}/intents/${encodeURIComponent(intentId)}/user`,
    "POST",
    body,
    { token: false },
  );
}
/** Piezas del documento abierto (vacío hasta que el panel se haya cargado). */
export function designPiecesOf(docId) {
  return controllers.get(docId)?.data?.pieces ?? [];
}
/** Vuelve a leer el panel si ya existe (p. ej. tras añadir una marca). */
export function refreshDesign(entry) {
  const controller = controllers.get(entry.ficha.id);
  if (controller?.data) void controller.load();
}

// ---------------- Pequeños constructores de DOM ----------------

function button(parent, label, fn) {
  const b = el("button", label, { type: "button", className: "fj-btn" });
  b.onclick = fn;
  parent.append(b);
  return b;
}
function options(parent, label, values, current) {
  const s = el("label", label);
  const select = el("select");
  for (const [value, text] of values) select.append(el("option", text, { value }));
  if (current != null) select.value = current;
  s.append(select);
  parent.append(s);
  return select;
}
function section(parent, title) {
  const s = el("section", null, { className: "fj-design__seccion" });
  s.append(el("h4", title));
  parent.append(s);
  return s;
}
function listChecks(parent, checks) {
  for (const r of checks?.requirements ?? [])
    parent.append(
      el(
        "p",
        `${statusLabels[r.status] ?? r.status} · ${r.description || r.id}${r.reason ? ` · ${r.reason}` : ""}`,
      ),
    );
}
function showEvidence(parent, data) {
  if (data?.requirements)
    for (const r of data.requirements)
      parent.append(
        el(
          "p",
          `${statusLabels[r.status]} · ${r.description || r.id}${r.measured_mm != null ? ` · ${r.measured_mm.toFixed(3)} mm` : ""}${r.reason ? ` · ${r.reason}` : ""}`,
        ),
      );
  else if (data?.parts)
    for (const [name, p] of Object.entries(data.parts))
      parent.append(
        el(
          "p",
          `${name}: ${Object.keys(p.issues || {}).length ? Object.keys(p.issues).join(", ") : "Sin incidencias detectadas"} · ${(p.dimensions_mm || []).map((x) => x.toFixed(2)).join(" × ")} mm`,
        ),
      );
  else if (data?.distance_mm != null)
    parent.append(
      el(
        "p",
        `Separación: ${data.distance_mm.toFixed(3)} mm. ${data.interference ? "Interferencia: las piezas comparten volumen." : data.contact ? "Se tocan sin penetrar." : ""} ${data.note || ""}`,
      ),
    );
  else if (data?.max_error_mm != null)
    parent.append(
      el(
        "p",
        `Error máximo de alineación: ${data.max_error_mm.toFixed(3)} mm. RMS: ${data.rms_mm.toFixed(3)} mm.`,
      ),
    );
  else if (data?.radius_mm != null)
    parent.append(
      el(
        "p",
        `Radio: ${data.radius_mm.toFixed(3)} mm · error radial RMS ${data.radial_rms_mm.toFixed(4)} mm.`,
      ),
    );
  else if (data?.plane_rms_mm != null)
    parent.append(
      el("p", `Plano estimado · error RMS ${data.plane_rms_mm.toFixed(4)} mm.`),
    );
  else if (data?.dimensions_mm)
    parent.append(
      el(
        "p",
        `Dimensiones: ${data.dimensions_mm.map((x) => x.toFixed(3)).join(" × ")} mm · ${data.closed ? "malla cerrada" : "malla abierta"} · ${data.bodies} cuerpos.`,
      ),
    );
  else if (data?.polylines)
    parent.append(
      el(
        "p",
        `Corte: ${data.polylines.length} contornos${data.more ? ` y ${data.more} adicionales` : ""}.`,
      ),
    );
  if (data?.limitations)
    for (const note of data.limitations) parent.append(el("p", note));
  const details = el("details"),
    title = el("summary", "Detalle de la medición");
  details.append(title, el("pre", JSON.stringify(data, null, 2)));
  parent.append(details);
}

// ---------------- Controlador por documento ----------------

class Workspace {
  constructor(entry, root) {
    this.entry = entry;
    this.root = root;
    this.base = docPath(entry.ficha.id);
    this.drafts = draftsFor(entry.ficha.id);
    this.busy = false;
    this.pending = false;
    this.data = null;
    this.candidates = [];
    this.views = new Map();
    this.filter = "open";
    this.openDetails = new Set();
    this.notice = null;
    this.message = null;
    this.outputs = {};
    this.outputZones = {};
    this.photos = [];
    this.points = [];
    this.capturing = false;
    this.target = [];
    this.alignment = null;
    this.printPiece = null;
    this.workPiece = null;
    this.pointCount = null;
    this.previewId = null;
    this.pollTimer = null;
  }

  get pieces() {
    return this.data?.pieces ?? [];
  }
  get intents() {
    return this.data?.design?.intents ?? [];
  }
  get marks() {
    const notes = this.entry.notas;
    return [...(notes?.notas ?? []), ...(notes?.trazos ?? [])];
  }
  markById(id) {
    return this.marks.find((m) => m.n === id);
  }
  viewOf(intent) {
    return this.views.get(intent.id);
  }
  marksOf(intent, view) {
    if (view?.marks_detail)
      return view.marks_detail.map((m) =>
        Array.isArray(m.puntos) ? m : (this.markById(m.n) ?? m),
      );
    return (intent.marks ?? []).map((id) => this.markById(id)).filter(Boolean);
  }
  workPieceDefault() {
    return (
      resolveDesignPiece(this.pieces, this.entry.viewer.selectedPiece) ||
      this.pieces[0]?.name ||
      ""
    );
  }

  stop() {
    clearTimeout(this.pollTimer);
    this.pollTimer = null;
  }

  // Lectura y escritura del estado

  async load() {
    if (this.busy) {
      this.pending = true;
      return;
    }
    this.busy = true;
    try {
      await this._read();
      if (this.root.dataset.designDocument === this.entry.ficha.id) this.render();
    } catch (error) {
      if (this.root.dataset.designDocument === this.entry.ficha.id)
        this.root.replaceChildren(el("p", error.message));
    } finally {
      this.busy = false;
    }
    if (this.pending) {
      this.pending = false;
      await this.load();
    }
  }
  async _read() {
    this.data = await request(this.base);
    this.candidates = (await request(this.base + "/candidates")).candidates ?? [];
    this.views = new Map((this.data.intents_view ?? []).map((v) => [v.id, v]));
  }
  /** Guarda el bloque `design` completo (PUT con revisiones). */
  async saveDesign(reason = "Guardar cambios en el diseño") {
    this.data = await request(this.base, "PUT", {
      revision: this.data.revision,
      state_revision: this.data.state_revision,
      design: this.data.design,
    }, { reason });
  }
  /** Ejecuta una acción que puede cambiar el servidor y vuelve a leer el panel. */
  async act(task) {
    if (this.busy) {
      this.notify("Espera: la acción anterior todavía no termina.", true);
      return;
    }
    this.busy = true;
    try {
      await task();
    } catch (error) {
      this.notify(error.message, true);
    } finally {
      this.busy = false;
    }
    await this.load();
  }
  notify(text, error = false) {
    this.notice = { text, error };
    if (this.message) {
      this.message.textContent = text;
      this.message.classList.toggle("is-error", error);
    }
  }
  async _query(query, background = false) {
    const result = await request(this.base + "/query", "POST", {
      revision: this.data.revision,
      queries: [query],
      background,
    }, { reason: "Calcular esta comprobación necesita la clave" });
    if (background) return result;
    const row = result.results[0];
    if (row.error) throw new Error(row.error);
    return row.result;
  }
  async _uploadPhoto(file) {
    if (file.size > MAX_PHOTO_BYTES) throw new Error("La foto supera 6 MB");
    const token = await requestSessionToken("Adjuntar una foto");
    if (!token) throw new Error("Carga cancelada");
    const body = new FormData();
    body.append("file", file);
    const response = await fetch(this.base + "/images", {
      method: "POST",
      headers: { "X-Forja-Token": token },
      body,
    });
    if (response.status === 401) forgetSessionToken();
    if (!response.ok) throw new Error("No se pudo adjuntar la foto");
    return (await response.json()).id;
  }
  _pickPhoto(handler) {
    const input = el("input", null, { type: "file", accept: PHOTO_TYPES, hidden: true });
    this.root.append(input);
    input.addEventListener("change", () => {
      const file = input.files?.[0];
      input.remove();
      if (file) void handler(file);
    });
    input.click();
  }
  _intentAction(intentId, body, message) {
    return this.act(async () => {
      await sendIntentAction(this.entry.ficha.id, intentId, body);
      this.notify(message);
    });
  }
  _trigger(name) {
    const actions = this.entry.designActions;
    if (!actions?.[name]) {
      this.notify("Abre el documento en el visor para señalar.", true);
      return false;
    }
    actions[name]();
    return true;
  }
  _focusReply(intentId) {
    const key = "reply:" + intentId;
    const target = [...this.root.querySelectorAll("[data-draft-key]")].find(
      (e) => e.dataset.draftKey === key,
    );
    target?.focus();
    this.notify("Escribe el ajuste o la respuesta y pulsa Responder.");
  }
  _showMarks(marks) {
    const points = collectMarkPoints(marks);
    if (!points.length) {
      this.notify("Estas marcas no tienen posición en el modelo.", true);
      return;
    }
    this.entry.viewer.showDesignPoints?.(points);
    this.entry.viewer.focusDesignPoints?.(points);
    this.notify(`Marcas mostradas en el modelo (${points.length} puntos).`);
  }

  // Render

  /** Renderiza el panel completo. Los borradores, el foco, los <details>
   * abiertos y el análisis de impresión sobreviven a cada render. */
  render() {
    const root = this.root;
    const focus = this._captureFocus();
    root.replaceChildren();
    root.classList.add("fj-design");
    root.append(el("h3", "Indicaciones"));
    this.message = el("p", this.notice?.text ?? "Lo que escribes o señalas queda guardado para el agente.", {
      className: "fj-design__estado" + (this.notice?.error ? " is-error" : ""),
    });
    this.message.setAttribute("aria-live", "polite");
    root.append(this.message);
    root.append(el("p", HONEST_BANNER, { className: "fj-design__aviso-honesto" }));
    this._renderExplain(root);
    this._renderReview(root);
    this._renderLooseMarks(root);
    this._renderObjective(root);
    this._renderPrinting(root);
    this._renderAdvanced(root);
    this._restoreFocus(focus);
    this._resumePolling();
  }
  _captureFocus() {
    const active = document.activeElement;
    if (!active || !this.root.contains(active) || !active.dataset?.draftKey) return null;
    return { key: active.dataset.draftKey, start: active.selectionStart, end: active.selectionEnd };
  }
  _restoreFocus(saved) {
    if (!saved) return;
    const target = [...this.root.querySelectorAll("[data-draft-key]")].find(
      (e) => e.dataset.draftKey === saved.key,
    );
    if (!target) return;
    target.focus();
    if (saved.start != null && target.setSelectionRange)
      target.setSelectionRange(saved.start, saved.end);
  }
  /** Campo de texto cuyo valor vive en el mapa de borradores del documento. */
  draftField(parent, label, key, fallback = "", tag = "input") {
    const wrap = el("label", label);
    const input = el(tag, null, {
      value: this.drafts.read(key, fallback),
      className: tag === "textarea" ? "fj-textarea" : "fj-input",
    });
    if (tag === "textarea") input.rows = 3;
    input.dataset.draftKey = key;
    input.addEventListener("input", () => this.drafts.write(key, input.value));
    wrap.append(input);
    parent.append(wrap);
    return input;
  }
  /** `<details>` que recuerda si estaba abierto entre renders. */
  details(parent, key, title) {
    const d = el("details", null, { className: "fj-design__detalles" });
    d.dataset.detalle = key;
    d.open = this.openDetails.has(key);
    d.addEventListener("toggle", () => {
      if (d.open) this.openDetails.add(key);
      else this.openDetails.delete(key);
    });
    d.append(el("summary", title));
    parent.append(d);
    return d;
  }

  /** Objetivo compacto y plegable: el trabajo principal está arriba. */
  _renderObjective(root) {
    const objective = this.data.design.objective ?? "";
    const d = this.details(root, "objective", `Objetivo · ${shortText(objective, 60) || "sin definir"}`);
    const text = this.draftField(d, "Qué queremos conseguir", "objective", objective, "textarea");
    text.maxLength = 8000;
    button(d, "Guardar objetivo", () =>
      this.act(async () => {
        this.data.design.objective = text.value;
        await this.saveDesign("Guardar el objetivo");
        this.drafts.forget("objective");
        this.notify("Objetivo guardado.");
      }),
    );
  }

  _renderExplain(root) {
    const s = section(root, "Explicar lo que quiero");
    s.append(
      el("p", "Señala un punto o una zona del modelo, o escribe lo que quieres cambiar. Cada indicación queda guardada para el agente.", {
        className: "fj-design__ayuda",
      }),
    );
    const row = el("div", null, { className: "fj-design__acciones" });
    button(row, "Señalar un punto", () => {
      if (this._trigger("pointMode")) this.notify("Haz clic en el modelo para marcar el punto.");
    });
    button(row, "Dibujar sobre la pieza", () => {
      if (this._trigger("drawMode")) this.notify("Elige el plano y dibuja; el trazo queda como marca de la indicación.");
    });
    s.append(row);
    const pieza = pendientes.get(this.entry.ficha.id)?.pieza ?? null;
    if (pieza) {
      s.append(el("p", `Esta indicación es sobre la pieza «${pieza}».`, { className: "fj-design__ayuda" }));
      button(s, "Quitar la pieza de la indicación", () => {
        const pendiente = pendientes.get(this.entry.ficha.id);
        if (pendiente) pendiente.pieza = null;
        this.render();
      });
    }
    const text = this.draftField(s, "Escribir sin marcar", "new", "", "textarea");
    text.maxLength = 4000;
    const photos = el("div", null, { className: "fj-design__acciones" });
    button(photos, "Adjuntar foto", () =>
      this._pickPhoto((file) =>
        this.act(async () => {
          if (this.photos.length >= MAX_PHOTOS) throw new Error("Máximo seis fotos por indicación");
          this.photos.push(await this._uploadPhoto(file));
          this.notify(`${pluralize(this.photos.length, "foto preparada", "fotos preparadas")} para la indicación.`);
        }),
      ),
    );
    if (this.photos.length)
      photos.append(el("span", pluralize(this.photos.length, "foto preparada", "fotos preparadas"), { className: "fj-design__ayuda" }));
    s.append(photos);
    const save = button(s, "Guardar indicación", () =>
      this.act(async () => {
        const value = text.value.trim();
        if (!value) throw new Error("Escribe la indicación");
        await createIntent(this.entry.ficha.id, {
          text: value,
          ...(pieza ? { piece: pieza } : {}),
          ...(this.photos.length ? { images: [...this.photos] } : {}),
        });
        const pendiente = pendientes.get(this.entry.ficha.id);
        if (pendiente) pendiente.pieza = null;
        this.photos = [];
        this.drafts.forget("new");
        this.notify("Indicación guardada. El agente la leerá cuando le pidas revisar Forja.");
      }),
    );
    save.classList.add("fj-btn--primary");
  }

  _renderReview(root) {
    const s = section(root, "Revisar lo que se entendió");
    const filters = el("div", null, { className: "fj-design__acciones" });
    filters.setAttribute("role", "group");
    filters.setAttribute("aria-label", "Filtro de indicaciones");
    for (const [key, text] of [["open", "Abiertas"], ["all", "Todas"]]) {
      const b = button(filters, text, () => {
        this.filter = key;
        this.render();
      });
      b.setAttribute("aria-pressed", String(this.filter === key));
      b.classList.toggle("is-active", this.filter === key);
    }
    s.append(filters);
    const targetId = this.entry.notas?.targetIntent;
    if (targetId) {
      const target = this.intents.find((i) => i.id === targetId);
      const box = el("div", null, { className: "fj-design__destino" });
      box.append(el("p", `La próxima marca del modelo se añadirá a «${shortText(target?.text ?? "", 60)}».`));
      button(box, "Cancelar", () => {
        this.entry.notas.targetIntent = null;
        this.render();
      });
      s.append(box);
    }
    if (!this.intents.length)
      s.append(el("p", "Todavía no hay indicaciones. Señala un punto o escribe lo que quieres cambiar.", { className: "fj-design__ayuda" }));
    const shown = this.intents.filter(
      (i) => this.filter === "all" || isOpenAttention(attentionOf(i, this.viewOf(i))),
    );
    if (this.intents.length && !shown.length)
      s.append(el("p", "No hay indicaciones abiertas. Pulsa Todas para verlas.", { className: "fj-design__ayuda" }));
    for (const intent of shown) s.append(this._card(intent));
  }

  _card(intent) {
    const view = this.viewOf(intent);
    const attention = attentionOf(intent, view);
    const card = el("article", null, { className: "fj-design-card" });
    card.dataset.intentId = intent.id;
    card.dataset.attention = attention;
    card.append(el("span", attentionLabel(attention), { className: `fj-chip fj-chip--${attention}` }));
    card.append(el("p", intent.text, { className: "fj-design-card__texto" }));

    const linked = (intent.marks ?? []).length;
    const marks = this.marksOf(intent, view);
    const missing = view?.missing_marks ?? (intent.marks ?? []).filter((id) => !this.markById(id));
    const stale = view?.stale ?? Boolean(this.data.stale_intents?.includes(intent.id));
    if (linked) {
      const row = el("div", null, { className: "fj-design__acciones" });
      row.append(el("span", pluralize(linked, "marca", "marcas")));
      button(row, "Ver en el modelo", () => this._showMarks(marks));
      card.append(row);
    }
    if (missing.length)
      card.append(el("p", pluralize(missing.length, "marca borrada", "marcas borradas"), { className: "fj-design-aviso" }));
    if (stale)
      card.append(el("p", "La pieza cambió desde que la señalaste; revisa las marcas.", { className: "fj-design-aviso" }));
    if ((intent.images ?? []).length) {
      const gallery = el("div", null, { className: "fj-design-card__fotos" });
      for (const image of intent.images)
        gallery.append(
          el("img", null, {
            src: this.base + "/images/" + encodeURIComponent(image),
            alt: "Foto adjunta a la indicación",
            loading: "lazy",
            className: "fj-design-photo",
          }),
        );
      card.append(gallery);
    }
    if (intent.interpretation) {
      card.append(el("h5", "Lo que entendió el agente"));
      card.append(el("p", intent.interpretation));
    }
    if (intent.question)
      card.append(el("p", `El agente pregunta: ${intent.question}`, { className: "fj-design-pregunta" }));
    if (visibleMessages(intent).length) {
      const log = el("ol", null, { className: "fj-design-mensajes" });
      for (const m of visibleMessages(intent)) {
        const item = el("li", `${m.author === "user" ? "Tú" : "Agente"}: ${m.text}`);
        item.className = `is-${m.author}`;
        log.append(item);
      }
      card.append(log);
    }

    if (intent.status !== "dismissed") {
      const replyKey = "reply:" + intent.id;
      const reply = this.draftField(card, "Responder o pedir un ajuste", replyKey, "", "textarea");
      reply.maxLength = 4000;
      button(card, "Responder", () =>
        this.act(async () => {
          const value = reply.value.trim();
          if (!value) throw new Error("Escribe la respuesta");
          await sendIntentAction(this.entry.ficha.id, intent.id, { action: "reply", text: value });
          this.drafts.forget(replyKey);
          this.notify("Respuesta guardada para el agente.");
        }),
      );
    }
    for (const candidate of view?.candidates ?? []) card.append(this._proposal(intent, candidate));

    const actions = el("div", null, { className: "fj-design__acciones" });
    if (intent.status !== "dismissed") {
      button(actions, "Añadir otra marca", () => {
        if (!this.entry.notas) return;
        this.entry.notas.targetIntent = intent.id;
        const armed = this._trigger("pointMode");
        this.render();
        if (armed) this.notify("Marca un punto en el modelo; se añadirá a esta indicación.");
      });
      button(actions, "Adjuntar foto", () =>
        this._pickPhoto((file) =>
          this.act(async () => {
            if ((intent.images ?? []).length >= MAX_PHOTOS) throw new Error("Máximo seis fotos por indicación");
            const image = await this._uploadPhoto(file);
            await sendIntentAction(this.entry.ficha.id, intent.id, { action: "attach", images: [image] });
            this.notify("Foto añadida a la indicación.");
          }),
        ),
      );
      button(actions, "Descartar indicación", () =>
        this._intentAction(intent.id, { action: "dismiss" }, "Indicación descartada. Puedes reabrirla."),
      );
    }
    if (attention === "dismissed" || attention === "resolved")
      button(actions, "Reabrir", () =>
        this._intentAction(intent.id, { action: "reopen" }, "Indicación reabierta."),
      );
    card.append(actions);
    return card;
  }

  _proposal(intent, candidate) {
    const full = this.candidates.find((c) => c.id === candidate.id) ?? {};
    const merged = { ...full, ...candidate };
    const stale = isProposalStale(merged, this.data);
    const applied = merged.state === "applied";
    const reverted = merged.state === "reverted";
    const box = el("div", null, { className: "fj-design-propuesta" });
    box.append(el("h5", applied ? "Propuesta aplicada" : reverted ? "Propuesta deshecha" : "Propuesta preparada"));
    box.append(el("p", merged.summary || "Propuesta sin descripción"));
    if (stale)
      box.append(el("p", "La pieza o las condiciones cambiaron; vuelve a prepararla.", { className: "fj-design-aviso" }));
    else {
      box.append(el("p", checkSummary(merged.checks)));
      if ((merged.checks?.requirements ?? []).length)
        listChecks(this.details(box, "checks:" + candidate.id, "Ver comprobaciones"), merged.checks);
    }
    const row = el("div", null, { className: "fj-design__acciones" });
    if (reverted) {
      box.append(el("p", "El cambio se deshizo con el historial. Puedes pedir un ajuste o esperar la siguiente propuesta.", { className: "fj-design__ayuda" }));
      button(row, "Pedir un ajuste", () => this._focusReply(intent.id));
    } else if (!applied) {
      const showing = this.previewId === candidate.id;
      button(row, showing ? "Ocultar propuesta" : "Ver propuesta", () =>
        showing ? this._hidePreview() : this._showPreview(merged),
      );
      const accept = button(row, "Aceptar propuesta", () => this._applyCandidate(candidate.id));
      accept.disabled = stale;
      button(row, "Pedir un ajuste", () => this._focusReply(intent.id));
      button(row, "Descartar propuesta", () => this._discardCandidate(candidate.id));
    } else {
      if (merged.before_snapshot)
        button(row, "Volver atrás", () => this._undo(merged.before_snapshot));
      button(row, "Sí, está resuelto", () =>
        this._intentAction(intent.id, { action: "resolve" }, "Indicación marcada como resuelta por ti."),
      );
      button(row, "No, sigue mal", () => this._focusReply(intent.id));
    }
    box.append(row);
    return box;
  }
  /** Sin `act`: lo llaman tanto `_showPreview` como tareas que ya están dentro de `act`. */
  async _fetchPreview(candidate) {
    if (!candidate.preview_url) throw new Error("Vista previa no disponible");
    const comparar = Boolean(this.entry.compararPropuesta);
    // Con piezas, la vista antes/después colorea solo lo que cambió.
    const url = comparar ? `${candidate.preview_url}?piezas=1` : candidate.preview_url;
    const response = await fetch(url);
    if (!response.ok) throw new Error("Vista previa no disponible");
    const buffer = await response.arrayBuffer();
    this.previewId = candidate.id;
    if (comparar) {
      await this.entry.compararPropuesta(buffer, proposalStates(candidate), {
        lineas: proposalLines(candidate),
        alSalir: () => {
          this.previewId = null;
          this.render();
        },
      });
      this.notify("Propuesta a la derecha del divisor; el modelo actual a la izquierda.");
    } else {
      this.entry.viewer.showDesignPreview?.(buffer);
      this.notify("Propuesta mostrada en translúcido sobre el modelo.");
    }
  }
  _showPreview(candidate) {
    return this.act(() => this._fetchPreview(candidate));
  }
  /** Quita la vista de la propuesta, sea la comparación o la capa translúcida. */
  _clearPreview() {
    this.previewId = null;
    this.entry.viewer.showDesignPreview?.(null);
    if (this.entry.alSalirComparacion) this.entry.salirComparacion?.();
  }
  _hidePreview() {
    return this.act(async () => this._clearPreview());
  }
  _applyCandidate(id) {
    return this.act(async () => {
      await request(`${this.base}/candidates/${encodeURIComponent(id)}/apply`, "POST", undefined, { reason: "Aceptar una propuesta cambia la geometría" });
      this._clearPreview();
      this.notify("Propuesta aplicada. Revisa el modelo y confirma si queda bien.");
    });
  }
  _discardCandidate(id) {
    return this.act(async () => {
      await request(`${this.base}/candidates/${encodeURIComponent(id)}`, "DELETE", undefined, { reason: "Descartar una propuesta preparada" });
      if (this.previewId === id) this._clearPreview();
      this.notify("Propuesta descartada.");
    });
  }
  _undo(snapshot) {
    if (!window.confirm("¿Volver al estado anterior a esta propuesta? Se añadirá un paso de restauración al historial.")) return;
    return this.act(async () => {
      // La clave se pide aquí con su motivo; restaurarDocumento la reutiliza de la sesión.
      const token = await requestSessionToken("Volver atrás restaura el historial");
      if (!token) throw new Error("Operación cancelada: no se proporcionó la clave");
      const record = await restaurarDocumento(this.entry.ficha.id, snapshot);
      this.previewId = null;
      await this.entry.alRestaurar?.(record);
      this.notify("Estado anterior restaurado.");
    });
  }

  _renderLooseMarks(root) {
    const loose = this.data.loose_marks ?? looseMarks(this.marks, this.intents);
    if (!loose.length) return;
    const s = section(root, "Marcas sin indicación");
    s.append(
      el("p", "Notas o trazos guardados sin texto para el agente. Conviértelos en indicación para que el agente los lea junto con su texto.", {
        className: "fj-design__ayuda",
      }),
    );
    for (const mark of loose) {
      const card = el("article", null, { className: "fj-design-card" });
      card.dataset.markId = mark.n;
      card.append(el("p", mark.comentario || "(sin comentario)", { className: "fj-design-card__texto" }));
      card.append(el("p", mark.tipo === "nota" ? "Nota" : "Trazo", { className: "fj-design__ayuda" }));
      if (mark.referencia_perdida)
        card.append(el("p", "La referencia se perdió: la geometría cambió.", { className: "fj-design-aviso" }));
      const row = el("div", null, { className: "fj-design__acciones" });
      button(row, "Convertir en indicación", () =>
        this.act(async () => {
          await createIntent(this.entry.ficha.id, {
            text: mark.comentario || "(sin texto)",
            marks: [mark.n],
          });
          this.notify("Marca convertida en indicación.");
        }),
      );
      button(row, "Borrar", () => {
        if (!window.confirm("¿Borrar esta marca?")) return;
        const notes = this.entry.notas;
        const task = mark.tipo === "nota" ? notes.eliminarNota(mark.n) : notes.eliminarTrazo(mark.n);
        task.then(
          () => this.notify("Marca borrada."),
          (error) => this.notify(error.message, true),
        );
      });
      card.append(row);
      s.append(card);
    }
  }

  _renderPrinting(root) {
    const s = section(root, "Preparar para imprimir");
    s.append(el("p", "Elige qué se imprime y cómo se orienta. Estos cambios se guardan al instante.", { className: "fj-design__ayuda" }));
    for (const piece of this.pieces) {
      const role = this.data.design.parts?.[piece.name] ?? { role: "design", locked: false };
      const choices = [["design", printRoleLabels.design], ["reference", printRoleLabels.reference]];
      if (role.role === "reserved") choices.push(["reserved", printRoleLabels.reserved]);
      const select = options(s, piece.name, choices, role.role);
      select.onchange = () =>
        this.act(async () => {
          this.setPart(piece.name, select.value);
          await this.saveDesign("Cambiar qué se imprime");
          this.notify(`${piece.name}: ${printRoleLabels[select.value]}.`);
        });
    }
    if (!this.printPiece || !this.pieces.some((p) => p.name === this.printPiece))
      this.printPiece = this.pieces[0]?.name ?? null;
    const pick = options(
      s,
      "Pieza a preparar",
      this.pieces.map((p) => [p.name, p.name]),
      this.printPiece,
    );
    pick.onchange = () => {
      this.printPiece = pick.value;
      this.render();
    };
    const settings = this.details(s, "print-settings", "Ajustes de impresión");
    const nozzle = this.draftField(settings, "Boquilla (mm)", "print:nozzle", DEFAULT_NOZZLE);
    const bed = this.draftField(settings, "Cama X, Y, Z (mm)", "print:bed", DEFAULT_BED.join(", "));

    const row = el("div", null, { className: "fj-design__acciones" });
    button(row, "Buscar mejor orientación", () =>
      this.act(async () => {
        const piece = this.printPiece;
        const result = await this._query({ kind: "orientations", piece, bed: vector(bed.value) });
        this.outputs.print = { type: "options", piece, data: result.options };
      }),
    );
    button(row, "Revisar si se puede imprimir", () =>
      this.act(async () => {
        const job = await this._query(
          { kind: "fdm", piece: this.printPiece, nozzle: Number(nozzle.value), bed: vector(bed.value) },
          true,
        );
        this.outputs.print = { type: "job", data: job };
      }),
    );
    s.append(row);
    const zone = el("section", null, { className: "fj-design-results" });
    zone.setAttribute("aria-live", "polite");
    this.outputZones.print = zone;
    s.append(zone);
    const exports = el("div", null, { className: "fj-design__acciones" });
    for (const format of ["3mf", "stl"])
      button(exports, `Descargar ${format.toUpperCase()}`, () =>
        this.act(async () => {
          const manifest = await request(`${this.base}/export/${format}?manifest=true`);
          this.outputs.print = { type: "export", format, manifest };
        }),
      );
    s.append(exports);
    this._paintOutput("print");
  }
  setPart(name, roleValue) {
    this.data.design.parts = this.data.design.parts ?? {};
    this.data.design.parts[name] = {
      ...(this.data.design.parts[name] ?? {}),
      role: roleValue,
      locked: roleValue !== "design",
    };
  }

  /** Repinta una zona de resultados desde el estado guardado (sobrevive a renders). */
  _paintOutput(key) {
    const zone = this.outputZones[key];
    if (!zone) return;
    zone.replaceChildren();
    const output = this.outputs[key];
    if (!output) return;
    if (output.type === "evidence") showEvidence(zone, output.data);
    else if (output.type === "job") this._paintJob(zone, output.data);
    else if (output.type === "options") {
      zone.append(el("p", `Opciones para ${output.piece}. Elige una para guardar esa postura.`));
      for (const op of output.data)
        button(
          zone,
          `${op.rotation_deg.join(", ")}° · apoyo ${op.contact_mm2.toFixed(1)} mm² · ${op.fits ? "cabe" : "no cabe"}`,
          () =>
            this.act(async () => {
              this.data.design.print_rotations = {
                ...(this.data.design.print_rotations ?? {}),
                [output.piece]: op.rotation_deg.map(Number),
              };
              await this.saveDesign("Guardar la postura de impresión");
              this.notify(`Postura guardada para ${output.piece}.`);
            }),
        );
    } else if (output.type === "export") {
      zone.append(
        el("p", `Incluidas: ${output.manifest.pieces.join(", ")}. Excluidas: ${output.manifest.excluded.join(", ") || "ninguna"}.`),
        el("a", `Descargar ${output.format.toUpperCase()}`, {
          href: this.base + "/export/" + output.format,
          download: `design.${output.format}`,
          className: "fj-btn",
        }),
      );
    }
  }
  _paintJob(zone, job) {
    zone.append(el("p", `Análisis: ${job.state} · ${Math.round(job.progress * 100)}%`));
    if (job.state === "done")
      for (const r of job.result?.results ?? []) {
        if (r.error) zone.append(el("p", r.error));
        else showEvidence(zone, r.result);
      }
    if (["queued", "running"].includes(job.state))
      button(zone, "Cancelar análisis", () =>
        this.act(async () => {
          this.outputs.print = { type: "job", data: await request(`/design/jobs/${encodeURIComponent(job.id)}/cancel`, "POST") };
          this.notify("Cancelación solicitada; terminará al salir del cálculo actual.");
        }),
      );
    if (job.message) zone.append(el("p", job.message));
  }
  /** Sondea el análisis de impresión cada 2 s mientras corra y el panel esté abierto. */
  _resumePolling() {
    clearTimeout(this.pollTimer);
    this.pollTimer = null;
    const output = this.outputs.print;
    if (output?.type !== "job" || !["queued", "running"].includes(output.data.state)) return;
    this.pollTimer = setTimeout(() => void this._poll(output.data.id), POLL_INTERVAL_MS);
  }
  async _poll(jobId) {
    this.pollTimer = null;
    if (this.root.closest("[hidden]") || this.root.dataset.designDocument !== this.entry.ficha.id) return;
    try {
      const job = await request(`/design/jobs/${encodeURIComponent(jobId)}`);
      this.outputs.print = { type: "job", data: job };
      this._paintOutput("print");
    } catch (error) {
      this.notify(error.message, true);
      return;
    }
    this._resumePolling();
  }

  _renderAdvanced(root) {
    const s = this.details(root, "advanced", "Herramientas avanzadas");
    s.append(
      el("p", "Vectores, condiciones, mediciones y alternativas manuales. Para el uso normal, usa las indicaciones y las propuestas de arriba.", {
        className: "fj-design__ayuda",
      }),
    );
    const pieces = this.pieces.map((p) => [p.name, p.name]);
    this.workPiece = options(s, "Pieza de trabajo", pieces, this.workPieceDefault());
    this.workPiece.onchange = () => this.entry.viewer.selectPiece(this.workPiece.value);
    this._renderRoles(s, pieces);
    this._renderConditions(s, pieces);
    this._renderMeasure(s, pieces);
    this._renderAlternative(s);
    this._renderPose(s);
    this._renderCandidates(s);
  }
  _renderRoles(parent) {
    const d = this.details(parent, "roles", "Función y protección de las piezas");
    for (const piece of this.pieces) {
      const role = this.data.design.parts?.[piece.name] ?? { role: "design", locked: false };
      const choice = options(d, piece.name, Object.entries(labels), role.role);
      const lockLabel = el("label", "Bloquear edición");
      const lock = el("input", null, { type: "checkbox", checked: Boolean(role.locked || role.role !== "design") });
      lockLabel.prepend(lock);
      d.append(lockLabel);
      const update = () =>
        this.act(async () => {
          this.data.design.parts = this.data.design.parts ?? {};
          this.data.design.parts[piece.name] = {
            ...(this.data.design.parts[piece.name] ?? {}),
            role: choice.value,
            locked: lock.checked || choice.value !== "design",
          };
          await this.saveDesign("Cambiar la protección de una pieza");
          this.notify(`${piece.name}: función guardada.`);
        });
      choice.onchange = update;
      lock.onchange = update;
    }
  }
  _renderConditions(parent, pieces) {
    const d = this.details(parent, "conditions", "Condiciones del diseño");
    const piece = options(d, "Pieza de la condición", pieces, this.workPieceDefault());
    const kind = options(d, "Condición", [
      ["closed", "Malla cerrada"],
      ["single_body", "Un solo cuerpo"],
      ["clearance", "Holgura mínima"],
      ["dimension", "Dimensión"],
      ["preserve", "Conservar geometría"],
      ["manual", "Comprobación física pendiente"],
    ]);
    const other = options(d, "Segunda pieza (holgura)", pieces);
    const axis = options(d, "Eje", [["x", "X"], ["y", "Y"], ["z", "Z"]]);
    const value = this.draftField(d, "Medida requerida (mm)", "cond:value", "1");
    const tolerance = this.draftField(d, "Tolerancia (mm)", "cond:tolerance", "0.1");
    const description = this.draftField(d, "Descripción", "cond:description", "");
    button(d, "Añadir condición", () =>
      this.act(async () => {
        const name = piece.value;
        const found = this.pieces.find((p) => p.name === name);
        this.data.design.requirements = this.data.design.requirements ?? [];
        this.data.design.requirements.push({
          id: crypto.randomUUID(),
          kind: kind.value,
          piece: name,
          other: kind.value === "clearance" ? other.value : null,
          axis: axis.value,
          value: Number(value.value),
          tolerance: Number(tolerance.value),
          baseline: kind.value === "preserve" ? (found?.signature ?? null) : null,
          description: description.value,
          source: "user",
        });
        await this.saveDesign("Cambiar las condiciones del diseño");
        this.drafts.forget("cond:description");
        this.notify("Condición añadida.");
      }),
    );
    for (const r of this.data.design.requirements ?? []) {
      const row = el("div", null, { className: "fj-design-card" });
      row.append(el("p", `${r.description || r.kind} · ${r.piece}`));
      button(row, "Quitar condición", () =>
        this.act(async () => {
          this.data.design.requirements = this.data.design.requirements.filter((x) => x.id !== r.id);
          await this.saveDesign("Cambiar las condiciones del diseño");
          this.notify("Condición quitada.");
        }),
      );
      d.append(row);
    }
  }
  _renderMeasure(parent, pieces) {
    const d = this.details(parent, "measure", "Medir y comprobar");
    this.pointCount = el("p", `${this.points.length} puntos capturados en coordenadas del modelo`);
    d.append(this.pointCount);
    const capture = button(d, this.capturing ? "Finalizar captura de puntos" : "Capturar puntos sobre el modelo", () => {
      this.capturing = !this.capturing;
      capture.textContent = this.capturing ? "Finalizar captura de puntos" : "Capturar puntos sobre el modelo";
    });
    button(d, "Limpiar puntos", () => {
      this.points = [];
      this.pointCount.textContent = "0 puntos capturados en coordenadas del modelo";
      this.entry.viewer.showDesignPoints?.([]);
    });
    const second = options(d, "Comparar con", pieces, this.workPieceDefault());
    const measures = [
      ["Inspeccionar selección", () => ({ kind: "inspect", piece: this.workPiece.value, points: this.points })],
      ["Medir separación", () => ({ kind: "clearance", piece: this.workPiece.value, other: second.value })],
      ["Ajustar plano a puntos", () => ({ kind: "fit_plane", points: this.points })],
      ["Ajustar círculo a puntos", () => ({ kind: "fit_circle", points: this.points })],
      ["Comprobar condiciones", () => ({ kind: "evaluate" })],
    ];
    for (const [label, build] of measures)
      button(d, label, () =>
        this.act(async () => {
          this.outputs.measure = { type: "evidence", data: await this._query(build()) };
          this.openDetails.add("advanced");
        }),
      );
    button(d, "Guardar puntos como destino de alineación", () => {
      this.target = this.points.map((p) => [...p]);
      this.points = [];
      this.pointCount.textContent = "Captura ahora los puntos de la pieza móvil, en el mismo orden.";
    });
    button(d, "Calcular alineación", () =>
      this.act(async () => {
        this.alignment = await this._query({ kind: "align", points: this.points, target: this.target });
        this.outputs.measure = { type: "evidence", data: this.alignment };
        this.openDetails.add("advanced");
      }),
    );
    const z = this.draftField(d, "Altura del corte Z (mm)", "section:z", "0");
    button(d, "Mostrar corte", () =>
      this.act(async () => {
        const result = await this._query({
          kind: "section",
          piece: this.workPiece.value,
          origin: [0, 0, Number(z.value)],
          normal: [0, 0, 1],
        });
        this.entry.viewer.showDesignLines?.(result.polylines);
        this.outputs.measure = { type: "evidence", data: result };
        this.openDetails.add("advanced");
      }),
    );
    const zone = el("section", null, { className: "fj-design-results" });
    zone.setAttribute("aria-live", "polite");
    this.outputZones.measure = zone;
    d.append(zone);
    this._paintOutput("measure");
  }
  _renderAlternative(parent) {
    const id = this.entry.ficha.id;
    const preset = pendientes.get(id)?.operacion ?? {};
    const d = this.details(parent, "alternative", "Preparar una alternativa manual");
    const tipo = options(
      d,
      "Cambio",
      [
        ["transform", "Mover / girar"],
        ["scale", "Escalar"],
        ["align", "Alinear con otra pieza"],
        ["drop_to_bed", "Apoyar sobre la cama"],
        ["union", "Añadir apoyo"],
        ["subtract", "Perforar / recortar"],
        ["intersect", "Conservar intersección"],
        ["deform_region", "Desplazar región STL"],
      ],
      preset.tipo ?? "transform",
    );
    const cuerpo = options(
      d,
      "Cuerpo de la booleana",
      [
        ["primitive", "Forma primitiva"],
        ["piece", "Otra pieza del documento"],
      ],
      preset.cuerpo ?? "primitive",
    );
    const primitive = options(d, "Forma del apoyo o corte", PRIMITIVAS, preset.primitiva ?? "box");
    d.append(el("p", AYUDA_FORMAS, { className: "fj-design__ayuda" }));
    const translation = this.draftField(d, "Desplazamiento X, Y, Z (mm)", "alt:translation", "0, 0, 0");
    const rotation = this.draftField(d, "Giro X, Y, Z (grados)", "alt:rotation", "0, 0, 0");
    const size = this.draftField(d, "Tamaño X, Y, Z (mm) según la forma", "alt:size", "10, 10, 10");
    const center = this.draftField(d, "Centro X, Y, Z (mm); centro del giro", "alt:center", "0, 0, 0");
    const radius = this.draftField(d, "Radio de región (mm)", "alt:radius", "5");
    const factor = this.draftField(d, "Factor de escala X, Y, Z (1 = sin cambio)", "alt:factor", "1, 1, 1");
    const otherDefault = this.pieces.find((p) => p.name !== this.workPieceDefault())?.name ?? "";
    const otra = options(
      d,
      "Otra pieza (referencia de alineación o cuerpo de la booleana)",
      this.pieces.map((p) => [p.name, p.name]),
      otherDefault,
    );
    const modo = options(d, "Alinear por", MODOS_ALINEAR, "center");
    const ejes = this.draftField(d, "Ejes a alinear (x, y, z)", "alt:axes", "x, y, z");
    const normal = this.draftField(d, "Cara que apoya en la cama: normal X, Y, Z", "alt:normal", "0, 0, -1");
    const link = options(
      d,
      "Vincular a una indicación",
      [["", "Ninguna"], ...this.intents
        .filter((i) => isOpenAttention(attentionOf(i, this.viewOf(i))))
        .map((i) => [i.id, shortText(i.text)])],
      "",
    );
    const summary = this.draftField(d, "Resumen para el agente", "summary", "");
    summary.maxLength = 500;
    const extra = () => ({
      ...(link.value ? { intent: link.value } : {}),
      ...(summary.value.trim() ? { summary: summary.value.trim() } : {}),
    });
    const valores = () => ({
      tipo: tipo.value,
      cuerpo: cuerpo.value,
      pieza: this.workPiece.value,
      primitiva: primitive.value,
      otra: otra.value,
      translation: translation.value,
      rotation: rotation.value,
      size: size.value,
      center: center.value,
      radius: radius.value,
      factor: factor.value,
      modo: modo.value,
      ejes: ejes.value,
      normal: normal.value,
    });
    const finish = () => {
      const pendiente = pendientes.get(id);
      if (pendiente) pendiente.operacion = null;
      this.drafts.forget("summary");
      this.notify("Candidato preparado. Compáralo antes de aplicarlo.");
    };
    button(d, "Crear candidato sin cambiar el original", () =>
      this.act(async () => {
        await this._prepare(construirOperacion(valores()), extra());
        finish();
      }),
    );
    button(d, "Crear candidato con alineación calculada", () =>
      this.act(async () => {
        if (!this.alignment) throw new Error("Calcula una alineación primero");
        await this._prepare({ kind: "transform", piece: this.workPiece.value, matrix: this.alignment.matrix }, extra());
        finish();
      }),
    );
  }
  async _prepare(operation, extra = {}) {
    await request(this.base + "/candidates", "POST", {
      revision: this.data.revision,
      state_revision: this.data.state_revision,
      request_id: crypto.randomUUID(),
      operations: [operation],
      ...extra,
    }, { reason: "Preparar una propuesta" });
  }
  _renderPose(parent) {
    const d = this.details(parent, "pose", "Postura de impresión (vector)");
    const current = this.data.design.print_rotations?.[this.workPieceDefault()] ?? [0, 0, 0];
    const pose = this.draftField(d, "Giro de impresión X, Y, Z (grados)", "pose", current.join(", "));
    button(d, "Guardar postura de la pieza", () =>
      this.act(async () => {
        const piece = this.workPiece.value;
        this.data.design.print_rotations = {
          ...(this.data.design.print_rotations ?? {}),
          [piece]: vector(pose.value),
        };
        await this.saveDesign("Guardar la postura de impresión");
        this.drafts.forget("pose");
        this.notify("Postura guardada para esta pieza.");
      }),
    );
  }
  _renderCandidates(parent) {
    if (!this.candidates.length) return;
    const d = this.details(parent, "candidates", "Candidatos preparados");
    for (const c of this.candidates) {
      const card = el("div", null, { className: "fj-design-card" });
      card.append(el("h5", `${statusLabels[c.state] || c.state} · ${c.id.slice(0, 8)}`));
      if (c.summary) card.append(el("p", c.summary));
      const stale = isProposalStale(c, this.data);
      card.append(
        el(
          "p",
          stale
            ? "La pieza o las condiciones cambiaron; vuelve a prepararla."
            : `${pluralize(c.difference?.changed?.length ?? 0, "pieza modificada", "piezas modificadas")} · ${checkSummary(c.checks)}`,
          { className: stale ? "fj-design-aviso" : "" },
        ),
      );
      button(card, "Comparar en el visor", () =>
        this.act(async () => {
          await this._fetchPreview(c);
          this.outputs.measure = { type: "evidence", data: c.checks };
          this.openDetails.add("advanced");
        }),
      );
      button(card, "Ocultar candidato", () => this._hidePreview());
      if (c.state === "prepared" && !stale) button(card, "Aplicar candidato", () => this._applyCandidate(c.id));
      if (c.state === "prepared") button(card, "Descartar candidato", () => this._discardCandidate(c.id));
      d.append(card);
    }
  }
}

export function renderDesign(entry, root) {
  let c = controllers.get(entry.ficha.id);
  if (!c) {
    c = new Workspace(entry, root);
    controllers.set(entry.ficha.id, c);
  }
  c.entry = entry;
  c.root = root;
  root.dataset.designDocument = entry.ficha.id;
  if (c.data) c.render();
  void c.load();
}
export function designHit(entry, hit) {
  const c = controllers.get(entry.ficha.id);
  if (!c || !c.capturing || !hit) return;
  if (c.points.length >= MAX_INTENT_POINTS) return;
  const piece = resolveDesignPiece(
    c.pieces,
    hit.pieceName || entry.viewer.selectedPiece || c.workPiece?.value,
  );
  if (!piece) {
    c.notify("No se pudo identificar la pieza seleccionada.", true);
    return;
  }
  if (c.points.length && c.selectedPiece !== piece) {
    c.notify("Finaliza o limpia los puntos antes de seleccionar otra pieza.", true);
    return;
  }
  c.points.push(hit.punto);
  c.selectedPiece = piece;
  if (c.workPiece) c.workPiece.value = piece;
  if (c.pointCount)
    c.pointCount.textContent = `${c.points.length} puntos capturados en coordenadas del modelo`;
  entry.viewer.showDesignPoints?.(c.points);
}
export function disposeDesign(id) {
  controllers.get(id)?.stop();
  controllers.delete(id);
  pendientes.delete(id);
  draftsByDocument.delete(id);
}
