import test from 'node:test';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import { listarEjemplos, abrirEjemplo } from '../../app/web/static/js/ejemplos.js';

function memoriaSesion(token) {
  const datos = new Map(token ? [['fj_token', token]] : []);
  return {
    getItem: clave => datos.get(clave) ?? null,
    setItem: (clave, valor) => datos.set(clave, valor),
    removeItem: clave => datos.delete(clave),
    has: clave => datos.has(clave),
  };
}

test('listar ejemplos pide GET /ejemplos sin token', async () => {
  const llamadas = [];
  globalThis.fetch = async (url, opciones) => {
    llamadas.push({ url, opciones });
    return { ok: true, json: async () => [{ id: 'caja_tapa_encaje' }] };
  };
  assert.deepEqual(await listarEjemplos(), [{ id: 'caja_tapa_encaje' }]);
  assert.equal(llamadas[0].url, '/ejemplos');
  assert.equal(llamadas[0].opciones, undefined);
});

test('abrir sin token cancela sin enviar ninguna petición', async () => {
  let enviadas = 0;
  globalThis.sessionStorage = memoriaSesion(null);
  globalThis.window = { prompt: () => null };
  globalThis.fetch = async () => { enviadas++; return { ok: true }; };
  await assert.rejects(abrirEjemplo('caja_tapa_encaje'), /cancelada/);
  assert.equal(enviadas, 0);
});

test('abrir envía POST con el token y devuelve la ficha creada', async () => {
  const llamadas = [];
  globalThis.sessionStorage = memoriaSesion('token-de-prueba');
  globalThis.fetch = async (url, opciones) => {
    llamadas.push({ url, opciones });
    return { ok: true, json: async () => ({ id: 'nuevo', nombre: 'Ejemplo — Caja.step' }) };
  };
  const ficha = await abrirEjemplo('caja_tapa_encaje');
  assert.equal(ficha.id, 'nuevo');
  assert.equal(llamadas[0].url, '/ejemplos/caja_tapa_encaje/abrir');
  assert.equal(llamadas[0].opciones.method, 'POST');
  assert.equal(llamadas[0].opciones.headers['X-Forja-Token'], 'token-de-prueba');
});

test('401 borra el token guardado; un id desconocido se reporta con su código', async () => {
  globalThis.sessionStorage = memoriaSesion('token-viejo');
  globalThis.fetch = async () => ({ ok: false, status: 401, statusText: 'Unauthorized', json: async () => ({ detail: 'token requerido' }) });
  await assert.rejects(abrirEjemplo('caja_tapa_encaje'), /401 token requerido/);
  assert.equal(globalThis.sessionStorage.has('fj_token'), false);

  globalThis.sessionStorage = memoriaSesion('token-bueno');
  globalThis.fetch = async () => ({ ok: false, status: 404, statusText: 'Not Found', json: async () => ({ detail: 'ejemplo no encontrado' }) });
  await assert.rejects(abrirEjemplo('no-existe'), /404 ejemplo no encontrado/);
  assert.equal(globalThis.sessionStorage.has('fj_token'), true);
});

// Bienvenida con la galería: el contenedor es desplazable y arranca desde
// arriba. Centrar el contenido con flex (align-items/justify-content:center)
// recorta el título y los botones por encima del viewport, que no se pueden
// alcanzar con el scroll; por eso el bloque se centra con margin:auto.
const raizWeb = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const cssBase = fs.readFileSync(path.join(raizWeb, 'app/web/static/css/forja-base.css'), 'utf8');

function declaracionesDe(selector) {
  const escapado = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const bloques = [...cssBase.matchAll(new RegExp(`(?:^|\\n)${escapado}\\s*\\{([^}]*)\\}`, 'g'))];
  return bloques.map(m => m[1]).join(';');
}

test('la bienvenida no se centra con flex (recorta arriba y no se puede desplazar)', () => {
  const vacio = declaracionesDe('.fj-main__vacio');
  assert.doesNotMatch(vacio, /align-items\s*:\s*center/);
  assert.doesNotMatch(vacio, /justify-content\s*:\s*center/);
  assert.match(declaracionesDe('.fj-welcome'), /margin\s*:\s*auto/);
});

test('las tarjetas de bienvenida se ordenan en 3 columnas', () => {
  assert.match(declaracionesDe('.fj-welcome .fj-ejemplos__lista'), /repeat\(3,/);
});
