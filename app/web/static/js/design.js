// Shared human/agent design workspace; never interpret annotations as code.
import { requestSessionToken, forgetSessionToken } from "./auth.js";
const controllers = new Map();
const labels = {
  design: "Diseño",
  reference: "Referencia",
  reserved: "Espacio reservado",
};
const statusLabels = {
  pass: "Cumple",
  fail: "No cumple",
  not_checked: "Sin comprobar",
  prepared: "Preparado",
  applied: "Aplicado",
};
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
function el(tag, text, attrs = {}) {
  const n = document.createElement(tag);
  if (text != null) n.textContent = text;
  Object.assign(n, attrs);
  return n;
}
async function request(path, method = "GET", body) {
  const headers = {};
  if (body) headers["Content-Type"] = "application/json";
  if (method !== "GET") {
    const token = await requestSessionToken();
    if (!token) throw new Error("Edición cancelada");
    headers["X-Forja-Token"] = token;
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
    throw new Error(
      typeof data?.detail === "string"
        ? data.detail
        : `No se pudo completar (${response.status})`,
    );
  }
  return response.json();
}
function field(parent, label, value = "", tag = "input") {
  const l = el("label", label),
    input = el(tag, null, { value });
  l.append(input);
  parent.append(l);
  return input;
}
function options(parent, label, values, current) {
  const s = field(parent, label, "", "select");
  for (const [value, text] of values) s.append(el("option", text, { value }));
  if (current != null) s.value = current;
  return s;
}
function button(parent, label, fn) {
  const b = el("button", label, { type: "button", className: "fj-btn" });
  b.onclick = fn;
  parent.append(b);
  return b;
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
        `Separación: ${data.distance_mm.toFixed(3)} mm. ${data.interference ? "Contacto o interferencia detectados." : ""} ${data.note || ""}`,
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
class Workspace {
  constructor(entry, root) {
    this.entry = entry;
    this.root = root;
    this.base = `/documentos/${encodeURIComponent(entry.ficha.id)}/design`;
    this.points = [];
    this.target = [];
    this.capture = false;
    this.busy = false;
    this.dirty = false;
    this.pendingImages = [];
  }
  async load() {
    if (this.busy) return;
    if (this.dirty) {
      if (this.root.dataset.designDocument !== this.entry.ficha.id) {
        this.root.replaceChildren(
          el(
            "p",
            "Este documento tiene cambios sin guardar. Vuelve a su panel para guardarlos.",
          ),
        );
      }
      return;
    }
    this.busy = true;
    try {
      this.data = await request(this.base);
      this.candidates = (await request(this.base + "/candidates")).candidates;
      if (this.root.dataset.designDocument === this.entry.ficha.id)
        this.render();
    } catch (e) {
      this.root.replaceChildren(el("p", e.message));
    } finally {
      this.busy = false;
    }
  }
  async act(fn) {
    if (this.busy) return;
    this.busy = true;
    try {
      await fn();
    } catch (e) {
      this.message.textContent = e.message;
    } finally {
      this.busy = false;
    }
  }
  async save() {
    this.data = await request(this.base, "PUT", {
      revision: this.data.revision,
      state_revision: this.data.state_revision,
      design: this.data.design,
    });
    this.dirty = false;
    this.message.textContent = "Intención guardada; disponible para el agente.";
  }
  async query(q, background = false) {
    const result = await request(this.base + "/query", "POST", {
      revision: this.data.revision,
      queries: [q],
      background,
    });
    this.output.replaceChildren();
    if (background) {
      this.job = result;
      this.renderJob();
      return;
    }
    const row = result.results[0];
    if (row.error) throw new Error(row.error);
    showEvidence(this.output, row.result);
    return row.result;
  }
  renderJob() {
    this.output.replaceChildren(
      el(
        "p",
        `Análisis: ${this.job.state} · ${Math.round(this.job.progress * 100)}%`,
      ),
    );
    button(this.output, "Actualizar resultado", () =>
      this.act(async () => {
        this.job = await request("/design/jobs/" + this.job.id);
        if (this.job.state === "done") {
          this.output.replaceChildren();
          for (const r of this.job.result.results) {
            if (r.error) this.output.append(el("p", r.error));
            else showEvidence(this.output, r.result);
          }
        } else this.renderJob();
      }),
    );
    if (["queued", "running"].includes(this.job.state))
      button(this.output, "Cancelar análisis", () =>
        this.act(async () => {
          this.job = await request(
            "/design/jobs/" + this.job.id + "/cancel",
            "POST",
          );
          this.message.textContent =
            "Cancelación solicitada; terminará al salir del cálculo actual.";
        }),
      );
    if (this.job.message) this.output.append(el("p", this.job.message));
  }
  onHit(hit) {
    if (!this.capture || !hit) return;
    if (this.points.length >= 256) return;
    const selected =
      hit.pieceName || this.entry.viewer.selectedPiece || this.piece.value;
    if (this.points.length && this.selected !== selected) {
      this.message.textContent =
        "Finaliza o limpia los puntos antes de seleccionar otra pieza.";
      return;
    }
    this.points.push(hit.punto);
    this.selected = selected;
    this.piece.value = selected;
    this.pointLabel.textContent = `${this.points.length} puntos capturados en coordenadas del modelo`;
    this.entry.viewer.showDesignPoints?.(this.points);
  }
  render() {
    const root = this.root;
    root.replaceChildren();
    root.classList.add("fj-design");
    root.append(el("h3", "Diseño compartido"));
    this.message = el(
      "p",
      "Objetivo, selecciones y comprobaciones disponibles para ti y para el agente.",
      { role: "status" },
    );
    root.append(this.message);
    const objective = field(
      root,
      "Qué queremos conseguir",
      this.data.design.objective,
      "textarea",
    );
    objective.maxLength = 8000;
    objective.oninput = () => {
      this.data.design.objective = objective.value;
      this.dirty = true;
    };
    button(root, "Guardar intención", () => this.act(() => this.save()));
    button(root, "Recargar estado", () => {
      if (
        !this.dirty ||
        window.confirm("Hay cambios sin guardar. ¿Descartarlos y recargar?")
      ) {
        this.dirty = false;
        void this.load();
      }
    });
    if (this.data.stale_intents.length)
      root.append(
        el(
          "p",
          `${this.data.stale_intents.length} selecciones necesitan revisión: la geometría cambió.`,
        ),
      );
    const selected =
      this.entry.viewer.selectedPiece || this.data.pieces[0]?.name;
    this.piece = options(
      root,
      "Pieza de trabajo",
      this.data.pieces.map((p) => [p.name, p.name]),
      selected,
    );
    this.piece.onchange = () => this.entry.viewer.selectPiece(this.piece.value);
    const roles = el("details");
    roles.append(el("summary", "Función y protección de las piezas"));
    root.append(roles);
    for (const p of this.data.pieces) {
      const role = this.data.design.parts[p.name] || {
        role: "design",
        locked: false,
      };
      const choice = options(roles, p.name, Object.entries(labels), role.role);
      const check = field(roles, "Bloquear edición", "", "input");
      check.type = "checkbox";
      check.checked = role.locked || role.role !== "design";
      const update = () => {
        this.data.design.parts[p.name] = {
          role: choice.value,
          locked: check.checked || choice.value !== "design",
        };
        this.dirty = true;
      };
      choice.onchange = update;
      check.onchange = update;
    }
    const annotate = el("details");
    annotate.append(el("summary", "Señalar y explicar un cambio"));
    root.append(annotate);
    this.pointLabel = el(
      "p",
      `${this.points.length} puntos capturados en coordenadas del modelo`,
    );
    annotate.append(this.pointLabel);
    const capture = button(
      annotate,
      this.capture
        ? "Finalizar captura de puntos"
        : "Capturar puntos sobre el modelo",
      () => {
        this.capture = !this.capture;
        capture.textContent = this.capture
          ? "Finalizar captura de puntos"
          : "Capturar puntos sobre el modelo";
      },
    );
    button(annotate, "Limpiar puntos", () => {
      this.points = [];
      this.pointLabel.textContent = "0 puntos";
      this.entry.viewer.showDesignPoints?.([]);
    });
    const photo = field(annotate, "Foto de referencia", "", "input");
    photo.type = "file";
    photo.accept = "image/png,image/jpeg,image/webp";
    const photoStatus = el("p", "");
    annotate.append(photoStatus);
    photo.onchange = () =>
      this.act(async () => {
        const file = photo.files?.[0];
        if (!file) return;
        if (file.size > 6 * 1024 * 1024) throw new Error("La foto supera 6 MB");
        if ((this.pendingImages || []).length >= 6)
          throw new Error("Máximo seis fotos por indicación");
        const token = await requestSessionToken();
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
        const image = await response.json();
        this.pendingImages ??= [];
        this.pendingImages.push(image.id);
        photoStatus.textContent = `${this.pendingImages.length} fotos preparadas para la indicación`;
      });
    const text = field(annotate, "Qué quieres cambiar", "", "textarea");
    const direction = field(annotate, "Dirección opcional (X, Y, Z)", "");
    button(annotate, "Guardar indicación", () =>
      this.act(async () => {
        if (!text.value.trim()) throw new Error("Escribe la indicación");
        this.data.design.intents.push({
          id: crypto.randomUUID(),
          images: this.pendingImages || [],
          text: text.value,
          source: "user",
          status: "pending",
          direction: direction.value ? vector(direction.value) : null,
          region: {
            piece: this.piece.value,
            revision: this.data.revision,
            points: this.points,
            radius_mm: 5,
            face_id: null,
          },
        });
        this.dirty = true;
        await this.save();
        this.pendingImages = [];
        this.render();
      }),
    );
    for (const i of this.data.design.intents) {
      const card = el("div", null, { className: "fj-design-card" });
      card.append(el("p", i.text));
      for (const image of i.images || [])
        card.append(
          el("img", null, {
            src: this.base + "/images/" + encodeURIComponent(image),
            alt: "Foto adjunta a la indicación",
            loading: "lazy",
            className: "fj-design-photo",
          }),
        );
      const st = options(
        card,
        "Estado",
        [
          ["pending", "Pendiente"],
          ["accepted", "Aceptada"],
          ["resolved", "Resuelta"],
        ],
        i.status,
      );
      st.onchange = () => {
        i.status = st.value;
        this.dirty = true;
      };
      root.append(card);
    }
    const req = el("details");
    req.append(el("summary", "Condiciones del diseño"));
    root.append(req);
    const kind = options(req, "Condición", [
      ["closed", "Malla cerrada"],
      ["single_body", "Un solo cuerpo"],
      ["clearance", "Holgura mínima"],
      ["dimension", "Dimensión"],
      ["preserve", "Conservar geometría"],
      ["manual", "Comprobación física pendiente"],
    ]);
    const other = options(
      req,
      "Segunda pieza",
      this.data.pieces.map((p) => [p.name, p.name]),
    );
    const axis = options(req, "Eje", [
      ["x", "X"],
      ["y", "Y"],
      ["z", "Z"],
    ]);
    const val = field(req, "Medida requerida (mm)", "1");
    const tolerance = field(req, "Tolerancia (mm)", "0.1");
    const desc = field(req, "Descripción", "");
    button(req, "Añadir condición", () =>
      this.act(async () => {
        const piece = this.piece.value;
        this.data.design.requirements.push({
          id: crypto.randomUUID(),
          kind: kind.value,
          piece,
          other: kind.value === "clearance" ? other.value : null,
          axis: axis.value,
          value: Number(val.value),
          tolerance: Number(tolerance.value),
          baseline:
            kind.value === "preserve"
              ? this.data.pieces.find((p) => p.name === piece).signature
              : null,
          description: desc.value,
          source: "user",
        });
        this.dirty = true;
        await this.save();
        this.render();
      }),
    );
    for (const r of this.data.design.requirements) {
      const row = el("div", null, { className: "fj-design-card" });
      row.append(el("p", `${r.description || r.kind} · ${r.piece}`));
      button(row, "Quitar condición", () =>
        this.act(async () => {
          this.data.design.requirements = this.data.design.requirements.filter(
            (x) => x.id !== r.id,
          );
          this.dirty = true;
          await this.save();
          this.render();
        }),
      );
      req.append(row);
    }
    const measure = el("details");
    measure.append(el("summary", "Medir y comprobar"));
    root.append(measure);
    const second = options(
      measure,
      "Comparar con",
      this.data.pieces.map((p) => [p.name, p.name]),
    );
    for (const [label, q] of [
      [
        "Inspeccionar selección",
        () => ({
          kind: "inspect",
          piece: this.piece.value,
          points: this.points,
        }),
      ],
      [
        "Medir separación",
        () => ({
          kind: "clearance",
          piece: this.piece.value,
          other: second.value,
        }),
      ],
      [
        "Ajustar plano a puntos",
        () => ({ kind: "fit_plane", points: this.points }),
      ],
      [
        "Ajustar círculo a puntos",
        () => ({ kind: "fit_circle", points: this.points }),
      ],
      ["Comprobar condiciones", () => ({ kind: "evaluate" })],
    ])
      button(measure, label, () => this.act(() => this.query(q())));
    button(measure, "Guardar puntos como destino de alineación", () => {
      this.target = this.points.map((p) => [...p]);
      this.points = [];
      this.pointLabel.textContent =
        "Captura ahora los puntos de la pieza móvil, en el mismo orden.";
    });
    button(measure, "Calcular alineación", () =>
      this.act(async () => {
        this.alignment = await this.query({
          kind: "align",
          points: this.points,
          target: this.target,
        });
      }),
    );
    const z = field(measure, "Altura del corte Z (mm)", "0");
    button(measure, "Mostrar corte", () =>
      this.act(async () => {
        const result = await this.query({
          kind: "section",
          piece: this.piece.value,
          origin: [0, 0, Number(z.value)],
          normal: [0, 0, 1],
        });
        this.entry.viewer.showDesignLines?.(result.polylines);
      }),
    );
    const edit = el("details");
    edit.append(el("summary", "Preparar una alternativa"));
    root.append(edit);
    const operation = options(edit, "Cambio", [
      ["transform", "Mover / girar"],
      ["union", "Añadir apoyo"],
      ["subtract", "Perforar / recortar"],
      ["intersect", "Conservar intersección"],
      ["deform_region", "Desplazar región STL"],
    ]);
    const translation = field(edit, "Desplazamiento X, Y, Z (mm)", "0, 0, 0"),
      rotation = field(edit, "Giro X, Y, Z (grados)", "0, 0, 0");
    const primitive = options(edit, "Forma del apoyo o corte", [
      ["box", "Caja"],
      ["cylinder", "Cilindro"],
    ]);
    const size = field(
        edit,
        "Tamaño X, Y, Z; cilindro: diámetro, reservado, altura",
        "10, 10, 10",
      ),
      center = field(edit, "Centro X, Y, Z (mm)", "0, 0, 0"),
      radius = field(edit, "Radio de región (mm)", "5");
    const prepare = async (op) => {
      if (this.dirty) await this.save();
      const c = await request(this.base + "/candidates", "POST", {
        revision: this.data.revision,
        state_revision: this.data.state_revision,
        request_id: crypto.randomUUID(),
        operations: [op],
      });
      this.candidates.push(c);
      this.render();
    };
    button(edit, "Crear candidato sin cambiar el original", () =>
      this.act(() =>
        prepare({
          kind: operation.value,
          piece: this.piece.value,
          translation: vector(translation.value),
          rotation_deg: vector(rotation.value),
          origin: vector(center.value),
          primitive: primitive.value,
          size: vector(size.value),
          center: vector(center.value),
          radius_mm: Number(radius.value),
        }),
      ),
    );
    button(edit, "Crear candidato con alineación calculada", () =>
      this.act(() => {
        if (!this.alignment) throw new Error("Calcula una alineación primero");
        return prepare({
          kind: "transform",
          piece: this.piece.value,
          matrix: this.alignment.matrix,
        });
      }),
    );
    for (const c of this.candidates || []) {
      const card = el("div", null, { className: "fj-design-card" });
      card.append(
        el("h4", `${statusLabels[c.state] || c.state} · ${c.id.slice(0, 8)}`),
      );
      card.append(
        el(
          "p",
          `${c.difference.changed.length} piezas modificadas · ${c.checks.failed} condiciones fallidas · ${c.checks.not_checked} pendientes`,
        ),
      );
      button(card, "Comparar en el visor", () =>
        this.act(async () => {
          const response = await fetch(c.preview_url);
          if (!response.ok) throw new Error("Vista previa no disponible");
          this.entry.viewer.showDesignPreview?.(await response.arrayBuffer());
          this.output.replaceChildren();
          showEvidence(this.output, c.checks);
        }),
      );
      button(card, "Ocultar candidato", () =>
        this.entry.viewer.showDesignPreview?.(null),
      );
      if (c.state === "prepared") {
        button(card, "Aplicar candidato", () =>
          this.act(async () => {
            await request(this.base + "/candidates/" + c.id + "/apply", "POST");
            this.entry.viewer.showDesignPreview?.(null);
            this.dirty = false;
            this.data = await request(this.base);
            this.candidates = (
              await request(this.base + "/candidates")
            ).candidates;
            this.render();
          }),
        );
        button(card, "Descartar candidato", () =>
          this.act(async () => {
            await request(this.base + "/candidates/" + c.id, "DELETE");
            this.candidates = this.candidates.filter((x) => x.id !== c.id);
            this.render();
          }),
        );
      }
      root.append(card);
    }
    const printing = el("details");
    printing.append(el("summary", "Preparar fabricación"));
    root.append(printing);
    const nozzle = field(printing, "Boquilla (mm)", "0.4");
    const bed = field(printing, "Cama X, Y, Z (mm)", "220, 220, 250");
    const pose = field(
      printing,
      "Giro de impresión X, Y, Z (grados)",
      (this.data.design.print_rotations[this.piece.value] || [0, 0, 0]).join(
        ", ",
      ),
    );
    button(printing, "Guardar postura de la pieza", () =>
      this.act(async () => {
        this.data.design.print_rotations[this.piece.value] = vector(pose.value);
        this.dirty = true;
        await this.save();
      }),
    );
    button(printing, "Comparar seis orientaciones", () =>
      this.act(async () => {
        const result = await this.query({
          kind: "orientations",
          piece: this.piece.value,
          bed: vector(bed.value),
        });
        for (const op of result.options)
          button(
            this.output,
            `${op.rotation_deg.join(", ")}° · apoyo ${op.contact_mm2.toFixed(1)} mm² · ${op.fits ? "cabe" : "no cabe"}`,
            () => {
              pose.value = op.rotation_deg.join(", ");
            },
          );
      }),
    );
    button(printing, "Analizar impresión (en segundo plano)", () =>
      this.act(() =>
        this.query(
          {
            kind: "fdm",
            piece: this.piece.value,
            nozzle: Number(nozzle.value),
            bed: vector(bed.value),
          },
          true,
        ),
      ),
    );
    for (const format of ["3mf", "stl"])
      button(
        printing,
        `Verificar y descargar diseño ${format.toUpperCase()}`,
        () =>
          this.act(async () => {
            if (this.dirty) await this.save();
            const manifest = await request(
              this.base + "/export/" + format + "?manifest=true",
            );
            this.output.replaceChildren(
              el(
                "p",
                `Incluidas: ${manifest.pieces.join(", ")}. Excluidas: ${manifest.excluded.join(", ") || "ninguna"}.`,
              ),
            );
            this.output.append(
              el("a", `Descargar ${format.toUpperCase()}`, {
                href: this.base + "/export/" + format,
                download: `design.${format}`,
              }),
            );
          }),
      );
    this.output = el("section", null, {
      className: "fj-design-results",
      ariaLive: "polite",
    });
    root.append(this.output);
  }
}
export function renderDesign(entry, root) {
  let c = controllers.get(entry.ficha.id);
  if (!c) {
    c = new Workspace(entry, root);
    controllers.set(entry.ficha.id, c);
  }
  c.root = root;
  root.dataset.designDocument = entry.ficha.id;
  if (c.dirty && c.data) c.render();
  else void c.load();
}
export function designHit(entry, hit) {
  controllers.get(entry.ficha.id)?.onHit(hit);
}
export function disposeDesign(id) {
  controllers.delete(id);
}
