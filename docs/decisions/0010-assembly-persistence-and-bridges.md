# ADR 0010: Assembly persistence, the schema refusal, and the read-only service bridges

Date: 2026-09-28
Status: Accepted

## Context

Phase 6 added two things Forja did not have: **assemblies** (joints between
named solids of one document, plus an absolute pose per joint) and
**bridges** to local engineering services (`suspension-sim`, Blender,
kybercore). The first cut arrived from an external session and Phases
6.2-6.4 hardened it: the assembly became part of the document lifecycle,
the wire vocabulary turned Spanish (6.3), and MCP gained `ensamble`,
`suspension` and `puentes` (6.4).

Four decisions were load-bearing enough that a reader of the code alone
would have to reverse-engineer them, and two of them (pose semantics,
persistence) are the kind of thing that silently rots if the next phase
"improves" it without knowing why:

1. **Where does a pose live?** A joint could have been expressed relative
   to the parent's current pose (chained) or to the immutable rest STEP.
2. **Where does the state live, and does it survive a restore?** The state
   belongs to one specific geometry revision, but the snapshot store
   (ADR-0006) is file-set based and predates it.
3. **What happens when a persisted state cannot be read?** Phase 6.3 renamed
   every persisted key; a `state.json` written before that rename has no
   `articulaciones` key at all.
4. **How much of an external service may Forja touch?** The bridges are the
   only place Forja talks to something it does not own.

## Decision

### 1. Pose semantics: rest frame, then all ancestors

Joints live in the **world frame of the immutable rest STEP**: `origen`
(mm) and `eje` are interpreted against `base.step`, the byte copy of the
document geometry taken when the assembly was defined, never against the
current, already-posed file. Angles are **degrees**, translations are
**millimetres**, and a joint's `limites` verify its `valor` before anything
is built.

`assemblies.pose_shapes()` walks each named solid up its parent chain: the
child's own rigid transform (`giro` about `eje`, `deslizamiento` along it)
is applied **in rest coordinates**, then every ancestor's motion, in
parent-to-root order. Poses are therefore **absolute** — `valores` names the
angle/offset a joint should have, not a delta from where it is now — so the
same `valores` always produces the same geometry, in the viewer and in MCP
alike.

A pose is one transaction: snapshot first, then re-pose → re-export →
reimport → re-validate (volume/solid count), then commit the STEP, the named
solids, the re-resolved note references and the new revision digest. Any
failure restores the previous bytes and the previous registry record.
Definition is the exception that proves the rule: initial joint values must
be **zero**, because defining must not move the geometry.

### 2. Persistence: outside the history, carried through it

The assembly lives in its own directory,
`/data/documentos/.ensambles/{doc_id}/` (`state.json` + `base.step`), next
to the document but **outside** `.historial/`, because it is document state,
not a snapshot: `.historial/` is the version store, and a restore must be
able to *replace* it.

It is nevertheless **carried through** the snapshot store, because the state
is only meaningful for the geometry revision it was defined against:

- A **geometry** snapshot (document STEP present) also carries
  `ensamble.json` + `ensamble_base.step` (`_con_ensamble_snapshot`, mirroring
  the parametric state's `_con_parametros_snapshot`), so restoring a pose
  restores the joints that belong to those exact bytes.
- A **notes-only** snapshot does not: undoing an annotation must leave the
  assembly alone.
- A snapshot taken **before** the assembly existed carries none of those
  files, so restoring it *removes* the assembly (`restore_files({})`) rather
  than leaving today's joints pinned to yesterday's geometry.
- Restoring routes those two keys through `assemblies.restore_files`, never
  through the generic file writer: written as ordinary files they would land
  in the documents **root**, where `recargar_documentos()` globs `*.step` at
  startup and would register a phantom `ensamble_base` document (the bug
  Phase 6.0 found and Phase 6.2 fixed).
- Deleting a document deletes its assembly (`assemblies.clear`), because a
  document must leave nothing behind that no restore could ever reach.

### 3. `version: 2`: refuse an unreadable state, never plant it

The persisted state carries a schema marker, `version: FORMAT_VERSION`
(currently **2**; 1 is the pre-6.3 English vocabulary). Every read path
validates it through `_estado_compatible`, and a state this build cannot
read raises `EstadoIncompatible` (a `ValueError` subclass, so existing
error handling keeps working) that the routes map to **409** with
`ensamble en formato anterior; quitar las articulaciones y redefinirlas`.

There is nothing to migrate — the geometry and the joints are still correct,
only the vocabulary changed — so the message names the recovery path:
`DELETE /documentos/{id}/ensamble` never reads the state, removes the two
files, and the assembly is defined again against the same rest bytes.

**Refusing beats planting.** `restore_files` validates the snapshot before
writing anything, and `documents._restaurar_documento_bloqueado` pre-flights
it (`assemblies.validate_snapshot`) *before* the document STEP is replaced:
without that pre-flight a refused restore would still have swapped the
geometry, leaving a half-applied restore behind a 409.

Two limits are known and accepted:

- A **hand-corrupted** `state.json` (truncated, hand-edited) fails in
  `json.loads` with no message of its own. Unreachable through the API:
  every write is atomic (`_save` writes `state.tmp`, then `os.replace`), so
  no caller can observe or produce a partial file. Editing the file by hand
  is out of contract.
- A snapshot that **carries** a v1 assembly stays refused (409) even after
  `DELETE …/ensamble` clears the live state. That is the direct consequence
  of refusing rather than planting: the alternative is a restore that
  silently drops the assembly it could not read, or one that plants it back.
  A snapshot whose assembly is unusable is not restorable — deliberately.

### 4. Bridges: a read-only allowlist, not a proxy

`app/bridges.py` reaches local services through a **hard-coded**
`(service, route)` allowlist — `suspension`: `GET /salud`, `GET /config`,
`POST /pose`, `POST /punto`; `kybercore`: `GET /health`; Blender one socket
verb `get_scene_info`. A caller can never supply a URL or a route. Only
routes that are pure computation are on it: `/cinematica`, `/golpe`,
`/buscar`, `/registro` and `/piedra` write to `suspension-sim`'s own
`resultados/` and are refused with **400** before any connection is opened.

The base URL comes only from the environment
(`FORJA_{SUSPENSION,BLENDER,KYBERCORE}_URL/HOST/PORT`), never from a request;
`FORJA_<SERVICIO>_ENABLED` gates each bridge and only `suspension` is enabled
by default (Blender :9877 and kybercore :8100 are feature-flagged **off**).
The envelope is bounded: **256 KB** maximum response (streamed and counted),
**8 s** timeout, **no redirect following** — a redirect is how a fixed route
would otherwise reach an arbitrary one. The body itself is parsed with
`parse_float`/`parse_constant` hooks that refuse a **non-finite** number
(`Infinity`, `NaN`, or a literal that overflows to infinity): such a value
cannot be serialised into a response, so it answers **502** through the same
"no se pudo consultar" path as any other unreachable-service failure, never a
500 plus a traceback (Phase 6.4 closing round).

`host.docker.internal` resolves inside the container only because of the
compose entry `extra_hosts: ["host.docker.internal:host-gateway"]`. One entry
covers both bridge flavours: the httpx bridges and the raw-socket Blender
bridge dial the same hostname where changing the Python default to a Docker
IP would have fixed only `_http`.

`GET /puentes` answers the three readiness booleans
(`{habilitado, disponible}`, plus `mensaje` when an enabled service is down)
and never fails on a down service — the MCP tools `suspension`/`puentes`
inherit that, so an agent can skip a doomed call instead of reading a stack
trace.

### 5. Lock scope: one per-document lock, not reentrant

`parametros.bloqueo(doc_id)` remains **the** per-document lock for the whole
project: `documents.py` (script execution, parameters, restore, delete),
`notes.py` and `assembly_routes.py` all take the same one, so two mutations
of one document serialize no matter which route or transport (viewer or MCP)
they arrive from. It is **not reentrant**: a route takes it exactly once, at
entry, and every helper reachable while holding it is lock-free. The document
id is validated *before* acquisition (Phase 6.3), because the lock table is
created-and-kept per id and must not be growable by a caller that names no
document.

Head-of-line blocking is accepted and bounded: a slow pose or a long script
delays other mutations of *that same* document (never of another), and the
script's own timeout bounds the wait. Fixing it would mean either lock
eviction (which can hand the same lock to two live requests) or a reentrant
lock (which invites the self-deadlock Phase 6.0 cleared as unreachable only
because of the call-graph property above).

## Consequences

- The viewer and MCP drive **one** REST contract
  (`app/assembly_routes.py`), so a pose is the same numbers with the same
  validation from either side, and the agent — the primary consumer — can
  define, pose, inspect and clear an assembly without touching the panel.
- Assembly state survives a container restart with the document, travels
  with geometry snapshots, and cannot be left pinned to a stale STEP: a
  restore either brings the matching joints or removes them. Changing the
  document's geometry through `ejecutar_script` stales the pose
  (`obsoleto: true`) instead of silently posing the wrong revision.
- The cost is a refusal path with no migration: an assembly defined before
  Phase 6.3 is lost (two commands) rather than migrated, and a snapshot
  carrying one is not restorable. Accepted because no consumer had shipped
  (decision D5) and because the alternative — guessing at incompatible state
  — is exactly what the project refuses everywhere else.
- Forja can never write to `suspension-sim`, Blender or kybercore: the
  allowlist is the boundary, and widening it is a code change plus an ADR,
  not a request payload.
- Enabling the Blender or kybercore bridges is an env change with no code
  change, but each one ships without MCP tools of its own — by decision D3
  their data tools are deferred until the bridges prove useful.
