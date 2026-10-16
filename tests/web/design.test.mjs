import test from "node:test";
import assert from "node:assert/strict";
import {
  vector,
  resolveDesignPiece,
  attentionLabel,
  attentionOf,
  isOpenAttention,
  pluralize,
  checkSummary,
  isProposalStale,
  panelSectionFor,
  shortText,
} from "../../app/web/static/js/design.js";
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

test("every attention state has a plain Spanish label and unknown states fall back to pending", () => {
  assert.equal(attentionLabel("waiting_agent"), "Esperando al agente");
  assert.equal(attentionLabel("question"), "El agente pregunta");
  assert.equal(attentionLabel("understood"), "Entendida · sin propuesta aún");
  assert.equal(attentionLabel("proposal"), "Propuesta lista para revisar");
  assert.equal(attentionLabel("applied"), "Cambio aplicado · ¿lo resuelve?");
  assert.equal(attentionLabel("resolved"), "Resuelta");
  assert.equal(attentionLabel("dismissed"), "Descartada");
  assert.equal(attentionLabel("reverted"), "Cambio deshecho");
  assert.equal(attentionLabel("inventado"), "Estado sin clasificar");
});

test("attention prefers the server view and derives a legacy intent honestly", () => {
  assert.equal(attentionOf({ status: "pending" }, { attention: "proposal" }), "proposal");
  assert.equal(attentionOf({ status: "pending" }, { attention: "??" }), "waiting_agent");
  assert.equal(attentionOf({ status: "dismissed" }), "dismissed");
  assert.equal(attentionOf({ status: "resolved" }), "resolved");
  assert.equal(attentionOf({ status: "pending", question: "¿Qué lado?" }), "question");
  assert.equal(attentionOf({ status: "pending", interpretation: "Subir el soporte" }), "understood");
  assert.equal(attentionOf({ status: "pending" }), "waiting_agent");
  // Un legado «accepted» no se da por resuelto: sigue pendiente de revisión.
  assert.equal(attentionOf({ status: "accepted" }), "waiting_agent");
});

test("open filter hides only resolved and dismissed indications", () => {
  assert.equal(isOpenAttention("resolved"), false);
  assert.equal(isOpenAttention("dismissed"), false);
  for (const open of ["waiting_agent", "question", "understood", "proposal", "applied"])
    assert.equal(isOpenAttention(open), true);
});

test("check summary speaks in words and never claims success with pending checks", () => {
  assert.equal(checkSummary({ passed: 3, failed: 0, not_checked: 0 }), "Todas las comprobaciones pasan");
  assert.equal(checkSummary({ passed: 2, failed: 1, not_checked: 0 }), "1 comprobación no se cumple");
  assert.equal(checkSummary({ passed: 1, failed: 2, not_checked: 0 }), "2 comprobaciones no se cumplen");
  assert.equal(checkSummary({ passed: 1, failed: 0, not_checked: 2 }), "2 sin comprobar");
  assert.equal(checkSummary({ passed: 0, failed: 1, not_checked: 1 }), "1 comprobación no se cumple · 1 sin comprobar");
  assert.equal(checkSummary({ passed: 0, failed: 0, not_checked: 0 }), "Sin comprobaciones definidas");
  assert.equal(checkSummary(undefined), "Sin comprobaciones definidas");
});

test("only a prepared proposal on another revision is stale", () => {
  const current = { revision: "r2", state_revision: 7 };
  assert.equal(isProposalStale({ state: "prepared", revision: "r1", state_revision: 7 }, current), true);
  assert.equal(isProposalStale({ state: "prepared", revision: "r2", state_revision: 6 }, current), true);
  assert.equal(isProposalStale({ state: "prepared", revision: "r2", state_revision: 7 }, current), false);
  // Una propuesta ya aplicada no se marca como obsoleta: el cambio ya ocurrió.
  assert.equal(isProposalStale({ state: "applied", revision: "r1", state_revision: 1 }, current), false);
  // Sin revisiones en el manifiesto (servidor antiguo): no se inventa obsolescencia.
  assert.equal(isProposalStale({ state: "prepared" }, current), false);
});

test("server stale flag wins over client revision comparison", () => {
  const current = { revision: "r2", state_revision: 7 };
  // Una respuesta del usuario cambia state_revision pero no la restricción: no es obsoleta.
  assert.equal(isProposalStale({ state: "prepared", stale: false, revision: "r1", state_revision: 6 }, current), false);
  assert.equal(isProposalStale({ state: "prepared", stale: true, revision: "r2", state_revision: 7 }, current), true);
});

test("notes and history map to the unified panel and legacy ramas to historial", () => {
  assert.equal(panelSectionFor("notas"), "design");
  assert.equal(panelSectionFor("design"), "design");
  assert.equal(panelSectionFor("ramas"), "historial");
  assert.equal(panelSectionFor("historial"), "historial");
  assert.equal(panelSectionFor("piezas"), "piezas");
});

test("pluralize and shortText keep Spanish labels readable", () => {
  assert.equal(pluralize(1, "marca", "marcas"), "1 marca");
  assert.equal(pluralize(0, "marca", "marcas"), "0 marcas");
  assert.equal(shortText("  corto  "), "corto");
  assert.equal(shortText("a".repeat(50), 10), "aaaaaaaaa…");
  assert.equal(shortText(null), "");
});
