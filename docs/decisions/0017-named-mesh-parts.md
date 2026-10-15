# ADR-0017: Named mesh regions without B-rep conversion

Status: accepted, 2026-10-14.

## Context

STL has no object names. Incorporating complete documents previously flattened their meshes, so the viewer could not select or export the duct separately from its references. Converting faceted STL to B-rep solely to carry names can produce degenerate faces and a non-watertight re-tessellation.

## Decision

Keep canonical mesh triangles unchanged. Incorporation records document/part provenance in the existing versioned `solidos.json` sidecar: entries tagged `mesh-parts-v1`, validated unique names, consecutive exhaustive triangle ranges and SHA-256 of the ordered float32 triangle stream. Identity never comes from connected components; disconnected reference geometry stays one named part. Geometry and metadata are read under document locks; incorporation locks both documents in ID order and rolls back both files on a failed write.

Reuse the FJP1 component manifest, per-piece download routes and 3MF object writer. STL objects with no sidecar retain legacy single-mesh behavior. Bad metadata produces a visible whole-model fallback in the viewer and a selective-export error, never an inferred replacement mapping. Existing snapshots, revisions, deletion and restart paths already carry the sidecar.

## Consequences

STL exports remain standard STL and cannot carry names externally; use 3MF to transfer named objects. Exporting a named region preserves its original tessellation, including existing invalidity (reported, never automatically repaired). This does not add analytic topology or enable STEP-only FDM/assembly checks for meshes. Existing flattened assemblies need to be rebuilt once from their source documents to recover provenance. No geometry inference, automatic repair, new dependency, or project-specific hardcoding.
