// F23a (ADR-0022): cotas vivas en el panel de parámetros. Funciones puras de
// `cotas.js` (etiquetas, validación del campo, descarte de respuestas viejas)
// y una guarda: el módulo nunca escribe HTML, todo texto va por textContent.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { etiquetaCota, validarValorCota, esGeneracionVigente } from '../../app/web/static/js/cotas.js';

const editable = { id: 'tamano_total:x:40.000', tipo: 'tamano_total', eje: 'x', valor_mm: 40, operacion: 'parametro', min: 10, max: 80 };
const sinTope = { id: 'tamano_total:z:20.000', tipo: 'tamano_total', eje: 'z', valor_mm: 20, operacion: 'parametro' };

test('etiquetaCota usa nombres en español y cuenta los agujeros de un grupo', () => {
  assert.equal(etiquetaCota(editable), 'Tamaño X');
  assert.equal(etiquetaCota({ tipo: 'diametro', eje: 'z', cantidad: 3 }), 'Diámetro Z (3 agujeros)');
  assert.equal(etiquetaCota({ tipo: 'posicion', eje: 'y', cantidad: 1 }), 'Posición Y');
});

test('validarValorCota acepta números finitos positivos dentro de min/max', () => {
  assert.deepEqual(validarValorCota(editable, '50'), { ok: true, valor: 50 });
  assert.deepEqual(validarValorCota(editable, ' 12.5 '), { ok: true, valor: 12.5 });
});

test('validarValorCota rechaza vacio, NaN, cero, negativos e infinitos', () => {
  for (const texto of ['', '   ', 'abc', '0', '-3', 'Infinity', 'NaN', '1e999']) {
    assert.equal(validarValorCota(editable, texto).ok, false, texto);
  }
});

test('validarValorCota respeta min y max del parámetro', () => {
  assert.equal(validarValorCota(editable, '90').ok, false);
  assert.match(validarValorCota(editable, '90').mensaje, /Máximo 80/);
  assert.equal(validarValorCota(editable, '5').ok, false);
  assert.match(validarValorCota(editable, '5').mensaje, /Mínimo 10/);
});

test('sin max del parámetro el tope es 10 veces la medida actual', () => {
  assert.equal(validarValorCota(sinTope, '200').ok, true);
  assert.equal(validarValorCota(sinTope, '201').ok, false);
});

test('esGeneracionVigente descarta respuestas de una lectura superada', () => {
  assert.equal(esGeneracionVigente(3, 3), true);
  assert.equal(esGeneracionVigente(2, 3), false);
});

test('cotas.js no escribe HTML: solo textContent, sin innerHTML ni insertAdjacentHTML', () => {
  const fuente = readFileSync(new URL('../../app/web/static/js/cotas.js', import.meta.url), 'utf8');
  assert.equal(/innerHTML|outerHTML|insertAdjacentHTML|document\.write/.test(fuente), false);
  assert.ok(fuente.includes('textContent'));
});
