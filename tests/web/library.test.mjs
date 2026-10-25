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

// Tarjeta falsa: solo lo que usa retirarTarjeta (clases, estilos, posicion por indice en la rejilla).
function fakeCard(id, grid) {
  const transformes = [];
  const clases = new Set();
  const el = {
    dataset: {docId: id},
    transformes,
    clases,
    classList: {
      add: c => clases.add(c),
      remove: c => clases.delete(c),
      contains: c => clases.has(c),
    },
    style: {
      transition: '',
      get transform() { return transformes.at(-1) ?? ''; },
      set transform(v) { transformes.push(v); },
    },
    getBoundingClientRect() { return {left: grid.children.indexOf(el) * 100, top: 0}; },
    addEventListener() {},
    removeEventListener() {},
    remove() { const i = grid.children.indexOf(el); if (i >= 0) grid.children.splice(i, 1); },
  };
  return el;
}

test('papelera con animación: si la petición falla, la tarjeta se queda sin tocar', async () => {
  const { retirarTarjeta } = await import('../../app/web/static/js/library.js');
  const grid = {children: []};
  const card = fakeCard('a', grid);
  grid.children.push(card);
  await assert.rejects(retirarTarjeta({enviar: async () => { throw new Error('red caída'); }, grid, card, salidaMs: 1, reacomodoMs: 1}), /red caída/);
  assert.equal(grid.children.includes(card), true);
  assert.equal(card.clases.has('fj-gallery-card--saliendo'), false);
});

test('papelera con animación: la tarjeta sale, las demás hacen FLIP y los estilos se limpian', async () => {
  const { retirarTarjeta } = await import('../../app/web/static/js/library.js');
  const grid = {children: []};
  const a = fakeCard('a', grid), b = fakeCard('b', grid), c = fakeCard('c', grid);
  grid.children.push(a, b, c);
  let enviados = 0;
  await retirarTarjeta({enviar: async () => { enviados++; }, grid, card: a, salidaMs: 1, reacomodoMs: 1});
  assert.equal(enviados, 1);
  assert.deepEqual(grid.children.map(el => el.dataset.docId), ['b', 'c']);
  // Invert: b estaba en x=100 y ahora en x=0, así que empieza desplazada 100 px.
  assert.equal(b.transformes[0], 'translate(100px, 0px)');
  assert.equal(c.transformes[0], 'translate(100px, 0px)');
  // Play + cleanup: sin transform en línea ni clase de reacomodo al terminar.
  for (const el of [b, c]) {
    assert.equal(el.style.transform, '');
    assert.equal(el.style.transition, '');
    assert.equal(el.clases.has('fj-gallery-card--reacomodando'), false);
  }
});

test('papelera con movimiento reducido: la tarjeta sale al instante, sin animación', async () => {
  const { retirarTarjeta } = await import('../../app/web/static/js/library.js');
  const grid = {children: []};
  const a = fakeCard('a', grid), b = fakeCard('b', grid);
  grid.children.push(a, b);
  await retirarTarjeta({enviar: async () => {}, grid, card: a, reducirMovimiento: true});
  assert.deepEqual(grid.children.map(el => el.dataset.docId), ['b']);
  assert.equal(b.transformes.length, 0);
  assert.equal(a.clases.has('fj-gallery-card--saliendo'), false);
});
