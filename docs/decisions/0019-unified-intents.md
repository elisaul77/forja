# ADR 0019 — Unified intents: notes as the marks of an indicación

Date: 2026-10-16. Status: implemented; automated and browser review verified. Supersedes nothing; extends ADR 0018 (intent as document data, typed operations).

## Decision

A design **indicación** (`Intent` in `design.json`) is the single place where a person expresses what they want changed. Notes and strokes made in the viewer are its **marks**: `Intent.marks` stores the note/stroke ids (`n`) that already live in `{doc}.notas.json`. Marks are linked by id; they are not copied, moved or migrated, and `notas.json` keeps its format. A legacy note or stroke without an indicación is listed as a **loose mark** and can be converted with «Convertir en indicación».

`Intent` fields:

- `text` — what the person asked for.
- `marks` — up to 64 note/stroke ids, each linked to at most one indicación.
- `region` — optional piece and points, bound to the geometry revision at creation.
- `images` — up to 6 reference photos.
- `interpretation` (≤ 4000) — the agent's reading, written only through the agent route.
- `question` (≤ 2000) — the agent's clarification question.
- `messages` (≤ 100) — an ordered log of `{author: "user"|"agent", text, created}`. It is data for the reviewer, not a chat transport.
- `status` — `pending`, `accepted`, `resolved`, `dismissed`. Only what a person or agent sets explicitly. Legacy values keep their meaning.

Candidate manifests gain `intent` (the linked indicación id), `summary` (≤ 500, the agent's human explanation), `constraints_revision` (digest of the design **without** intents) and, once applied, `before_snapshot` (the history snapshot taken before applying). `preparar_candidato` and `Prepare` accept the optional `intent` and `summary`; `intent` must exist.

The schema version stays 1. All new fields are optional with defaults, so an old `design.json` and an old `notas.json` load unchanged.

### Derived attention

The attention shown to the user is computed by the server in `read()`, from stored data only. Order, first match wins:

1. `dismissed` — stored status `dismissed`.
2. `resolved` — stored status `resolved` or `accepted`. Only the user sets this.
3. `waiting_agent` — the last message is from the user and is newer than every linked candidate.
4. `applied` — a linked candidate is applied at the current geometry revision.
5. `proposal` — a linked candidate is prepared (and not stale).
6. `reverted` — a linked candidate was applied, but the document has moved since (restore or later geometry change).
7. `question` — the agent set a question.
8. `understood` — the agent set an interpretation.
9. `waiting_agent` — otherwise.

Step 3 deliberately comes before 4–5: a user reply newer than an earlier proposal means the proposal must be reviewed again. `reverted` is never shown as `resolved`.

`read()` also returns, per intent, `marks_detail`, `missing_marks` (ids that no longer exist, reported rather than dropped), and `stale`. `stale` is true when the region revision differs from the current one, its piece is missing, or any linked mark is `referencia_perdida`. A stale reference is flagged, never reassigned. A prepared candidate is stale when the geometry revision or `constraints_revision` changed. `loose_marks` lists the notes and strokes that no indicación links.

### Routes

| Route | Auth | Effect |
|---|---|---|
| `POST /documentos/{id}/design/intents` | none (user route) | Creates an indicación with text, marks, optional region and photos |
| `POST /documentos/{id}/design/intents/{iid}/user` | none (user route) | `reply`, `edit`, `attach`, `resolve`, `reopen`, `dismiss` |
| `POST /documentos/{id}/design/intents/{iid}/agent` | `requiere_token` | Sets `interpretation`, `question` and/or a message, author `agent` |
| `POST /documentos/{id}/design/candidates` (prepare), `…/apply`, `DELETE …/candidates/{key}`, `PUT /design`, `…/design/images`, restore | `requiere_token` | Unchanged: geometry, protected state and history |

Every intent mutation takes `parametros.bloqueo(doc_id)`, snapshots the document **before** the change, saves atomically and publishes `anotaciones_actualizadas` with `design` and `historial`. Intent routes do not require `state_revision`: they append or set fields under the lock. Candidate prepare, apply and discard publish the same event after they finish.

### Auth policy (explicit, not a shortcut)

The protection of Forja is kept. The split is by what an operation can change:

- **Token-free**: the user routes that create or edit an indicación, reply, attach marks/photos, and set resolved/dismissed. They follow the same policy as the existing token-free notes and strokes: editor content, bounded by Pydantic limits, stored as data and never executed. Explaining an intent does not ask for a key.
- **Token required**: the agent route (`responder_indicacion`), and every route that changes geometry, protected roles, reference images, history or the design state through candidates.
- Token prompts in the interface appear only for those actions, and the prompt states the reason. The token is never read, printed or copied by Forja tooling.

### Honesty

There is **no transport** that sends a message to an AI. Saving an indicación makes it available to the agent when it reads Forja (`contexto_diseno`). The interface says so in a banner. The displayed states are:

- **pendiente** (waiting for the agent): no reading or proposal yet, or the user replied after the last one.
- **entendida** / **con pregunta**: the agent has interpreted it or asked something.
- **propuesta disponible**: a prepared candidate exists.
- **cambio aplicado** (awaiting the user): applied, with «Volver atrás» and the user's own confirmation.
- **resuelta**: only the user marks it.
- **descartada**.

Running an operation never marks an indicación resolved. There are no fake chat or «enviar a la IA» buttons.

### Candidates and undo

A candidate links its indicación and carries a human `summary`. `constraints_digest` covers roles, locks, requirements, objective and postures, but not the intent conversation. A reply, attachment or agent reading therefore does not invalidate a prepared proposal. An objective change still does, as before.

Apply records `before_snapshot` using the existing history mechanism. «Volver atrás» uses the existing `POST /documentos/{id}/restaurar` with that snapshot. After any later geometry change, the candidate reads as `reverted`.

## Existing mechanisms retained

Notes and strokes (`app/notes.py`, `notas.json`), photos and their protection, design state revisions, the `expect()` guard in apply, history snapshots and restore, branch capture, reference protection, per-piece export, the scripting sandbox, stale-reference rules and the token dependency. Nothing is copied or migrated; the intent holds ids.

## Limits

- The agent reads intents only when it calls `contexto_diseno`. Forja cannot notify it and does not show whether it has read anything.
- Marks are linked, so deleting a note leaves a missing id that is reported, not repaired.
- A mark belongs to one indicación at a time (409 otherwise). There is no action to detach a mark, so a mark stays with its indicación until the document changes by other means.
- The attention states are a view of stored data. They are not an audit of what the agent understood.
- Undo restores the whole document to the pre-apply snapshot. It does not undo individual later edits.
- The interface was verified by review and browser walkthrough on an isolated instance with a fictitious token. No end-to-end test with a real AI client was run.
- Limits from ADR 0018 still apply to geometry methods.
