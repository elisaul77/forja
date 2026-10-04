import test from 'node:test';
import assert from 'node:assert/strict';
import {
  calcularCarriles, filtrarNodos, fechaRelativa, chipsDeCambios, predicadoFiltros,
  ramaPropia, ramasPorPrimerPadre, coloresDeRamas, trazoArista, numeroEs, piezasTocadas, autor,
} from '../../app/web/static/js/historial-grafo.js';

const n = (sha, padres = [], extra = {}) => ({ sha_corto: sha, padres, ...extra });

test('historia lineal: un solo carril', () => {
  const r = calcularCarriles([n('c', ['b']), n('b', ['a']), n('a')]);
  assert.deepEqual(r.filas.map(f => f.carril), [0, 0, 0]);
  assert.equal(r.carriles, 1);
  assert.deepEqual(r.aristas.map(e => [e.de, e.a]), [[0, 1], [1, 2]]);
});

test('fusión de dos padres abre un segundo carril y converge', () => {
  // m = fusión(a2, b1); b1 sale de o; a2 -> a1 -> o
  const nodos = [n('m', ['a2', 'b1']), n('a2', ['a1']), n('b1', ['o']), n('a1', ['o']), n('o')];
  const r = calcularCarriles(nodos);
  const carril = Object.fromEntries(r.filas.map(f => [f.sha, f.carril]));
  assert.equal(carril.m, 0);
  assert.equal(carril.a2, 0);
  assert.equal(carril.b1, 1);
  assert.equal(carril.o, 0);
  assert.equal(r.carriles, 2);
  const deM = r.aristas.filter(e => e.de === 0);
  assert.equal(deM.length, 2);
  assert.deepEqual(deM.map(e => e.orden).sort(), [0, 1]);
  // b1 -> o baja por el carril 1 y entra en el carril 0
  const bo = r.aristas.find(e => e.de === 2);
  assert.equal(bo.carrilVia, 1);
  assert.equal(bo.carrilA, 0);
  assert.equal(bo.a, 4);
});

test('dos puntas de rama sobre el mismo padre', () => {
  const r = calcularCarriles([n('x', ['o']), n('y', ['o']), n('o')]);
  assert.deepEqual(r.filas.map(f => f.carril), [0, 1, 0]);
  assert.equal(r.aristas.length, 2);
  assert.ok(r.aristas.every(e => e.a === 2 && e.carrilA === 0));
});

test('padre no cargado: arista que sale por abajo', () => {
  const r = calcularCarriles([n('b', ['a'])]);
  assert.equal(r.aristas[0].fuera, true);
  assert.equal(r.aristas[0].a, 1);
});

test('trazoArista produce rutas SVG', () => {
  const geo = { alto: 80, ancho: 16 };
  assert.equal(trazoArista({ de: 0, a: 1, carrilDe: 0, carrilVia: 0, carrilA: 0 }, geo), 'M8 40L8 120');
  assert.match(trazoArista({ de: 0, a: 3, carrilDe: 0, carrilVia: 1, carrilA: 0 }, geo), /^M8 40C.*L24 .*C/);
});

test('filtrarNodos reescribe padres al antepasado conservado', () => {
  const nodos = [n('m', ['a', 'b'], { k: 1 }), n('a', ['o']), n('b', ['o'], { k: 1 }), n('o', [], { k: 1 })];
  const r = filtrarNodos(nodos, x => x.k);
  assert.deepEqual(r.map(x => x.sha_corto), ['m', 'b', 'o']);
  assert.deepEqual(r[0].padres, ['o', 'b']);
  assert.deepEqual(r[1].padres, ['o']);
});

test('predicadoFiltros: texto, autor, hitos y rama', () => {
  const p = n('abc1234567', [], { mensaje: 'Parámetros: alto=30', autor: 'agente', hitos: [], puntas: [], ramas: ['main'] });
  assert.ok(predicadoFiltros({ texto: 'ALTO' })(p));
  assert.ok(predicadoFiltros({ texto: 'abc12' })(p));
  assert.ok(!predicadoFiltros({ autor: 'humano' })(p));
  assert.ok(!predicadoFiltros({ soloHitos: true })(p));
  assert.ok(predicadoFiltros({ soloHitos: true })({ ...p, hitos: ['v1'] }));
  assert.ok(!predicadoFiltros({ rama: 'b' })(p));
});

test('fechaRelativa en español', () => {
  const ahora = Date.parse('2026-10-04T12:00:00Z');
  assert.equal(fechaRelativa('2026-10-04T11:59:50Z', ahora), 'ahora mismo');
  assert.equal(fechaRelativa('2026-10-04T11:55:00Z', ahora), 'hace 5 min');
  assert.equal(fechaRelativa('2026-10-04T10:00:00Z', ahora), 'hace 2 h');
  assert.equal(fechaRelativa('2026-10-03T12:00:00Z', ahora), 'hace 1 día');
  assert.equal(fechaRelativa('2026-09-30T12:00:00Z', ahora), 'hace 4 días');
  assert.equal(fechaRelativa('2026-09-13T12:00:00Z', ahora), 'hace 3 semanas');
  assert.equal(fechaRelativa('2025-10-04T12:00:00Z', ahora), 'hace 1 año');
  assert.equal(fechaRelativa('basura', ahora), '');
});

test('chipsDeCambios', () => {
  const c = { 'añadidas': ['rueda'], cambiadas: ['mástil'], quitadas: ['soporte'], volumen_pct: -0.08,
    parametros: [{ nombre: 'alto', antes: 35, despues: 20 }], materiales: [], script_cambiado: false };
  const chips = chipsDeCambios(c);
  assert.deepEqual(chips.map(x => x.texto), ['+rueda', '~mástil', '−soporte', 'Δvol −0,08 %', 'alto 35→20']);
  assert.deepEqual(chips.map(x => x.tipo), ['añadida', 'cambiada', 'quitada', 'volumen', 'parametro']);
  const muchos = chipsDeCambios({ 'añadidas': ['a', 'b', 'c', 'd', 'e', 'f', 'g'] }, 4);
  assert.equal(muchos.length, 4);
  assert.equal(muchos[3].texto, '+4 más');
  assert.deepEqual(chipsDeCambios({ sin_cambios: true }).map(x => x.texto), ['sin cambios']);
  assert.equal(chipsDeCambios({ error: true })[0].tipo, 'error');
  // the root step lists its initial parameters but they are not «changes»
  assert.deepEqual(chipsDeCambios({ raiz: true, 'añadidas': ['x'], parametros: [{ nombre: 'a', antes: null, despues: 1 }] }).map(x => x.texto), ['+x']);
});

test('numeroEs, ramaPropia, colores, piezasTocadas y autor', () => {
  assert.equal(numeroEs(1234.5), '1.234,5');
  assert.equal(numeroEs(-0.08), '−0,08');
  assert.equal(numeroEs(null), '—');
  assert.equal(ramaPropia({ ramas: ['b', 'main'] }, ['main', 'b']), 'main');
  assert.equal(ramaPropia({ ramas: ['b'] }, ['main', 'b']), 'b');
  assert.deepEqual(coloresDeRamas([{ nombre: 'main' }, { nombre: 'b' }]), { main: 0, b: 1 });
  assert.deepEqual(piezasTocadas({ 'añadidas': ['a'], cambiadas: ['b'], quitadas: [], materiales: ['b', 'c'] }),
    [{ nombre: 'a', estado: 'añadida' }, { nombre: 'b', estado: 'cambiada' }, { nombre: 'c', estado: 'material' }]);
  assert.equal(autor('agente').icono, '🤖');
  assert.equal(autor('humano').icono, '👤');
  assert.equal(autor('forja').icono, '⚙');
});

test('ramasPorPrimerPadre conserva el color de la rama fusionada', () => {
  const nodos = [n('m', ['a1', 'b1'], { puntas: ['main'], ramas: ['main'] }), n('b1', ['o'], { puntas: ['b'], ramas: ['main', 'b'] }),
    n('a1', ['o'], { ramas: ['main'] }), n('o', [], { ramas: ['main', 'b'] })];
  const r = ramasPorPrimerPadre(nodos, ['main', 'b']);
  assert.deepEqual(r, { m: 'main', a1: 'main', o: 'main', b1: 'b' });
});

test('tituloPaso: títulos humanos', async () => {
  const { tituloPaso } = await import('../../app/web/static/js/historial-grafo.js');
  assert.equal(tituloPaso({ mensaje: 'parametros: alto=45, radio=10', cambios: { parametros: [{ nombre: 'alto', antes: 20, despues: 45 }] } }), 'Parámetros: alto 20→45');
  assert.equal(tituloPaso({ mensaje: 'restaurar pieza mastil a d3aa0864f1', cambios: {} }), 'Restaurar «mastil» a d3aa086');
  assert.equal(tituloPaso({ mensaje: 'fusionar ruedas-grandes en main', cambios: {} }), 'Fusión de «ruedas-grandes» en «main»');
  assert.equal(tituloPaso({ mensaje: 'documento creado (script)', cambios: { raiz: true } }), 'Documento creado');
  assert.equal(tituloPaso({ mensaje: 'materiales', cambios: {} }), 'Materiales');
});

test('trazoArista usa los centros medidos', () => {
  const d = trazoArista({ de: 0, a: 1, carrilDe: 0, carrilVia: 0, carrilA: 0 }, { alto: 80, ancho: 16, ys: [50, 150] });
  assert.equal(d, 'M8 50L8 150');
});

test('trazoArista: padre fuera de la lista = tramo corto con flecha, sin trazo libre', () => {
  const d = trazoArista({ de: 2, a: 3, carrilDe: 0, carrilVia: 1, carrilA: 1, fuera: true }, { alto: 80, ancho: 16, ys: [40, 120, 200, 300] });
  const ys = [...d.matchAll(/[ML](-?[\d.]+) (-?[\d.]+)/g)].map(m => Number(m[2]));
  assert.ok(Math.max(...ys) <= 200 + 80, 'no baja más de una fila');
  assert.match(d, /M[\d.]+ [\d.]+L24 256L[\d.]+ [\d.]+$/, 'termina en punta de flecha');
});

test('trazoArista: arista de una fila con carril intermedio = una sola curva al centro del padre', () => {
  const d = trazoArista({ de: 0, a: 1, carrilDe: 2, carrilVia: 1, carrilA: 0 }, { alto: 80, ancho: 16 });
  assert.equal(d, 'M40 40C40 84 8 76 8 120');
});

test('trazoArista: rama que sale y vuelve termina en el centro del padre', () => {
  const d = trazoArista({ de: 0, a: 4, carrilDe: 0, carrilVia: 1, carrilA: 0 }, { alto: 80, ancho: 16 });
  assert.ok(d.startsWith('M8 40C'));
  assert.ok(d.endsWith('8 360'));
  assert.equal((d.match(/C/g) || []).length, 2);
});
