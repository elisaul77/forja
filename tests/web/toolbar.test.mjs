import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { PESTANAS, ACCIONES } from '../../app/web/static/js/toolbar-actions.js';
import {
  estadoAccion, tooltipLineas, filtrarPaleta, claveDesdeEvento, debeIgnorarAtajo,
  buscarPorAtajo, validarRegistro, ATAJOS_RESERVADOS,
  nuevaPilaRevisiones, sincronizarPilaRevisiones, tomarDeshacer, tomarRehacer, fijarCabeza, devolverPaso,
  pilaDesdeHistorial, repartirGrupos,
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

test('deshacer y rehacer están en el registro con sus atajos (Ctrl+Y como alterno)', () => {
  const deshacer = porId('hist-deshacer');
  assert.equal(deshacer.estado, 'listo');
  assert.equal(deshacer.atajo, 'Ctrl+Z');
  assert.equal(typeof deshacer.ejecutar, 'function');
  const rehacer = porId('hist-rehacer');
  assert.equal(rehacer.estado, 'listo');
  assert.equal(rehacer.atajo, 'Ctrl+Shift+Z');
  assert.equal(rehacer.atajoAlterno, 'Ctrl+Y');
  assert.equal(buscarPorAtajo(ACCIONES, 'Ctrl+Y')?.id, 'hist-rehacer');
  assert.deepEqual(validarRegistro(ACCIONES), []);
});

test('pila de revisiones: deshacer recorre hacia atrás y rehacer vuelve, sin borrar la historia', () => {
  const estado = nuevaPilaRevisiones();
  sincronizarPilaRevisiones(estado, ['r1', 'r2', 'r3']);
  assert.equal(tomarDeshacer(estado), 'r2');
  fijarCabeza(estado, 'n4');
  assert.equal(tomarDeshacer(estado), 'r1');
  fijarCabeza(estado, 'n5');
  assert.equal(tomarDeshacer(estado), null, 'no hay más pasos');
  assert.equal(tomarRehacer(estado), 'n4', 'rehacer debe volver a la revisión dejada');
  fijarCabeza(estado, 'n6');
  assert.equal(tomarRehacer(estado), 'r3');
  fijarCabeza(estado, 'n7');
  assert.equal(tomarRehacer(estado), null);
});

test('pila de revisiones: una edición nueva pasa la cabeza a la pila e invalida el rehacer', () => {
  const estado = nuevaPilaRevisiones();
  sincronizarPilaRevisiones(estado, ['r1', 'r2']);
  assert.equal(tomarDeshacer(estado), 'r1');
  fijarCabeza(estado, 'n3');
  sincronizarPilaRevisiones(estado, ['r1', 'r2', 'n3', 'e4']);
  assert.deepEqual(estado.rehacer, []);
  assert.equal(tomarRehacer(estado), null);
  assert.equal(tomarDeshacer(estado), 'n3', 'deshacer debe volver a la revisión anterior a la edición');
});

test('pila de revisiones: un fallo devuelve el paso a su pila', () => {
  const estado = nuevaPilaRevisiones();
  sincronizarPilaRevisiones(estado, ['r1', 'r2']);
  const destino = tomarDeshacer(estado);
  devolverPaso(estado, destino, 'deshacer');
  assert.deepEqual(estado.pila, ['r1']);
  assert.deepEqual(estado.rehacer, []);
  assert.equal(tomarDeshacer(sincronizarPilaRevisiones(nuevaPilaRevisiones(), [])), null, 'sin historial no hay paso');
});

test('la cinta es una sola fila (no envuelve) y los grupos ocultos y el «más» se ocultan con [hidden]', () => {
  const css = leer('app/web/static/css/forja-toolbar.css');
  const regla = css.match(/\.fj-cinta__grupos\s*\{[^}]*\}/)?.[0] ?? '';
  assert.match(regla, /flex-wrap:\s*nowrap/);
  assert.doesNotMatch(regla, /flex-wrap:\s*wrap/);
  assert.match(css, /\.fj-cinta__grupo\[hidden\]\s*\{[^}]*display:\s*none/);
  assert.match(css, /\.fj-cinta__mas-wrap\[hidden\]\s*\{[^}]*display:\s*none/);
});

test('repartirGrupos: los grupos que no caben pasan al «más» en orden, sin saltar huecos', () => {
  assert.deepEqual(repartirGrupos([200, 200, 200, 200], 700, 64), { visibles: 3, ocultos: [3] });
  // Todo cabe: no hay «más».
  assert.deepEqual(repartirGrupos([100, 100], 300, 64), { visibles: 2, ocultos: [] });
  // Justo cabe el total: tampoco hay «más».
  assert.deepEqual(repartirGrupos([100, 100], 200, 64), { visibles: 2, ocultos: [] });
  // El 50 cabría, pero no se salta al 300: el orden de la cinta manda.
  assert.deepEqual(repartirGrupos([100, 300, 50], 400, 64), { visibles: 1, ocultos: [1, 2] });
  // Ni el primero cabe junto al «más».
  assert.deepEqual(repartirGrupos([300], 200, 64), { visibles: 0, ocultos: [0] });
  // Sin medida (fila oculta o sin layout) todo se considera visible.
  assert.deepEqual(repartirGrupos([0, 0], 0, 64), { visibles: 2, ocultos: [] });
});

test('repartirGrupos rechaza medidas no finitas o negativas en vez de ocultar grupos en silencio', () => {
  assert.throws(() => repartirGrupos([Number.NaN], 100, 64), TypeError);
  assert.throws(() => repartirGrupos([-1], 100, 64), TypeError);
  assert.throws(() => repartirGrupos([10], Number.NaN, 64), TypeError);
  assert.throws(() => repartirGrupos([10], 100, -5), TypeError);
});

test('pila desde el historial: una restauración de documento rebobina; una de pieza cuenta como edición', () => {
  const h = (id, mensaje = '') => ({ id, mensaje });
  // r1 → r2 → r3, y luego «restaurar todo a r2» (creada como n4): deshacer debe ir a r1.
  const conRestauracion = [h('aaaaaaa001'), h('aaaaaaa002'), h('aaaaaaa003'),
    h('bbbbbbb004', 'restaurar todo el documento a aaaaaaa002')];
  assert.deepEqual(pilaDesdeHistorial(conRestauracion), ['aaaaaaa001']);
  // Restaurar una pieza no rebobina: es una edición más.
  const conPieza = [h('aaaaaaa001'), h('aaaaaaa002'), h('bbbbbbb003', 'restaurar pieza eje a aaaaaaa001')];
  assert.deepEqual(pilaDesdeHistorial(conPieza), ['aaaaaaa001', 'aaaaaaa002']);
  // Un sha que no está en el historial vacía la pila: no se deshace a un estado desconocido.
  const desconocido = [h('aaaaaaa001'), h('aaaaaaa002'), h('bbbbbbb003', 'restaurar todo el documento a ffffffffff')];
  assert.deepEqual(pilaDesdeHistorial(desconocido), []);
});

test('sincronizar desde historial con mensajes deja la pila correcta para deshacer', () => {
  const estado = nuevaPilaRevisiones();
  sincronizarPilaRevisiones(estado, [
    { id: 'aaaaaaa001', mensaje: '' }, { id: 'aaaaaaa002', mensaje: '' },
    { id: 'bbbbbbb003', mensaje: 'restaurar todo el documento a aaaaaaa002' },
  ]);
  assert.equal(estado.cabeza, 'bbbbbbb003');
  assert.equal(tomarDeshacer(estado), 'aaaaaaa001');
});

test('rehacer tiene su glifo en el sprite de la barra', () => {
  const sprite = leer('app/web/static/icons/forja-icons.svg');
  assert.equal(porId('hist-rehacer').icono, 'redo');
  assert.match(sprite, /<symbol id="i-redo"/);
});
