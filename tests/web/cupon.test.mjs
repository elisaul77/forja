import test from 'node:test';
import assert from 'node:assert/strict';
import { cuerpoCupon, resumenCupon, errorCupon, crearBotonCupon } from '../../app/web/static/js/cupon.js';

test('cuerpo: solo la pieza seleccionada', () => {
  assert.deepEqual(cuerpoCupon(null), {});
  assert.deepEqual(cuerpoCupon(''), {});
  assert.deepEqual(cuerpoCupon('eje'), { pieza: 'eje' });
});

test('resumen y errores en español', () => {
  const r = resumenCupon({ piezas: [{ nombre: 'cupon_eje' }, { nombre: 'cupon_buje' }], zonas: [{}] });
  assert.equal(r, 'Cupón creado (1 zona): cupon_eje, cupon_buje.');
  assert.match(errorCupon(422, 'sin zonas de encaje'), /sin zonas de encaje/);
  assert.equal(errorCupon(401), 'Clave de edición incorrecta.');
});

function docFalso() {
  return {
    createElement() {
      const oyentes = {};
      return { addEventListener: (ev, fn) => { oyentes[ev] = fn; }, click: () => oyentes.click() };
    },
  };
}

test('el botón envía la pieza, abre el documento nuevo y ofrece Orca', async () => {
  const llamadas = [];
  const mensajes = [];
  const abiertos = [];
  const fetchFn = async (url, op) => {
    llamadas.push({ url, op });
    return { ok: true, status: 200, json: async () => ({ id_nuevo: 'n1', nombre: 'Cupón — x.step', piezas: [], zonas: [] }) };
  };
  const b = crearBotonCupon(docFalso(), {
    id: 'abc', obtenerPieza: () => 'eje', obtenerToken: () => 't', origin: 'http://localhost:8710',
    abrirDocumento: async (id) => abiertos.push(id), mostrar: (t, e, enlace) => mensajes.push({ t, e, enlace }), fetchFn,
  });
  assert.equal(b.textContent, 'Cupón de prueba');
  await b.click();
  assert.equal(llamadas[0].url, '/documentos/abc/cupon');
  assert.deepEqual(JSON.parse(llamadas[0].op.body), { pieza: 'eje' });
  assert.equal(llamadas[0].op.headers['X-Forja-Token'], 't');
  assert.deepEqual(abiertos, ['n1']);
  const ultimo = mensajes.at(-1);
  assert.equal(ultimo.e, false);
  assert.match(ultimo.enlace.href, /^orcaslicer:\/\/open\?file=http%3A%2F%2Flocalhost%3A8710%2Fdocumentos%2Fn1%2Fdescarga%2FCupon_x\.3mf$/);
});

test('sin token no llama al servidor', async () => {
  let llamado = false;
  const mensajes = [];
  const b = crearBotonCupon(docFalso(), {
    id: 'abc', obtenerToken: () => '', mostrar: (t, e) => mensajes.push(e), fetchFn: async () => { llamado = true; },
  });
  await b.click();
  assert.equal(llamado, false);
  assert.deepEqual(mensajes, [true]);
});
