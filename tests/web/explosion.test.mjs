import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { desplazamientosExplosion, aplicarExplosion } from '../../app/web/static/js/explosion.js';

const aqui = path.dirname(fileURLToPath(import.meta.url));
const raiz = path.resolve(aqui, '..', '..');
const casos = JSON.parse(fs.readFileSync(path.join(raiz, 'tests', 'fixtures', 'explosion_offsets.json'), 'utf8'));

// Malla falsa con la misma forma que usa el visor: position + geometry.boundingBox.
function piezaFalsa(caja) {
  const mesh = {
    position: { x: 0, y: 0, z: 0, set(x, y, z) { this.x = x; this.y = y; this.z = z; } },
    geometry: { boundingBox: { min: { x: caja[0], y: caja[2], z: caja[4] }, max: { x: caja[1], y: caja[3], z: caja[5] } } },
  };
  return { mesh };
}

test('paridad con el oráculo Python: la tabla compartida da los mismos desplazamientos', () => {
  assert.ok(casos.length >= 5);
  for (const caso of casos) {
    const obtenido = desplazamientosExplosion(caso.cajas, caso.articulaciones, caso.factor);
    assert.deepEqual(Object.keys(obtenido).sort(), Object.keys(caso.esperado).sort(), caso.nombre);
    for (const [nombre, vector] of Object.entries(caso.esperado)) {
      vector.forEach((valor, k) => {
        assert.ok(Math.abs(obtenido[nombre][k] - valor) <= 1e-9, `${caso.nombre} ${nombre}[${k}]: ${obtenido[nombre][k]} != ${valor}`);
      });
    }
  }
});

test('ningún desplazamiento es NaN, ni siquiera con el centro coincidente', () => {
  const caso = casos.find((c) => c.nombre === 'centro_coincidente_va_a_mas_z');
  const obtenido = desplazamientosExplosion(caso.cajas, caso.articulaciones, 1);
  for (const vector of Object.values(obtenido)) for (const v of vector) assert.ok(Number.isFinite(v));
});

test('factor fuera de rango, booleano o no numérico: error en español', () => {
  const caja = { A: [0, 1, 0, 1, 0, 1] };
  for (const factor of [-0.1, 1.5, NaN, Infinity, '0.5', true, null]) {
    assert.throws(() => desplazamientosExplosion(caja, [], factor), /entre 0 y 1/);
  }
});

test('caja no finita o invertida: error', () => {
  assert.throws(() => desplazamientosExplosion({ A: [0, NaN, 0, 1, 0, 1] }, [], 0.5));
  assert.throws(() => desplazamientosExplosion({ A: [5, 0, 0, 1, 0, 1] }, [], 0.5));
});

test('aplicarExplosion coloca cada pieza en su desplazamiento y devuelve el mismo mapa', () => {
  const caso = casos.find((c) => c.nombre === 'tres_piezas_en_linea');
  const piezas = new Map(Object.entries(caso.cajas).map(([n, c]) => [n, piezaFalsa(c)]));
  const offsets = aplicarExplosion(piezas, 1, []);
  for (const [nombre, vector] of Object.entries(caso.esperado)) {
    const { x, y, z } = piezas.get(nombre).mesh.position;
    assert.ok(Math.abs(x - vector[0]) <= 1e-9 && Math.abs(y - vector[1]) <= 1e-9 && Math.abs(z - vector[2]) <= 1e-9, nombre);
    assert.deepEqual(offsets[nombre], [x, y, z]);
  }
});

test('factor 0 vuelve las piezas a su sitio; sin piezas no hace nada', () => {
  const piezas = new Map([['A', piezaFalsa([0, 10, 0, 10, 0, 10])], ['B', piezaFalsa([20, 30, 0, 10, 0, 10])]]);
  aplicarExplosion(piezas, 1, []);
  aplicarExplosion(piezas, 0, []);
  for (const pieza of piezas.values()) assert.deepEqual([pieza.mesh.position.x, pieza.mesh.position.y, pieza.mesh.position.z], [0, 0, 0]);
  assert.deepEqual(aplicarExplosion(new Map(), 0.5, []), {});
});

test('una pieza sin triángulos (caja vacía) queda en su sitio y no rompe a las demás', () => {
  // Box3 vacío de three.js: min = +Infinity, max = -Infinity.
  const vacia = { mesh: {
    position: { x: 9, y: 9, z: 9, set(x, y, z) { this.x = x; this.y = y; this.z = z; } },
    geometry: { boundingBox: { min: { x: Infinity, y: Infinity, z: Infinity }, max: { x: -Infinity, y: -Infinity, z: -Infinity } } },
  } };
  const piezas = new Map([['A', piezaFalsa([0, 10, 0, 10, 0, 10])], ['B', piezaFalsa([20, 30, 0, 10, 0, 10])], ['vacia', vacia]]);
  assert.doesNotThrow(() => aplicarExplosion(piezas, 1, []));
  assert.deepEqual([vacia.mesh.position.x, vacia.mesh.position.y, vacia.mesh.position.z], [0, 0, 0]);
  assert.notDeepEqual([piezas.get('A').mesh.position.x, piezas.get('B').mesh.position.x], [0, 0]);
});
