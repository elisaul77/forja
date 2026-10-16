import test from "node:test";
import assert from "node:assert/strict";
import { vector, resolveDesignPiece } from "../../app/web/static/js/design.js";
test("design input rejects ambiguous, missing and unbounded coordinates", () => {
  assert.deepEqual(vector("1, -2, 3.5"), [1, -2, 3.5]);
  for (const value of ["1,2", "NaN,0,0", "Infinity,2,3", "1e40,0,0", ""])
    assert.throws(() => vector(value));
});
test("single STL capture maps viewer label to canonical design name", () => {
  assert.equal(resolveDesignPiece([{name: "part"}], "Malla completa"), "part");
  assert.equal(resolveDesignPiece([{name: "part"}], "part"), "part");
  assert.equal(resolveDesignPiece([{name: "part"}, {name: "reference"}], "reference"), "reference");
  assert.equal(resolveDesignPiece([{name: "part"}, {name: "reference"}], "Malla completa"), null);
  assert.equal(resolveDesignPiece([], null), null);
});
