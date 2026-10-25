import test from "node:test";
import assert from "node:assert/strict";
import { requestSessionToken, forgetSessionToken } from "../../app/web/static/js/auth.js";

function setup() {
  const storage = new Map();
  globalThis.sessionStorage = {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
    removeItem: (key) => storage.delete(key),
  };
  const nodes = [];
  globalThis.document = {
    body: { append() {} },
    createElement(tag) {
      const node = { tag, value: "", children: [], append(...children) { this.children.push(...children); },
        setAttribute() {}, showModal() { this.open = true; }, close() { this.open = false; },
        remove() { this.removed = true; }, focus() {} };
      nodes.push(node);
      return node;
    },
  };
  return { storage, nodes };
}

test("authentication reuses existing tab credential without opening UI", async () => {
  const { storage, nodes } = setup();
  storage.set("fj_token", "dummy-test-key");
  assert.equal(await requestSessionToken(), "dummy-test-key");
  assert.equal(nodes.length, 0);
  forgetSessionToken();
  assert.equal(storage.size, 0);
});

test("concurrent requests share one dialog; submit clears input and retains tab-only key", async () => {
  const { storage, nodes } = setup();
  const first = requestSessionToken(), second = requestSessionToken();
  assert.equal(first, second);
  const input = nodes.find(n => n.tag === "input");
  assert.equal(input.type, "password");
  input.value = "  dummy-test-key  ";
  nodes.find(n => n.tag === "form").onsubmit({ preventDefault() {} });
  assert.equal(await first, "dummy-test-key");
  assert.equal(storage.get("fj_token"), "dummy-test-key");
  assert.equal(input.value, "");
  assert.equal(nodes[0].removed, true);
});

test("cancel leaves no credential and permits a later request", async () => {
  const { storage, nodes } = setup();
  const first = requestSessionToken();
  nodes[0].oncancel({ preventDefault() {} });
  assert.equal(await first, "");
  assert.equal(storage.size, 0);
  const second = requestSessionToken();
  nodes.filter(n => n.tag === "button" && n.textContent === "Cancelar").at(-1).onclick();
  assert.equal(await second, "");
  assert.equal(nodes.filter(n => n.tag === "dialog").length, 2);
});
