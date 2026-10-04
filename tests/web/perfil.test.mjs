import test from 'node:test';
import assert from 'node:assert/strict';
import { compensar, construirMediciones } from '../../app/web/static/js/perfil.js';

test('compensar invierte offset = a + b·d (igual que perfil_fdm.py)', () => {
  assert.equal(compensar(3, { a: -0.15, b: 0 }), 3.15);
  const coef = { a: -0.1, b: -0.02 };
  const m = compensar(5, coef);
  assert.ok(Math.abs(m + coef.a + coef.b * m - 5) < 1e-3);
});

test('construirMediciones ignora vacíos, acepta coma y valida', () => {
  const m = construirMediciones(
    { agujeros: { 3: '2,85', 5: '', 8: '7.8' }, ranuras: { 2: ' ' } },
    { holgura_deslizante: '0.3', holgura_presion: '' },
  );
  assert.deepEqual(m, {
    agujeros: [{ nominal: 3, medido: 2.85 }, { nominal: 8, medido: 7.8 }],
    holgura_deslizante: 0.3,
  });
  assert.throws(() => construirMediciones({ ejes: { 5: 'abc' } }), /no válida/);
});
