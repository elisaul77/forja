import test from "node:test";
import assert from "node:assert/strict";
import { decodePieceBundle, nombreIdentificado } from "../../app/web/static/js/piece-data.js";

function bundle(pieces, count = 3, extra = {}) {
  const stl = new Uint8Array(84 + count * 50);
  new DataView(stl.buffer).setUint32(80, count, true);
  const header = new TextEncoder().encode(JSON.stringify({ version: 1, triangulos: count, piezas: pieces, ...extra }));
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

// Nombres de sólido como los de un STEP importado (con guiones bajos e índices).
const SOLIDOS_STEP = ["base", "piso2", "columna_1", "columna_2"];

test("con piezas identificadas el visor usa los nombres de sólido del servidor", () => {
  const data = bundle(
    SOLIDOS_STEP.map((n, i) => piece(n, [[i, 1]])),
    4,
    { identificadas: true, nombres_inciertos: false },
  );
  const { manifest } = decodePieceBundle(data);
  assert.deepEqual(
    manifest.piezas.map((p) => nombreIdentificado(manifest, p.nombre)),
    SOLIDOS_STEP,
  );
});

test("sin piezas identificadas el visor no entrega un nombre de pieza inventado", () => {
  // Caso real: caras compartidas entre sólidos → una sola pieza «Modelo completo».
  const data = bundle([piece("Modelo completo", [[0, 3]])], 3, {
    identificadas: false,
    nombres_inciertos: true,
    aviso: "Hay caras compartidas o sin una pieza propietaria inequívoca; se muestra el modelo completo.",
  });
  const { manifest } = decodePieceBundle(data);
  assert.equal(nombreIdentificado(manifest, "Modelo completo"), null);
});

test("nombres inciertos anulan la identificación aunque el paquete diga identificadas", () => {
  assert.equal(nombreIdentificado({ identificadas: true, nombres_inciertos: true }, "base"), null);
  assert.equal(nombreIdentificado({ nombres_inciertos: false }, "base"), null);
});
