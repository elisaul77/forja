// Miniaturas locales: un único contexto WebGL y trabajo serializado.
import * as THREE from "three";
import { STLLoader } from "three/addons/loaders/STLLoader.js";

const WIDTH = 320;
const HEIGHT = 240;
const MAX_CACHE = 32;
const MAX_PENDING = 64;
const cache = new Map();
const pending = new Map();
const loader = new STLLoader();
let queue = Promise.resolve();
let renderer;

function themeColor(token, fallback) {
  const color = getComputedStyle(document.documentElement).getPropertyValue(token).trim();
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 1;
  const context = canvas.getContext("2d");
  context.fillStyle = fallback;
  if (color) context.fillStyle = color;
  context.fillRect(0, 0, 1, 1);
  const [r, g, b] = context.getImageData(0, 0, 1, 1).data;
  return new THREE.Color(`rgb(${r}, ${g}, ${b})`);
}

async function renderPreview(doc) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 45000);
  let geometry;
  let material;
  let scene;
  try {
    const response = await fetch(`/documentos/${encodeURIComponent(doc.id)}/malla`, {
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(`Vista previa no disponible (${response.status}).`);
    geometry = loader.parse(await response.arrayBuffer());
    geometry.computeBoundingSphere();
    const sphere = geometry.boundingSphere;
    if (!sphere || !Number.isFinite(sphere.radius) || sphere.radius <= 0) {
      throw new Error("El modelo no tiene una geometría visible.");
    }
    // Normalizar evita planos de recorte inestables para modelos muy grandes
    // o pequeños; no se modifica el documento ni el visor de trabajo.
    geometry.translate(-sphere.center.x, -sphere.center.y, -sphere.center.z);
    geometry.scale(1 / sphere.radius, 1 / sphere.radius, 1 / sphere.radius);
    if (!geometry.getAttribute("normal")) geometry.computeVertexNormals();
    if (!renderer || renderer.getContext().isContextLost()) {
      renderer?.dispose();
      renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
      renderer.setPixelRatio(1);
      renderer.setSize(WIDTH, HEIGHT, false);
    }
    scene = new THREE.Scene();
    scene.background = themeColor("--fj-sunk", "#14110f");
    scene.add(new THREE.AmbientLight(0xffffff, 0.8));
    const light = new THREE.DirectionalLight(0xffffff, 2);
    light.position.set(2, -3, 4);
    scene.add(light);
    material = new THREE.MeshStandardMaterial({
      color: themeColor("--fj-ink-3", "#a8a29e"),
      roughness: 0.65,
      metalness: 0.12,
      side: THREE.DoubleSide,
    });
    scene.add(new THREE.Mesh(geometry, material));
    const halfHeight = 1.15;
    const halfWidth = halfHeight * WIDTH / HEIGHT;
    const camera = new THREE.OrthographicCamera(-halfWidth, halfWidth, halfHeight, -halfHeight, 0.1, 20);
    camera.up.set(0, 0, 1);
    camera.position.set(3, -4, 2.8);
    camera.lookAt(0, 0, 0);
    renderer.render(scene, camera);
    return renderer.domElement.toDataURL("image/webp", 0.85);
  } finally {
    clearTimeout(timeout);
    scene?.clear();
    geometry?.dispose();
    material?.dispose();
    renderer?.renderLists.dispose();
  }
}

/** Devuelve una imagen; los fallos se dejan al placeholder de la galería. */
export function previewUrl(doc) {
  const key = JSON.stringify([doc.id, doc.actualizado ?? ""]);
  if (cache.has(key)) {
    const image = cache.get(key);
    cache.delete(key);
    cache.set(key, image);
    return Promise.resolve(image);
  }
  if (pending.has(key)) return pending.get(key);
  if (pending.size >= MAX_PENDING) return Promise.reject(new Error("Hay demasiadas vistas previas en espera."));
  const job = queue.then(() => renderPreview(doc)).then(image => {
    cache.set(key, image);
    while (cache.size > MAX_CACHE) cache.delete(cache.keys().next().value);
    return image;
  }).finally(() => pending.delete(key));
  pending.set(key, job);
  queue = job.catch(() => {});
  return job;
}
