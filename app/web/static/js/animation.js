import { poseDeltas, validateMotionAssembly } from "./assembly-motion.js?v=1";
import { overlapPairs, transformedBounds } from "./motion-overlaps.js?v=1";
import { springForce } from "./motion-physics.js?v=1";
import { interpolateTracks } from "./motion-tracks.js?v=1";

function make(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

export class AnimationView {
  constructor(container, viewer, cadToolbar, beforeAnimation = () => true, docId = null) {
    this.container = container;
    this.viewer = viewer;
    this.cadToolbar = cadToolbar;
    this.beforeAnimation = beforeAnimation;
    this.docId = docId;
    this.mode = "cad";
    this.assembly = null;
    this.fraction = 0;
    this.baseOverlaps = new Set();
    this.lastScreenAt = 0;
    this.playing = false;
    this.frame = null;
    this.multiRows = new Map();
    this.motionLimit = 1;

    this.switch = make("div", "fj-mode-switch");
    this.switch.setAttribute("role", "group");
    this.switch.setAttribute("aria-label", "Modo de trabajo");
    this.cadButton = make("button", "fj-btn", "CAD");
    this.animationButton = make("button", "fj-btn", "Animación");
    for (const button of [this.cadButton, this.animationButton]) button.type = "button";
    this.cadButton.onclick = () => this.setMode("cad");
    this.animationButton.onclick = () => this.setMode("animation");
    this.switch.append(this.cadButton, this.animationButton);
    container.append(this.switch);

    this.panel = make("section", "fj-animation");
    this.panel.setAttribute("aria-label", "Vista de animación");
    this.panel.append(make("h3", "", "Movimiento del ensamble"));
    this.hint = make("p", "fj-animation__hint");
    this.panel.append(this.hint);
    const jointLabel = make("label", "fj-animation__field", "Articulación");
    this.jointSelect = make("select", "fj-select");
    jointLabel.append(this.jointSelect);
    const sceneLabel = make("label", "fj-animation__field", "Escena");
    this.sceneSelect = make("select", "fj-select");
    sceneLabel.append(this.sceneSelect);
    this.sceneCaption = make("p", "fj-animation__caption");
    this.sceneCaption.setAttribute("aria-live", "polite");
    this.sceneCaption.hidden = true;
    this.sceneSteps = make("ol", "fj-animation__steps");
    this.sceneSteps.hidden = true;
    this.panel.append(sceneLabel, this.sceneSteps, this.sceneCaption);
    this.sceneLabel = sceneLabel; sceneLabel.hidden = true;
    this.scenes = []; this.scene = null;
    this.sceneSelect.onchange = () => this.selectScene();
    this.panel.append(jointLabel);
    const limits = make("div", "fj-animation__limits");
    const fromLabel = make("label", "fj-animation__field", "Desde");
    this.from = make("input", "fj-input"); this.from.type = "number"; this.from.step = "any";
    const toLabel = make("label", "fj-animation__field", "Hasta");
    this.to = make("input", "fj-input"); this.to.type = "number"; this.to.step = "any";
    fromLabel.append(this.from); toLabel.append(this.to); limits.append(fromLabel, toLabel);
    this.panel.append(limits);
    this.singleControls = [jointLabel, limits];
    const multiLabel = make("label", "fj-animation__multi-toggle");
    this.multiToggle = make("input", ""); this.multiToggle.type = "checkbox";
    multiLabel.append(this.multiToggle, document.createTextNode(" Coordinar varias articulaciones"));
    this.panel.append(multiLabel);
    this.multiPanel = make("div", "fj-animation__multi");
    this.multiPanel.hidden = true;
    this.panel.append(this.multiPanel);
    this.multiLabel = multiLabel;
    this.multiToggle.onchange = () => {
      this.pause(); this.motionLimit = 1;
      this.motionResult.textContent = "";
      for (const control of this.singleControls) control.hidden = this.multiToggle.checked;
      this.multiPanel.hidden = !this.multiToggle.checked;
      this.spring.hidden = this.multiToggle.checked || this.currentJoint()?.tipo !== "deslizamiento";
      this.setFraction(0);
    };
    const playback = make("div", "fj-animation__playback");
    this.playButton = make("button", "fj-btn fj-btn--primary", "Reproducir");
    this.playButton.type = "button";
    this.playButton.onclick = () => { if (this.playing) this.pause(); else { this.motionLimit = 1; this.play(); } };
    this.scrub = make("input", "fj-animation__scrub");
    this.scrub.type = "range"; this.scrub.min = "0"; this.scrub.max = "1000"; this.scrub.value = "0";
    this.scrub.setAttribute("aria-label", "Posición del movimiento");
    this.scrub.oninput = () => { this.pause(); this.motionLimit = 1; this.setFraction(Number(this.scrub.value) / 1000); };
    playback.append(this.playButton, this.scrub);
    this.panel.append(playback);
    this.checkButton = make("button", "fj-btn", "Comprobar posición");
    this.checkButton.type = "button";
    this.checkButton.onclick = () => this.checkPosition();
    this.checkResult = make("p", "fj-animation__check");
    this.checkResult.setAttribute("role", "status");
    this.panel.append(this.checkButton, this.checkResult);
    this.motionButton = make("button", "fj-btn", "Reproducir con contacto");
    this.motionButton.type = "button";
    this.motionButton.onclick = () => this.checkMotion();
    this.motionResult = make("p", "fj-animation__check");
    this.motionResult.setAttribute("role", "status");
    this.panel.append(this.motionButton, this.motionResult);
    this.spring = make("details", "fj-animation__spring");
    this.spring.append(make("summary", "", "Resorte de compresión (opcional)"));
    const springFields = make("div", "fj-animation__spring-fields");
    const springField = (label, type = "number") => {
      const wrapper = make("label", "fj-animation__field", label);
      const input = make("input", "fj-input"); input.type = type; input.step = "any"; input.min = "0";
      wrapper.append(input); springFields.append(wrapper);
      return input;
    };
    this.stiffness = springField("Rigidez (N/mm)");
    this.referenceForce = springField("Fuerza en «Desde» (N)");
    const directionLabel = make("label", "fj-animation__field", "Se comprime cuando el valor");
    this.springDirection = make("select", "fj-select");
    for (const [value, text] of [["", "Selecciona"], ["1", "Aumenta"], ["-1", "Disminuye"]]) {
      const option = make("option", "", text); option.value = value; this.springDirection.append(option);
    }
    directionLabel.append(this.springDirection); springFields.append(directionLabel);
    this.springResult = make("p", "fj-animation__check");
    this.spring.append(springFields, this.springResult);
    for (const input of [this.stiffness, this.referenceForce, this.springDirection]) input.oninput = () => this.updateSpring();
    this.panel.append(this.spring);
    this.state = make("p", "fj-animation__state");
    this.screen = make("p", "fj-animation__screen");
    this.panel.append(this.state, this.screen,
      make("p", "fj-animation__disclaimer", "Los cuerpos son rígidos. «Reproducir con contacto» examina 13 posiciones y se detiene antes del primer choque nuevo observado; puede haber choques entre muestras. No calcula fuerzas de contacto, rozamiento, flexión ni inercia."));
    this.jointSelect.onchange = () => this.selectJoint();
    this.from.onchange = () => { this.pause(); this.motionLimit = 1; this.motionResult.textContent = ""; this.setFraction(0); };
    this.to.onchange = () => { this.pause(); this.motionLimit = 1; this.motionResult.textContent = ""; this.setFraction(0); };
    container.append(this.panel);
    this.setMode("cad");
  }

  setAssembly(data, assemblyRevision, meshRevision) {
    this.pause();
    this.resetPose();
    this.assembly = data;
    this.motionLimit = 1;
    this.motionResult.textContent = "";
    this.assemblyRevision = assemblyRevision;
    this.meshRevision = meshRevision;
    this.clearCheck();
    const previous = this.jointSelect.value;
    this.jointSelect.replaceChildren();
    for (const joint of data?.articulaciones ?? []) {
      if (joint.tipo === "fijo") continue;
      const option = make("option", "", `${joint.id} · ${joint.tipo === "giro" ? "giro" : "deslizamiento"}`);
      option.value = joint.id;
      this.jointSelect.append(option);
    }
    this.multiRows.clear();
    this.multiPanel.replaceChildren();
    for (const joint of data?.articulaciones ?? []) {
      if (joint.tipo === "fijo") continue;
      const row = make("div", "fj-animation__multi-row");
      const label = make("label", "fj-animation__multi-name");
      const enabled = make("input", ""); enabled.type = "checkbox";
      label.append(enabled, document.createTextNode(` ${joint.id} (${joint.tipo === "giro" ? "°" : "mm"})`));
      const from = make("input", "fj-input"); from.type = "number"; from.step = "any";
      const to = make("input", "fj-input"); to.type = "number"; to.step = "any";
      for (const input of [from, to]) { input.min = String(joint.limites[0]); input.max = String(joint.limites[1]); }
      const current = Number(data.valores[joint.id] ?? 0);
      from.value = String(current);
      to.value = String(current === joint.limites[1] ? joint.limites[0] : joint.limites[1]);
      from.setAttribute("aria-label", `${joint.id}: desde`);
      to.setAttribute("aria-label", `${joint.id}: hasta`);
      row.append(label, from, to); this.multiPanel.append(row);
      this.multiRows.set(joint.id, { enabled, from, to });
      for (const input of [enabled, from, to]) input.onchange = () => { this.pause(); this.motionLimit = 1; this.motionResult.textContent = ""; this.setFraction(0); };
    }
    if ([...this.jointSelect.options].some(option => option.value === previous)) this.jointSelect.value = previous;
    this.selectJoint();
    this.loadScenes();
  }

  async loadScenes() {
    this.scenes = []; this.scene = null;
    this.sceneSelect.replaceChildren(make("option", "", "Manual (sin escena)"));
    this.sceneSelect.options[0].value = "";
    this.sceneLabel.hidden = true;
    if (!this.docId || !this.assembly?.articulaciones?.length) return;
    try {
      const response = await fetch(`/documentos/${encodeURIComponent(this.docId)}/ensamble/escenas`, { cache: "no-store" });
      if (!response.ok) return;
      const data = await response.json();
      this.scenes = (data.escenas ?? []).filter(scene => Array.isArray(scene.claves) && scene.claves.length > 1);
    } catch { return; }
    this.scenes.forEach((scene, index) => {
      const option = make("option", "", scene.nombre ?? `Escena ${index + 1}`);
      option.value = String(index);
      this.sceneSelect.append(option);
    });
    this.sceneLabel.hidden = !this.scenes.length;
  }

  selectScene() {
    this.pause(); this.motionLimit = 1;
    this.scene = this.sceneSelect.value === "" ? null : this.scenes[Number(this.sceneSelect.value)];
    const manual = !this.scene;
    for (const control of this.singleControls) control.hidden = !manual || this.multiToggle.checked;
    this.multiLabel.hidden = !manual;
    this.multiPanel.hidden = !manual || !this.multiToggle.checked;
    this.checkButton.hidden = this.motionButton.hidden = !manual;
    this.spring.hidden = !manual || this.multiToggle.checked || this.currentJoint()?.tipo !== "deslizamiento";
    this.sceneCaption.hidden = manual;
    this.buildSteps();
    this.ghost(manual ? [] : (this.scene.transparentes ?? []));
    this.setFraction(0);
  }

  /** Numbered step list: each keyframe with a `titulo` starts a step. */
  buildSteps() {
    this.sceneSteps.replaceChildren();
    this.stepItems = [];
    this.sceneSteps.hidden = !this.scene;
    if (!this.scene) return;
    const keys = this.scene.claves, start = keys[0].t;
    for (const key of keys) {
      if (!key.titulo) continue;
      const item = make("li", "fj-animation__step");
      const button = make("button", "", key.titulo); button.type = "button";
      button.onclick = () => { this.pause(); this.setFraction((key.t - start) / this.sceneDuration()); };
      item.append(button); this.sceneSteps.append(item);
      this.stepItems.push({ t: key.t, item });
    }
  }

  /** Accent colour on the pieces doing the work in the current step. */
  highlight(names) {
    const key = names.join("|");
    if (key === this.highlightKey) return;
    this.highlightKey = key;
    for (const [mesh, material] of this.highlighted ?? []) { mesh.material.dispose(); mesh.material = material; }
    this.highlighted = [];
    for (const name of names) {
      const mesh = this.viewer.pieces.get(name)?.mesh;
      if (!mesh) continue;
      this.highlighted.push([mesh, mesh.material]);
      mesh.material = mesh.material.clone();
      mesh.material.transparent = false; mesh.material.opacity = 1; mesh.material.depthWrite = true;
      mesh.material.color?.set(0xf07a2a);
      mesh.material.emissive?.set(0x5a2208);
    }
  }

  /** Scene-only see-through pieces (e.g. a frame hiding the mechanism). */
  ghost(names) {
    this.highlight([]);
    for (const [mesh, material] of this.ghosted ?? []) { mesh.material.dispose(); mesh.material = material; }
    this.ghosted = [];
    for (const name of names) {
      const mesh = this.viewer.pieces.get(name)?.mesh;
      if (!mesh) continue;
      this.ghosted.push([mesh, mesh.material]);
      mesh.material = mesh.material.clone();
      Object.assign(mesh.material, { transparent: true, opacity: 0.12, depthWrite: false });
    }
    this.viewer.render();
  }

  sceneDuration() {
    const keys = this.scene.claves;
    return Math.max(1e-6, keys[keys.length - 1].t - keys[0].t);
  }

  /** Values and caption at `fraction` of the scene: linear between keyframes;
   * joints a keyframe omits keep their last given value. */
  sceneValues(fraction) {
    const keys = this.scene.claves;
    const time = keys[0].t + fraction * this.sceneDuration();
    const values = { ...this.assembly.valores };
    let caption = "", title = "", marked = [];
    let index = 0;
    for (let i = 0; i < keys.length; i++) {
      if (keys[i].t <= time) index = i;
      if (keys[i].t <= time + 1e-9) {
        if (keys[i].rotulo) caption = keys[i].rotulo;
        if (keys[i].titulo) title = keys[i].titulo;
        if (keys[i].resaltar) marked = keys[i].resaltar;
      }
      if (keys[i].t <= time) Object.assign(values, keys[i].valores);
    }
    const next = keys[index + 1];
    if (next) {
      const span = next.t - keys[index].t;
      const k = span > 0 ? (time - keys[index].t) / span : 1;
      for (const [id, target] of Object.entries(next.valores)) {
        const start = Number(values[id] ?? 0);
        values[id] = start + (Number(target) - start) * k;
      }
    }
    for (const joint of this.assembly.articulaciones) {
      if (joint.tipo === "fijo" || values[joint.id] === undefined) continue;
      values[joint.id] = Math.max(joint.limites[0], Math.min(joint.limites[1], Number(values[joint.id])));
    }
    return { values, caption, title, marked, time };
  }

  setSceneFraction(error) {
    if (this.mode !== "animation") { this.resetPose(); return; }
    this.playButton.disabled = this.scrub.disabled = Boolean(error);
    if (error) { this.state.textContent = error; this.resetPose(); return; }
    const { values, caption, title, marked, time } = this.sceneValues(this.fraction);
    this.highlight(marked);
    for (const [name, matrix] of poseDeltas(this.assembly, values)) {
      const mesh = this.viewer.pieces.get(name)?.mesh;
      if (!mesh) continue;
      mesh.matrixAutoUpdate = false;
      mesh.matrix.fromArray(matrix);
      mesh.updateMatrixWorld(true);
    }
    this.viewer.render();
    this.sceneCaption.replaceChildren();
    if (title) this.sceneCaption.append(make("strong", "", title), make("br"));
    this.sceneCaption.append(caption);
    let current = -1;
    this.stepItems?.forEach((step, index) => { if (step.t <= time + 1e-9) current = index; });
    this.stepItems?.forEach((step, index) => {
      step.item.classList.toggle("is-current", index === current);
      step.item.classList.toggle("is-done", index < current);
    });
    this.state.textContent = `${time.toFixed(1)} s / ${this.sceneDuration().toFixed(1)} s`;
    this.screen.textContent = "";
  }

  selectJoint() {
    this.pause();
    this.motionLimit = 1;
    this.motionResult.textContent = "";
    const joint = this.currentJoint();
    if (joint) {
      const current = Number(this.assembly.valores[joint.id] ?? 0);
      this.from.min = this.to.min = String(joint.limites[0]);
      this.from.max = this.to.max = String(joint.limites[1]);
      this.from.value = String(current);
      this.to.value = String(current === joint.limites[1] ? joint.limites[0] : joint.limites[1]);
      this.from.title = this.to.title = joint.tipo === "giro" ? "grados" : "milímetros";
    }
    this.setFraction(0);
    this.spring.hidden = this.multiToggle.checked || joint?.tipo !== "deslizamiento";
  }

  currentJoint() {
    return this.assembly?.articulaciones?.find(joint => joint.id === this.jointSelect.value);
  }

  available() {
    const error = validateMotionAssembly(this.assembly, new Set(this.viewer.pieces.keys()));
    if (error) return error;
    if (!this.assemblyRevision || !this.meshRevision || this.assemblyRevision !== this.meshRevision) {
      return "La malla y las articulaciones tienen revisiones distintas. Espera a que se actualice el documento.";
    }
    return !this.currentJoint() ? "El ensamble solo tiene uniones fijas." : "";
  }

  setMode(mode) {
    if (mode === "animation" && !this.beforeAnimation()) return;
    if (mode === "cad") { this.pause(); this.clearCheck(); this.resetPose(); this.ghost([]); }
    else if (this.scene) this.ghost(this.scene.transparentes ?? []);
    this.mode = mode;
    this.cadToolbar.hidden = mode !== "cad";
    this.panel.hidden = mode !== "animation";
    this.cadButton.setAttribute("aria-pressed", String(mode === "cad"));
    this.animationButton.setAttribute("aria-pressed", String(mode === "animation"));
    this.container.classList.toggle("fj-viewport--animation", mode === "animation");
    if (mode === "animation") this.setFraction(0);
  }

  resetPose() {
    for (const piece of this.viewer.pieces.values()) {
      piece.mesh.matrix.identity();
      piece.mesh.matrixAutoUpdate = true;
      piece.mesh.updateMatrixWorld(true);
    }
    this.viewer.render();
  }

  endpoints() {
    const joint = this.currentJoint();
    if (!joint) return null;
    const low = joint.limites[0], high = joint.limites[1];
    const from = Number(this.from.value), to = Number(this.to.value);
    if (!Number.isFinite(from) || !Number.isFinite(to) || from < low || from > high || to < low || to > high) return null;
    return { joint, from, to };
  }

  tracks() {
    if (!this.multiToggle.checked) {
      const endpoints = this.endpoints();
      return endpoints ? { [endpoints.joint.id]: { from: endpoints.from, to: endpoints.to } } : null;
    }
    const selected = {};
    for (const [id, row] of this.multiRows) {
      if (!row.enabled.checked) continue;
      if (row.from.value === "" || row.to.value === "") return null;
      selected[id] = { from: Number(row.from.value), to: Number(row.to.value) };
    }
    return Object.keys(selected).length && interpolateTracks(this.assembly, selected, 0) ? selected : null;
  }

  setFraction(fraction, analyze = true) {
    this.clearCheck();
    this.fraction = Math.max(0, Math.min(1, fraction));
    this.scrub.value = String(Math.round(this.fraction * 1000));
    const error = this.available();
    if (this.scene) {
      this.hint.textContent = error || (this.scene.descripcion ?? "");
      this.setSceneFraction(error);
      return;
    }
    this.hint.textContent = error || (this.multiToggle.checked ?
      "Marca varias articulaciones y asigna sus posiciones de inicio y fin." :
      "Mueve una articulación; las demás conservan su posición actual.");
    const tracks = this.tracks();
    const changes = tracks && Object.values(tracks).some(track => track.from !== track.to);
    this.playButton.disabled = Boolean(error || !tracks || !changes);
    this.scrub.disabled = Boolean(error || !tracks);
    this.checkButton.disabled = Boolean(error || !tracks || !this.docId);
    this.motionButton.disabled = Boolean(error || !tracks || !changes || !this.docId);
    for (const control of [this.jointSelect, this.from, this.to]) control.disabled = Boolean(error);
    if (this.mode !== "animation") { this.resetPose(); return; }
    if (error || !tracks) {
      this.state.textContent = error || "Selecciona articulaciones y valores dentro de sus límites.";
      this.screen.textContent = "";
      this.resetPose();
      return;
    }
    const targetValues = interpolateTracks(this.assembly, tracks, this.fraction);
    const deltas = poseDeltas(this.assembly, targetValues);
    for (const [name, matrix] of deltas) {
      const mesh = this.viewer.pieces.get(name).mesh;
      mesh.matrixAutoUpdate = false;
      mesh.matrix.fromArray(matrix);
      mesh.updateMatrixWorld(true);
    }
    this.viewer.render();
    this.state.textContent = `${Object.entries(tracks).map(([id]) => `${id}: ${targetValues[id].toFixed(2)}`).join(" · ")} · ${Math.round(this.fraction * 100)} %`;
    this.updateSpring();
    if (analyze) {
      const overlaps = this.overlapPairs();
      if (this.fraction === 0) this.baseOverlaps = overlaps;
      const newCount = [...overlaps].filter(pair => !this.baseOverlaps.has(pair)).length;
      this.screen.textContent = `${newCount} pares nuevos de cajas envolventes superpuestas (muestreo espacial preliminar).`;
    }
  }

  overlapPairs() {
    const boxes = [];
    let id = 0;
    for (const data of this.viewer.pieces.values()) {
      const mesh = data.mesh;
      if (mesh.geometry.boundingBox) boxes.push(transformedBounds(mesh.geometry.boundingBox, mesh.matrixWorld.elements, id));
      id++;
    }
    return overlapPairs(boxes).pairs;
  }

  clearCheck() {
    this.checkController?.abort();
    this.checkController = null;
    if (this.checkResult) this.checkResult.textContent = "";
  }

  updateSpring() {
    if (!this.springResult || this.spring.hidden) return;
    const endpoints = this.endpoints();
    const stiffness = this.stiffness.value === "" ? NaN : Number(this.stiffness.value);
    const referenceForce = this.referenceForce.value === "" ? NaN : Number(this.referenceForce.value);
    const direction = Number(this.springDirection.value);
    const force = endpoints && springForce({ stiffness, referenceForce,
      referencePosition: endpoints.from,
      position: endpoints.from + (endpoints.to - endpoints.from) * this.fraction,
      direction });
    this.springResult.textContent = force === null ? "Introduce los tres datos para calcular la fuerza." :
      `Fuerza del resorte: ${force.toFixed(3)} N (estimación estática).`;
  }

  async checkPosition() {
    if (this.available() || !this.tracks() || !this.docId) return;
    this.pause();
    this.clearCheck();
    const tracks = this.tracks();
    const values = interpolateTracks(this.assembly, tracks, this.fraction);
    const controller = new AbortController();
    this.checkController = controller;
    this.checkButton.disabled = true;
    this.checkResult.textContent = "Comprobando sólidos exactos…";
    try {
      const response = await fetch(`/documentos/${encodeURIComponent(this.docId)}/ensamble/comprobar-escena`, {
        method: "POST", headers: { "Content-Type": "application/json" }, signal: controller.signal,
        cache: "no-store", body: JSON.stringify({ revision: this.meshRevision, valores: values,
          articulaciones_activas: Object.keys(tracks) }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "No se pudo comprobar la posición.");
      if (this.checkController !== controller) return;
      const collision = result.choques.length;
      const gap = result.holgura_minima_mm === null ? "sin holgura medida dentro de 1 mm" :
        `holgura mínima ${result.holgura_minima_mm.toFixed(3)} mm`;
      const status = result.completo ? "Muestra completa" : "Muestra incompleta";
      this.checkResult.textContent = `${status}: ${collision} choques mayores de ${result.umbral_choque_mm3} mm³; ${result.contactos.length} contactos; ${gap}. ${result.pares_comprobados}/${result.pares_en_radio} pares cercanos comprobados.` +
        (result.errores.length ? ` ${result.errores.length} errores de geometría.` : "") +
        (result.choques.length ? ` Primer choque: ${result.choques[0].a} × ${result.choques[0].b}.` : "");
    } catch (error) {
      if (error.name !== "AbortError" && this.checkController === controller) this.checkResult.textContent = error.message;
    } finally {
      if (this.checkController === controller) { this.checkController = null; this.checkButton.disabled = false; }
    }
  }

  async checkMotion() {
    const tracks = this.tracks();
    if (this.available() || !tracks || !this.docId) return;
    this.pause();
    this.clearCheck();
    this.motionResult.textContent = "Comprobando 13 posiciones del recorrido…";
    this.motionButton.disabled = true;
    const controller = new AbortController();
    this.checkController = controller;
    const from = Object.fromEntries(Object.entries(tracks).map(([id, track]) => [id, track.from]));
    const to = Object.fromEntries(Object.entries(tracks).map(([id, track]) => [id, track.to]));
    try {
      const response = await fetch(`/documentos/${encodeURIComponent(this.docId)}/ensamble/comprobar-recorrido`, {
        method: "POST", headers: { "Content-Type": "application/json" }, signal: controller.signal,
        cache: "no-store", body: JSON.stringify({ revision: this.meshRevision,
          valores_desde: from, valores_hasta: to, muestras: 12 }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "No se pudo comprobar el recorrido.");
      if (this.checkController !== controller) return;
      if (!result.completo) {
        this.motionResult.textContent = `Comprobación incompleta en ${result.muestras_comprobadas} posiciones; revisa la geometría antes de reproducir con contacto.`;
        return;
      }
      this.motionLimit = result.primer_choque ? result.fraccion_segura_muestreada : 1;
      this.motionResult.textContent = result.primer_choque ?
        `Choque nuevo entre ${result.primer_choque.par.a} y ${result.primer_choque.par.b} cerca del ${Math.round(result.primer_choque.fraccion * 100)} %. Se reproduce hasta el ${Math.round(this.motionLimit * 100)} % seguro muestreado.` :
        `Sin choques nuevos en ${result.muestras_comprobadas} posiciones comprobadas. ${result.choques_iniciales} choques ya presentes al inicio.`;
      this.setFraction(0);
      if (this.motionLimit > 0) this.play();
    } catch (error) {
      if (error.name !== "AbortError" && this.checkController === controller) this.motionResult.textContent = error.message;
    } finally {
      if (this.checkController === controller) { this.checkController = null; this.motionButton.disabled = false; }
    }
  }

  play() {
    if (this.available() || (!this.scene && !this.tracks())) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      this.setFraction(this.motionLimit);
      return;
    }
    if (this.fraction >= this.motionLimit) this.setFraction(0);
    this.playing = true;
    this.playButton.textContent = "Pausar";
    let last = null;
    const tick = now => {
      if (!this.playing) return;
      if (last !== null) {
        const next = Math.min(this.motionLimit, this.fraction + Math.min(now - last, 100) / (this.scene ? this.sceneDuration() * 1000 : 4000));
        const analyze = now - this.lastScreenAt >= 100 || next === this.motionLimit;
        this.setFraction(next, analyze);
        if (analyze) this.lastScreenAt = now;
      }
      last = now;
      if (this.fraction >= this.motionLimit) this.pause();
      else this.frame = requestAnimationFrame(tick);
    };
    this.frame = requestAnimationFrame(tick);
  }

  pause() {
    this.playing = false;
    if (this.frame !== null) cancelAnimationFrame(this.frame);
    this.frame = null;
    this.playButton.textContent = "Reproducir";
  }

  dispose() { this.ghost([]); this.pause(); this.clearCheck(); this.resetPose(); this.switch.remove(); this.panel.remove(); }
}
