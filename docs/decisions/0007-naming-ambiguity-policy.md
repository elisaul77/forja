# ADR 0007: Naming ambiguity policy — never guess between tied candidates

Date: 2026-09-27
Status: Accepted

## Context

ADR-0003 requires that `app/naming.py`'s `resolver()` never silently rebind
a reference to the wrong element; a genuinely unmatched reference must
surface as "referencia perdida" instead. The Phase 4 review found that the
original implementation only handled the "zero candidates within
tolerance" case explicitly — when **two or more** candidates all passed the
centroid/direction/area tolerance gates, it picked whichever happened to
have the smallest centroid distance, with no margin requirement over the
runner-up. For two near-tied candidates, that pick is effectively decided
by floating-point noise, and is not guaranteed to be the same across two
runs that hand the candidates to `resolver()` in a different order —
exactly the "silent wrong match" ADR-0003 forbids, just one level removed
(silently *chooses* instead of silently *rebinds*).

## Decision

When more than one candidate passes every tolerance gate, `resolver()`
(via `resolver_detallado()`) only accepts the best one if it is a **clear
winner** over the second-best, using two constants in `app/naming.py`:

- `TOL_GANADOR_CLARO_MM = 0.1`: the best candidate's centroid distance must
  itself be very small.
- `FACTOR_GANADOR_CLARO = 3.0`: the second-best candidate's centroid
  distance must be at least 3x the best's (and strictly greater than it,
  which also rules out an exact tie at distance 0).

If both hold, the best candidate is returned exactly as before. If not —
including the common case of two genuinely-tied candidates — the reference
is treated as **unresolved**, identically to the zero-candidate case
(`referencia_perdida: true`), but additionally flagged `ambigua: true` so a
caller/UI can tell "nothing was close enough" apart from "more than one
thing was close enough" without extra cost (the flag is just carried
through from the tolerance-gate loop already being done).

The margin check is computed by sorting the passing candidates by distance
and comparing the top two values — never by iteration order — so the
result is the same regardless of what order `candidatas` is handed in.

## Consequences

- `resolver()`'s public signature and return type (`Fingerprint | None`)
  are unchanged — existing callers that only care about "did it resolve"
  keep working. Callers that want to distinguish "lost" from "ambiguous"
  use the new `resolver_detallado()` (returns `(Fingerprint | None, bool)`).
- `resolver_referencia()` (and therefore `app/documents.py`'s script
  update-in-place path and `app/notes.py`'s note/stroke schema) now also
  surfaces `ambigua: true` alongside `referencia_perdida: true` when
  applicable — an additive field, not a breaking schema change.
- A pin on a genuinely ambiguous feature (e.g. two symmetric, identical
  holes whose centroids end up within tolerance of the original — not the
  common case, since distinct hole locations are almost always more than
  `TOL_CENTROIDE_MM` apart, but possible after a rebuild that also nudges
  geometry near the tolerance boundary) will now correctly report as lost
  rather than silently jumping to the wrong hole. This trades a small
  amount of extra "referencia perdida" noise for the correctness guarantee
  ADR-0003 already committed to.
- The two constants are conservative defaults chosen for this project's
  single-user, mm-scale parts; if a future part family needs different
  margins, that is a tuning change to these constants, not a reason to
  revisit this ADR's policy.
