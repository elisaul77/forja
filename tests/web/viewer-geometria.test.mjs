import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  calcularEncuadre, aplicarMarcosOrtograficos, liberarAristas, liberarMalla,
} from '../../app/web/static/js/viewer-geometria.js';

const aqui = path.dirname(fileURLToPath(import.meta.url));
const raiz = path.resolve(aqui, '..', '..');
const leer = (rel) => fs.readFileSync(path.join(raiz, rel), 'utf8');

// Recurso con dispose() que registra su liberación en `log`.
const recurso = (log, nombre) => ({ dispose() { log.push(nombre); } });

test('encuadre ortográfico: sin fov ni aspecto válidos devuelve números finitos', () => {
  const e = calcularEncuadre({ ortogonal: true, fov: undefined, aspecto: NaN, radio: 10 });
  assert.ok(Number.isFinite(e.distancia) && e.distancia > 0, `distancia ${e.distancia}`);
  assert.ok(Number.isFinite(e.altura) && e.altura > 0, `altura ${e.altura}`);
  assert.equal(e.altura, 10 * 1.15);
});

test('encuadre perspectiva usa fov y aspecto; más radio, más distancia', () => {
  const cerca = calcularEncuadre({ ortogonal: false, fov: 45, aspecto: 1.5, radio: 10 });
  const lejos = calcularEncuadre({ ortogonal: false, fov: 45, aspecto: 1.5, radio: 20 });
  assert.ok(Number.isFinite(cerca.distancia));
  assert.ok(lejos.distancia > cerca.distancia);
  // Un visor más estrecho necesita más distancia para caber el mismo radio.
  const estrecho = calcularEncuadre({ ortogonal: false, fov: 45, aspecto: 0.5, radio: 10 });
  assert.ok(estrecho.distancia > cerca.distancia);
});

test('aspecto inválido se trata como 1, nunca como NaN', () => {
  for (const aspecto of [0, -2, NaN, undefined]) {
    const e = calcularEncuadre({ ortogonal: true, aspecto, radio: 5 });
    assert.ok(Number.isFinite(e.distancia) && Number.isFinite(e.altura), `aspecto ${aspecto}`);
  }
});

test('marcos ortográficos: conservan el zoom del usuario y actualizan la proyección', () => {
  let actualizaciones = 0;
  const camara = { zoom: 2.5, left: 0, right: 0, top: 0, bottom: 0, updateProjectionMatrix() { actualizaciones += 1; } };
  aplicarMarcosOrtograficos(camara, { altura: 10, aspecto: 2 });
  assert.equal(camara.zoom, 2.5);
  assert.deepEqual([camara.left, camara.right, camara.top, camara.bottom], [-20, 20, 10, -10]);
  assert.equal(actualizaciones, 1);
});

test('marcos ortográficos: solo el encuadre explícito reinicia el zoom', () => {
  const camara = { zoom: 3, updateProjectionMatrix() {} };
  aplicarMarcosOrtograficos(camara, { altura: 4, aspecto: 1, reiniciarZoom: true });
  assert.equal(camara.zoom, 1);
});

test('liberarMalla libera geometría, material y solo las aristas hijas', () => {
  const log = [];
  const aristas = { userData: { esAristas: true }, geometry: recurso(log, 'geo-aristas'), material: recurso(log, 'mat-aristas') };
  const otro = { userData: {}, geometry: recurso(log, 'geo-otro'), material: recurso(log, 'mat-otro') };
  const malla = {
    geometry: recurso(log, 'geo-malla'),
    material: recurso(log, 'mat-malla'),
    children: [aristas, otro],
    remove(hijo) { this.children = this.children.filter((h) => h !== hijo); },
  };
  liberarMalla(malla);
  assert.deepEqual(log.sort(), ['geo-aristas', 'geo-malla', 'mat-aristas', 'mat-malla']);
  assert.deepEqual(malla.children, [otro]);
});

test('liberarAristas es idempotente: una segunda pasada no vuelve a liberar nada', () => {
  const log = [];
  const aristas = { userData: { esAristas: true }, geometry: recurso(log, 'geo'), material: recurso(log, 'mat') };
  const objeto = { children: [aristas], remove(hijo) { this.children = this.children.filter((h) => h !== hijo); } };
  assert.equal(liberarAristas(objeto), 1);
  assert.equal(liberarAristas(objeto), 0);
  assert.deepEqual(log, ['geo', 'mat']);
});

test('liberarMalla admite materiales en array y tolera objetos nulos', () => {
  const log = [];
  liberarMalla(null);
  liberarMalla({ geometry: recurso(log, 'g'), material: [recurso(log, 'm1'), recurso(log, 'm2')], children: [] });
  assert.deepEqual(log, ['g', 'm1', 'm2']);
});

test('viewer.js cablea el encuadre y la liberación compartidos', () => {
  const visor = leer('app/web/static/js/viewer.js');
  assert.match(visor, /from "\.\/viewer-geometria\.js(\?v=\d+)?"/, 'no importa viewer-geometria.js');
  assert.match(visor, /calcularEncuadre\(/, 'ajustarVista no usa calcularEncuadre');
  // Sin el campo de perspectiva en la rama ortográfica.
  assert.doesNotMatch(visor, /_ajustarFrustum/, 'queda el frustum antiguo que reinicia el zoom');
  const aristas = visor.slice(visor.indexOf('setAristas(enabled) {'), visor.indexOf('setRayosX(enabled) {'));
  assert.ok(aristas.length > 0, 'no encuentro setAristas');
  assert.doesNotMatch(aristas, /\.dispose\(\)/, 'setAristas libera las aristas a mano');
  assert.ok((visor.match(/liberarMalla\(/g) ?? []).length >= 3, 'faltan liberaciones con liberarMalla');
});
