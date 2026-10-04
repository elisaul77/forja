// Per-tab display state belongs to the viewer, never to the stored document.
// Exception (fdm-D): the material of each piece IS document data, edited
// through `materiales` ({materiales, guardar(cambios)}) and saved by POST.
import { entradaDesdeFormulario, etiquetaMaterial } from "./materiales.js?v=1";

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function button(text, action) {
  const node = element("button", text, "fj-btn");
  node.type = "button";
  node.addEventListener("click", action);
  return node;
}

export function renderPiecesPanel(viewer, documentName, materiales = null) {
  const asignados = materiales?.materiales ?? {};
  const panel = document.getElementById("fj-panel-piezas");
  panel.replaceChildren();
  panel.append(element("h3", "Piezas"));
  if (!viewer.pieces.size) {
    panel.append(element("p", "Este modelo se muestra como una malla completa. Vuelve a abrirlo para cargar su árbol de piezas.", "fj-piece-hint"));
    return;
  }

  const manifest = viewer.pieceManifest;
  if (manifest.aviso || manifest.nombres_inciertos || !manifest.identificadas) {
    panel.append(element("p", manifest.aviso || (manifest.nombres_inciertos
      ? "No se pudieron confirmar los nombres originales. Se muestran nombres genéricos."
      : "Malla única: este archivo no contiene piezas identificadas."), "fj-piece-hint"));
  }
  const searchLabel = element("label", "Buscar piezas", "fj-campo fj-piece-search");
  const search = element("input", undefined, "fj-input");
  search.type = "search";
  search.placeholder = "Nombre de la pieza…";
  search.value = viewer.pieceSearch ?? "";
  searchLabel.append(search);
  panel.append(searchLabel);

  const status = element("p", "", "fj-piece-hint");
  status.setAttribute("role", "status");
  const actions = element("div", undefined, "fj-piece-actions");
  const isolate = button("Aislar selección", () => { viewer.isolatePiece(viewer.selectedPiece); update(); });
  const restore = button("Salir de aislamiento", () => { viewer.isolatePiece(null); update(); });
  const frame = button("Encuadrar selección", () => viewer.framePiece(viewer.selectedPiece));
  const showAll = button("Mostrar todas", () => { viewer.showAllPieces(); update(); });
  actions.append(isolate, restore, frame, showAll);
  panel.append(actions, status);

  const root = element("details", undefined, "fj-piece-tree");
  root.open = true;
  root.append(element("summary", documentName));
  const list = element("ul", undefined, "fj-piece-list");
  const rows = [];
  for (const [name, piece] of viewer.pieces) {
    const row = element("li", undefined, "fj-piece-row");
    const visible = element("input");
    visible.type = "checkbox";
    visible.setAttribute("aria-label", `Mostrar ${name}`);
    visible.addEventListener("change", () => { viewer.setPieceVisible(name, visible.checked); update(); });
    const select = button(name.replaceAll("_", " "), () => { viewer.selectPiece(name); update(); });
    select.className = "fj-piece-name";
    select.title = name;
    select.dataset.piece = name;
    select.addEventListener("dblclick", () => viewer.framePiece(name));
    const swatch = element("span", undefined, "fj-piece-swatch");
    swatch.setAttribute("aria-hidden", "true");
    if (asignados[name]?.color) swatch.style.background = asignados[name].color;
    else swatch.classList.add("is-empty");
    if (asignados[name]) swatch.title = etiquetaMaterial(asignados[name]);
    row.append(visible, swatch, select);
    list.append(row);
    rows.push({ name, piece, row, visible, select });
  }
  root.append(list);
  const empty = element("p", "No hay piezas que coincidan con la búsqueda.", "fj-piece-hint");
  root.append(empty);
  const detail = element("div", undefined, "fj-piece-detail");
  panel.append(root, detail);
  search.addEventListener("input", () => { viewer.pieceSearch = search.value; update(); });

  function update() {
    const selected = viewer.pieces.get(viewer.selectedPiece);
    const query = search.value.trim().toLocaleLowerCase("es").replaceAll("_", " ");
    let shown = 0;
    let visibleCount = 0;
    for (const { name, piece, row, visible, select } of rows) {
      row.hidden = !name.toLocaleLowerCase("es").replaceAll("_", " ").includes(query);
      if (!row.hidden) shown++;
      if (piece.mesh.visible) visibleCount++;
      visible.checked = piece.mesh.visible;
      visible.disabled = Boolean(viewer.isolatedPiece);
      select.setAttribute("aria-pressed", String(name === viewer.selectedPiece));
      row.classList.toggle("is-selected", name === viewer.selectedPiece);
      row.classList.toggle("is-hidden", !piece.mesh.visible);
    }
    empty.hidden = shown > 0;
    isolate.disabled = !selected || viewer.pieces.size < 2;
    restore.hidden = !viewer.isolatedPiece;
    isolate.hidden = Boolean(viewer.isolatedPiece);
    frame.disabled = !selected || !selected.mesh.visible;
    status.textContent = viewer.isolatedPiece
      ? `Aislada: ${viewer.isolatedPiece.replaceAll("_", " ")}. Al salir se restaura la visibilidad anterior.`
      : `${visibleCount} de ${viewer.pieces.size} piezas visibles${query ? ` · ${shown} coincidencias` : ""}`;
    detail.replaceChildren();
    if (selected) {
      detail.append(element("strong", selected.nombre.replaceAll("_", " ")));
      const [xmin, xmax, ymin, ymax, zmin, zmax] = selected.bbox;
      detail.append(element("span", `${(xmax - xmin).toFixed(1)} × ${(ymax - ymin).toFixed(1)} × ${(zmax - zmin).toFixed(1)} mm`));
      if (Number.isFinite(selected.volumen)) detail.append(element("span", `${selected.volumen.toLocaleString("es", { maximumFractionDigits: 2 })} mm³`));
      if (selected.fragmentos > 1) detail.append(element("span", `${selected.fragmentos} sólidos forman esta pieza`));
      if (!selected.mesh.visible) detail.append(element("span", "Pieza oculta"));
      if (materiales) detail.append(materialForm(selected.nombre));
    } else {
      detail.append(element("span", "Selecciona una pieza en la lista o sobre el modelo. Doble clic en su nombre para encuadrarla."));
    }
  }
  update();

  function materialForm(name) {
    const actual = asignados[name] ?? {};
    const form = element("form", undefined, "fj-piece-material");
    form.append(element("span", `Material: ${etiquetaMaterial(asignados[name])}`));
    const campo = (texto, input) => { const l = element("label", texto, "fj-campo"); l.append(input); return l; };
    const material = element("input", undefined, "fj-input");
    material.maxLength = 64; material.placeholder = "p. ej. PETG negro"; material.value = actual.material ?? "";
    const conColor = element("input"); conColor.type = "checkbox"; conColor.checked = Boolean(actual.color);
    const color = element("input", undefined, "fj-input"); color.type = "color"; color.value = (actual.color ?? "#a8a29e").toLowerCase();
    color.disabled = !conColor.checked;
    conColor.addEventListener("change", () => { color.disabled = !conColor.checked; });
    const extrusor = element("input", undefined, "fj-input");
    extrusor.type = "number"; extrusor.min = "1"; extrusor.max = "16"; extrusor.step = "1"; extrusor.placeholder = "—";
    extrusor.value = actual.extrusor ?? "";
    const colorRow = element("div", undefined, "fj-piece-material__color");
    colorRow.append(campo("Usar color", conColor), campo("Color", color));
    const msg = element("span", "", "fj-piece-hint"); msg.setAttribute("role", "status");
    const guardar = element("button", "Guardar material", "fj-btn"); guardar.type = "submit";
    const quitar = button("Quitar", () => enviar(null));
    quitar.disabled = !asignados[name];
    form.append(campo("Material", material), colorRow, campo("Extrusor (1–16)", extrusor), guardar, quitar, msg);
    form.addEventListener("submit", event => {
      event.preventDefault();
      try {
        enviar(entradaDesdeFormulario({ material: material.value, color: color.value, extrusor: extrusor.value, conColor: conColor.checked }));
      } catch (error) { msg.textContent = error.message; }
    });
    async function enviar(entrada) {
      guardar.disabled = true; quitar.disabled = true; msg.textContent = "Guardando…";
      try {
        await materiales.guardar({ [name]: entrada });
      } catch (error) {
        msg.textContent = `No se pudo guardar: ${error.message}`;
        guardar.disabled = false; quitar.disabled = !asignados[name];
      }
    }
    return form;
  }
}
