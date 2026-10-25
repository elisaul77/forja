// F22 (ADR-0021): the viewer's "Plano" anchors. Pure URL helper plus the
// anchors `construirAnclas` appends; hostile names only ever reach hrefs as slugs.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildDescargaPlanoUrl, construirAnclas, buildDescargaUrl } from '../../app/web/static/js/orca.js';

const ID = 'doc_01-abc';
const origin = 'http://localhost:8710';

function fakeDoc() {
  return { createElement: (tag) => {
    const el = { tag, attrs: {}, listeners: {}, className: '', textContent: '' };
    el.setAttribute = (k, v) => { el.attrs[k] = v; };
    el.addEventListener = (ev, fn) => { el.listeners[ev] = fn; };
    return el;
  } };
}

test('buildDescargaPlanoUrl only for the three plano formats', () => {
  assert.equal(buildDescargaPlanoUrl(origin, ID, 'mesa', 'pdf'), `${origin}/documentos/${ID}/descarga/mesa.pdf`);
  assert.equal(buildDescargaPlanoUrl(origin, ID, 'mesa', 'svg'), `${origin}/documentos/${ID}/descarga/mesa.svg`);
  assert.equal(buildDescargaPlanoUrl(origin, ID, 'mesa', 'dxf'), `${origin}/documentos/${ID}/descarga/mesa.dxf`);
  for (const formato of ['stl', '3mf', 'png', 'PDF', '', undefined]) {
    assert.equal(buildDescargaPlanoUrl(origin, ID, 'mesa', formato), null, String(formato));
  }
});

test('plano URL is null for an invalid id or origin (same guard as the other downloads)', () => {
  assert.equal(buildDescargaPlanoUrl(origin, 'a b', 'mesa', 'pdf'), null);
  assert.equal(buildDescargaPlanoUrl('javascript:alert(1)', ID, 'mesa', 'pdf'), null);
  assert.equal(buildDescargaUrl(origin, ID, 'mesa', 'pdf'), `${origin}/documentos/${ID}/descarga/mesa.pdf`);
});

test('construirAnclas appends Plano, Plano SVG and Plano DXF after the three existing anchors', () => {
  const anclas = construirAnclas(fakeDoc(), origin, { id: ID, nombre: 'mesa' });
  assert.deepEqual(
    anclas.slice(3).map((a) => a.textContent),
    ['Plano', 'Plano SVG', 'Plano DXF'],
  );
  assert.equal(anclas[3].attrs.href, `${origin}/documentos/${ID}/descarga/mesa.pdf`);
  assert.equal(anclas[4].attrs.href, `${origin}/documentos/${ID}/descarga/mesa.svg`);
  assert.equal(anclas[5].attrs.href, `${origin}/documentos/${ID}/descarga/mesa.dxf`);
  assert.equal(anclas[3].className, 'fj-btn');
  assert.match(anclas[3].attrs.title, /PDF/);
});

test('hostile document name: href carries only the slug, text stays literal', () => {
  const hostil = '<img src=x onerror=alert(1)>';
  const anclas = construirAnclas(fakeDoc(), origin, { id: ID, nombre: hostil });
  for (const a of anclas.slice(3)) {
    assert.doesNotMatch(a.attrs.href, /[<>"' ]/);
    assert.doesNotMatch(a.textContent, /<img/);
  }
});

test('no plano anchors when the ficha is missing (no anchors at all)', () => {
  assert.deepEqual(construirAnclas(fakeDoc(), origin, null), []);
});
