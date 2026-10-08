import { poseDeltas, validateMotionAssembly } from "./assembly-motion.js?v=1";
import { overlapPairs, transformedBounds } from "./motion-overlaps.js?v=1";
import { springForce } from "./motion-physics.js?v=1";

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
    this.panel.append(jointLabel);
    const limits = make("div", "fj-animation__limits");
    const fromLabel = make("label", "fj-animation__field", "Desde");
    this.from = make("input", "fj-input"); this.from.type = "number"; this.from.step = "any";
    const toLabel = make("label", "fj-animation__field", "Hasta");
    this.to = make("input", "fj-input"); this.to.type = "number"; this.to.step = "any";
    fromLabel.append(this.from); toLabel.append(this.to); limits.append(fromLabel, toLabel);
    this.panel.append(limits);
    const playback = make("div", "fj-animation__playback");
    this.playButton = make("button", "fj-btn fj-btn--primary", "Reproducir");
    this.playButton.type = "button";
    this.playButton.onclick = () => this.playing ? this.pause() : this.play();
    this.scrub = make("input", "fj-animation__scrub");
    this.scrub.type = "range"; this.scrub.min = "0"; this.scrub.max = "1000"; this.scrub.value = "0";
    this.scrub.setAttribute("aria-label", "Posición del movimiento");
    this.scrub.oninput = () => { this.pause(); this.setFraction(Number(this.scrub.value) / 1000); };
    playback.append(this.playButton, this.scrub);
    this.panel.append(playback);
    this.checkButton = make("button", "fj-btn", "Comprobar posición");
    this.checkButton.type = "button";
    this.checkButton.onclick = () => this.checkPosition();
    this.checkResult = make("p", "fj-animation__check");
    this.checkResult.setAttribute("role", "status");
    this.panel.append(this.checkButton, this.checkResult);
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
      make("p", "fj-animation__disclaimer", "La comprobación exacta solo evalúa esta posición y las piezas que cruzan el grupo móvil a 1 mm o menos. El resorte usa tus datos y la ley de Hooke; no simula rozamiento, flexión, inercia ni todo el recorrido."));
    this.jointSelect.onchange = () => this.selectJoint();
    this.from.onchange = () => { this.pause(); this.setFraction(0); };
    this.to.onchange = () => { this.pause(); this.setFraction(0); };
    container.append(this.panel);
    this.setMode("cad");
  }

  setAssembly(data, assemblyRevision, meshRevision) {
    this.pause();
    this.resetPose();
    this.assembly = data;
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
    if ([...this.jointSelect.options].some(option => option.value === previous)) this.jointSelect.value = previous;
    this.selectJoint();
  }

  selectJoint() {
    this.pause();
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
    this.spring.hidden = joint?.tipo !== "deslizamiento";
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
    if (mode === "cad") { this.pause(); this.clearCheck(); this.resetPose(); }
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

  setFraction(fraction, analyze = true) {
    this.clearCheck();
    this.fraction = Math.max(0, Math.min(1, fraction));
    this.scrub.value = String(Math.round(this.fraction * 1000));
    const error = this.available();
    this.hint.textContent = error || "Mueve una articulación; las demás conservan su posición actual.";
    const endpoints = this.endpoints();
    this.playButton.disabled = Boolean(error || !endpoints || endpoints.from === endpoints.to);
    this.scrub.disabled = Boolean(error || !endpoints);
    this.checkButton.disabled = Boolean(error || !endpoints || !this.docId);
    for (const control of [this.jointSelect, this.from, this.to]) control.disabled = Boolean(error);
    if (this.mode !== "animation") { this.resetPose(); return; }
    if (error || !endpoints) {
      this.state.textContent = endpoints ? "" : "Introduce valores dentro de los límites de la articulación.";
      this.screen.textContent = "";
      this.resetPose();
      return;
    }
    const { joint, from, to } = endpoints;
    const value = from + (to - from) * this.fraction;
    const targetValues = { ...this.assembly.valores, [joint.id]: value };
    const deltas = poseDeltas(this.assembly, targetValues);
    for (const [name, matrix] of deltas) {
      const mesh = this.viewer.pieces.get(name).mesh;
      mesh.matrixAutoUpdate = false;
      mesh.matrix.fromArray(matrix);
      mesh.updateMatrixWorld(true);
    }
    this.viewer.render();
    this.state.textContent = `${joint.id}: ${value.toFixed(2)} ${joint.tipo === "giro" ? "°" : "mm"} · ${Math.round(this.fraction * 100)} %`;
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
    if (this.available() || !this.endpoints() || !this.docId) return;
    this.pause();
    this.clearCheck();
    const { joint, from, to } = this.endpoints();
    const value = from + (to - from) * this.fraction;
    const controller = new AbortController();
    this.checkController = controller;
    this.checkButton.disabled = true;
    this.checkResult.textContent = "Comprobando sólidos exactos…";
    const params = new URLSearchParams({ articulacion: joint.id, valor: String(value), revision: this.meshRevision });
    try {
      const response = await fetch(`/documentos/${encodeURIComponent(this.docId)}/ensamble/comprobar-posicion?${params}`, {
        signal: controller.signal, cache: "no-store",
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

  play() {
    if (this.available() || !this.endpoints()) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      this.setFraction(1);
      return;
    }
    if (this.fraction >= 1) this.setFraction(0);
    this.playing = true;
    this.playButton.textContent = "Pausar";
    let last = null;
    const tick = now => {
      if (!this.playing) return;
      if (last !== null) {
        const next = Math.min(1, this.fraction + Math.min(now - last, 100) / 4000);
        const analyze = now - this.lastScreenAt >= 100 || next === 1;
        this.setFraction(next, analyze);
        if (analyze) this.lastScreenAt = now;
      }
      last = now;
      if (this.fraction >= 1) this.pause();
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

  dispose() { this.pause(); this.clearCheck(); this.resetPose(); this.switch.remove(); this.panel.remove(); }
}
