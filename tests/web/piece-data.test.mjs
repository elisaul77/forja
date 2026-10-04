import test from "node:test";
import assert from "node:assert/strict";
import { decodePieceBundle } from "../../app/web/static/js/piece-data.js";

function bundle(pieces, count = 3) {
  const stl = new Uint8Array(84 + count * 50);
  new DataView(stl.buffer).setUint32(80, count, true);
  const header = new TextEncoder().encode(JSON.stringify({ version: 1, triangulos: count, piezas: pieces }));
  const result = new Uint8Array(8 + header.length + stl.length);
  result.set(new TextEncoder().encode("FJP1"));
  new DataView(result.buffer).setUint32(4, header.length, true);
  result.set(header, 8);
  result.set(stl, 8 + header.length);
  return result.buffer;
}

const piece = (name, ranges) => ({ nombre: name, bbox: [0, 1, 0, 1, 0, 1], rangos: ranges });

test("Unicode names and non-contiguous ranges retain the canonical STL bytes", () => {
  const data = bundle([piece("piñón", [[0, 1], [2, 1]]), piece("eje", [[1, 1]])]);
  const decoded = decodePieceBundle(data);
  assert.equal(decoded.manifest.piezas[0].nombre, "piñón");
  assert.equal(new DataView(decoded.stl).getUint32(80, true), 3);
  assert.equal(decoded.stl.byteLength, 234);
});

test("legacy standalone STL remains supported", () => {
  const stl = new ArrayBuffer(84);
  assert.deepEqual(decodePieceBundle(stl), { stl, manifest: null });
});

test("refuses overlapping ownership rather than showing the wrong piece", () => {
  assert.throws(() => decodePieceBundle(bundle([piece("a", [[0, 3]]), piece("b", [[2, 1]])])), /varias piezas/);
});

test("refuses missing, out-of-bounds and duplicate named components", () => {
  assert.throws(() => decodePieceBundle(bundle([piece("a", [[0, 2]])])), /sin pieza/);
  assert.throws(() => decodePieceBundle(bundle([piece("a", [[0, 4]])])), /fuera/);
  assert.throws(() => decodePieceBundle(bundle([piece("a", [[0, 2]]), piece("a", [[2, 1]])])), /inválidos/);
});

test("truncated envelope and changed triangle count cannot be paired silently", () => {
  const data = bundle([piece("a", [[0, 3]])]);
  assert.throws(() => decodePieceBundle(data.slice(0, data.byteLength - 1)), /no coinciden/);
  const offset = 8 + new DataView(data).getUint32(4, true);
  new DataView(data).setUint32(offset + 80, 4, true);
  assert.throws(() => decodePieceBundle(data), /no coinciden/);
});
