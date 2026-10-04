# ADR 0002: Frontend stack — vanilla ES modules + vendored three.js

Date: 2026-09-27
Status: Accepted

## Context

Forja needs a web viewer capable of rendering B-rep-derived meshes with
per-tab independent camera/selection state (Phase 2), plus UI for notes/pins
(Phase 4) and parametric script inputs (Phase 5). Options considered: a
modern framework (React/Vue/Svelte) with a build pipeline, or vanilla
JavaScript with ES modules and no build step.

The verified environment (`plans/forja-plan-detail.md`) confirms `node` is
present on the host but explicitly **not to be used** — the project commits
to zero host-Python (ADR-0001) and, by the same reasoning, zero host-Node
build dependency. A framework would require a bundler (webpack/vite/esbuild)
running either on the host (violates Docker-only runtime) or as an extra
Docker build stage (adds complexity disproportionate to a single-page
viewer with a handful of tabs). Three.js is required for WebGL rendering of
meshes; a CDN import was ruled out because Forja must work offline and the
MCP/viewer stack must not depend on external network access at runtime
(read-only external *sources* are for design reference only, not a runtime
dependency).

The `suspension-sim` project (read-only template referenced in the plan) was
inspected for prior art: it already vendors a Dockerfile + compose pattern
with a bind-mounted `./app:/app` and a three.js viewer, which Forja follows
directly.

## Decision

Use **vanilla ES modules** (no framework, no bundler, no transpilation) for
the entire frontend, served statically by the FastAPI backend from
`app/web/`. **three.js is vendored locally** under
`app/web/static/vendor/three/` (committed into the repo / Docker image, not
fetched from a CDN at runtime). CSS uses hand-written `--fj-*` custom
properties (ported from kybercore's Forge `--kc-*` design tokens), no CSS
framework.

## Consequences

- No `node_modules`, no build step, no host-Node dependency — the frontend
  is just static files the browser loads directly via `<script type="module">`.
- Adding a new vendored library later means manually committing its files
  under `static/vendor/`, not adding an npm dependency.
- Per-tab state (camera, selection) is plain JS objects keyed by tab id, not
  a framework store — acceptable at the planned scale (a handful of tabs,
  no deep component tree).
- Phase 2's "no CDN requests" acceptance criterion is satisfied by
  construction, and is also a privacy/offline requirement (the project
  targets a single-user local Docker deployment).
- Future growth into a more complex UI (e.g. undo/redo trees, multi-pane
  layouts) may eventually justify revisiting this decision; if so, it must
  be done via a new ADR that supersedes this one, not an edit to it.
