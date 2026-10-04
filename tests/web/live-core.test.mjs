import test from "node:test";
import assert from "node:assert/strict";
import { createRefreshScheduler, parseLiveEvent } from "../../app/web/static/js/live-core.js";

const tick = () => new Promise(resolve => setTimeout(resolve, 0));
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };

test("bursts coalesce, self echo skips, and a new event cannot overlap an in-flight mesh", async () => {
  const first = deferred();
  const second = deferred();
  const downloads = [first, second];
  const applied = [];
  let calls = 0;
  const scheduler = createRefreshScheduler({
    fetchMesh: () => { const next = downloads[calls++]; return next.promise; },
    applyMesh: result => applied.push(result.revision),
    canRun: () => true,
    delay: 0,
  });
  scheduler.setShown("a");
  scheduler.notify("b");
  scheduler.notify("b");
  await tick();
  assert.equal(calls, 1);
  scheduler.notify("c");
  await tick();
  assert.equal(calls, 1);
  first.resolve({ revision: "b" });
  await tick();
  await tick();
  assert.equal(calls, 2);
  assert.deepEqual(applied, []);
  second.resolve({ revision: "c" });
  await tick();
  assert.deepEqual(applied, ["c"]);
  scheduler.notify("c");
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(calls, 2);
  scheduler.dispose();
});

test("inactive or busy tabs defer until flush, and disposal discards late results", async () => {
  let ready = false;
  const response = deferred();
  let calls = 0;
  let applied = 0;
  const scheduler = createRefreshScheduler({
    fetchMesh: () => { calls++; return response.promise; },
    applyMesh: () => { applied++; },
    canRun: () => ready,
    delay: 0,
  });
  scheduler.setShown("a");
  scheduler.notify("b");
  await tick();
  assert.equal(calls, 0);
  ready = true;
  void scheduler.flush();
  await tick();
  assert.equal(calls, 1);
  scheduler.dispose();
  response.resolve({ revision: "b" });
  await tick();
  assert.equal(applied, 0);
});

test("a local edit waits for the active download to settle before fetching its own mesh", async () => {
  const response = deferred();
  let aborted = false;
  const scheduler = createRefreshScheduler({
    fetchMesh: signal => { signal.addEventListener("abort", () => { aborted = true; response.resolve({ revision: "old" }); }); return response.promise; },
    applyMesh: () => assert.fail("an aborted response must not replace the local edit"),
    canRun: () => !aborted,
    delay: 0,
  });
  scheduler.notify("new");
  await tick();
  await scheduler.cancelAndWait();
  assert.equal(aborted, true);
  scheduler.dispose();
});

test("unknown or malformed SSE messages are ignored", () => {
  assert.equal(parseLiveEvent("otro", '{"id":"x"}'), null);
  assert.equal(parseLiveEvent("documento_actualizado", "oops"), null);
  assert.equal(parseLiveEvent("hola", '{}'), null);
  assert.deepEqual(parseLiveEvent("hola", '{"documentos":[]}'), { documentos: [] });
  assert.deepEqual(parseLiveEvent("documento_actualizado", '{"id":"x","revision":"ab"}'), { id: "x", revision: "ab" });
});
