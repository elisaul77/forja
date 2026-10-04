# ADR 0011: Opening a document in OrcaSlicer from the browser, and the token-free download route behind it

Date: 2026-10-02
Status: Accepted

## Context

The user's requirement for Phase 10 was the flow Printables and Thingiverse
already give him: a button on a web page, the browser hands an
`orcaslicer://open?file=<url>` link to the operating system, OrcaSlicer
downloads the file itself and loads it. **Nothing runs a command on his
machine, and the Forja container gains no way to launch host programs.** On
this PC the scheme is registered: `x-scheme-handler/orcaslicer` points at
`com.orcaslicer.OrcaSlicer.desktop` (the Flatpak, user install) and the
default browser is Firefox.

The contract of OrcaSlicer's downloader was first read from its source
(SoftFever/OrcaSlicer `main`: `Downloader.cpp`, `DownloaderFileGet.cpp`,
`Http.cpp`) and then **confirmed by a real launch on this machine** (Phase
10 acceptance, 2026-10-02, `xdg-open` of the link, Orca's own log quoting
the URL it fetched):

- The link must match `orcaslicer://open?file=<url>`; the `file=` value is
  **percent-decoded once** (`curl_easy_unescape`), so it must be encoded
  exactly once.
- The saved filename is whatever follows the **last `/` of the decoded URL,
  query string included**. A URL such as `/documentos/x/exportar?formato=stl`
  is saved as `exportar?formato=stl`, with no extension, and the extension
  decides how the file is loaded afterwards.
- `Content-Disposition` (`filename="..."`, **quoted form only**; `filename*=`
  is ignored) is only the fallback when the URL's own name has no extension.
- Redirects are followed; plain `http://` works; the size limit is 1 GiB.
- Orca sends **no custom headers** (so no `X-Forja-Token`) and a
  `Range: 0-` request; a `200` with the whole body is accepted.
- On completion it loads the file (`plater->load_files`). In the real launch
  the file landed in Orca's own download folder (`download_path`,
  `~/Descargas` here) and Orca opened with the user's own machine/process/filament
  profile already selected.

The existing routes could not serve this. `GET /documentos/{id}/exportar`
takes its format in the query string, writes a file under
`documentos_data/exportados/`, answers JSON and takes no lock. The saved name
would be wrong and the body would not be a model.

Phase 10.3 also tried to add a safeguard against silent geometry loss; it was
built, measured and dropped. That is recorded below because the reason it was
dropped is a fact about the code that the next person to try will need.

## Decision

### 1. A dedicated read-only route with a real filename in the path

`GET /documentos/{id}/descarga/{slug}.{stl|3mf}` (`app/documents.py`,
`descargar_documento`; `HEAD` is accepted too, so `curl -I` works).

- **Token-free by necessity**: Orca cannot send a header. The exposure is the
  same as `/malla` and `/exportar`, which are already token-free GETs: same
  data, ids are `uuid4().hex`, the port is open on the LAN, and no listing is
  needed to use it. Every mutating route keeps `X-Forja-Token` (ADR-0005).
- **Built from memory, writes nothing**: nothing is created under
  `documentos_data`, and no filesystem path is derived from user input. The
  3MF is produced by `export.bytes_3mf` / `export.objetos_3mf` (the same
  object-building function `exportar` uses; `escribir_3mf` is unchanged in
  signature and output).
- **The tail is validated, the stem is ignored**: it must fully match
  `[A-Za-z0-9_-]{1,64}\.(stl|3mf)` and the real `scope["path"]` must end
  exactly with it (Starlette's `{archivo:path}` regex ends in `$`, which
  otherwise lets `x.stl%0a` through). Anything else is 404. The name sent in
  `Content-Disposition` is derived from the document's own name
  (`_slug_descarga`: NFKD, ASCII `[A-Za-z0-9_-]`, at most 64 characters,
  fallback `modelo`), never from the request.
  The JS slug function in the viewer and `_slug_descarga` can differ for
  characters added in Unicode 16 and later: a 158,612-name differential test
  found six code points (for example U+A7F1 and four astral ones around
  U+1CCxx), because the container's Python 3.12 ships Unicode 15.0.0 while
  Node 22's ICU is Unicode 17.0, so the server drops them and the viewer folds
  them to letters. The only effect is a different saved filename for a
  document whose name contains one of those characters.
- **Locking**: the document id is validated first (at most 128 characters
  **and** 128 UTF-8 bytes, `notes._doc_id_valido`, plus membership in
  `_registry`) *before* `parametros.bloqueo(doc_id)` is taken, so an unknown
  id cannot grow the lock table (ADR-0010 §5); the registry is **re-checked
  inside the lock**, so a delete that won the race answers 404 instead of
  reading a half-replaced STEP.
- **Triangle cap**: `MAX_TRIANGULOS_DESCARGA = 1_000_000`, over it 413. The
  cap was chosen by measurement (the largest document, 646,158 triangles,
  builds a 3MF in about 15 s, 8.2 MB, ~761 MB peak RSS) and is checked
  **after** tessellation.
- **Response**: `Content-Disposition: attachment; filename="<slug>.<ext>"`
  (quoted, ASCII-only), `Content-Type` `model/stl` / `model/3mf`,
  `Cache-Control: no-store`.
- **Error policy** (Phase 10.1 fix rounds). Expected on-disk drift gives a
  **clean 4xx with no traceback**: a stale `solidos.json` is the same 400,
  with the same message, that `exportar` gives; a registered document whose
  file is gone is **404** ("archivo del documento no encontrado"); a failing
  import (`OSError` or a kernel import error) or an unexpected sidecar shape is
  400; an unknown document or tail is 404.
  Anything unexpected is a server defect: it is logged with
  `logger.exception` and answered with a generic **500** chained `from None`,
  so no exception text reaches the client and a real bug is never blamed on
  the user's data. One shared emptiness check (`_geometria_vacia`, a face
  count) runs **before** the format builders: a corrupt STEP imports as an
  empty shape instead of raising, and without it would have been served as a
  200 with an empty body (or reported as a misleading sidecar mismatch).
- **Query-string rule**: a non-empty query string is 404 (Orca would save
  `x.stl?formato=step` verbatim). A **bare trailing `?`** (`x.stl?`) cannot be
  rejected: uvicorn hands the application an empty `query_string` and a
  `raw_path` without the `?` for it, proven identically under httptools (the
  production default) and h11, so it is served exactly like `x.stl`. The
  contract is therefore placed on the **link builders**: they must never emit
  a `?` or anything after the final `.stl|.3mf` (enforced in
  `mcp_server/client.py` and `app/web/static/js/orca.js`, and pinned by
  tests).

### 2. The container never launches anything on the host

- **Viewer**: three plain `<a class="fj-btn">` anchors in each document's
  toolbar, built client-side (`app/web/static/js/orca.js`) from
  `window.location.origin`, so the same page works from `localhost` and from
  a LAN address: "Abrir en Orca" (`orcaslicer://open?file=` +
  `encodeURIComponent(url)`, the 3MF), "Descargar 3MF" and "Descargar STL".
  Clicking "Abrir en Orca" shows a Spanish hint in the existing
  `#fj-feedback` region and never calls `preventDefault`.
- **MCP**: the existing `exportar` tool gains `descarga`, `url_descarga` and
  `enlace_orca` for a single-file `stl`/`3mf` (absent for `step`, `por_solido`
  and errors). `descarga` is the backend's relative path; the link is
  composed in `mcp_server/client.py` (the single HTTP boundary, ADR-0005),
  from `FORJA_PUBLIC_URL`: read **at call time**, default
  `http://localhost:8710`, accepted only as `http|https://host[:port]` with an
  optional trailing `/` (no userinfo, path, query, fragment, whitespace or
  control characters, no non-ASCII host); anything else **falls back to the
  default without raising**, and the fallback log never includes the value
  (a rejected value may carry credentials). The `descarga` path must fully
  match `/documentos/<id>/descarga/<slug>.(stl|3mf)` or no link is emitted.
  `enlace_orca` is `quote(url_descarga, safe="")` after the scheme prefix: one
  encoding, only `[A-Za-z0-9%_.~-]`, safe inside single quotes in a shell.
- **The agent opens the link from the host shell** with
  `xdg-open '<enlace_orca>'`. Forja executes nothing; the browser or the
  host shell does the launching.
- **No 20th MCP tool.** The inventory stays at 19 (server registrations,
  `SKILL.md` table, its frontmatter `allowed-tools` and the README agree).

### 3. 3MF is the default, STL the fallback

The 3MF declares `unit="millimeter"` and carries **one named object per named
solid**, which is what Orca lists. STL has no unit and is assumed to be mm; it
is offered as the second link.

### 4. Rejected: a non-failing `aviso_volumen` on `exportar`

Motivation: `valido: true` missed a 37% volume loss on `casa_del_arbol` (a
27-sphere STEP round trip), and the slicer is the worst place to find out.
Phase 10.3 built a warning when the exported mesh volume disagreed with the
volume recorded for the document by more than 2%. It was implemented in
`2440c09` and **reverted in `6c5fd30`** (history kept). Its premise was false:

- The `solidos.json` volumes are measured on the **re-imported STEP on every
  path that writes them**: script creation, in-place script/parameter update,
  assembly pose, restore and startup (and uploads, which have no build at
  all). The only code that ever sees the shape before the STEP round trip is
  the script subprocess, which exports the STEP and hands back names, not a
  volume. A sidecar-versus-reimport check therefore **cannot detect a STEP
  round-trip loss**: measured on all 6 real STEP documents, the relative
  difference between the sidecar sum and the re-imported B-rep volume is
  exactly 0.
- The mesh-versus-expected check was **silenced by its own watertight guard
  on 5 of the 6 real documents** (OpenCascade tessellates per face, so
  T-junction cracks persist at every `merge_vertices` precision tried, even
  though the signed volume is accurate to 0.5% or better). "No warning" was
  therefore ambiguous: checked and fine, or could not check.
- The real 37% `casa_del_arbol` loss **could not be reproduced** with the
  current script, and it may have happened inside OpenCascade's fuse at build
  time, which no sidecar comparison would catch either.

Shipping a half-useful check with an ambiguous silence is worse than shipping
none.

## Consequences

- One click in the viewer, or one `xdg-open` from the agent, takes a document
  into OrcaSlicer the way Printables does, with a real filename, millimetres
  declared and a named object per solid. The container stays incapable of
  launching host programs, and no tool was added.
- **Reachability.** The link works only where Orca can reach the origin: with
  `localhost` only on the PC that runs Orca, with a LAN address only if Orca
  can reach it. From a phone, the viewer's own origin is a LAN address, but
  `FORJA_PUBLIC_URL` (used by the MCP link) is a single value for the host.
- **No profile can be preloaded.** The scheme carries only `file=`. The user's
  `Klipper_PETG_4dlab` / `PLA_VF` / `Eli_ceramic` stay a manual choice; in the
  real launch Orca opened with that profile already selected.
- **Orca asks «Objeto multipieza detectado».** For a multi-solid assembly
  whose solids are not all on the bed (Orca's heuristic for a 3MF without its
  own metadata), Orca shows a modal dialog and holds the model until it is
  answered: Sí = one object with several parts, No = separate objects. It
  recurs for any such assembly; it is a different window from Orca's main one.
  The viewer hint says so. Forja keeps the current behaviour (separate named
  objects) and does not answer for the user.
- **`valido: true` does not detect geometry loss, and Forja currently has no
  detector for it.** A proper one would need a pre-STEP volume recorded in the
  script subprocess and an independent estimate (for example a Monte Carlo of
  the bounding box against the B-rep volume, to also catch a fuse-time loss).
  That is its own phase, not a patch.
- **The 3MF is not byte-deterministic**: its zip members carry their creation
  time, so two downloads differ in `sha256` while the content of every member
  is identical. Harmless (the route sends `no-store`). Backlog: pin
  `ZipInfo.date_time`.
- **Cost of a token-free GET.** The cap bounds serialisation, not STEP
  tessellation, so a download of the largest document can cost about 15 s and
  ~760 MB while holding that document's lock. `/malla` tessellates identically
  with no cap at all, so the exposure is not new. `HEAD` builds and discards
  the whole body (the same cost as `GET`), deliberately kept so `curl -I`
  works; a cheap `HEAD` would answer 200 where `GET` answers 400 or 413.
- **Pre-existing gaps found on the way and not fixed here**:
  - `exportar` writes to disk through a token-free GET.
  - On a corrupt STEP (an empty shape) the new route answers 400, while
    (measured on 2026-10-02 on a corrupt STEP: `/malla` answers 200 (an empty mesh) with or without a sidecar; `exportar` with `formato=stl` answers 200 in both cases; `exportar` with `formato=3mf` answers 400 (sidecar mismatch) when a `solidos.json` exists and 200 when it does not).
  - Uploading an open (non-watertight) STL fails with 422 "No module named
    'networkx'" on the live container (a closed tetrahedron uploads fine).
  - A zero-area-triangle STL passes the face-count emptiness check and is
    served (as it is by `exportar`).
