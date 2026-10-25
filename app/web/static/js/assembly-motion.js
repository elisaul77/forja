// Matrices column-major, in the rest-world coordinate frame used by Forja.
const identity = () => [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];

function multiply(a, b) {
  const out = new Array(16);
  for (let col = 0; col < 4; col++) for (let row = 0; row < 4; row++) {
    out[col * 4 + row] = a[row] * b[col * 4] + a[4 + row] * b[col * 4 + 1] +
      a[8 + row] * b[col * 4 + 2] + a[12 + row] * b[col * 4 + 3];
  }
  return out;
}

function rigidInverse(m) {
  const out = identity();
  for (let col = 0; col < 3; col++) for (let row = 0; row < 3; row++) out[col * 4 + row] = m[row * 4 + col];
  for (let row = 0; row < 3; row++) out[12 + row] = -(out[row] * m[12] + out[4 + row] * m[13] + out[8 + row] * m[14]);
  return out;
}

function jointMatrix(joint, value) {
  const m = identity();
  const [x, y, z] = joint.eje;
  if (joint.tipo === "deslizamiento") {
    m[12] = x * value; m[13] = y * value; m[14] = z * value;
  } else if (joint.tipo === "giro") {
    const rad = value * Math.PI / 180;
    const c = Math.cos(rad), s = Math.sin(rad), t = 1 - c;
    m[0] = t * x * x + c;     m[4] = t * x * y - s * z; m[8] = t * x * z + s * y;
    m[1] = t * x * y + s * z; m[5] = t * y * y + c;     m[9] = t * y * z - s * x;
    m[2] = t * x * z - s * y; m[6] = t * y * z + s * x; m[10] = t * z * z + c;
    const [ox, oy, oz] = joint.origen;
    m[12] = ox - (m[0] * ox + m[4] * oy + m[8] * oz);
    m[13] = oy - (m[1] * ox + m[5] * oy + m[9] * oz);
    m[14] = oz - (m[2] * ox + m[6] * oy + m[10] * oz);
  }
  return m;
}

function worldPose(name, byChild, values) {
  let matrix = identity();
  let cursor = name;
  const seen = new Set();
  while (byChild.has(cursor)) {
    if (seen.has(cursor)) throw new Error("El ensamble contiene un ciclo");
    seen.add(cursor);
    const joint = byChild.get(cursor);
    matrix = multiply(jointMatrix(joint, values[joint.id] ?? 0), matrix);
    cursor = joint.padre;
  }
  return matrix;
}

/** Transform the currently posed component mesh into a preview pose.
 * Backend order: child joint first, then each ancestor. No STEP is changed. */
export function poseDeltas(assembly, targetValues) {
  const byChild = new Map(assembly.articulaciones.map(joint => [joint.hijo, joint]));
  const result = new Map();
  for (const name of assembly.piezas) {
    const current = worldPose(name, byChild, assembly.valores);
    const target = worldPose(name, byChild, targetValues);
    result.set(name, multiply(target, rigidInverse(current)));
  }
  return result;
}

export function validateMotionAssembly(assembly, meshNames) {
  if (!assembly?.articulaciones?.length) return "Define articulaciones en la vista CAD para animar este documento.";
  if (assembly.obsoleto) return "El ensamble está desactualizado. Revisa las articulaciones en CAD.";
  if (assembly.piezas.some(name => !meshNames.has(name))) return "La malla no contiene todas las piezas del ensamble; actualiza el documento.";
  return "";
}
