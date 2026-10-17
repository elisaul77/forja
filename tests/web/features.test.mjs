import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { ACCIONES } from '../../app/web/static/js/toolbar-actions.js';
import {
  RASGOS, construirOperacion, etiquetaElemento, piezasEditables,
} from '../../app/web/static/js/features.js';

const aqui = path.dirname(fileURLToPath(import.meta.url));
const raiz = path.resolve(aqui, '..', '..');
const leer = (rel) => fs.readFileSync(path.join(raiz, rel), 'utf8');

const arista = (over = {}) => ({
  tipo: 'arista', subtipo: 'LINE', centroide: [10, 10, 0], direccion: [0, 0, 1], medida: 10, ...over,
});
const cara = (over = {}) => ({
  tipo: 'cara', subtipo: 'PLANE', centroide: [0, 0, 5], direccion: [0, 0, 1], medida: 400, ...over,
});

// ---------------- Construcción de la operación (contrato Operation) ----------------

test('filete: la operación lleva solo los campos del contrato y el radio en mm', () => {
  const op = construirOperacion('fillet', { pieza: 'tapa', huellas: [arista()], valor: '2' });
  assert.deepEqual(Object.keys(op).sort(), ['huellas', 'kind', 'piece', 'valor_mm']);
  assert.equal(op.kind, 'fillet');
  assert.equal(op.piece, 'tapa');
  assert.equal(op.valor_mm, 2);
  assert.deepEqual(op.huellas[0], arista());
});

test('la coma decimal se acepta y los números no finitos se rechazan', () => {
  assert.equal(construirOperacion('chamfer', { pieza: 'a', huellas: [arista()], valor: '1,5' }).valor_mm, 1.5);
  for (const malo of ['', 'abc', 'Infinity', 'NaN', '1e400']) {
    assert.throws(() => construirOperacion('fillet', { pieza: 'a', huellas: [arista()], valor: malo }), /número válido/);
  }
});

test('valores fuera de tope o cero donde no cabe se rechazan', () => {
  assert.throws(() => construirOperacion('fillet', { pieza: 'a', huellas: [arista()], valor: '0' }), /entre/);
  assert.throws(() => construirOperacion('fillet', { pieza: 'a', huellas: [arista()], valor: '1001' }), /entre/);
  assert.throws(() => construirOperacion('push_pull', { pieza: 'a', huellas: [cara()], valor: '0' }), /no puede ser cero/);
  assert.equal(construirOperacion('push_pull', { pieza: 'a', huellas: [cara()], valor: '-3' }).valor_mm, -3);
});

test('sin pieza o sin referencias no se construye nada', () => {
  assert.throws(() => construirOperacion('fillet', { pieza: '', huellas: [arista()], valor: '1' }), /Elige una pieza/);
  assert.throws(() => construirOperacion('fillet', { pieza: 'a', huellas: [], valor: '1' }), /al menos una arista/);
  assert.throws(() => construirOperacion('shell', { pieza: 'a', huellas: [], valor: '1' }), /al menos una cara/);
});

test('el tipo de referencia debe coincidir con el rasgo', () => {
  assert.throws(() => construirOperacion('fillet', { pieza: 'a', huellas: [cara()], valor: '1' }), /aristas/);
  assert.throws(() => construirOperacion('shell', { pieza: 'a', huellas: [arista()], valor: '1' }), /caras/);
});

test('desfase de cara admite exactamente una cara', () => {
  assert.throws(() => construirOperacion('push_pull', { pieza: 'a', huellas: [cara(), cara({ centroide: [1, 0, 5] })], valor: '1' }), /exactamente una cara/);
});

test('matriz lineal lleva separación; la circular, ángulo y no separación', () => {
  const lineal = construirOperacion('pattern', { pieza: 'a', patron: 'lineal', eje: 'x', cantidad: '3', valor: '30' });
  assert.equal(lineal.patron, 'lineal');
  assert.equal(lineal.cantidad, 3);
  assert.equal(lineal.valor_mm, 30);
  const circular = construirOperacion('pattern', { pieza: 'a', patron: 'circular', eje: 'z', cantidad: '4', angulo: '360', valor: 'basura' });
  assert.equal(circular.patron, 'circular');
  assert.equal(circular.angulo_deg, 360);
  assert.equal('valor_mm' in circular, false);
});

test('matriz acota copias, ángulo y eje', () => {
  const base = { pieza: 'a', patron: 'lineal', eje: 'x', valor: '10' };
  assert.throws(() => construirOperacion('pattern', { ...base, cantidad: '33' }), /entre 2 y 32/);
  assert.throws(() => construirOperacion('pattern', { ...base, cantidad: '1' }), /entre 2 y 32/);
  assert.throws(() => construirOperacion('pattern', { ...base, cantidad: '2.5' }), /entero/);
  assert.throws(() => construirOperacion('pattern', { ...base, patron: 'circular', cantidad: '4', angulo: '400' }), /entre 0.01 y 360/);
  assert.throws(() => construirOperacion('pattern', { ...base, eje: 'w' }), /Eje no válido/);
});

test('simetría y división llevan eje y plano; el plano por defecto es 0', () => {
  const op = construirOperacion('mirror', { pieza: 'a', eje: 'y' });
  assert.deepEqual([op.eje, op.plano_mm], ['y', 0]);
  assert.equal(construirOperacion('split', { pieza: 'a', eje: 'z', plano: '-4,5' }).plano_mm, -4.5);
  assert.throws(() => construirOperacion('split', { pieza: 'a', eje: 'z', plano: 'x' }), /número válido/);
});

test('reparar malla no lleva referencias ni valores', () => {
  assert.deepEqual(construirOperacion('mesh_repair', { pieza: 'malla' }), { kind: 'mesh_repair', piece: 'malla' });
});

test('rasgo desconocido se rechaza', () => {
  assert.throws(() => construirOperacion('inventado', { pieza: 'a' }), /Rasgo desconocido/);
});

test('cada rasgo del formulario tiene título en español', () => {
  for (const [clave, spec] of Object.entries(RASGOS)) {
    assert.ok(spec.titulo.length > 0, clave);
  }
});

// ---------------- Etiquetas y piezas ----------------

test('la etiqueta de una arista da forma, longitud y posición en mm', () => {
  const texto = etiquetaElemento({ id: 'x', huella: arista({ medida: 24, centroide: [1.5, -2, 0] }) });
  assert.match(texto, /^Arista recta · 24 mm · en \(1,5, -2, 0\)$/);
  assert.match(etiquetaElemento({ id: 'y', huella: cara({ subtipo: 'CYLINDER', medida: 3.14159 }) }), /^Cara cilíndrica · 3,14 mm²/);
});

test('las piezas editables excluyen bloqueadas y de referencia', () => {
  const diseno = {
    pieces: [{ name: 'tapa' }, { name: 'base' }, { name: 'guia' }, { name: 'ref' }],
    design: { parts: { base: { locked: true }, guia: { role: 'reference' }, ref: { role: 'reserved' } } },
  };
  assert.deepEqual(piezasEditables(diseno), ['tapa']);
  assert.deepEqual(piezasEditables({ pieces: [] }), []);
});

// ---------------- Registro de la barra ----------------

test('los rasgos 24d quedan activos en el registro y llaman al formulario', () => {
  const porId = (id) => ACCIONES.find((a) => a.id === id);
  const esperados = {
    'solido-filete': 'fillet', 'solido-chaflan': 'chamfer', 'solido-vaciar': 'shell',
    'solido-desfasar': 'push_pull', 'solido-dividir': 'split', 'solido-reparar': 'mesh_repair',
    'solido-simetria': 'mirror', 'solido-matriz': 'pattern',
  };
  for (const [id, tipo] of Object.entries(esperados)) {
    const accion = porId(id);
    assert.ok(accion, id);
    assert.equal(accion.estado, 'listo', id);
    let llamado = null;
    accion.ejecutar({ rasgo: (t) => { llamado = t; } });
    assert.equal(llamado, tipo, id);
  }
});

test('los rasgos no construidos siguen visibles como próximamente 24d', () => {
  for (const id of ['solido-combinar', 'solido-malla-solido', 'solido-desmoldeo', 'solido-sanar']) {
    const accion = ACCIONES.find((a) => a.id === id);
    assert.equal(accion.estado, 'proximamente', id);
    assert.equal(accion.fase, '24d', id);
  }
});

test('cada icono del registro tiene su símbolo en el sprite', () => {
  const sprite = leer('app/web/static/icons/forja-icons.svg');
  const faltan = [...new Set(ACCIONES.map((a) => a.icono))].filter((i) => !sprite.includes(`<symbol id="i-${i}"`));
  assert.deepEqual(faltan, []);
});

test('los módulos nuevos no contienen direcciones http(s) ni CDN', () => {
  for (const rel of ['app/web/static/js/features.js', 'app/web/static/css/forja-toolbar.css']) {
    assert.doesNotMatch(leer(rel), /https?:\/\//, rel);
  }
});

test('cada módulo se importa con una sola versión ?v=', () => {
  const versiones = new Map();
  const dirs = [
    path.join(raiz, 'app/web/static/js'),
    path.join(raiz, 'tests/web'),
  ];
  for (const dir of dirs) {
    for (const f of fs.readdirSync(dir)) {
      if (!/\.(m?js)$/.test(f)) continue;
      const texto = fs.readFileSync(path.join(dir, f), 'utf8');
      for (const m of texto.matchAll(/([a-z-]+\.m?js)\?v=(\d+)/g)) {
        if (!versiones.has(m[1])) versiones.set(m[1], new Set());
        versiones.get(m[1]).add(m[2]);
      }
    }
  }
  for (const modulo of ['api.js', 'toolbar-actions.js', 'toolbar-ui.js', 'features.js']) {
    assert.equal(versiones.get(modulo)?.size, 1, `${modulo}: ${[...(versiones.get(modulo) ?? [])]}`);
  }
});
