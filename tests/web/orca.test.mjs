import test from 'node:test';
import assert from 'node:assert/strict';
import { slugDescarga, buildOrcaLink, buildDescargaUrl, construirAnclas } from '../../app/web/static/js/orca.js';

// Expected slugs were obtained by calling the real server function
// `documents._slug_descarga` inside the running container (Python 3.12) on
// each name; JS must reproduce them exactly. JSON escapes keep CR/LF/emoji exact.
const SLUG_TABLE = [
  ["mesa", "mesa"],
  ["Mesa Caf\u00e9", "Mesa_Cafe"],
  ["\u00d1and\u00fa \u2014 ni\u00f1o", "Nandu_nino"],
  ["pieza \ud83d\udd27 rara", "pieza_rara"],
  ["", "modelo"],
  ["   ", "modelo"],
  ["a\r\nb", "a_b"],
  ["say \"hi\" 'x'", "say_hi_x"],
  ["AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"],
  [".stl", "stl"],
  [".3mf", "3mf"],
  ["MiPieza.STL", "MiPieza"],
  ["caja.v2.stl", "caja_v2"],
  ["dir/sub/pieza.stl", "pieza"],
  ["a/", "a"],
  ["x.", "x"],
  ["...", "modelo"],
  ["a b  c", "a_b_c"],
  ["__x__", "x"],
  ["\u65e5\u672c\u8a9e", "modelo"],
  ["\u65e5\u672c\u8a9e.stl", "modelo"],
  ["\u00dcn\u00ef-c\u00f6d\u00e9_1", "Uni-code_1"],
  ["a?b#c", "a_b_c"],
  ["  .hidden", "modelo"],
  ["tab\tname", "tab_name"],
  ["e\u0301clair", "eclair"],
  ["\ufb01ne \u00bd", "fine_12"],
  ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\u00e9b", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaae"],
  ["-guion-", "-guion-"],
  ["archivo.tar.gz", "archivo_tar"],
  ["./x", "x"],
  ["a//b.stl", "b"],
  ["a\\b.stl", "a_b"],
];

test('slug parity with server _slug_descarga', () => {
  for (const [name, expected] of SLUG_TABLE) {
    assert.equal(slugDescarga(name), expected, JSON.stringify(name));
  }
});

test('slug fallbacks and charset', () => {
  assert.equal(slugDescarga(''), 'modelo');
  assert.equal(slugDescarga('🔧'), 'modelo');
  assert.equal(slugDescarga('Café'), 'Cafe');
  for (const [name] of SLUG_TABLE) assert.match(slugDescarga(name), /^[A-Za-z0-9_-]{1,64}$/);
});

const ID = '0123456789abcdef0123456789abcdef';
for (const origin of ['http://localhost:8710', 'http://192.168.1.50:8710', 'https://forja.example.com']) {
  for (const ext of ['3mf', 'stl']) {
    test(`orca link round-trips once: ${origin} ${ext}`, () => {
      const link = buildOrcaLink(origin, ID, 'Mesa Café', ext);
      const prefix = 'orcaslicer://open?file=';
      assert.ok(link.startsWith(prefix));
      const value = link.slice(prefix.length);
      const inner = `${origin}/documentos/${ID}/descarga/Mesa_Cafe.${ext}`;
      assert.equal(decodeURIComponent(value), inner);
      assert.equal(buildDescargaUrl(origin, ID, 'Mesa Café', ext), inner);
      assert.ok(!link.includes('%25'));
      assert.ok(!inner.includes('?') && !inner.includes('#'));
      assert.ok(decodeURIComponent(value).endsWith('.' + ext));
      assert.ok(!/[\s']/.test(link));
      assert.equal(link.split('?').length, 2); // only the scheme's own "?"
    });
  }
}

test('hostile names never leak into the link', () => {
  const link = buildOrcaLink('http://localhost:8710', ID, "a'b c\r\n?x#y.stl", '3mf');
  assert.ok(!/[\s']/.test(link));
  assert.ok(decodeURIComponent(link.slice(23)).endsWith('.3mf'));
});

test('invalid inputs return null', () => {
  const o = 'http://localhost:8710';
  const bad = [
    [o, 'bad id', 'x', '3mf'], [o, '', 'x', '3mf'], [o, 'a/b', 'x', '3mf'], [o, '../x', 'x', '3mf'],
    [o, 'a?b', 'x', '3mf'], [o, 5, 'x', '3mf'], [o, null, 'x', '3mf'],
    [o, ID, 'x', 'step'], [o, ID, 'x', '3MF'], [o, ID, 'x', ''], [o, ID, 'x', undefined],
    ['http://h/path', ID, 'x', '3mf'], ['http://h:1/', ID, 'x', '3mf'], ['http://h?q=1', ID, 'x', '3mf'],
    ['http://h#f', ID, 'x', '3mf'], ['http://u:p@h', ID, 'x', '3mf'], ['http://u@h:80', ID, 'x', '3mf'],
    ['null', ID, 'x', '3mf'], ['', ID, 'x', '3mf'], [undefined, ID, 'x', '3mf'], [null, ID, 'x', '3mf'],
    [42, ID, 'x', '3mf'], ['ftp://h', ID, 'x', '3mf'], ['file://', ID, 'x', '3mf'], ['localhost:8710', ID, 'x', '3mf'],
    ['http://h x', ID, 'x', '3mf'],
  ];
  for (const args of bad) {
    assert.equal(buildOrcaLink(...args), null, JSON.stringify(args));
    assert.equal(buildDescargaUrl(...args), null, JSON.stringify(args));
  }
});

// Minimal DOM fake: construirAnclas only needs createElement/setAttribute/addEventListener.
function fakeDoc() {
  return { createElement: (tag) => {
    const el = { tag, attrs: {}, listeners: {}, className: '', textContent: '' };
    el.setAttribute = (k, v) => { el.attrs[k] = v; };
    el.addEventListener = (t, f) => { el.listeners[t] = f; };
    return el;
  } };
}

test('three anchors with the right hrefs, texts and a11y', () => {
  const origin = 'http://192.168.1.50:8710';
  const [abrir, d3, ds] = construirAnclas(fakeDoc(), origin, { id: ID, nombre: 'mesa' });
  assert.deepEqual([abrir, d3, ds].map((a) => a.textContent), ['Abrir en Orca', 'Descargar 3MF', 'Descargar STL']);
  assert.equal(abrir.attrs.href, buildOrcaLink(origin, ID, 'mesa', '3mf'));
  assert.equal(d3.attrs.href, `${origin}/documentos/${ID}/descarga/mesa.3mf`);
  assert.equal(ds.attrs.href, `${origin}/documentos/${ID}/descarga/mesa.stl`);
  for (const a of [abrir, d3, ds]) {
    assert.equal(a.tag, 'a'); assert.equal(a.className, 'fj-btn');
    assert.ok(a.attrs.title && a.attrs['aria-label']);
  }
});

test('click shows hint without preventDefault; works with no feedback callback', () => {
  const shown = [];
  const [abrir] = construirAnclas(fakeDoc(), 'http://localhost:8710', { id: ID, nombre: 'x' }, (t) => shown.push(t));
  let prevented = false;
  abrir.listeners.click({ preventDefault() { prevented = true; } });
  assert.equal(prevented, false);
  assert.equal(shown.length, 1);
  assert.match(shown[0], /OrcaSlicer/); assert.match(shown[0], /Descargar 3MF/);
  // Orca's modal "Objeto multipieza detectado" question (observed in the real launch).
  assert.match(shown[0], /multipieza/); assert.match(shown[0], /S\u00ed/); assert.match(shown[0], /No/);
  const [sinCb] = construirAnclas(fakeDoc(), 'http://localhost:8710', { id: ID, nombre: 'x' });
  assert.doesNotThrow(() => sinCb.listeners.click({}));
});

test('no anchors for invalid id / origin / missing ficha', () => {
  assert.deepEqual(construirAnclas(fakeDoc(), 'http://localhost:8710', { id: 'a b', nombre: 'x' }), []);
  assert.deepEqual(construirAnclas(fakeDoc(), 'null', { id: ID, nombre: 'x' }), []);
  assert.deepEqual(construirAnclas(fakeDoc(), 'http://localhost:8710', null), []);
});

test('selected piece: links target only that piece; none selected = whole document', () => {
  const origin = 'http://localhost:8710';
  let pieza = null;
  const anclas = construirAnclas(fakeDoc(), origin, { id: ID, nombre: 'mesa' }, null, () => pieza);
  const [abrir, d3, ds] = anclas;
  pieza = 'pata_1';
  anclas.refrescar();
  assert.equal(d3.attrs.href, `${origin}/documentos/${ID}/descarga/pata_1/mesa.3mf`);
  assert.equal(ds.attrs.href, `${origin}/documentos/${ID}/descarga/pata_1/mesa.stl`);
  assert.equal(abrir.attrs.href, buildOrcaLink(origin, ID, 'mesa', '3mf', 'pata_1'));
  assert.match(abrir.textContent, /pata_1/);
  pieza = null;
  anclas.refrescar();
  assert.equal(d3.attrs.href, `${origin}/documentos/${ID}/descarga/mesa.3mf`);
  assert.equal(abrir.textContent, 'Abrir en Orca');
});
