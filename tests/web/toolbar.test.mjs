import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { PESTANAS, ACCIONES } from '../../app/web/static/js/toolbar-actions.js';
import {
  estadoAccion, tooltipLineas, filtrarPaleta, claveDesdeEvento, debeIgnorarAtajo,
  buscarPorAtajo, validarRegistro, ATAJOS_RESERVADOS,
} from '../../app/web/static/js/toolbar.js';

const aqui = path.dirname(fileURLToPath(import.meta.url));
const raiz = path.resolve(aqui, '..', '..');
const leer = (rel) => fs.readFileSync(path.join(raiz, rel), 'utf8');

const DOC_STEP = { formato: 'step' };
const DOC_STL = { formato: 'stl' };
const porId = (id) => ACCIONES.find((a) => a.id === id);

test('ids de acción únicos y pestañas válidas', () => {
  const ids = ACCIONES.map((a) => a.id);
  assert.equal(new Set(ids).size, ids.length, `ids repetidos: ${ids.filter((x, i) => ids.indexOf(x) !== i)}`);
  const pestanas = new Set(PESTANAS.map((p) => p.id));
  assert.equal(pestanas.size, PESTANAS.length);
  for (const a of ACCIONES) assert.ok(pestanas.has(a.pestana), `${a.id}: pestaña desconocida ${a.pestana}`);
});

test('atajos únicos, con formato válido y sin combinaciones reservadas por el navegador', () => {
  const atajos = ACCIONES.filter((a) => a.atajo).map((a) => a.atajo);
  assert.equal(new Set(atajos).size, atajos.length, `atajos repetidos: ${atajos.filter((x, i) => atajos.indexOf(x) !== i)}`);
  assert.deepEqual(validarRegistro(ACCIONES), []);
  for (const atajo of atajos) assert.ok(!ATAJOS_RESERVADOS.includes(atajo), `atajo reservado: ${atajo}`);
});

test('cada acción lista tiene ejecutar; las próximas tienen fase y no ejecutan', () => {
  for (const a of ACCIONES) {
    if (a.estado === 'listo') assert.equal(typeof a.ejecutar, 'function', `${a.id} sin ejecutar`);
    if (a.estado === 'proximamente') {
      assert.ok(a.fase, `${a.id} sin fase`);
      assert.equal(a.ejecutar, undefined, `${a.id} es próxima y no debe ejecutar`);
    }
    assert.ok(['listo', 'proximamente'].includes(a.estado), `${a.id}: estado ${a.estado}`);
  }
});

test('catálogo 24a: acciones EXISTS/UI_ONLY y placeholders de fases F18-F23 presentes', () => {
  for (const id of ['vista-ajustar', 'vista-frontal', 'vista-isometrica', 'vista-ortografica', 'vista-alambre',
    'vista-rejilla', 'vista-plano-corte', 'vista-aislar', 'vista-captura', 'inspec-seleccionar',
    'ens-articulacion', 'ens-reproducir', 'hist-deshacer', 'imp-exportar-3mf', 'imp-abrir-orca', 'claude-bandeja',
    'ens-barrido', 'ens-explosionada', 'vista-captura-enfocada', 'inspec-medir', 'planos-generar', 'inspec-cotas-vivas']) {
    assert.ok(porId(id), `falta ${id}`);
  }
  assert.equal(porId('ens-barrido').fase, 'F18');
  assert.equal(porId('inspec-medir').fase, 'F21');
  assert.equal(porId('planos-generar').fase, 'F22');
});

test('habilitación: sin documento, STL, sin selección y próximas', () => {
  const ajustar = porId('vista-ajustar');
  const sinDoc = estadoAccion(ajustar, { documento: null, seleccion: null });
  assert.equal(sinDoc.habilitada, false);
  assert.match(sinDoc.motivo, /Abre un documento/);

  assert.equal(estadoAccion(ajustar, { documento: DOC_STEP, seleccion: null }).habilitada, true);

  const aislar = porId('vista-aislar');
  const sinPieza = estadoAccion(aislar, { documento: DOC_STEP, seleccion: null });
  assert.equal(sinPieza.habilitada, false);
  assert.match(sinPieza.motivo, /Selecciona una pieza/);
  assert.equal(estadoAccion(aislar, { documento: DOC_STEP, seleccion: 'eje' }).habilitada, true);

  // Las acciones solo-B-rep se prueban con un fixture: en 24a ninguna acción
  // listada necesita B-rep (los placeholders muestran su fase, no el motivo B-rep).
  const soloBrep = { id: 'x', etiqueta: 'X', estado: 'listo', requiere: ['documento', 'b-rep'], ejecutar() {} };
  const stl = estadoAccion(soloBrep, { documento: DOC_STL, seleccion: null });
  assert.equal(stl.habilitada, false);
  assert.match(stl.motivo, /B-rep/);
  assert.equal(estadoAccion(soloBrep, { documento: DOC_STEP, seleccion: null }).habilitada, true);

  const medir = estadoAccion(porId('inspec-medir'), { documento: DOC_STEP, seleccion: null });
  assert.equal(medir.habilitada, false);
  assert.equal(medir.motivo, 'Próximamente · F21');
});

test('tooltip: etiqueta con atajo, efecto y motivo en líneas separadas', () => {
  const ajustar = porId('vista-ajustar');
  const listo = tooltipLineas(ajustar, { documento: DOC_STEP, seleccion: null });
  assert.equal(listo[0], 'Ajustar a pantalla (F)');
  assert.equal(listo.length, 2);
  const cerrado = tooltipLineas(ajustar, { documento: null, seleccion: null });
  assert.equal(cerrado.length, 3);
  assert.equal(cerrado[2], 'Abre un documento primero');
});

test('paleta: filtra por etiqueta en español, sin distinguir tildes', () => {
  const todas = filtrarPaleta(ACCIONES, '');
  assert.equal(todas.length, ACCIONES.length);
  assert.ok(filtrarPaleta(ACCIONES, 'ajustar').some((a) => a.id === 'vista-ajustar'));
  const estandar = filtrarPaleta(ACCIONES, 'estandar').map((a) => a.id);
  assert.ok(estandar.includes('vista-frontal'));
  assert.deepEqual(filtrarPaleta(ACCIONES, 'zzzqqq'), []);
  assert.ok(filtrarPaleta(ACCIONES, 'AJUSTAR').some((a) => a.id === 'vista-ajustar'));
});

test('claveDesdeEvento normaliza teclas y modificadores', () => {
  assert.equal(claveDesdeEvento({ key: 'f' }), 'F');
  assert.equal(claveDesdeEvento({ key: 'Escape' }), 'Esc');
  assert.equal(claveDesdeEvento({ key: '?', shiftKey: true }), '?');
  assert.equal(claveDesdeEvento({ key: 'k', ctrlKey: true }), 'Ctrl+K');
  assert.equal(claveDesdeEvento({ key: 'Z', ctrlKey: true, shiftKey: true }), 'Ctrl+Shift+Z');
  assert.equal(claveDesdeEvento({ key: 'i', altKey: true }), 'Alt+I');
  assert.equal(buscarPorAtajo(ACCIONES, 'Ctrl+K'), undefined);
  assert.equal(buscarPorAtajo(ACCIONES, 'F')?.id, 'vista-ajustar');
});

test('atajos ignorados en campos, zonas editables y diálogos abiertos', () => {
  const body = { tagName: 'BODY', isContentEditable: false };
  const input = { tagName: 'INPUT', isContentEditable: false };
  const area = { tagName: 'TEXTAREA', isContentEditable: false };
  const editable = { tagName: 'DIV', isContentEditable: true };
  const evento = { key: 'f', ctrlKey: false, altKey: false };
  assert.equal(debeIgnorarAtajo(evento, { target: body, dialogoAbierto: false, zonaVisor: true }), false);
  assert.equal(debeIgnorarAtajo(evento, { target: input, dialogoAbierto: false, zonaVisor: true }), true);
  assert.equal(debeIgnorarAtajo(evento, { target: area, dialogoAbierto: false, zonaVisor: true }), true);
  assert.equal(debeIgnorarAtajo(evento, { target: editable, dialogoAbierto: false, zonaVisor: true }), true);
  assert.equal(debeIgnorarAtajo(evento, { target: body, dialogoAbierto: true, zonaVisor: true }), true);
  // Letras sueltas solo con el foco en el visor o en la cinta.
  assert.equal(debeIgnorarAtajo(evento, { target: body, dialogoAbierto: false, zonaVisor: false }), true);
  // Con Ctrl la letra sigue activa aunque el foco no esté en el visor.
  assert.equal(debeIgnorarAtajo({ key: 'k', ctrlKey: true }, { target: input, dialogoAbierto: false, zonaVisor: false }), true);
});

test('los módulos de la barra no usan innerHTML, fetch, CDN ni colores fijos', () => {
  const archivos = ['app/web/static/js/toolbar.js', 'app/web/static/js/toolbar-actions.js',
    'app/web/static/js/toolbar-ui.js', 'app/web/static/css/forja-toolbar.css'];
  for (const rel of archivos) {
    const texto = leer(rel);
    assert.doesNotMatch(texto, /innerHTML/, `${rel} usa innerHTML`);
    // Único URL permitido: el espacio de nombres SVG (identificador, no una petición de red).
    const sinNamespaceSvg = texto.split('http://www.w3.org/2000/svg').join('');
    assert.doesNotMatch(sinNamespaceSvg, /https?:\/\//, `${rel} referencia una URL externa`);
    if (rel.endsWith('.js')) assert.doesNotMatch(texto, /\bfetch\(/, `${rel} llama a fetch directamente`);
  }
  assert.doesNotMatch(leer('app/web/static/css/forja-toolbar.css'), /#[0-9a-fA-F]{3,8}\b/, 'color fijo en CSS');
});
