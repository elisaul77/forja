import test from "node:test";
import assert from "node:assert/strict";
import { interpolateTracks } from "../../app/web/static/js/motion-tracks.js";

const assembly = { valores: { spin: 0, slide: 0 }, articulaciones: [
  { id: "spin", tipo: "giro", limites: [-90, 90] },
  { id: "slide", tipo: "deslizamiento", limites: [0, 20] },
] };

test("coordinated drives interpolate both joints at the same time", () => {
  assert.deepEqual(interpolateTracks(assembly, {
    spin: { from: 0, to: 90 }, slide: { from: 0, to: 20 },
  }, 0.5), { spin: 45, slide: 10 });
});

test("rejects limits, unknown joints and missing drives", () => {
  assert.equal(interpolateTracks(assembly, {}, 0.5), null);
  assert.equal(interpolateTracks(assembly, { spin: { from: 0, to: 100 } }, 0.5), null);
  assert.equal(interpolateTracks(assembly, { unknown: { from: 0, to: 1 } }, 0.5), null);
});
