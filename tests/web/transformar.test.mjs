// Fase 24b (ADR-0026): acciones de transformar, crear y booleanas desde la barra, y
// el formulario que las convierte en operaciones de candidato.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { ACCIONES } from '../../app/web/static/js/toolbar-actions.js';
import { construirOperacion } from '../../app/web/static/js/design.js';

const aqui = path.dirname(fileURLToPath(import.meta.url));
const raiz = path.resolve(aqui, '..', '..');
const porId = (id) => ACCIONES.find((a) => a.id === id);

const base = {
  tipo: 'transform', cuerpo: 'primitive', pieza: 'solido_1', primitiva: 'box', otra: 'solido_2',
  translation: '1, 2, 3', rotation: '0, 0, 90', size: '10, 10, 10', center: '0, 0, 0', radius: '5',
  factor: '2, 2, 2', modo: 'center', ejes: 'x, y, z', normal: '0, 0, -1',
};
const con = (cambios) => ({ ...base, ...cambios });

test('24b: mover, escalar, alinear, apoyar y los útiles están listos y preparan su tipo', () => {
  const casos = [
    ['solido-mover', { tipo: 'transform' }],
    ['solido-escala', { tipo: 'scale' }],
    ['solido-alinear', { tipo: 'align' }],
    ['solido-apoyar', { tipo: 'drop_to_bed' }],
    ['solido-caja', { tipo: 'union', primitiva: 'box' }],
    ['solido-cilindro', { tipo: 'union', primitiva: 'cylinder' }],
    ['solido-esfera', { tipo: 'union', primitiva: 'sphere' }],
    ['solido-cono', { tipo: 'union', primitiva: 'cone' }],
    ['solido-toro', { tipo: 'union', primitiva: 'torus' }],
    ['solido-cuna', { tipo: 'union', primitiva: 'wedge' }],
    ['solido-tubo', { tipo: 'union', primitiva: 'tube' }],
    ['solido-unir', { tipo: 'union' }],
    ['solido-restar', { tipo: 'subtract' }],
    ['solido-intersecar', { tipo: 'intersect' }],
  ];
  for (const [id, preset] of casos) {
    const accion = porId(id);
    assert.equal(accion.estado, 'listo', id);
    assert.equal(accion.fase, undefined, id);
    assert.deepEqual(accion.requiere, ['documento', 'seleccion'], id);
    const recibido = [];
    accion.ejecutar({ operacion: (p) => recibido.push(p) });
    assert.deepEqual(recibido, [preset], id);
  }
});

test('24b fila 113: «Pedir cambio con lo seleccionado» está lista y llama a c.pedirCambio()', () => {
  const accion = porId('claude-cambio-seleccion');
  assert.equal(accion.estado, 'listo');
  assert.deepEqual(accion.requiere, ['documento', 'seleccion']);
  let llamadas = 0;
  accion.ejecutar({ pedirCambio: () => { llamadas += 1; } });
  assert.equal(llamadas, 1);
});

test('ninguna acción de la fase 24b queda como «Próximamente · 24b»', () => {
  assert.equal(ACCIONES.filter((a) => a.fase === '24b').length, 0);
});

test('cada icono del registro existe en el sprite propio', () => {
  const sprite = fs.readFileSync(path.join(raiz, 'app/web/static/icons/forja-icons.svg'), 'utf8');
  const faltan = [...new Set(ACCIONES.map((a) => a.icono).filter((i) => i && !sprite.includes(`id="i-${i}"`)))];
  assert.deepEqual(faltan, []);
});

test('construirOperacion: mover envía traslación, giro y origen en mm y grados', () => {
  assert.deepEqual(construirOperacion(con({ tipo: 'transform' })), {
    kind: 'transform', piece: 'solido_1', translation: [1, 2, 3], rotation_deg: [0, 0, 90], origin: [0, 0, 0],
  });
});

test('construirOperacion: escalar envía el factor por eje y nada más', () => {
  assert.deepEqual(construirOperacion(con({ tipo: 'scale', factor: '2, 1, 0.5' })), {
    kind: 'scale', piece: 'solido_1', factor: [2, 1, 0.5],
  });
});

test('construirOperacion: alinear por centros en X y Z; apilar no envía ejes', () => {
  assert.deepEqual(construirOperacion(con({ tipo: 'align', modo: 'center', ejes: 'X; z', otra: 'solido_2' })), {
    kind: 'align', piece: 'solido_1', other: 'solido_2', mode: 'center', axes: ['x', 'z'],
  });
  assert.deepEqual(construirOperacion(con({ tipo: 'align', modo: 'stack', ejes: 'x', otra: 'solido_2' })), {
    kind: 'align', piece: 'solido_1', other: 'solido_2', mode: 'stack',
  });
});

test('construirOperacion: alinear rechaza ejes desconocidos o sin pieza de referencia', () => {
  assert.throws(() => construirOperacion(con({ tipo: 'align', ejes: 'w', otra: 'solido_2' })), /x, y o z/);
  assert.throws(() => construirOperacion(con({ tipo: 'align', otra: '' })), /otra pieza/);
});

test('construirOperacion: apoyar sobre cama envía la normal de la cara que apoya', () => {
  assert.deepEqual(construirOperacion(con({ tipo: 'drop_to_bed', normal: '1, 0, 0' })), {
    kind: 'drop_to_bed', piece: 'solido_1', normal: [1, 0, 0],
  });
});

test('construirOperacion: booleana con primitiva envía forma, tamaño, centro y giro del útil', () => {
  assert.deepEqual(construirOperacion(con({ tipo: 'subtract', cuerpo: 'primitive', primitiva: 'cylinder', size: '3, 0, 40' })), {
    kind: 'subtract', piece: 'solido_1', primitive: 'cylinder', size: [3, 0, 40], center: [0, 0, 0], rotation_deg: [0, 0, 90],
  });
});

test('construirOperacion: booleana con otra pieza envía other y no envía forma', () => {
  assert.deepEqual(construirOperacion(con({ tipo: 'union', cuerpo: 'piece' })), {
    kind: 'union', piece: 'solido_1', other: 'solido_2',
  });
});

test('construirOperacion: valores no finitos o vacíos se rechazan antes de enviar', () => {
  assert.throws(() => construirOperacion(con({ tipo: 'scale', factor: '1, NaN, 2' })));
  assert.throws(() => construirOperacion(con({ tipo: 'drop_to_bed', normal: '' })));
});

test('construirOperacion: región STL conserva desplazamiento, centro y radio', () => {
  assert.deepEqual(construirOperacion(con({ tipo: 'deform_region', radius: '7' })), {
    kind: 'deform_region', piece: 'solido_1', translation: [1, 2, 3], center: [0, 0, 0], radius_mm: 7,
  });
});
