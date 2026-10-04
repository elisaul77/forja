import test from 'node:test';
import assert from 'node:assert/strict';
import { latestStep } from '../../app/web/static/js/library.js';

test('latest STEP uses modification time, ignores newer STL, and preserves input', () => {
  const docs = [
    {id:'old', formato:'step', actualizado:'2026-01-01T00:00:00Z'},
    {id:'mesh', formato:'stl', actualizado:'2026-09-01T00:00:00Z'},
    {id:'new', formato:'step', actualizado:'2026-08-01T00:00:00Z'},
  ];
  assert.equal(latestStep(docs).id, 'new');
  assert.equal(docs[0].id, 'old');
});
test('legacy extension, empty library and timestamp ties', () => {
  assert.equal(latestStep([]), undefined);
  assert.equal(latestStep([{id:'a', nombre:'part.STP'}]).id, 'a');
  assert.equal(latestStep([{id:'b', formato:'step'}, {id:'a', formato:'step'}]).id, 'a');
});

test('delete cancellation sends no request; unauthorized clears saved token', async () => {
  const { eliminarDocumento } = await import('../../app/web/static/js/api.js?v=9');
  let sent = 0;
  let removed = false;
  globalThis.sessionStorage = {getItem: () => null, setItem() {}, removeItem() { removed = true; }};
  globalThis.window = {prompt: () => null};
  globalThis.fetch = async () => { sent++; return {status:401, ok:false, json:async () => ({detail:'no autorizado'})}; };
  await assert.rejects(eliminarDocumento('test'), /cancelada/);
  assert.equal(sent, 0);
  window.prompt = () => 'test-only';
  await assert.rejects(eliminarDocumento('test'), /401/);
  assert.equal(sent, 1);
  assert.equal(removed, true);
});
