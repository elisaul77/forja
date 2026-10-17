# ADR 0020 — Continuous joint sweep: what "continua" proves and does not

Date: 2026-10-16. Revised: 2026-10-17 (phase 18, review round 1). Status: implemented (phase 18, branch `phase-18-barrido-continuo`). Extends ADR 0010 (assembly persistence) and the sampled checks in `app/checks/pose_clearance.py`; it does not replace them.

## Decision

`ensamble(id, barrido="todas"|[ids])` and `POST /documentos/{id}/ensamble/barrido` sweep one joint at a time over its `limites` (or `desde`/`hasta`) by **conservative advancement** and report the first contact in the joint's own unit, the pair of solids, the largest penetration volume seen, the smallest clearance seen and a guarantee label `garantia`. The sweep is read-only: no STEP, history entry, snapshot, registry change or revision change. The route loads the rest shapes under the document lock and then sweeps **outside** that lock: rest shapes are immutable once read, and every pose is a fresh copy (`transform_rigid`), so the sweep never writes to them. It replies with `Cache-Control: no-store`.

The engine lives in `app/checks/barrido.py` and is pure: it takes an `evaluar(valor)` callable and the names of the moving group.

### Algorithm

- The moving group is `pose_clearance.moving_names`; the pairs are moving × stationary.
- The step is at most `max((g - 0.05 mm) / tasa, tolerance)`, where `g` is the smallest gap among the advancing pairs and `tasa` bounds the speed of any point of the group. The step never passes the touch distance by more than one tolerance step, so no contact is skipped.
- `tasa` for a hinge is `max` over bounding-box corners of the distance to the axis, times π/180 (mm per degree), measured on the group before the joint's own rotation. For a slide it is `|eje|` (mm per mm). Both are upper bounds, so the advancement is safe.
- Pairs whose bbox lower bound is more than 10 mm away advance on that bound, which is never larger than the true gap.
- **Contact is the real predicate only**: `gap <= 0.05 mm` (which includes penetration). A proximity band is not contact. Near the touch distance the step is never smaller than one joint tolerance (0.05 deg or 0.01 mm), so a run at a constant small clearance advances instead of stalling; it is never reported as contact.
- At the first contact the step is bisected on the same predicate to the joint tolerance. The reported value is the first contact (`primer_choque.valor`); the value before it is `ultimo_seguro`.
- **Pairs that start in contact** (touching at rest, such as a pin in its bore or an arm resting on a base) are not advanced. They are **sampled by volume** at each step, compared with their baseline, and listed in `pares_en_contacto_inicial`. Their step is at most `ESPESOR_MUESTREO_MM / tasa` (0.2 mm of displacement of the far point, never below the tolerance), so a 0.2 mm fin is seen inside its window. A penetration counts above `TOLERANCIA_VOLUMEN_CONTACTO_MM3` (1e-3 mm³) over the baseline, because such a pair touches at rest and any real overlap is above noise.

### Budget and deadline

- One `Presupuesto` per request is shared by all joints. It counts every evaluation and every OCC pair query (gap or volume), including the initial classification, bisection and the final volume checks. `CAP_EVALUACIONES = 2000` units.
- A wall-clock deadline of `PLAZO_SEGUNDOS = 25` s starts when the sweep starts (after the rest shapes are loaded) and covers every joint of the request.
- On exhaustion the joint reports `completo: false`, `garantia: "muestreada"`, `ok: false`, `agotado: "evaluaciones"|"tiempo"` and `agotado_en` (the phase: `evaluacion`, `pares` or `biseccion`), and a Spanish sentence. Exhaustion is never a contact and never a pass.
- Errors from OCC are logged on the server with their detail. The client receives `errores[].mensaje = "error de geometría al evaluar el par"` and no OCC text.

### Joint selection

- Implicit selection (`barrido="todas"`, or no body) skips `fijo` joints and lists them in `omitidas`. An assembly with only fixed joints is `ok` with an empty `barrido` and a sentence that says nothing moves.
- Naming a `fijo` joint explicitly is still `422`.
- `desde` and `hasta` take at most 60 keys each.

## What `garantia: "continua"` proves

For every pair that starts apart, no contact occurs on the swept range before the reported value, up to the joint tolerance and the bound `tasa`. The sweep covers the whole range when `primer_choque` is null and the budget was not exhausted. A joint with any pair in `pares_en_contacto_inicial` is never `continua`, and neither is the request.

## What it does not prove

- Pairs that start in contact (press fits, pins in bores, arms resting on bases) are sampled, not proven. They force `muestreada` for the joint and the request, so the label cannot read as a proof they do not have. They are listed so nothing is hidden. A penetration between samples thinner than `ESPESOR_MUESTREO_MM` can still be missed.
- Several joints moving together. The sweep is single-joint only; coordinated motion stays with `comprobar-recorrido` (a documented limit).
- The `holgura_minima_mm` is the smallest gap **among the visited values**. It is an upper bound on the true minimum; the sweep does not refine a minimum that was stepped over.
- Geometry faults that OCC reports as errors. Any error gives `garantia: "muestreada"`, `completo: false` and `ok: false`; it is never a silent pass.
- Exhaustion of the budget or the deadline. It gives `muestreada`, `completo: false`, `ok: false`, with `agotado` saying which. A long run at a small clearance can exhaust the budget: that is reported as unresolved, not as contact.

## Choque versus contacto

A contact found by the advancing sweep is reported as `primer_choque` with `tipo: "contacto"` (zero or tiny volume) or `"choque"` (volume above `TOLERANCIA_MM3_POR_DEFECTO`, 0.5 mm³). A penetration of an initial-contact pair above `TOLERANCIA_VOLUMEN_CONTACTO_MM3` is `"choque"`. Both make `ok` false, because a moving pair that reaches the touch distance blocks the audit. Touching in a single pose (`comprobar-posicion`, `comprobar-escena`) keeps the older rule: contact is not choque.

The route's summary sentence separates three outcomes: a confirmed contact or collision (names the joints), an inconclusive result (error, budget or deadline), and a clean sweep (which repeats the coverage limits).

## Rejected

- **FCL or another collision library.** It would add a runtime dependency and a second geometry kernel to a project that keeps OpenCascade as its only kernel (ADR-0001). The exact OCC distance and boolean already exist in `checks/distancia.py`.
- **Denser sampling for every pair.** Sampling cannot prove absence between samples; it is used only where a pair starts in contact, and it is labelled as sampling.
- **Treating the proximity band as contact.** It reported a 0.055 mm parallel clearance as a contact at zero travel (review r1 #2).
- **A per-joint budget.** Several joints in one request multiplied the cost; one shared budget and deadline bound the request (review r1 #3).
