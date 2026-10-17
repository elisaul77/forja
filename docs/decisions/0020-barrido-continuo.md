# ADR 0020 — Continuous joint sweep: what "continua" proves and does not

Date: 2026-10-16. Status: implemented (phase 18, branch `phase-18-barrido-continuo`). Extends ADR 0010 (assembly persistence) and the sampled checks in `app/checks/pose_clearance.py`; it does not replace them.

## Decision

`ensamble(id, barrido="todas"|[ids])` and `POST /documentos/{id}/ensamble/barrido` sweep one joint at a time over its `limites` (or `desde`/`hasta`) by **conservative advancement** and report the first contact in the joint's own unit, the pair of solids, the largest penetration volume seen, the smallest clearance seen and a guarantee label `garantia`. The sweep is read-only: no STEP, history entry, snapshot, registry change or revision change. It holds the document lock while the rest shapes are loaded, and replies with `Cache-Control: no-store`.

The engine lives in `app/checks/barrido.py` and is pure: it takes an `evaluar(valor)` callable and the names of the moving group.

### Algorithm

- The moving group is `pose_clearance.moving_names`; the pairs are moving × stationary.
- The step is `(g - 0.05 mm) / tasa`, where `g` is the smallest gap among the advancing pairs and `tasa` bounds the speed of any point of the group. The step never passes a point where the gap could reach the touch distance, so no contact is skipped.
- `tasa` for a hinge is `max` over bounding-box corners of the distance to the axis, times π/180 (mm per degree), measured on the group before the joint's own rotation. For a slide it is `|eje|` (mm per mm). Both are upper bounds, so the advancement is safe.
- Pairs whose bbox lower bound is more than 10 mm away advance on that bound, which is never larger than the true gap.
- Contact is declared at `gap <= 0.05 + tasa × tolerance`. Without this band the step `(gap - 0.05)/tasa` shrinks geometrically and never reaches the touch distance (a Zeno stall; it was observed hitting the 400-evaluation cap in the hinge test).
- At the first contact the step is bisected to the joint tolerance (0.05 deg or 0.01 mm). The reported value is the first contact (`primer_choque.valor`); the value before it is `ultimo_seguro`.
- Pairs that already touch at the start are not advanced. They are **sampled by volume** at each step, compared with their baseline, and listed in `pares_en_contacto_inicial`.

## What `garantia: "continua"` proves

For every pair that starts apart, no contact occurs on the swept range before the reported value, up to the joint tolerance and the bound `tasa`. The sweep covers the whole range when `primer_choque` is null.

## What it does not prove

- Pairs that start in contact (press fits, pins in bores) are sampled, not proven. They do not affect the label, but they are listed so nothing is hidden.
- Several joints moving together. The sweep is single-joint only; coordinated motion stays with `comprobar-recorrido` (a documented limit).
- The `holgura_minima_mm` is the smallest gap **among the visited values**. It is an upper bound on the true minimum; the sweep does not refine a minimum that was stepped over.
- Geometry faults that OCC reports as errors. Any error gives `garantia: "muestreada"`, `completo: false` and `ok: false`; it is never a silent pass.
- The evaluation cap (400 per joint). Hitting it gives `muestreada`, `completo: false`, `ok: false`.

## Choque versus contacto

A contact found by the advancing sweep is reported as `primer_choque` with `tipo: "contacto"` (zero or tiny volume) or `"choque"` (volume above `TOLERANCIA_MM3_POR_DEFECTO`, 0.5 mm³). Both make `ok` false, because a moving pair that reaches the touch distance blocks the audit. Touching in a single pose (`comprobar-posicion`, `comprobar-escena`) keeps the older rule: contact is not choque.

## Rejected

- **FCL or another collision library.** It would add a runtime dependency and a second geometry kernel to a project that keeps OpenCascade as its only kernel (ADR-0001). The exact OCC distance and boolean already exist in `checks/distancia.py`.
- **Denser sampling.** Sampling cannot prove absence between samples; the motivating 0.2 mm plate is missed by any grid that does not hit its window.
