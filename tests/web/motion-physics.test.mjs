import test from "node:test";
import assert from "node:assert/strict";
import { springForce } from "../../app/web/static/js/motion-physics.js";

test("Hooke estimate uses only supplied data and releases in tension", () => {
  const input = { stiffness: 0.5, referenceForce: 2, referencePosition: 4, direction: 1 };
  assert.equal(springForce({ ...input, position: 6 }), 3);
  assert.equal(springForce({ ...input, position: 0 }), 0);
  assert.equal(springForce({ ...input, stiffness: NaN, position: 6 }), null);
  assert.equal(springForce({ ...input, direction: 0, position: 6 }), null);
});
