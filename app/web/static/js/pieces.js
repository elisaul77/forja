// Per-tab display state belongs to the viewer, never to the stored document.
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

export function renderPiecesPanel(viewer, documentName) {
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
    row.append(visible, select);
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
    } else {
      detail.append(element("span", "Selecciona una pieza en la lista o sobre el modelo. Doble clic en su nombre para encuadrarla."));
    }
  }
  update();
}
