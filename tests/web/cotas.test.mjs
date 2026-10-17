// F23a (ADR-0023), F23b (ADR-0025): cotas vivas en el panel de parámetros. Funciones puras de
// `cotas.js` (etiquetas, validación del campo, descarte de respuestas viejas)
// y una guarda: el módulo nunca escribe HTML, todo texto va por textContent.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { etiquetaCota, validarValorCota, esGeneracionVigente, textoOperacion, etiquetasFlotantes } from '../../app/web/static/js/cotas.js';

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

const escala = { id: 'tamano_total:x:40.000', tipo: 'tamano_total', eje: 'x', valor_mm: 40, operacion: 'escalar_eje' };
const taladro = { id: 'diametro:z:8.000:n1', tipo: 'diametro', eje: 'z', valor_mm: 8, cantidad: 1, operacion: 'rellenar_y_taladrar' };
const estirar = { id: 'distancia:z:10.000:-5.000', tipo: 'distancia', eje: 'z', valor_mm: 10, operacion: 'mover_cara' };

test('textoOperacion dice qué hará cada operación de un documento importado', () => {
  assert.equal(textoOperacion(escala, 60), 'Escala la pieza en X hasta 60 mm; los demás ejes no cambian.');
  assert.match(textoOperacion({ ...taladro, cantidad: 3 }, 6), /Rellena los 3 agujeros y lo retaladra a Ø6 mm/);
  assert.match(textoOperacion(taladro, 10), /Rellena el agujero y lo retaladra a Ø10 mm/);
  assert.match(textoOperacion(estirar, 14), /Separa o junta las dos caras hasta 14 mm/);
  assert.equal(textoOperacion({ operacion: null }), '');
});

test('validarValorCota aplica a una medida geométrica el suelo de 0,1 veces y el tope de 10', () => {
  assert.equal(validarValorCota(escala, '4').ok, true);
  assert.equal(validarValorCota(escala, '3.9').ok, false);
  assert.match(validarValorCota(escala, '3.9').mensaje, /Mínimo 0.1 veces/);
  assert.equal(validarValorCota(escala, '400').ok, true);
  assert.equal(validarValorCota(escala, '401').ok, false);
});

test('validarValorCota rechaza el mismo valor que la medida actual en una cota geométrica', () => {
  assert.equal(validarValorCota(taladro, '8').ok, false);
  assert.match(validarValorCota(taladro, '8').mensaje, /ya mide ese valor/);
});

test('el suelo y el cambio nulo no se aplican a un parámetro de script', () => {
  assert.equal(validarValorCota(sinTope, '1').ok, true); // sin suelo geometrico: 1 mm < 0,1 x 20 mm
  assert.equal(validarValorCota(sinTope, '20').ok, true);
});

test('etiquetasFlotantes solo pone etiqueta en las cotas editables con ancla (F23b)', () => {
  const conAncla = { ...taladro, ancla: [10, 0, 0] };
  const sinAncla = { ...escala };
  const soloLectura = { id: 'posicion:x:30.000:x', tipo: 'posicion', eje: 'x', valor_mm: 30, solo_lectura: true, razon: 'x', ancla: [1, 2, 3] };
  assert.deepEqual(etiquetasFlotantes([conAncla, sinAncla, soloLectura]), [
    { id: 'diametro:z:8.000:n1', texto: 'Diámetro Z 8 mm', ancla: [10, 0, 0] },
  ]);
});

test('etiquetasFlotantes separa las distancias que caen sobre el mismo punto (F23b)', () => {
  // Una placa centrada: las distancias X, Y y Z tienen su ancla en el origen y se apilarían.
  const distancia = (eje) => ({ id: `distancia:${eje}:10.000:n`, tipo: 'distancia', eje, valor_mm: 10, operacion: 'mover_cara', ancla: [0, 0, 0] });
  const anclas = etiquetasFlotantes([distancia('x'), distancia('y'), distancia('z')]).map((e) => e.ancla);
  assert.deepEqual(anclas, [[0, 0, 0], [0, 8, 0], [0, 0, 8]]);
});
