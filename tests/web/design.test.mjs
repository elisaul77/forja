import test from "node:test";
import assert from "node:assert/strict";
import { vector } from "../../app/web/static/js/design.js";
test("design input rejects ambiguous, missing and unbounded coordinates", () => {
  assert.deepEqual(vector("1, -2, 3.5"), [1, -2, 3.5]);
  for (const value of ["1,2", "NaN,0,0", "Infinity,2,3", "1e40,0,0", ""])
    assert.throws(() => vector(value));
});
