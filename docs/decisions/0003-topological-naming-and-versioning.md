# ADR 0003: Topological naming and versioning

Date: 2026-09-27
Status: Accepted

## Context

CAD kernels (OpenCascade included) do not guarantee stable face/edge/vertex
indices across a rebuild: a parametric change (Phase 5 scripts) can
re-trigger the kernel and silently reorder or renumber topological
elements. Forja's notes/pins system (Phase 4, modeled on the FreeCAD
`ClaudeNotas` add-on's `pizarra.py` schema: `tipo`, `comentario`, `puntos`,
`plano_origen`/`plano_normal`, `referencia`, `visible`) attaches annotations
to specific faces/edges. If a pin's reference silently pointed to the wrong
face after a rebuild, that would corrupt the design record without any
error — unacceptable for an AI-driven editor where the agent trusts pin
references as ground truth.

Two strategies were considered: (a) trust raw kernel indices and accept
that pins can silently drift, or (b) compute a **stable identity** per
topological element (derived from geometric invariants — e.g. centroid,
normal, adjacent-face signature — rather than raw index) and explicitly
detect when a stable match cannot be found after a rebuild.

Versioning was considered alongside naming because both protect the
integrity of the design history: an edit that breaks the kernel (invalid
geometry, non-manifold result) must not corrupt the last-known-good state.

## Decision

1. **Topological naming**: every referenceable element gets a
   geometry-derived stable id (`app/naming.py`), computed at rebuild time by
   matching new elements against the previous version's elements using
   geometric invariants (not raw kernel index). If no confident match is
   found for a previously-referenced element, the reference is marked
   explicitly as **"referencia perdida"** (lost reference) rather than
   silently rebound to an unrelated element or dropped.
2. **Versioning**: every accepted edit takes a full snapshot (`app/
   versioning.py`) **before** the change is applied. If the kernel operation
   fails or produces invalid geometry, the system rolls back to the
   pre-edit snapshot automatically. Every successfully applied edit
   corresponds to exactly one commit (one commit per applied edit, not
   batched).
3. History exposed to the MCP server stays compact (summaries/ids/diffs of
   what changed), never a full geometry dump per version.

## Consequences

- `app/naming.py` and `app/versioning.py` are core, load-bearing modules
  that `app/notes.py` and the parametric script layer (Phase 5) both depend
  on; they must be tested before those layers are trusted
  (`tests/test_naming.py`, "rebuild-and-resolve" acceptance in Phase 4).
- A manual pin surviving a face-index shift is a hard acceptance criterion,
  not a nice-to-have — if the matching heuristic cannot guarantee it for a
  given rebuild, the correct behavior is to surface "referencia perdida"
  explicitly, never a silent wrong match.
- Snapshot-before-every-accepted-change means storage grows with edit
  count; this is accepted for now given the single-user local scope, and
  may be revisited (e.g. with pruning/squashing) in a future ADR if it
  becomes a problem — not by editing this one.
- Because rollback-on-kernel-failure is automatic, the parametric script
  execution model (Phase 5, `construir(params) -> Shape` with a timeout)
  can be run with full trust (per `plans/forja-plan.md` decision 2) without
  risking corruption of the last-known-good design.
