import test from "node:test";
import assert from "node:assert/strict";
import { poseDeltas, validateMotionAssembly } from "../../app/web/static/js/assembly-motion.js";

const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-9, `${actual} != ${expected}`);
function point(matrix, [x, y, z]) {
  return [matrix[0] * x + matrix[4] * y + matrix[8] * z + matrix[12],
    matrix[1] * x + matrix[5] * y + matrix[9] * z + matrix[13],
    matrix[2] * x + matrix[6] * y + matrix[10] * z + matrix[14]];
}

test("rotates about the declared world origin from the current persisted pose", () => {
  const joint = { id: "hinge", tipo: "giro", padre: "base", hijo: "arm", origen: [10, 0, 0], eje: [0, 0, 1] };
  const assembly = { piezas: ["base", "arm"], articulaciones: [joint], valores: { hinge: 90 } };
  const deltas = poseDeltas(assembly, { hinge: 0 });
  // The mesh is already at 90 degrees. This world point corresponds to
  // rest point (20,0,0) and must return there without editing the STEP.
  const returned = point(deltas.get("arm"), [10, 10, 0]);
  returned.forEach((value, i) => near(value, [20, 0, 0][i]));
  point(deltas.get("base"), [4, 5, 6]).forEach((value, i) => near(value, [4, 5, 6][i]));
});

test("applies the child slide before the ancestor rotation", () => {
  const assembly = { piezas: ["base", "arm", "carriage"], valores: { spin: 0, slide: 0 }, articulaciones: [
    { id: "spin", tipo: "giro", padre: "base", hijo: "arm", origen: [0, 0, 0], eje: [0, 0, 1] },
    { id: "slide", tipo: "deslizamiento", padre: "arm", hijo: "carriage", origen: [0, 0, 0], eje: [1, 0, 0] },
  ] };
  const deltas = poseDeltas(assembly, { spin: 90, slide: 5 });
  point(deltas.get("carriage"), [1, 0, 0]).forEach((value, i) => near(value, [0, 6, 0][i]));
  point(deltas.get("arm"), [1, 0, 0]).forEach((value, i) => near(value, [0, 1, 0][i]));
  const posed = { ...assembly, valores: { spin: 90, slide: 5 } };
  const back = poseDeltas(posed, { spin: 0, slide: 0 });
  point(back.get("carriage"), [0, 6, 0]).forEach((value, i) => near(value, [1, 0, 0][i]));
});

test("rejects missing, stale, or incomplete assemblies before playback", () => {
  const assembly = { piezas: ["base", "arm"], articulaciones: [{ id: "a", tipo: "giro" }], valores: {} };
  assert.match(validateMotionAssembly(null, new Set()), /Define/);
  assert.match(validateMotionAssembly({ ...assembly, obsoleto: true }, new Set(assembly.piezas)), /desactualizado/);
  assert.match(validateMotionAssembly(assembly, new Set(["base"])), /no contiene/);
  assert.equal(validateMotionAssembly(assembly, new Set(assembly.piezas)), "");
});
