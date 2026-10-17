// Prueba la barra real (toolbar-ui.js) sobre un DOM mínimo sin dependencias:
// el keydown global, la cinta, el cancelar y las alternancias.
import test from 'node:test';
import assert from 'node:assert/strict';
import { ACCIONES } from '../../app/web/static/js/toolbar-actions.js';
import { montarBarraHerramientas } from '../../app/web/static/js/toolbar-ui.js';

globalThis.window = { location: { origin: 'http://forja.test' }, innerWidth: 1280 };

// ---------------- DOM mínimo ----------------
const camel = (texto) => texto.replace(/-([a-z])/g, (_, letra) => letra.toUpperCase());

function coincideCompuesto(nodo, compuesto) {
  const negados = [];
  const sinNegados = compuesto.replace(/:not\(([^)]*)\)/g, (_, s) => { negados.push(s); return ''; });
  const tokens = [...sinNegados.matchAll(/\[([\w-]+)(?:="([^"]*)")?\]|#([\w-]+)|\.([\w-]+)|([a-zA-Z][\w-]*)/g)];
  for (const [, attr, val, id, clase, tag] of tokens) {
    if (attr !== undefined) {
      const actual = attr === 'open' ? (nodo.open ? '' : null)
        : attr.startsWith('data-') ? nodo.dataset[camel(attr.slice(5))] ?? null
          : nodo.attributes[attr] ?? null;
      if (actual === null) return false;
      if (val !== undefined && String(actual) !== val) return false;
    } else if (id !== undefined) {
      if (nodo.id !== id) return false;
    } else if (clase !== undefined) {
      if (!nodo.classes.has(clase)) return false;
    } else if (tag !== undefined) {
      if (nodo.tagName !== tag.toUpperCase()) return false;
    }
  }
  return negados.every((s) => !coincideCompuesto(nodo, s));
}

function coincideSelector(nodo, selector) {
  const partes = selector.trim().split(/\s+/);
  if (!coincideCompuesto(nodo, partes.at(-1))) return false;
  let indice = partes.length - 2;
  for (let ancestro = nodo.parent; ancestro && indice >= 0; ancestro = ancestro.parent) {
    if (coincideCompuesto(ancestro, partes[indice])) indice -= 1;
  }
  return indice < 0;
}

const coincide = (nodo, selector) => selector.split(',').some((s) => coincideSelector(nodo, s));

function todos(raiz, selector) {
  const hallados = [];
  const visitar = (nodo) => {
    for (const hijo of nodo.children) {
      if (coincide(hijo, selector)) hallados.push(hijo);
      visitar(hijo);
    }
  };
  visitar(raiz);
  return hallados;
}

class Nodo {
  constructor(tag, doc) {
    this.tagName = String(tag).toUpperCase();
    this.doc = doc;
    this.parent = null;
    this.children = [];
    this.listeners = {};
    this.attributes = {};
    this.dataset = {};
    this.style = {};
    this.classes = new Set();
    this.textContent = '';
    this.hidden = false;
    this.open = false;
    this.isContentEditable = false;
    this.value = '';
    this.id = '';
    this.defaultPrevented = false;
    const nodo = this;
    this.classList = {
      add: (...c) => c.forEach((x) => nodo.classes.add(x)),
      remove: (...c) => c.forEach((x) => nodo.classes.delete(x)),
      contains: (c) => nodo.classes.has(c),
      toggle(c, fuerza) {
        const on = fuerza ?? !nodo.classes.has(c);
        if (on) nodo.classes.add(c); else nodo.classes.delete(c);
        return on;
      },
    };
  }
  set className(valor) { this.classes = new Set(String(valor).split(/\s+/).filter(Boolean)); }
  get className() { return [...this.classes].join(' '); }
  setAttribute(nombre, valor) {
    this.attributes[nombre] = String(valor);
    if (nombre === 'id') this.id = String(valor);
  }
  getAttribute(nombre) { return this.attributes[nombre] ?? null; }
  append(...nodos) { for (const n of nodos) this.appendChild(n); }
  appendChild(n) {
    n.parent?.removeChild(n);
    n.parent = this;
    this.children.push(n);
    return n;
  }
  removeChild(n) {
    this.children = this.children.filter((c) => c !== n);
    n.parent = null;
  }
  replaceChildren(...nodos) {
    for (const c of this.children) c.parent = null;
    this.children = [];
    this.append(...nodos);
  }
  remove() { this.parent?.removeChild(this); }
  addEventListener(tipo, fn) { (this.listeners[tipo] ??= []).push(fn); }
  removeEventListener(tipo, fn) { this.listeners[tipo] = (this.listeners[tipo] ?? []).filter((f) => f !== fn); }
  // Propaga como el navegador: primero el nodo, luego sus ancestros.
  dispatch(tipo, extra = {}) {
    const evento = { type: tipo, target: this, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...extra };
    for (let nodo = this; nodo; nodo = nodo.parent) {
      for (const fn of [...(nodo.listeners[tipo] ?? [])]) fn(evento);
    }
    return evento;
  }
  click() { this.dispatch('click'); }
  focus() { this.doc.activeElement = this; }
  getBoundingClientRect() { return { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 }; }
  closest(selector) {
    for (let nodo = this; nodo; nodo = nodo.parent) if (coincide(nodo, selector)) return nodo;
    return null;
  }
  querySelector(selector) { return todos(this, selector)[0] ?? null; }
  querySelectorAll(selector) { return todos(this, selector); }
  showModal() { this.open = true; }
  close() {
    if (!this.open) return;
    this.open = false;
    this.dispatch('close');
  }
}

function crearDocumento() {
  const doc = new Nodo('#document', null);
  doc.doc = doc;
  doc.body = new Nodo('body', doc);
  doc.children.push(doc.body);
  doc.body.parent = doc;
  doc.activeElement = doc.body;
  doc.createElement = (tag) => new Nodo(tag, doc);
  doc.createElementNS = (_ns, tag) => new Nodo(tag, doc);
  doc.getElementById = (id) => todos(doc, `#${id}`)[0] ?? null;
  return doc;
}

// ---------------- Escena ----------------
function visorFalso(lienzo) {
  return {
    renderer: { domElement: lienzo },
    selectedPiece: null,
    ortogonal: false,
    wireframe: false,
    aristas: false,
    rayosX: false,
    corteActivo: false,
    grid: { visible: true },
    ejes: { visible: true },
    selectPiece(nombre) { this.selectedPiece = nombre ?? null; },
    setOrtogonal(on) { this.ortogonal = on; },
    setWireframe(on) { this.wireframe = on; },
    setGridVisible(on) { this.grid.visible = on; },
    setEjes(on) { this.ejes.visible = on; },
    setRayosX(on) { this.rayosX = on; },
    setAristas(on) { this.aristas = on; },
    setVistaEstandar() {},
    ajustarVista() {},
    direccionHaciaCamara() { return [0, 0, 1]; },
    mirarDeFrente() {},
    restaurarVista() {},
    centroEscena() { return [0, 0, 0]; },
    raycastearMalla() { return null; },
    capturar() { return 'data:image/png;base64,'; },
  };
}

function crearEntrada(doc, id, historial) {
  const lienzo = doc.createElement('canvas');
  const visor = visorFalso(lienzo);
  const botonNota = doc.createElement('button');
  const botonPizarra = doc.createElement('button');
  const contenedor = doc.createElement('div');
  const cuadricula = doc.createElement('button');
  cuadricula.setAttribute('aria-label', 'Cuadrícula');
  cuadricula.setAttribute('aria-pressed', 'true');
  const malla = doc.createElement('button');
  malla.setAttribute('aria-label', 'Malla');
  malla.setAttribute('aria-pressed', 'false');
  // Mismo comportamiento que tabs.js para los dos botones de la barra del visor.
  cuadricula.addEventListener('click', () => {
    const on = cuadricula.getAttribute('aria-pressed') !== 'true';
    cuadricula.setAttribute('aria-pressed', String(on));
    visor.setGridVisible(on);
  });
  malla.addEventListener('click', () => {
    const on = malla.getAttribute('aria-pressed') !== 'true';
    malla.setAttribute('aria-pressed', String(on));
    visor.setWireframe(on);
  });
  contenedor.append(cuadricula, malla);
  const notas = {
    modo: null,
    cancelados: 0,
    cancelarModos() { this.cancelados += 1; this.modo = null; },
  };
  return {
    lienzo,
    entrada: {
      ficha: { id, nombre: `Doc ${id}`, formato: 'step', volumen: 10, solidos: 1 },
      viewer: visor,
      notas,
      acciones: { nota: botonNota, pizarra: botonPizarra, cupon: doc.createElement('button') },
      animation: { play() {} },
      contenedor,
      historial,
    },
    visor,
    botonNota,
    botonPizarra,
    cuadricula,
    malla,
  };
}

const tick = () => new Promise((resolver) => setTimeout(resolver, 0));
const porId = (id) => ACCIONES.find((a) => a.id === id);

function montarEscena({ historial } = {}) {
  const doc = crearDocumento();
  const cinta = doc.createElement('div');
  cinta.id = 'fj-cinta';
  const feedback = doc.createElement('div');
  feedback.id = 'fj-feedback';
  doc.body.append(cinta, feedback);
  const primera = crearEntrada(doc, 'd1', historial);
  const tabs = { tabs: new Map([['d1', primera.entrada]]), activeId: 'd1', onEstadoCambio: null, abrirPanel() {} };
  doc.body.append(primera.entrada.contenedor);
  const barra = montarBarraHerramientas({ tabs, documento: doc });
  return { doc, tabs, barra, feedback, e: primera };
}

const textoFeedback = (doc) => doc.getElementById('fj-feedback').textContent;
const boton = (doc, id) => doc.querySelector(`[data-accion="${id}"]`);

// ---------------- Pruebas ----------------

test('Ctrl+K no abre la paleta al escribir en campos, zonas editables ni con un diálogo abierto', () => {
  const { doc } = montarEscena();
  const paleta = () => doc.querySelector('#fj-paleta');
  for (const [nombre, propiedades] of [
    ['input', { tagName: 'INPUT' }],
    ['textarea', { tagName: 'TEXTAREA' }],
    ['contenteditable', { tagName: 'DIV', isContentEditable: true }],
  ]) {
    const campo = doc.createElement('div');
    Object.assign(campo, propiedades);
    doc.body.append(campo);
    doc.activeElement = campo;
    const evento = doc.dispatch('keydown', { key: 'k', ctrlKey: true, target: campo });
    assert.equal(paleta().open, false, `abrió la paleta en ${nombre}`);
    assert.equal(evento.defaultPrevented, false, `bloqueó el atajo nativo en ${nombre}`);
    doc.activeElement = doc.body;
  }
  const dialogo = doc.createElement('dialog');
  dialogo.open = true;
  doc.body.append(dialogo);
  doc.dispatch('keydown', { key: 'k', ctrlKey: true, target: doc.body });
  assert.equal(paleta().open, false, 'abrió la paleta con un diálogo abierto');
  dialogo.open = false;
  doc.dispatch('keydown', { key: 'k', ctrlKey: true, target: doc.body });
  assert.equal(paleta().open, true, 'en el cuerpo de la página sí debe abrir');
});

test('Seleccionar cancela la herramienta: notas, pizarra, selección y picking de cara', () => {
  const { doc, barra, e } = montarEscena();
  e.entrada.notas.modo = 'pizarra-dibujando';
  e.botonNota.classList.add('is-active');
  e.botonPizarra.classList.add('is-active');
  e.visor.selectedPiece = 'eje';
  barra.ejecutar(porId('vista-mirar-cara'));
  assert.equal(e.lienzo.listeners.pointerup?.length, 1, 'el picking no quedó armado');
  barra.ejecutar(porId('inspec-seleccionar'));
  assert.equal(e.entrada.notas.cancelados, 1, 'no cancela el modo de notas/pizarra');
  assert.equal(e.entrada.notas.modo, null);
  assert.equal(e.botonNota.classList.contains('is-active'), false, 'indicador de nota sin limpiar');
  assert.equal(e.botonPizarra.classList.contains('is-active'), false, 'indicador de pizarra sin limpiar');
  assert.equal(e.visor.selectedPiece, null);
  assert.equal(e.lienzo.listeners.pointerup?.length ?? 0, 0, 'el picking sigue armado');
});

// Medidas de la cinta sin layout: cada grupo mide `anchoGrupo` y la fila `medida.fila`.
function medirCinta(anchoGrupo, medida) {
  const proto = crearDocumento().createElement('div').constructor.prototype;
  Object.defineProperty(proto, 'offsetWidth', { configurable: true, get() { return this.classes.has('fj-cinta__grupo') ? anchoGrupo : 0; } });
  Object.defineProperty(proto, 'clientWidth', { configurable: true, get() { return this.classes.has('fj-cinta__grupos') ? medida.fila : 0; } });
  return () => { delete proto.offsetWidth; delete proto.clientWidth; };
}
const gruposDe = (pestana) => [...new Set(ACCIONES.filter((a) => a.pestana === pestana).map((a) => a.grupo))];

test('cinta de una fila: los grupos que no caben van al menú «más», accesible por teclado', () => {
  const medida = { fila: 700 };
  const restaurar = medirCinta(200, medida);
  try {
    const { doc, barra } = montarEscena();
    const grupos = gruposDe('vista');
    const bloques = doc.querySelectorAll('.fj-cinta__grupo');
    const visibles = bloques.filter((b) => !b.hidden).length;
    assert.equal(visibles, Math.floor((700 - 64) / 200));
    assert.ok(grupos.length > visibles, 'el caso de prueba necesita grupos ocultos');
    const mas = doc.querySelector('.fj-cinta__mas');
    assert.equal(mas.hidden, false);
    assert.equal(mas.getAttribute('aria-haspopup'), 'menu');
    assert.equal(mas.getAttribute('aria-expanded'), 'false');
    const menu = doc.querySelector('.fj-cinta__menu');
    assert.equal(menu.hidden, true, 'el menú debe nacer cerrado');
    // Enter/clic abre; las flechas bajan al primer elemento.
    mas.click();
    assert.equal(mas.getAttribute('aria-expanded'), 'true');
    assert.equal(menu.hidden, false);
    const items = doc.querySelectorAll('[role="menuitem"]');
    const esperadas = ACCIONES.filter((a) => a.pestana === 'vista' && grupos.indexOf(a.grupo) >= visibles);
    assert.equal(items.length, esperadas.length);
    items[0].dispatch('keydown', { key: 'Escape' });
    assert.equal(mas.getAttribute('aria-expanded'), 'false');
    assert.equal(doc.activeElement, mas, 'Escape debe devolver el foco al botón «más»');
    // Con la fila más ancha ya no hay nada que ocultar.
    medida.fila = 2000;
    barra.actualizar();
    assert.equal(doc.querySelector('.fj-cinta__mas-wrap').hidden, true);
    assert.equal(doc.querySelectorAll('.fj-cinta__grupo').filter((b) => b.hidden).length, 0);
  } finally {
    restaurar();
  }
});

test('las alternancias reflejan aria-pressed e is-on y conservan el foco', () => {
  const { doc, e } = montarEscena();
  assert.equal(boton(doc, 'vista-ortografica').getAttribute('aria-pressed'), 'false');
  boton(doc, 'vista-ortografica').focus();
  boton(doc, 'vista-ortografica').click();
  assert.equal(e.visor.ortogonal, true);
  const nuevo = boton(doc, 'vista-ortografica');
  assert.equal(nuevo.getAttribute('aria-pressed'), 'true');
  assert.equal(nuevo.classes.has('is-on'), true);
  assert.equal(doc.activeElement, nuevo, 'se perdió el foco al redibujar la cinta');
});

test('las alternancias sincronizan también los botones de la barra del visor', () => {
  const { doc, e } = montarEscena();
  boton(doc, 'vista-rejilla').click();
  assert.equal(e.visor.grid.visible, false);
  assert.equal(e.cuadricula.getAttribute('aria-pressed'), 'false', 'Cuadrícula del visor no se actualizó');
  e.malla.click();
  assert.equal(e.visor.wireframe, true);
  assert.equal(boton(doc, 'vista-alambre').getAttribute('aria-pressed'), 'true', 'Alambre de la cinta no se actualizó');
});

test('el picking de cara se desarma al cambiar de pestaña o cerrar el documento', () => {
  const { doc, tabs, barra, e } = montarEscena();
  barra.ejecutar(porId('vista-mirar-cara'));
  assert.equal(e.lienzo.listeners.pointerup?.length, 1);
  const otra = crearEntrada(doc, 'd2');
  tabs.tabs.set('d2', otra.entrada);
  tabs.activeId = 'd2';
  tabs.onEstadoCambio();
  assert.equal(e.lienzo.listeners.pointerup?.length ?? 0, 0, 'el listener sigue en el visor anterior');
  barra.ejecutar(porId('vista-mirar-cara'));
  tabs.tabs.delete('d2');
  tabs.activeId = 'd1';
  tabs.onEstadoCambio();
  assert.equal(otra.lienzo.listeners.pointerup?.length ?? 0, 0, 'el listener sobrevivió al cierre');
});

// Deshacer y rehacer están próximamente (24a2): ningún atajo, paleta ni clic restaura nada.
test('deshacer y rehacer están próximamente: ni los atajos ni la paleta restauran nada', async () => {
  const restauraciones = [];
  const historial = { restaurar: async (id) => { restauraciones.push(id); } };
  const { doc, barra, feedback } = montarEscena({ historial });
  for (const evento of [{ key: 'z', ctrlKey: true }, { key: 'Z', ctrlKey: true, shiftKey: true }, { key: 'y', ctrlKey: true }]) {
    const despachado = doc.dispatch('keydown', { ...evento, target: doc.body });
    assert.equal(despachado.defaultPrevented, false, `${evento.key}: el atajo no debe capturarse`);
  }
  barra.ejecutar(porId('hist-deshacer'));
  assert.match(textoFeedback(doc), /Próximamente · 24a2/);
  barra.ejecutar(porId('hist-rehacer'));
  assert.match(textoFeedback(doc), /Próximamente · 24a2/);
  await tick();
  assert.deepEqual(restauraciones, []);
  assert.equal(feedback.classes.has('is-error'), false, 'próximamente no es un error');
});
