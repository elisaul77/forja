import test from "node:test";
import assert from "node:assert/strict";
import { overlapPairs, transformedBounds } from "../../app/web/static/js/motion-overlaps.js";

const box = (id, x, y, z, dx = 1, dy = 1, dz = 1) =>
  ({ id, min: [x, y, z], max: [x + dx, y + dy, z + dz] });

test("transforms a local bounding box without allocating eight vertices", () => {
  const local = { min: { x: 0, y: 0, z: 0 }, max: { x: 2, y: 4, z: 6 } };
  // 90 degrees around Z, then +10 on X.
  const matrix = [0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1, 0, 10, 0, 0, 1];
  const transformed = transformedBounds(local, matrix, 7);
  assert.deepEqual(transformed, { id: 7, min: [6, 0, 0], max: [10, 2, 6] });
});

test("500 separated solids need no pair checks after the X sweep", () => {
  const boxes = Array.from({ length: 500 }, (_, i) => box(i, i * 3, 0, 0));
  const start = performance.now();
  const result = overlapPairs(boxes);
  assert.equal(result.pairs.size, 0);
  assert.equal(result.candidates, 0);
  assert.ok(performance.now() - start < 100, "500-piece broad phase should stay interactive");
});

test("pairs remain stable as X order changes and touching faces count", () => {
  const initial = overlapPairs([box(3, 0, 0, 0), box(8, 1, 0, 0)]);
  const swapped = overlapPairs([box(3, 1, 0, 0), box(8, 0, 0, 0)]);
  assert.deepEqual(initial.pairs, new Set(["3:8"]));
  assert.deepEqual(swapped.pairs, initial.pairs);
  assert.equal(overlapPairs([box(3, 0, 0, 0), box(8, 0.5, 3, 0)]).pairs.size, 0);
});
