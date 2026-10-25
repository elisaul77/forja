# ADR 0018 — Shared human/agent design workspace

Date: 2026-10-15. Status: implemented; automated integration verified.

## Decision

The application and MCP share typed REST operations. Intent is document data, never executable instructions. `design.json` stores objective, role/lock per named part, revision-bound regional intents, requirements with provenance, and separate print rotations. It travels with geometry snapshots, branch capture/materialization and full merge. Merge conflicts are explicit; whole-state intent does not use last-writer-wins.

Geometry revision remains backward compatible. A separate deterministic design-state digest detects edits to intent. Mutating workspace operations require both. Old face/point references are exposed as stale after geometry changes; they are not silently resolved to an arbitrary face. Named part ids are derived from document id and part name; they are not universal topology ids.

Candidates are prepared outside the active document. Operations are typed rigid transforms, primitive booleans and mesh regional displacement, not executable code. Applying a candidate refuses changed input revisions, failed requirements and open modified meshes. Unknown/manual requirements remain visible. Publication snapshots the document and rolls back state on failure. A request key is idempotent for identical input; reuse with different input is a conflict.

Geometry methods disclose their actual evidence: analytic STEP surfaces, selected-point fits, mesh bounds/topology, FCL triangle distance/intersection and heuristic FDM. Contact is not distinguished from penetration by triangle intersection alone. Open meshes cannot establish containment. Six print orientations are a bounded comparison, not a global optimizer. Regional STL displacement preserves connectivity but does not prove freedom from self-intersection.

Analysis jobs use one bounded worker, progress checkpoints and cooperative cancellation. They do not interrupt native kernels and expire on restart. Query cache is bounded and keyed by geometry revision, design digest and request. Export selects design roles, excludes references/reserved volumes, and applies print pose without changing assembly geometry; deterministic 3MF timestamps make its SHA-256 reproducible.

## Existing mechanisms retained

Sandboxed scripts, notes and strokes, revision/history, named STL parts, STEP groups, FDM helpers, web selection and transport authentication. Whole-document script rebuilding and assembly poses refuse protected roles because their legacy contracts cannot guarantee preservation. Use explicit design candidates or explicitly change protection.

## Limits

No universal automatic recognition of holes in arbitrary STL, no topology identity guarantee across arbitrary remeshing, no optimal print orientation search, no physical fit certification. Model selections are revision-bound rather than guessed across edits. Future improvements can add those capabilities without changing the state/transaction contract.
