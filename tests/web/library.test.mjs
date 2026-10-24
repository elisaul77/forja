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
  const { eliminarDocumento } = await import('../../app/web/static/js/api.js?v=16');
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

test('papelera: sin token no se envía nada y un 401 olvida el token guardado', async () => {
  const api = await import('../../app/web/static/js/api.js?v=16');
  let sent = 0;
  let removed = false;
  globalThis.sessionStorage = {getItem: () => null, setItem() {}, removeItem() { removed = true; }};
  globalThis.window = {prompt: () => null};
  globalThis.fetch = async () => { sent++; return {status:401, ok:false, json:async () => ({detail:'token requerido'})}; };
  await assert.rejects(api.enviarDocumentoAPapelera('test'), /cancelada/);
  await assert.rejects(api.listarPapelera(), /cancelada/);
  assert.equal(sent, 0);
  window.prompt = () => 'test-only';
  await assert.rejects(api.restaurarDePapelera('test'), /401/);
  assert.equal(sent, 1);
  assert.equal(removed, true);
});

test('borradores: la insignia dice cuanto falta para caducar', async () => {
  const { textoCaducidad } = await import('../../app/web/static/js/library.js');
  const ahora = Date.parse('2026-10-24T12:00:00Z');
  assert.equal(textoCaducidad('2026-10-25T12:00:00Z', ahora), 'Borrador · expira en 24 h');
  assert.equal(textoCaducidad('2026-10-24T12:30:00Z', ahora), 'Borrador · caduca en menos de 1 h');
  assert.equal(textoCaducidad('no-es-fecha', ahora), 'Borrador');
});

test('borradores: el listado pide ?borradores=true solo cuando se pide; conservar hace POST con token', async () => {
  const api = await import('../../app/web/static/js/api.js?v=16');
  const llamadas = [];
  globalThis.sessionStorage = {getItem: () => 'test-only', setItem() {}, removeItem() {}};
  globalThis.fetch = async (url, opts = {}) => {
    llamadas.push({url, metodo: opts.method ?? 'GET', token: opts.headers?.['X-Forja-Token']});
    return {ok: true, status: 200, json: async () => []};
  };
  await api.listarDocumentos();
  await api.listarDocumentos({borradores: true});
  await api.conservarDocumento('abc');
  assert.equal(llamadas[0].url, '/documentos');
  assert.equal(llamadas[1].url, '/documentos?borradores=true');
  assert.deepEqual(llamadas[2], {url: '/documentos/abc/conservar', metodo: 'POST', token: 'test-only'});
});
