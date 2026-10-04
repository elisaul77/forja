// Forja — visor three.js de una sola malla, con cámara/controles propios.
// Cada instancia de ForjaViewer es independiente: renderer, escena, cámara
// y OrbitControls propios, para que cada pestaña conserve su estado.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { STLLoader } from "three/addons/loaders/STLLoader.js";
import { decodePieceBundle } from "./piece-data.js?v=8";

const loader = new STLLoader();

/**
 * Resuelve un token CSS (--fj-*) a un color usable por three.js.
 *
 * `getComputedStyle(...).color` no alcanza sola: para colores `oklch()`
 * (usados por varios tokens `--fj-*`, ver `forja-tokens.css`) el navegador
 * puede devolver el `oklch(...)` sin convertir a `rgb()` (queda fuera del
 * gamut sRGB al resolverlo así), y el parser de color de three.js no
 * entiende `oklch()` — cae en blanco en silencio. El canvas 2D sí resuelve
 * `oklch()` a RGB real (`fillStyle` sigue CSS Color 4 completo), así que
 * se usa como conversor final. Nunca se hardcodea un color en JS: todo
 * sale de las custom properties del tema.
 */
function cssVarColor(nombre, fallback) {
  const sonda = document.createElement("span");
  sonda.style.display = "none";
  sonda.style.color = `var(${nombre})`;
  document.body.appendChild(sonda);
  const resuelto = getComputedStyle(sonda).color;
  document.body.removeChild(sonda);
  return _aRgbPorCanvas(resuelto) || resuelto || fallback;
}

const _lienzoColor = document.createElement("canvas");
_lienzoColor.width = 1;
_lienzoColor.height = 1;
const _ctxColor = _lienzoColor.getContext("2d");

function _aRgbPorCanvas(color) {
  if (!color || !_ctxColor) return null;
  try {
    _ctxColor.fillStyle = "#000000"; // limpia cualquier valor previo por si `color` es inválido
    _ctxColor.fillStyle = color;
    _ctxColor.fillRect(0, 0, 1, 1);
    const [r, g, b] = _ctxColor.getImageData(0, 0, 1, 1).data;
    return `rgb(${r}, ${g}, ${b})`;
  } catch {
    return null;
  }
}

export class ForjaViewer {
  constructor(container) {
    this.container = container;

    this.renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(window.devicePixelRatio || 1);
    container.appendChild(this.renderer.domElement);

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(cssVarColor("--fj-sunk", "#14110f"));

    this.camera = new THREE.PerspectiveCamera(45, 1, 0.1, 1e6);
    this.camera.up.set(0, 0, 1);
    this.camera.position.set(150, -150, 120);

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = false; // renderizado por evento, no por bucle continuo
    this.controls.target.set(0, 0, 0);
    this.controls.addEventListener("change", () => this.render());

    // Lighting follows the camera (headlight): whatever side the user orbits
    // to is lit, so no face is left in the dark. A soft sky/ground fill keeps
    // shape readable and the ambient stays low to preserve contrast.
    this.scene.add(new THREE.AmbientLight(0xffffff, 0.25));
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x444444, 0.35));
    const linterna = new THREE.DirectionalLight(0xffffff, 1.1);
    linterna.position.set(0.4, 0.6, 0); // a bit up-left of the eye, for relief
    linterna.target.position.set(0, 0, -1);
    this.camera.add(linterna);
    this.camera.add(linterna.target);
    this.scene.add(this.camera);

    const colorGrid = cssVarColor("--fj-line", "#3a3532");
    this.grid = new THREE.GridHelper(400, 40, colorGrid, colorGrid);
    this.grid.rotation.x = Math.PI / 2; // plano XY como suelo (convención Z arriba)
    this.scene.add(this.grid);
    this.scene.add(new THREE.AxesHelper(100));

    this.mesh = null;
    this.wireframe = false;
    this.pieces = new Map();
    this.selectedPiece = null;
    this.isolatedPiece = null;
    this.pieceManifest = null;
    this._piecePointerDown = event => {
      this._piecePointerStart = event.button === 0 ? [event.clientX, event.clientY] : null;
    };
    this._piecePointerUp = event => {
      const start = this._piecePointerStart;
      this._piecePointerStart = null;
      if (!start || event.button !== 0 || Math.hypot(event.clientX - start[0], event.clientY - start[1]) > 4 ||
          (this.canSelectPiece && !this.canSelectPiece())) return;
      const hit = this.raycastearMalla(event.clientX, event.clientY);
      this.selectPiece(hit?.pieceName ?? null);
      this.onPieceSelection?.();
    };
    this.renderer.domElement.addEventListener("pointerdown", this._piecePointerDown);
    this.renderer.domElement.addEventListener("pointerup", this._piecePointerUp);

    // Fase 4: anotaciones (pines + trazos de pizarra) y previsualización del
    // plano de dibujo, en un grupo aparte para poder limpiarlas sin tocar
    // la malla del documento.
    this.raycaster = new THREE.Raycaster();
    this._grupoAnotaciones = new THREE.Group();
    this.scene.add(this._grupoAnotaciones);
    this._planoPizarra = null;

    this._resizeObserver = new ResizeObserver(() => this.resize());
    this._resizeObserver.observe(container);
    this.resize();
  }

  /** Centro de la malla cargada (o el origen si no hay ninguna) — usado
   * para centrar el plano XY/XZ/YZ de la pizarra, igual que `pizarra.py`. */
  centroEscena() {
    if (this.mesh && this.mesh.geometry.boundingSphere) {
      const c = this.mesh.geometry.boundingSphere.center;
      return [c.x, c.y, c.z];
    }
    return [0, 0, 0];
  }

  /** Vector unitario que mira "hacia la cámara" (normal del plano de
   * pizarra en modo "vista actual", igual que `pizarra.py`). */
  direccionHaciaCamara() {
    const dir = new THREE.Vector3();
    this.camera.getWorldDirection(dir);
    dir.negate();
    return [dir.x, dir.y, dir.z];
  }

  /** Coordenadas normalizadas de dispositivo (-1..1) para un evento de mouse/click. */
  _ndc(clientX, clientY) {
    const rect = this.renderer.domElement.getBoundingClientRect();
    return {
      x: ((clientX - rect.left) / rect.width) * 2 - 1,
      y: -((clientY - rect.top) / rect.height) * 2 + 1,
    };
  }

  /**
   * Lanza un rayo desde la cámara bajo (clientX, clientY) contra la malla
   * cargada. Devuelve `{punto: [x,y,z], indiceTriangulo}` o `null` si no
   * hay malla o el rayo no la toca. `indiceTriangulo` es el índice del
   * triángulo golpeado tal como lo devuelve three.js sobre una geometría
   * sin indexar (mismo orden que `/documentos/{id}/caras_triangulos`,
   * ambos vienen del mismo STL binario servido por `/malla`).
   */
  raycastearMalla(clientX, clientY) {
    if (!this.mesh) return null;
    const { x, y } = this._ndc(clientX, clientY);
    this.raycaster.setFromCamera({ x, y }, this.camera);
    const targets = this.pieces.size ? [...this.pieces.values()].filter(piece => piece.mesh.visible).map(piece => piece.mesh) : [this.mesh];
    const hits = this.raycaster.intersectObjects(targets, false);
    if (hits.length === 0) return null;
    const hit = hits[0];
    return {
      punto: [hit.point.x, hit.point.y, hit.point.z],
      indiceTriangulo: hit.object.userData.triangleIds?.[hit.faceIndex] ?? hit.faceIndex,
      pieceName: hit.object.userData.pieceName ?? null,
    };
  }

  /** Rango [min, max] de la malla proyectada sobre `normal` (para mover el plano de corte). */
  rangoEscena(normal) {
    const caja = new THREE.Box3();
    if (this.mesh) caja.setFromObject(this.mesh);
    if (caja.isEmpty()) return [-50, 50];
    const n = new THREE.Vector3(...normal);
    let min = Infinity;
    let max = -Infinity;
    for (const x of [caja.min.x, caja.max.x])
      for (const y of [caja.min.y, caja.max.y])
        for (const z of [caja.min.z, caja.max.z]) {
          const d = n.dot(new THREE.Vector3(x, y, z));
          min = Math.min(min, d);
          max = Math.max(max, d);
        }
    return [min, max];
  }

  /** Corta la escena por el plano: oculta lo que queda entre el plano y la cámara. */
  activarCorte(origen, normal) {
    const n = new THREE.Vector3(...normal);
    const plano = new THREE.Plane(n.clone().negate(), n.dot(new THREE.Vector3(...origen)));
    // Local clipping: only the model is cut, never the plane or the strokes.
    this.renderer.localClippingEnabled = true;
    const modelos = [this.mesh, ...[...this.pieces.values()].map((p) => p.mesh)].filter(Boolean);
    for (const obj of modelos) {
      if (obj.userData._ladoOriginal === undefined) obj.userData._ladoOriginal = obj.material.side;
      obj.material.side = THREE.DoubleSide; // ver el interior del corte
      obj.material.clippingPlanes = [plano];
      obj.material.needsUpdate = true;
    }
    this.render();
  }

  desactivarCorte() {
    this.scene.traverse((obj) => {
      if (obj.isMesh && obj.userData._ladoOriginal !== undefined) {
        obj.material.side = obj.userData._ladoOriginal;
        obj.material.clippingPlanes = null;
        obj.material.needsUpdate = true;
        delete obj.userData._ladoOriginal;
      }
    });
    this.render();
  }

  /** Guarda la cámara actual y mira de frente al plano (dirección `normal`). */
  mirarDeFrente(normal) {
    this._vistaGuardada = {
      posicion: this.camera.position.clone(),
      objetivo: this.controls.target.clone(),
      near: this.camera.near,
      far: this.camera.far,
    };
    this.ajustarVista(normal);
  }

  restaurarVista() {
    const v = this._vistaGuardada;
    if (!v) return;
    this.camera.position.copy(v.posicion);
    this.controls.target.copy(v.objetivo);
    this.camera.near = v.near;
    this.camera.far = v.far;
    this.camera.updateProjectionMatrix();
    this.controls.update();
    this._vistaGuardada = null;
    this.render();
  }

  /** Muestra (o reemplaza) el plano semitransparente de la pizarra. */
  mostrarPlanoPizarra(origen, normal, tamano = 200) {
    this.ocultarPlanoPizarra();
    const geometria = new THREE.PlaneGeometry(tamano, tamano);
    const material = new THREE.MeshBasicMaterial({
      color: cssVarColor("--fj-warn", "#e0b23c"),
      transparent: true,
      opacity: 0.15,
      side: THREE.DoubleSide,
      depthWrite: false,
    });
    const plano = new THREE.Mesh(geometria, material);
    plano.position.set(origen[0], origen[1], origen[2]);
    plano.lookAt(origen[0] + normal[0], origen[1] + normal[1], origen[2] + normal[2]);
    this._planoPizarra = { mesh: plano, origen, normal };
    this.scene.add(plano);
    this.render();
  }

  ocultarPlanoPizarra() {
    if (this._planoPizarra) {
      this.scene.remove(this._planoPizarra.mesh);
      this._planoPizarra.mesh.geometry.dispose();
      this._planoPizarra.mesh.material.dispose();
      this._planoPizarra = null;
      this.render();
    }
  }

  /** Proyecta un punto de pantalla sobre el plano de la pizarra ya colocado. */
  proyectarEnPlanoPizarra(clientX, clientY) {
    if (!this._planoPizarra) return null;
    const { x, y } = this._ndc(clientX, clientY);
    this.raycaster.setFromCamera({ x, y }, this.camera);
    const plano = new THREE.Plane().setFromNormalAndCoplanarPoint(
      new THREE.Vector3(...this._planoPizarra.normal),
      new THREE.Vector3(...this._planoPizarra.origen),
    );
    const destino = new THREE.Vector3();
    const golpeo = this.raycaster.ray.intersectPlane(plano, destino);
    return golpeo ? [destino.x, destino.y, destino.z] : null;
  }

  /** Reemplaza las anotaciones visibles (pines + trazos) por las dadas.
   * `notas`: [{n, punto:[x,y,z], visible}]. `trazos`: [{n, puntos, tipo, visible}]. */
  mostrarAnotaciones(notas, trazos) {
    this._limpiarAnotaciones();
    const colorPin = cssVarColor("--fj-accent", "#e0813c");
    const colorTrazo = {
      quitar: cssVarColor("--fj-err", "#c0392b"),
      anadir: cssVarColor("--fj-run", "#2ecc71"),
      medida: cssVarColor("--fj-info", "#3f8fd6"),
      comentario: cssVarColor("--fj-ink-2", "#e7e5e4"),
    };
    const geometriaPin = new THREE.SphereGeometry(Math.max(this._escalaPin(), 0.05), 12, 12);
    const materialPin = new THREE.MeshBasicMaterial({ color: colorPin });
    for (const nota of notas) {
      if (!nota.visible) continue;
      const esfera = new THREE.Mesh(geometriaPin, materialPin);
      esfera.position.set(nota.punto[0], nota.punto[1], nota.punto[2]);
      this._grupoAnotaciones.add(esfera);
    }
    for (const trazo of trazos) {
      if (!trazo.visible || !trazo.puntos || trazo.puntos.length < 2) continue;
      const puntos = trazo.puntos.map((p) => new THREE.Vector3(p[0], p[1], p[2]));
      const geometria = new THREE.BufferGeometry().setFromPoints(puntos);
      const material = new THREE.LineBasicMaterial({
        color: colorTrazo[trazo.tipo] || colorPin,
        linewidth: 2,
      });
      this._grupoAnotaciones.add(new THREE.Line(geometria, material));
    }
    this.render();
  }

  _escalaPin() {
    if (!this.mesh || !this.mesh.geometry.boundingSphere) return 1;
    return Math.max(this.mesh.geometry.boundingSphere.radius * 0.02, 0.3);
  }

  _limpiarAnotaciones() {
    for (const hijo of [...this._grupoAnotaciones.children]) {
      this._grupoAnotaciones.remove(hijo);
      hijo.geometry.dispose();
      hijo.material.dispose();
    }
  }

  resize() {
    const rect = this.container.getBoundingClientRect();
    const ancho = Math.max(1, Math.round(rect.width));
    const alto = Math.max(1, Math.round(rect.height));
    this.renderer.setSize(ancho, alto, false);
    this.camera.aspect = ancho / alto;
    this.camera.updateProjectionMatrix();
    this.render();
  }

  /** Carga una malla STL binaria (ArrayBuffer) reemplazando la anterior.
   * `conservarVista` (Fase 5C): al re-calcular por parámetros no se
   * re-encuadra la cámara, para comparar el antes/después. */
  cargarMallaSTL(arrayBuffer, { conservarVista = false } = {}) {
    const { stl, manifest } = decodePieceBundle(arrayBuffer);
    const geometria = loader.parse(stl);
    geometria.computeVertexNormals();
    geometria.computeBoundingSphere();
    const previous = conservarVista ? new Map([...this.pieces].map(([name, piece]) => [name, piece.visible])) : new Map();
    const selected = conservarVista ? this.selectedPiece : null;
    const isolated = conservarVista ? this.isolatedPiece : null;
    this._clearPieces();

    if (this.mesh) {
      this.scene.remove(this.mesh);
      this.mesh.geometry.dispose();
      this.mesh.material.dispose();
    }

    const colorMalla = cssVarColor("--fj-ink-3", "#a8a29e");
    const material = new THREE.MeshStandardMaterial({ color: colorMalla, metalness: 0.1, roughness: 0.7, wireframe: this.wireframe });
    this.mesh = new THREE.Mesh(geometria, material);
    this.scene.add(this.mesh);
    this.pieceManifest = manifest;
    if (manifest) {
      for (const data of manifest.piezas) {
        const count = data.rangos.reduce((sum, range) => sum + range[1], 0);
        const triangleIds = new Uint32Array(count);
        const indices = new Uint32Array(count * 3);
        const bounds = new THREE.Box3();
        const vertex = new THREE.Vector3();
        const positions = geometria.getAttribute("position");
        let cursor = 0;
        for (const [first, length] of data.rangos) {
          for (let triangle = first; triangle < first + length; triangle++, cursor++) {
            triangleIds[cursor] = triangle;
            for (let corner = 0; corner < 3; corner++) {
              const vertexIndex = triangle * 3 + corner;
              indices[cursor * 3 + corner] = vertexIndex;
              bounds.expandByPoint(vertex.fromBufferAttribute(positions, vertexIndex));
            }
          }
        }
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute("position", geometria.getAttribute("position"));
        geometry.setAttribute("normal", geometria.getAttribute("normal"));
        geometry.setIndex(new THREE.BufferAttribute(indices, 1));
        // BufferGeometry.computeBoundingBox scans all shared positions, not
        // just this piece's indices. Accumulate this subset's actual bounds.
        geometry.boundingBox = bounds;
        geometry.boundingSphere = geometry.boundingBox.getBoundingSphere(new THREE.Sphere());
        const pieceMesh = new THREE.Mesh(geometry, material.clone());
        pieceMesh.userData = { triangleIds, pieceName: data.nombre };
        this.scene.add(pieceMesh);
        this.pieces.set(data.nombre, { ...data, mesh: pieceMesh, visible: previous.get(data.nombre) ?? true });
      }
      this.mesh.visible = this.pieces.size === 0;
    }
    this.isolatedPiece = this.pieces.has(isolated) ? isolated : null;
    this.selectPiece(this.pieces.has(selected) ? selected : null);
    this._applyPieceVisibility();
    if (conservarVista) this.render();
    else this.ajustarVista();
  }

  /** Encuadra la cámara sobre la malla cargada (o el origen si no hay ninguna). */
  ajustarVista(direction = [1, -1, 0.75], sphere = null) {
    if (!this.mesh || !this.mesh.geometry.boundingSphere) {
      this.render();
      return;
    }
    const esfera = sphere ?? this.mesh.geometry.boundingSphere;
    const centro = esfera.center.clone();
    const radio = Math.max(esfera.radius, 1);

    this.controls.target.copy(centro);
    const verticalFov = THREE.MathUtils.degToRad(this.camera.fov / 2);
    const horizontalFov = Math.atan(Math.tan(verticalFov) * this.camera.aspect);
    const distancia = radio / Math.sin(Math.min(verticalFov, horizontalFov)) * 1.15;
    this.camera.up.set(0, 0, 1);
    // OrbitControls caches the Z-up frame at construction. Keep it fixed;
    // a tiny offset avoids a degenerate look-at at the exact vertical pole.
    const viewDirection = new THREE.Vector3(...direction);
    if (viewDirection.x === 0 && viewDirection.y === 0) viewDirection.y = -1e-6;
    this.camera.position.copy(centro).add(viewDirection.normalize().multiplyScalar(distancia));
    this.camera.near = Math.max(radio / 100, 0.01);
    this.camera.far = distancia * 50;
    this.camera.updateProjectionMatrix();
    this.controls.update();
    this.render();
  }

  setWireframe(enabled) {
    this.wireframe = enabled;
    if (this.mesh) this.mesh.material.wireframe = enabled;
    for (const piece of this.pieces.values()) piece.mesh.material.wireframe = enabled;
    this.render();
  }

  setGridVisible(enabled) {
    this.grid.visible = enabled;
    this.render();
  }

  selectPiece(name) {
    this.selectedPiece = this.pieces.has(name) ? name : null;
    if (typeof this.onSeleccion === "function") this.onSeleccion(this.selectedPiece);
    const accent = new THREE.Color(cssVarColor("--fj-accent", "#e0813c"));
    for (const [key, piece] of this.pieces) {
      piece.mesh.material.emissive.copy(key === this.selectedPiece ? accent : new THREE.Color(0));
      piece.mesh.material.emissiveIntensity = key === this.selectedPiece ? 0.4 : 0;
    }
    this.render();
  }

  setPieceVisible(name, visible) {
    if (this.pieces.has(name)) this.pieces.get(name).visible = visible;
    this._applyPieceVisibility();
  }

  isolatePiece(name) {
    this.isolatedPiece = this.pieces.has(name) ? name : null;
    this._applyPieceVisibility();
  }

  showAllPieces() {
    this.isolatedPiece = null;
    for (const piece of this.pieces.values()) piece.visible = true;
    this._applyPieceVisibility();
  }

  framePiece(name) {
    const piece = this.pieces.get(name);
    if (piece) this.ajustarVista(this.direccionHaciaCamara(), piece.mesh.geometry.boundingSphere);
  }

  _applyPieceVisibility() {
    for (const [name, piece] of this.pieces) {
      piece.mesh.visible = this.isolatedPiece ? name === this.isolatedPiece : piece.visible;
    }
    this.render();
  }

  _clearPieces() {
    for (const piece of this.pieces.values()) {
      this.scene.remove(piece.mesh);
      piece.mesh.geometry.dispose();
      piece.mesh.material.dispose();
    }
    this.pieces.clear();
    this.selectedPiece = null;
    this.isolatedPiece = null;
  }

  render() {
    this.renderer.render(this.scene, this.camera);
  }

  dispose() {
    this._resizeObserver.disconnect();
    this.controls.dispose();
    this.renderer.domElement.removeEventListener("pointerdown", this._piecePointerDown);
    this.renderer.domElement.removeEventListener("pointerup", this._piecePointerUp);
    this._clearPieces();
    this._limpiarAnotaciones();
    this.ocultarPlanoPizarra();
    if (this.mesh) {
      this.mesh.geometry.dispose();
      this.mesh.material.dispose();
    }
    this.renderer.dispose();
    this.renderer.domElement.remove();
  }
}
