// Articulaciones y puente de suspensión: comparte la API utilizada por MCP.
import { obtenerTokenSesion } from "./api.js";

async function request(path, method = "GET", data) {
  const headers = method === "GET" ? {} : { "Content-Type": "application/json", "X-Forja-Token": obtenerTokenSesion() };
  const response = await fetch(path, { method, headers, body: data === undefined ? undefined : JSON.stringify(data) });
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : JSON.stringify(result.detail));
  return result;
}

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function field(parent, title, value, type = "number") {
  const label = element("label", title, "fj-campo");
  const input = element("input", undefined, "fj-input");
  input.type = type; input.value = value;
  if (type === "number") input.step = "any";
  label.append(input); parent.append(label);
  return input;
}

function select(parent, title, choices, value) {
  const label = element("label", title, "fj-campo");
  const input = element("select", undefined, "fj-select");
  for (const [key, text] of choices) {
    const option = element("option", text); option.value = key; input.append(option);
  }
  input.value = value;
  label.append(input); parent.append(label);
  return input;
}

export class ForjaEnsambleControlador {
  constructor(docId, onChange) {
    this.docId = docId; this.onChange = onChange;
    this.data = { piezas: [], articulaciones: [], valores: {} };
    this.error = ""; this.busy = false;
  }
  async cargarInicial() {
    try { this.data = await request(`/documentos/${this.docId}/ensamble`); this.error = ""; }
    catch (error) { this.error = error.message; }
  }
  async run(action, status) {
    if (this.busy) return;
    this.busy = true; status.textContent = "Procesando…";
    try { await action(); }
    catch (error) { status.textContent = error.message; }
    finally { this.busy = false; }
  }
}

export function renderizarPanelEnsamble(controller) {
  const panel = document.getElementById("fj-panel-ensamble");
  panel.replaceChildren(); panel.dataset.documentId = controller.docId;
  const refresh = () => { if (panel.dataset.documentId === controller.docId) renderizarPanelEnsamble(controller); };
  panel.append(element("h3", "Ensamble"));
  const status = element("p", controller.error, "fj-ensamble-status");
  panel.append(status);
  const data = controller.data;
  if (!data.piezas.length) return;
  const pieces = element("details");
  pieces.append(element("summary", `${data.piezas.length} piezas con nombre`));
  for (const name of data.piezas) pieces.append(element("div", name));
  panel.append(pieces);
  if (data.obsoleto) panel.append(element("p", "La geometría cambió. Revisá las piezas y redefiní las articulaciones.", "fj-ensamble-warning"));

  if (data.articulaciones.length) {
    const inputs = new Map();
    for (const joint of data.articulaciones) {
      const row = element("div", undefined, "fj-ensamble-joint");
      row.append(element("strong", `${joint.padre} → ${joint.hijo}`));
      if (joint.tipo === "fijo") row.append(element("p", `${joint.id}: unión fija`));
      else {
        const units = joint.tipo === "giro" ? "°" : "mm";
        const input = field(row, `${joint.id} (${units})`, data.valores[joint.id]);
        [input.min, input.max] = joint.limites;
        input.disabled = data.obsoleto;
        inputs.set(joint.id, input);
      }
      panel.append(row);
    }
    const apply = element("button", "Aplicar posición", "fj-btn fj-btn--primary");
    apply.disabled = data.obsoleto;
    apply.onclick = () => controller.run(async () => {
      const valores = Object.fromEntries([...inputs].map(([id, input]) => [id, Number(input.value)]));
      const result = await request(`/documentos/${controller.docId}/ensamble/pose`, "POST", { valores });
      await controller.onChange(result); await controller.cargarInicial(); refresh();
    }, status);
    const clear = element("button", "Quitar articulaciones; conservar posición", "fj-btn");
    clear.onclick = () => controller.run(async () => {
      controller.data = await request(`/documentos/${controller.docId}/ensamble`, "DELETE"); refresh();
    }, status);
    panel.append(apply, clear);
  } else if (data.piezas.length > 1) {
    panel.append(element("p", "Definí los ejes en la posición actual del modelo. Los límites incluyen cero; giro en grados y desplazamiento en mm."));
    const form = element("form"); const rows = [];
    const addRow = () => {
      const row = element("fieldset", undefined, "fj-ensamble-joint");
      row.append(element("legend", `Articulación ${rows.length + 1}`));
      const name = field(row, "Nombre", `joint_${rows.length + 1}`, "text");
      const choices = data.piezas.map(name => [name, name]);
      const parent = select(row, "Pieza padre", choices, data.piezas[0]);
      const child = select(row, "Pieza móvil", choices, data.piezas[Math.min(rows.length + 1, data.piezas.length - 1)]);
      const type = select(row, "Movimiento", [["giro", "Giro"], ["deslizamiento", "Deslizamiento"], ["fijo", "Fijo"]], "giro");
      const origin = field(row, "Origen X, Y, Z (mm)", "0, 0, 0", "text");
      const axis = field(row, "Dirección del eje X, Y, Z", "0, 0, 1", "text");
      const minimum = field(row, "Límite mínimo", -45); const maximum = field(row, "Límite máximo", 45);
      rows.push({name, parent, child, type, origin, axis, minimum, maximum}); form.append(row);
    };
    addRow();
    const add = element("button", "+ Articulación", "fj-btn"); add.type = "button";
    add.onclick = () => { if (rows.length < 60) addRow(); };
    const save = element("button", "Guardar articulaciones", "fj-btn fj-btn--primary"); save.type = "submit";
    const vector = input => {
      const values = input.value.split(",").map(value => Number(value.trim()));
      if (values.length !== 3 || values.some(value => !Number.isFinite(value))) throw new Error("Usá tres números separados por comas para cada vector.");
      return values;
    };
    form.onsubmit = event => {
      event.preventDefault();
      controller.run(async () => {
        const articulaciones = rows.map(r => ({ id: r.name.value, padre: r.parent.value, hijo: r.child.value,
          tipo: r.type.value, origen: vector(r.origin), eje: vector(r.axis),
          limites: r.type.value === "fijo" ? [0, 0] : [Number(r.minimum.value), Number(r.maximum.value)], valor: 0 }));
        controller.data = await request(`/documentos/${controller.docId}/ensamble`, "POST", { articulaciones }); refresh();
      }, status);
    };
    panel.append(form, add); form.append(save);
  }
  const bridge = element("button", "Consultar suspensión del mostertruck", "fj-btn");
  const output = element("div", undefined, "fj-ensamble-simulation");
  bridge.onclick = () => controller.run(async () => {
    const result = await request("/puentes/suspension/pose", "POST", {});
    output.replaceChildren(element("p", result.signo_correcto_al_subir ? "Los amortiguadores comprimen al subir la rueda." : "Revisar: el signo de compresión no coincide al subir la rueda."));
    for (const [key, title] of [["subida_rueda", "Subida de rueda"], ["cabeceo_sobre_eje_ruedas", "Cabeceo"], ["articulacion", "Articulación"]]) {
      output.append(element("strong", title));
      for (const row of result[key] ?? []) {
        const values = Object.entries(row).filter(([, value]) => typeof value === "object")
          .map(([side, value]) => `${side}: ${value.L} mm${value.dentro ? "" : " (fuera de carrera)"}`).join(" · ");
        output.append(element("p", `${row.mm ?? row.grados}${row.mm !== undefined ? " mm" : "°"} → ${values}`));
      }
    }
    status.textContent = "Consulta completada";
  }, status);
  panel.append(bridge, output);
}
