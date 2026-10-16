import test from "node:test";
import assert from "node:assert/strict";
import {
  createDraftStore,
  markPoints,
  collectMarkPoints,
  subsamplePoints,
  looseMarks,
  visibleMessages,
} from "../../app/web/static/js/design.js";

test("draft store keeps typed text per key and does not fall back once cleared", () => {
  const drafts = createDraftStore({ objective: "Soporte" });
  assert.equal(drafts.read("objective", "fallback"), "Soporte");
  assert.equal(drafts.read("reply:abc", "vacío"), "vacío");
  drafts.write("reply:abc", "Hola");
  assert.equal(drafts.read("reply:abc"), "Hola");
  assert.equal(drafts.has("reply:abc"), true);
  // Un borrador vaciado a propósito gana sobre el valor guardado.
  drafts.write("objective", "");
  assert.equal(drafts.read("objective", "Soporte guardado"), "");
  drafts.forget("reply:abc");
  assert.equal(drafts.read("reply:abc", "vacío"), "vacío");
});

test("draft stores are independent between documents", () => {
  const a = createDraftStore();
  const b = createDraftStore();
  a.write("new", "solo en A");
  assert.equal(b.read("new", "vacío"), "vacío");
});

test("mark points come from a note reference or a stroke and skip malformed points", () => {
  assert.deepEqual(markPoints({ referencia: { punto: [1, 2, 3] } }), [[1, 2, 3]]);
  assert.deepEqual(markPoints({ puntos: [[0, 0, 0], [1, 1, 1]] }), [[0, 0, 0], [1, 1, 1]]);
  assert.deepEqual(markPoints({ puntos: [[0, 0, 0], [1, "x", 1], [2]] }), [[0, 0, 0]]);
  assert.deepEqual(markPoints({ referencia: null }), []);
  assert.deepEqual(markPoints(undefined), []);
});

test("collected mark points are copies, not references into the notes list", () => {
  const trazo = { puntos: [[5, 5, 5]] };
  const [copy] = collectMarkPoints([trazo, { referencia: { punto: [0, 1, 2] } }]);
  assert.deepEqual(copy, [5, 5, 5]);
  copy[0] = 99;
  assert.equal(trazo.puntos[0][0], 5);
  assert.deepEqual(collectMarkPoints([{ referencia: { punto: [0, 1, 2] } }, { puntos: [[3, 3, 3]] }]), [[0, 1, 2], [3, 3, 3]]);
  assert.deepEqual(collectMarkPoints(null), []);
});

test("subsampling keeps short lists intact and caps long strokes at 256 points", () => {
  const short = [[0, 0, 0], [1, 0, 0]];
  assert.deepEqual(subsamplePoints(short), short);
  const long = Array.from({ length: 1000 }, (_, i) => [i, 0, 0]);
  const capped = subsamplePoints(long);
  assert.equal(capped.length, 256);
  assert.deepEqual(capped[0], [0, 0, 0]);
  assert.deepEqual(capped[255], [999, 0, 0]);
  // Puntos ordenados y sin repetir índices.
  for (let i = 1; i < capped.length; i++) assert.ok(capped[i][0] > capped[i - 1][0]);
  assert.equal(subsamplePoints(long, 2).length, 2);
  assert.deepEqual(subsamplePoints([[7, 7, 7], [8, 8, 8], [9, 9, 9]], 2), [[7, 7, 7], [9, 9, 9]]);
  assert.deepEqual(subsamplePoints([[1, 2, 3], [4, 5, 6]], 256), [[1, 2, 3], [4, 5, 6]]);
  assert.deepEqual(subsamplePoints(undefined), []);
});

test("loose marks are the ones no indication links to", () => {
  const marks = [{ n: "a" }, { n: "b" }, { n: "c" }];
  const intents = [{ id: "i1", marks: ["a"] }, { id: "i2" }];
  assert.deepEqual(looseMarks(marks, intents).map((m) => m.n), ["b", "c"]);
  assert.deepEqual(looseMarks(marks, []).map((m) => m.n), ["a", "b", "c"]);
});

test("agent messages that repeat the interpretation are not shown twice", () => {
  const intent = {
    interpretation: "Subir el soporte 5 mm",
    messages: [
      { author: "user", text: "¿Y el lado?" },
      { author: "agent", text: "Subir el soporte 5 mm" },
      { author: "agent", text: "  Subir el soporte 5 mm  " },
      { author: "agent", text: "Otra cosa" },
    ],
  };
  assert.deepEqual(visibleMessages(intent).map((m) => m.text), ["¿Y el lado?", "Otra cosa"]);
  // Sin interpretación todo se muestra; el usuario nunca se filtra.
  assert.equal(visibleMessages({ messages: [{ author: "agent", text: "x" }] }).length, 1);
  assert.equal(visibleMessages({ interpretation: "y", messages: [{ author: "user", text: "y" }] }).length, 1);
  assert.deepEqual(visibleMessages({}), []);
});
