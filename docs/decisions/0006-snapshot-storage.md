# ADR 0006: Plain-directory snapshot store for versioning (not git)

Date: 2026-09-27
Status: Accepted

## Context

ADR-0003 decided *that* every accepted edit takes a full snapshot before
being applied, with rollback on kernel failure, but deliberately left the
storage mechanism open ("git-backed or content-addressed, choose in Phase
4"). Two options were on the table:

1. **Git-backed**: `git init` one repo per document (or one repo for all of
   `documentos_data`), committing the STEP/STL file + `.notas.json` on every
   accepted mutation, using `gitpython` (or shelling out to `git`) for
   history/restore.
2. **Plain-directory snapshot store**: one directory per document under
   `/data/documentos/.historial/{doc_id}/`, each snapshot its own
   subdirectory holding a byte-for-byte copy of the mutated files, plus a
   small `manifest.json` (`[{id, fecha, mensaje}]`) recording order.

Forja's documents are a handful of small binary files (one STEP/STL, one
small `.notas.json`) per document, not a source tree — the main things git
would buy (line-level diffs, branching, merge) do not apply here; only
linear "list snapshots, read one back, restore" is needed. Git would also
pull in `gitpython` (a new dependency, against the "no new heavy deps"
constraint) or shell out to the `git` binary (not present in the
`python:3.12-slim-bookworm` image without an extra `apt-get install`,
another Dockerfile change for zero behavioral gain here).

## Decision

Use a plain-directory snapshot store (`app/versioning.py`): each accepted
mutation writes a full copy of the affected files into a fresh
`uuid4().hex[:12]`-named subdirectory under
`/data/documentos/.historial/{doc_id}/` and appends a compact entry
(`{id, fecha, mensaje, archivos}`) to that document's `manifest.json`.
Reading a snapshot back is a plain directory read; restoring is the
caller's (`app/documents.py`) responsibility — it writes the returned bytes
back to the live file paths and re-registers the document.

This is deliberately **not** content-addressed by hash (no dedup across
snapshots that happen to share identical bytes) — documents are small, the
project is explicitly single-user/local scope (per ADR-0003's storage-growth
consequence, already accepted), and hash-based dedup would add complexity
(reference counting on delete, garbage collection) for a benefit that does
not matter at this scale. "Content-addressed" in ADR-0003's phrasing is
satisfied loosely (each snapshot is immutable, keyed by its own id) without
literally hashing content.

## Consequences

- No new dependency, no new Dockerfile layer: `versioning.py` only uses
  `pathlib`/`json`/`uuid`/`time` from the standard library.
- `app/versioning.py` knows nothing about `build123d`, the document
  registry, or the notes schema — it only moves `dict[str, bytes]` in and
  out of `.historial/{doc_id}/`. This is what lets `app/documents.py` and
  `app/notes.py` both depend on it without depending on each other (avoids
  the circular import the Phase 4 graph warning flagged).
- Storage grows linearly with edit count, same accepted tradeoff as
  ADR-0003 already named; `.historial/` lives under the already
  bind-mounted, already-gitignored `documentos_data` volume.
- If Forja ever needs line-level diffs, branch/merge semantics, or
  cross-machine sync of history, that is a new requirement past this ADR's
  scope and would warrant its own superseding decision (not an edit here).
