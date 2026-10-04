import test from 'node:test';
import assert from 'node:assert/strict';
import { coloresDePiezas, etiquetaMaterial, entradaDesdeFormulario } from '../../app/web/static/js/materiales.js';

test('coloresDePiezas solo toma colores #RRGGBB válidos', () => {
  assert.deepEqual(coloresDePiezas({ a: { color: '#20a0ff' }, b: { material: 'PLA' }, c: { color: 'rojo' } }), { a: '#20A0FF' });
  assert.deepEqual(coloresDePiezas(null), {});
});

test('etiquetaMaterial', () => {
  assert.equal(etiquetaMaterial(undefined), 'Sin material');
  assert.equal(etiquetaMaterial({ material: 'TPU', extrusor: 2 }), 'TPU · E2');
  assert.equal(etiquetaMaterial({ color: '#000000' }), 'Solo color');
});

test('entradaDesdeFormulario valida y normaliza', () => {
  assert.deepEqual(entradaDesdeFormulario({ material: '  PETG   negro ', color: '#202020', extrusor: '2', conColor: true }),
    { material: 'PETG negro', color: '#202020', extrusor: 2 });
  assert.equal(entradaDesdeFormulario({ material: ' ', extrusor: '' }), null);
  assert.deepEqual(entradaDesdeFormulario({ material: 'PLA', color: '#ffffff', conColor: false }), { material: 'PLA' });
  assert.throws(() => entradaDesdeFormulario({ extrusor: '17' }), /1 a 16/);
  assert.throws(() => entradaDesdeFormulario({ extrusor: '1.5' }), /1 a 16/);
  assert.throws(() => entradaDesdeFormulario({ material: 'x'.repeat(65) }), /64/);
});
