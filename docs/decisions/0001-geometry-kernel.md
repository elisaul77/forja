# ADR 0001: Geometry kernel — build123d + OpenCascade (cadquery-ocp)

Date: 2026-09-27
Status: Accepted

## Context

Forja needs a real B-rep solid-modeling kernel to import/export STEP and
STL, run booleans, compute volume/bbox, and expose stable topology to the
naming/versioning layer (ADR-0003). Candidates considered: raw
`cadquery-ocp` (OpenCascade bindings) directly, `build123d` on top of it,
FreeCAD's Python API via `freecadcmd`, and a pure-mesh kernel
(`manifold3d`/`trimesh` only, no B-rep).

A pure-mesh kernel was ruled out early: it cannot represent or export true
STEP B-rep, only meshes, which breaks the "STEP round-trip preserves volume"
contract required by Phase 1. Driving FreeCAD headless via `freecadcmd` was
ruled out because it is heavier to containerize, harder to script
purely-programmatically at the level MCP tools need (module-level function
calls, not GUI-oriented document objects), and is only used read-only in
this project for the mostertruck pilot data (see `forja-plan.md`, decision
4), not as the live kernel.

PyPI availability was verified for Python 3.12 (`python:3.12-slim-bookworm`)
before locking versions (see `plans/forja-plan-detail.md`, "Verified
environment"):

- `build123d` 0.13.0 — requires `python>=3.11,<3.15`.
- `cadquery-ocp` 8.0.1.0.0 — ships `manylinux_2_28` wheels for `cp311`/`cp312`
  (no `cp310`), which is why Python 3.12 was chosen over 3.10/3.11.
- `manifold3d` 3.5.4 — mesh boolean/processing backend used alongside OCP.
- `python-fcl` 0.7.0.11 — collision/clearance detection (Phase 5/6).
- `trimesh` 5.1.0 — mesh analysis/validation, STL round-trip checks.

## Decision

Use **build123d 0.13.0** as the primary modeling API, built on
**cadquery-ocp 8.0.1.0.0** (OpenCascade) as the underlying B-rep kernel.
`manifold3d` 3.5.4 handles mesh-level boolean/processing where a full B-rep
operation is unnecessary or too slow. `python-fcl` 0.7.0.11 provides
collision/clearance queries against meshes. `trimesh` 5.1.0 is used for mesh
analysis, format conversion, and as an independent volume-check oracle in
the kernel smoke tests (Phase 1: "analytic vs mesh volume within 1e-3
rel").

The kernel runs on **Python 3.12** inside `python:3.12-slim-bookworm`,
exclusively in Docker — no host Python runtime is assumed anywhere in the
project.

## Consequences

- STEP is a first-class, round-trippable format (volume-preserving),
  satisfying the Phase 1 contract.
- Python is pinned to 3.12 project-wide (not just for the kernel), since
  `cadquery-ocp` has no `cp310` wheel and 3.12 is the newest version common
  to all five pinned packages.
- `app/kernel/` becomes a thin adapter module (`b123d_kernel.py`,
  `mesh.py`) so the rest of the backend (documents, notes, scripts, checks)
  never imports `build123d`/`OCP` directly — keeping the kernel swappable in
  principle, even though no swap is currently planned.
- FreeCAD (`freecadcmd` AppImage) remains an external, read-only tool used
  only to generate pilot STEP data from `mostertruck` `.FCStd` files; it is
  never a runtime dependency of the Forja service itself.
