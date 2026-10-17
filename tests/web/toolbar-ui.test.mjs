// Prueba la barra real (toolbar-ui.js) sobre un DOM mínimo sin dependencias:
// el keydown global, la cinta, el cancelar, las alternancias y el deshacer.
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

function montarEscena({ historialApi, historial } = {}) {
  const doc = crearDocumento();
  const cinta = doc.createElement('div');
  cinta.id = 'fj-cinta';
  const feedback = doc.createElement('div');
  feedback.id = 'fj-feedback';
  doc.body.append(cinta, feedback);
  const primera = crearEntrada(doc, 'd1', historial);
  const tabs = { tabs: new Map([['d1', primera.entrada]]), activeId: 'd1', onEstadoCambio: null, abrirPanel() {} };
  doc.body.append(primera.entrada.contenedor);
  const barra = montarBarraHerramientas({ tabs, documento: doc, historialApi });
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

// Historial falso: `revisiones` en orden del servidor (la más reciente al final).
function historialFalso(servidor) {
  const api = {
    obtenerHistorial: async () => servidor.revisiones.map((id) => ({ id, fecha: '', mensaje: '' })),
  };
  const controlador = {
    restaurar: async (id) => {
      if (servidor.fallar) throw new Error('boom');
      servidor.restauraciones.push(id);
      servidor.revisiones.push(`n${servidor.revisiones.length}`);
    },
  };
  return { api, controlador };
}

function escenaHistorial(revisiones = ['r1', 'r2', 'r3']) {
  const servidor = { revisiones: [...revisiones], restauraciones: [], fallar: false };
  const { api, controlador } = historialFalso(servidor);
  const escena = montarEscena({ historialApi: api, historial: controlador });
  return { ...escena, servidor };
}

test('Ctrl+Z vuelve a la revisión anterior y Ctrl+Shift+Z rehace, con la historia en modo append', async () => {
  const { doc, servidor } = escenaHistorial();
  doc.dispatch('keydown', { key: 'z', ctrlKey: true, target: doc.body });
  await tick();
  assert.deepEqual(servidor.restauraciones, ['r2']);
  doc.dispatch('keydown', { key: 'z', ctrlKey: true, target: doc.body });
  await tick();
  assert.deepEqual(servidor.restauraciones, ['r2', 'r1']);
  doc.dispatch('keydown', { key: 'Z', ctrlKey: true, shiftKey: true, target: doc.body });
  await tick();
  assert.deepEqual(servidor.restauraciones, ['r2', 'r1', 'n3']);
  assert.equal(servidor.revisiones.length, 3 + 3, 'la historia no creció solo con restauraciones');
});

test('Ctrl+Y también rehace', async () => {
  const { doc, servidor } = escenaHistorial();
  doc.dispatch('keydown', { key: 'z', ctrlKey: true, target: doc.body });
  await tick();
  doc.dispatch('keydown', { key: 'y', ctrlKey: true, target: doc.body });
  await tick();
  assert.deepEqual(servidor.restauraciones, ['r2', 'r3']);
});

test('una edición nueva invalida el rehacer y el deshacer toma la revisión previa a la edición', async () => {
  const { doc, servidor } = escenaHistorial();
  doc.dispatch('keydown', { key: 'z', ctrlKey: true, target: doc.body });
  await tick();
  servidor.revisiones.push('e9');
  doc.dispatch('keydown', { key: 'Z', ctrlKey: true, shiftKey: true, target: doc.body });
  await tick();
  assert.deepEqual(servidor.restauraciones, ['r2'], 'rehizo tras una edición nueva');
  assert.match(textoFeedback(doc), /No hay pasos que rehacer/);
  doc.dispatch('keydown', { key: 'z', ctrlKey: true, target: doc.body });
  await tick();
  assert.deepEqual(servidor.restauraciones, ['r2', 'n3'], 'no volvió a la revisión previa a la edición');
});

test('sin pasos anteriores avisa y no restaura nada', async () => {
  const { doc, servidor } = escenaHistorial(['r1']);
  doc.dispatch('keydown', { key: 'z', ctrlKey: true, target: doc.body });
  await tick();
  assert.deepEqual(servidor.restauraciones, []);
  assert.match(textoFeedback(doc), /No hay pasos anteriores/);
});

test('si la restauración falla, el error se muestra y el paso vuelve a estar disponible', async () => {
  const { doc, servidor, feedback } = escenaHistorial();
  servidor.fallar = true;
  doc.dispatch('keydown', { key: 'z', ctrlKey: true, target: doc.body });
  await tick();
  assert.match(textoFeedback(doc), /boom/);
  assert.equal(feedback.classes.has('is-error'), true);
  servidor.fallar = false;
  doc.dispatch('keydown', { key: 'z', ctrlKey: true, target: doc.body });
  await tick();
  assert.deepEqual(servidor.restauraciones, ['r2']);
});

test('Ctrl+Z dentro de un campo deja el deshacer nativo del campo', async () => {
  const { doc, servidor } = escenaHistorial();
  const campo = doc.createElement('input');
  campo.tagName = 'INPUT';
  doc.body.append(campo);
  doc.activeElement = campo;
  doc.dispatch('keydown', { key: 'z', ctrlKey: true, target: campo });
  await tick();
  assert.deepEqual(servidor.restauraciones, []);
});

test('cambiar de documento o cerrarlo descarta su pila; al volver se reconstruye desde el historial', async () => {
  const servidor = {
    revisiones: [{ id: 'aaaaaaa001', mensaje: '' }, { id: 'aaaaaaa002', mensaje: '' }, { id: 'aaaaaaa003', mensaje: '' }],
    restauraciones: [],
  };
  const api = { obtenerHistorial: async () => servidor.revisiones.map((r) => ({ ...r })) };
  const controlador = {
    restaurar: async (id) => {
      servidor.restauraciones.push(id);
      servidor.revisiones.push({ id: `bbbbbbb00${servidor.revisiones.length}`, mensaje: `restaurar todo el documento a ${id.slice(0, 10)}` });
    },
  };
  const { doc, tabs, e } = montarEscena({ historialApi: api, historial: controlador });
  doc.dispatch('keydown', { key: 'z', ctrlKey: true, target: doc.body });
  await tick();
  assert.deepEqual(servidor.restauraciones, ['aaaaaaa002']);

  // Cambio a otro documento y vuelta: el rehacer de la sesión anterior ya no existe.
  const otra = crearEntrada(doc, 'd2');
  tabs.tabs.set('d2', otra.entrada);
  tabs.activeId = 'd2';
  tabs.onEstadoCambio();
  tabs.activeId = 'd1';
  tabs.onEstadoCambio();
  doc.dispatch('keydown', { key: 'Z', ctrlKey: true, shiftKey: true, target: doc.body });
  await tick();
  assert.deepEqual(servidor.restauraciones, ['aaaaaaa002'], 'rehizo tras cambiar de documento');
  assert.match(textoFeedback(doc), /No hay pasos que rehacer/);

  // Deshacer sigue funcionando: la pila se reconstruyó desde el historial (restauración incluida).
  doc.dispatch('keydown', { key: 'z', ctrlKey: true, target: doc.body });
  await tick();
  assert.deepEqual(servidor.restauraciones, ['aaaaaaa002', 'aaaaaaa001']);

  // Cerrar el documento también descarta la pila.
  tabs.tabs.delete('d1');
  tabs.activeId = 'd2';
  tabs.onEstadoCambio();
  tabs.tabs.set('d1', e.entrada);
  tabs.activeId = 'd1';
  tabs.onEstadoCambio();
  doc.dispatch('keydown', { key: 'Z', ctrlKey: true, shiftKey: true, target: doc.body });
  await tick();
  assert.deepEqual(servidor.restauraciones, ['aaaaaaa002', 'aaaaaaa001'], 'rehizo tras cerrar el documento');
});
