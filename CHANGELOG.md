# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to phase-based development (see `plans/forja-plan.md`).

## [Unreleased]

### Added (G0/G1 — git como historial, ADR-0014)

- G0: reconstrucción determinista verificada en los 5 documentos con script (2 builds, STEP normalizado idéntico byte a byte); regla de comparación para G3 en `plans/maestro/fases/G0-resultados.md`.
- `app/git_store.py`: un repo git bare por documento en `.repos/{id}.git`, solo plumbing, sin shell, sin hooks, sin red, `safe.directory` acotado, timeouts, nombres validados. `git` en la imagen.
- `app/versioning.py` usa git por debajo con el mismo contrato (`{id, fecha, mensaje}`, mismos ids de 12 hex, mismos bytes); autor neutro `agente`/`humano`/`forja` por la cabecera `X-Forja-Origen` (el cliente MCP envía `agente`) o `Sec-Fetch-Site`; nuevo `versioning.listar_commits`.
- `python -m migrar_historial [--aplicar]`: migra `.historial/` a commits (dry-run, idempotente, verificación byte a byte, marcador, mueve a `.historial.migrado/`). Aplicado: 122 instantáneas de 11 documentos, 1,40 GB → 156 MB.
- ADR-0014 sustituye a ADR-0006. Pruebas: `tests/test_git_store.py` (31).

### Added (fdm-D — material/filamento por pieza con nombre)

- Scripts: dict literal `MATERIALES = {"tapa": "PETG negro", "junta": {"material": "TPU", "color": "#202020", "extrusor": 2}}` a nivel de módulo, leído con `ast.literal_eval` (nunca se ejecuta en el proceso web) y validado antes de correr (texto 1–64, color `#RRGGBB`, extrusor 1..16; 422 sin crear nada). Nombres que el script no produce se descartan y se informan en `materiales_ignorados` (aditivo).
- `app/materiales.py` + sidecar `{id}.materiales.json` (escritura atómica) con `{materiales, declarado}`: re-ejecutar con la MISMA declaración conserva las ediciones manuales; una declaración distinta las reemplaza; un script sin `MATERIALES` no toca lo guardado. Viaja en cada instantánea de geometría como `materiales.json`; `restaurar` lo repone (o lo quita si la instantánea es anterior); borrar el documento lo elimina.
- REST: `GET /documentos/{id}/materiales` → `{materiales, piezas}`; `POST` (token) `{materiales: {pieza: material|null}, reemplazar?}` sin re-ejecutar, con instantánea previa y evento `anotaciones_actualizadas` `["materiales","historial"]`. `GET /documentos/{id}` agrega `materiales` solo si hay alguno.
- 3MF (descarga completa, por pieza y `exportar`): `<basematerials>` del estándar (`name` + `displaycolor`) referenciado por `pid`/`pindex` de cada objeto, y `Metadata/model_settings.config` estilo Orca/Bambu con `name` y `extruder` por objeto (más `forja_material` informativo). Sin materiales el paquete es idéntico al anterior.
- MCP (sin herramientas nuevas): `resumen_documento` muestra `materiales`; `parametros(id, materiales={...})` los cambia sin re-ejecutar.
- Visor: piezas coloreadas con su color de material, muestra del color en la lista y formulario material/color/extrusor en el detalle de la pieza (guarda por POST). `materiales.js?v=1`, `pieces.js?v=9`, `viewer.js?v=12`, `tabs.js?v=19`, `app.js?v=21`, `forja-base.css?v=12`.
- Pruebas: `tests/test_materiales.py` (22), `tests/web/materiales.test.mjs` (3).

### Added (fdm-C — arreglos automáticos de diseño FDM)

- `app/fdm_ops.py`: `agujero_gota(d, largo, eje, centro)` (techo en punta a 45°, diámetro compensado como agujero horizontal), `chaflan_base(pieza, alto)` (aristas de la cara apoyada; si falla devuelve la pieza intacta + aviso), `puente_sacrificio(d, z, centro)` (disco de 1 altura de capa), `partir_para_cama(pieza, cama, union="pasadores"|"cola_milano", nombre)` (cortes por planos, giro 90° en Z si ahorra partes, hasta 2 pasadores por cara de corte con agujeros compensados `eje_presion`/`eje_deslizante` —gota si horizontales— y pasadores de pie al lado; cola de milano de 15° para cortes X/Y). Sin `voladizos_a_45`: no robusto en B-rep.
- Sandbox: `bootstrap._inyectar_fdm_ops` inyecta esas funciones ligadas al `PERFIL` + `AVISOS_FDM` con `setdefault` (requiere `docker compose build`).
- `check_fdm`: campo aditivo `sugerencias: [{pieza, funcion, motivo}]` (solo si hay): agujero redondo horizontal → `agujero_gota`; no cabe en ninguna orientación → `partir_para_cama`.
- MCP: descripción de `ejecutar_script` y `check_fdm` (solo texto).
- Pruebas: `tests/test_fdm_ops.py` (11).

### Fixed (fdm-B — ronda de revisión)

- `cupon._caja_explicita`: rechaza con 422 valores no finitos (NaN/±Infinity en JSON crudo) y lados de más de 1000 mm (`LADO_MAX_CAJA_MM`).
- Colocación en cuadrícula (`cupon.colocar`): filas que envuelven a 220 mm en X (`ANCHO_FILA_MM`), avanzan en Y con 5 mm de separación, centradas en una cama de 220×220 cuando caben. Respuesta con `avisos: [...]` (aditivo) si una pieza o el conjunto no cabe en 220×220. Sin reorientar a propósito: un eje tumbado pierde redondez y falsea la prueba de encaje.
- Lectura del STEP y `solids.cargar` bajo `parametros.bloqueo(doc_id)`; el candado se suelta antes de los booleanos (trabajan sobre la copia en memoria).
- Visor: el aviso de progreso dice que puede tardar hasta ~2 min. `cupon.js?v=2`, `tabs.js?v=18`, `app.js?v=20`.
- Pruebas: +5 en `tests/test_cupon.py` (NaN, Infinity, -Infinity, lado 1500 → 422; cuadrícula + avisos).

### Added (fdm-B — cupones de prueba)

- `app/cupon.py`: `POST /documentos/{id}/cupon` (token) con `{pieza?, holgura_max? (1 mm, 0–5), margen? (4 mm, 0–20), caja? {min, max}}`. Detecta parejas de sólidos de distinto nombre a menos de `holgura_max` (prefiltro de cajas de `checks/distancia.py` + `Shape.distance`), calcula una caja de interés por pareja (solape + margen; un lado se completa hasta el final de la pareja solo si quedaría menos de `margen` fuera, así un buje conserva su pared y el eje se corta a su largo), fusiona las cajas que se solapan, recorta cada pieza y las deja sobre la cama (z = 0, en fila en X, orientación original) como `cupon_<pieza>` (`_zN` con varias zonas) en un documento NUEVO «Cupón — <nombre>». Responde `{id_nuevo, nombre, descarga, piezas: [{nombre, de, bbox}], zonas: [{piezas, pares: [{a, b, dist}], caja}]}` (+ `zonas_mas`, `errores`). El original no se toca. `documents.crear_documento_desde_formas` registra un documento desde formas build123d en proceso.
- MCP: herramienta nueva `cupon` (20 herramientas; añade `url_descarga`/`enlace_orca`). Tests de inventario actualizados a propósito.
- Visor: botón «Cupón de prueba» en la barra del documento (usa la pieza seleccionada), abre el documento nuevo en una pestaña y ofrece «Abrir en Orca». `cupon.js?v=1`, `tabs.js?v=17`, `app.js?v=19`.
- Pruebas: `tests/test_cupon.py` (6) y `tests/web/cupon.test.mjs` (4).

### Fixed (fdm-B — revisión de fdm-A)

- `perfil_desde_mediciones`: 422 si el ajuste sale de |a| < 2 mm / |b| < 0.5 (u offset de ranuras ≥ 2 mm); descarta claves desconocidas de `mediciones`; `material` limitado a 40 caracteres.

### Added (fdm-A — perfil de tolerancias de la impresora)

- `app/perfil_fdm.py` (sin dependencias): compensación `agujero(d)`, `eje(d)`, `ranura(w)` y `ajuste(nombre, d=None)` (`M2`…`M5` `_pasante`/`_roscado`, `eje_deslizante`, `eje_presion`); ajuste lineal por mínimos cuadrados del offset sobre las mediciones (`a + b·d`). Perfil semilla Ender-3 V3 SE / 0.4 / PLA marcado `semilla: true`.
- `app/perfiles.py`: perfiles en `documentos_data/.perfiles/<nombre>.json` (escritura atómica tmp+replace) y perfil activo en `.perfiles/.activo`; rutas `GET /perfiles`, `GET /perfiles/{nombre}` (`activo` = alias), `POST /perfiles` (token; refit desde `mediciones`, `activar`) y `POST /perfiles/probeta` (token) que crea el documento «Calibración de tolerancias» (74×54 mm: agujeros 3/5/8 verticales y horizontales, ejes 3/5/8, ranuras 2/3/5, holguras .1–.5 con pin suelto de 5 mm, rótulos en relieve de 7 segmentos).
- Sandbox: cada script recibe el perfil activo como global `PERFIL` (un dict propio o el nombre de un perfil en `variables["PERFIL"]` lo sustituye) y las funciones `agujero`/`eje`/`ranura`/`ajuste`. Documentado en `ejecutar_script` (MCP) sin cambiar su contrato.
- Visor: botón y diálogo «Perfil de impresora» (resumen del activo con medidas a modelar, generar probeta, capturar mediciones). `app.js?v=18`, `perfil.js?v=1`, `forja-base.css?v=11`.
- Pruebas: `tests/test_perfiles.py` (10) y `tests/web/perfil.test.mjs` (2).

### Added (F11.1–F11.2 — visor en vivo)

- Revisión estable de cada documento, incluida en fichas y mallas; el encabezado STEP variable no provoca una actualización falsa.
- Canal SSE `/eventos` con estado inicial, avisos de construcción y cambios de geometría, notas y ensamble. El visor actualiza la pestaña abierta conservando cámara, selección y paneles; se recupera tras una desconexión.
- Indicador de construcción, error y conexión; los cambios de documentos inactivos se aplican al volver a su pestaña y la biblioteca abierta refleja altas y bajas.
- Pruebas de revisión, canal de eventos, identidad de malla y planificación de recargas del navegador.

### Security (F0.1 + F0.2 — token out of reach, scripts in `forja-sandbox`)

- **F0.1**: `README.md` and `AGENTS.md` no longer tell anyone to read the token;
  every `forja_token` mention is now a prohibition (`grep -n forja_token README.md
  AGENTS.md`). The skill patch (`plans/maestro/parches/skill-forja-sin-token.md`)
  stays pending the user (P-1).
- **New service `forja-sandbox`** (ADR-0013): same image, uid 65534,
  `network_mode: none`, read-only root, `tmpfs /tmp`, `pids_limit 128`,
  `cap_drop ALL`, `no-new-privileges`, only the `staging` volume. Its PID 1 is
  `app/sandbox/ejecutor.py` (non-dumpable, Unix socket, `posix_spawn` per job,
  kills the job's group and sweeps escaped processes); every child starts in
  `app/sandbox/bootstrap.py` (`umask 077`, `RLIMIT_DATA` 1.5 GB, `RLIMIT_CPU`
  timeout + 10 s, `RLIMIT_FSIZE` 512 MB, `oom_score_adj 1000`, allow-listed env
  with BLAS/OMP at 1 thread). No `preexec_fn`, no `RLIMIT_NPROC`.
- `app/scripts_runner.py` no longer runs anything locally: it writes the job to
  `staging/trabajos/<id>/`, asks the executor over the socket and reads the
  outputs as untrusted data (`lstat` single-link regular file, `O_NOFOLLOW |
  O_NONBLOCK` on the same inode, size caps, streamed copy). Same return
  contract and compact errors; new compact messages for CPU/size limits and
  for an unreachable sandbox.
- **`app/entrypoint.sh`** (image `ENTRYPOINT`): as root, moves the legacy token
  to `/run/secrets/forja_token` (named volume `secretos`, 0400, uid 1000) without
  printing it; migrates `documentos_data` to uid 1000 once (`--dry-run`, marker
  `.migrado-uid`, `chown -h` of the listed paths only, no `-R`, contents
  untouched); prepares `staging`; then `setpriv` to uid 1000 for uvicorn. Live
  run: 301 paths (79 dirs, 222 files, all `0:0`, no hard links), 228 files with
  identical sha256 before/after.
- `app/auth.py` reads the secret first (`FORJA_SECRET_FILE`), then
  `FORJA_TOKEN`, then the legacy file; `tests/conftest.py` points the secret at
  a missing path so the in-process app keeps the test token, and reads the live
  token from the secret for live tests (`-s`, `--pdb` and `log_cli` do not mask
  it, noted in its docstring).
- Compose healthchecks for both services; port bind unchanged; the 19 MCP tools
  and the REST API unchanged.
- `tests/test_sandbox.py`: the 11 penetration cases of the F0a card (+ gateway
  IP, 3 GB allocation, uid/env, bad requests), Box − Cylinder + export, named
  solids, `import build123d` < 5 s under the limits, latency, and unit tests
  of the output reader (symlink, FIFO, hard link, oversize).
- Latency of the hop: median 2.51 s vs 2.80 s before (no added cost).

### Security (F0.2 fix round — review NEEDS_REVISION)

- **Executor socket** (`app/sandbox/ejecutor.py`): the accept loop re-checks
  the socket once a second (`lstat`: same inode, socket type) and binds again
  if a script removed or replaced it; it also restores 0666/0755, and after
  every job, before answering, everything else in `/staging/ejecutor` is
  removed and both modes are restored. `ejecutar` is only accepted from a
  peer with another uid (`SO_PEERCRED`), so a script cannot queue jobs.
- **Children confined with Landlock** (`app/sandbox/bootstrap.py`, no
  capabilities needed, ABI 8 here): writes/removals/renames/node creation only
  beneath the job's own `salida/` and its private `HOME` (= `TMPDIR`), plus
  `/dev/null`; signals and abstract sockets scoped to the job (ABI >= 6). The
  child cannot touch the socket, another job's `salida/`, or signal PID 1.
- **No leak between jobs**: the job dir travels on the child's stdin, never in
  argv (`/proc/<pid>/cmdline`); the child marks itself non-dumpable; its
  `HOME` name carries no part of the id; the executor runs **one job at a
  time** (the queue wait counts against the job's timeout) and
  `scripts_runner` queues its own calls (up to 300 s, then "sandbox
  ocupado"). The "128 bits" claim is corrected in ADR-0013 and
  `protocolo.py`: the id is not the boundary.
- **SIGTERM to PID 1**: SIGTERM/SIGINT are blocked and collected by a
  `sigwaitinfo` thread; the executor only stops when `si_pid == 0` (sent from
  outside, `docker stop`: 0.3 s, exit 0). `os.kill(1, SIGTERM)` from a script
  is refused (Landlock) or ignored.
- **Per-job write cap**: a watcher thread kills the job's group if `salida/`
  exceeds 600 MB or 10 000 entries, cannot be measured, or the staging volume
  drops below 2 GB free. Cleanup of `salida/`, `HOME`, `/tmp` and the socket
  dir now works by dir fds, iteratively, chmod-ing our directories first: a
  mode-000 directory no longer survives (nor leaks the job dir).
- **`app/entrypoint.sh`**: `chown -h` everywhere, startup refused if
  `$STAGING`, `$STAGING/trabajos`, `$STAGING/ejecutor` or the secret is a
  symlink, temp files (`.migrado-uid`, the secret) via `mktemp` in the target
  directory, and `setpriv … --bounding-set=-all --no-new-privs` (live:
  uvicorn `NoNewPrivs 1`, `CapBnd 0`).
- `tests/test_sandbox.py`: +12 focused tests (socket unlink/creation refused,
  socket deleted or replaced is re-bound, chmod restored before answering,
  two concurrent jobs cannot see or touch each other, SIGTERM/SIGINT to PID 1,
  `ejecutar` refused from a script, mode-000 tree cleaned, write-cap watcher,
  deep-tree cleanup). Latency of the hop: median 2.42 s / 2.52 s.

### Not shipped (F0.2)

- HMAC-signed download URLs (step 6 of the card): would change `exportar`'s
  output and the download route, and closes nothing until the bind decision
  (P-2). Deferred, see ADR-0013.

### Added (Phase 10 — open in OrcaSlicer from the browser, token-free download)

- **`GET /documentos/{id}/descarga/{slug}.{stl|3mf}`** (`app/documents.py`,
  `descargar_documento`; `HEAD` too): a document as STL or 3MF, built in
  memory (nothing is written under `documentos_data`). OrcaSlicer's
  `orcaslicer://open?file=<url>` downloader names the saved file after
  whatever follows the last `/` of the decoded URL, query string included, so
  the real filename has to be in the path; it also sends no headers, so the
  route is **token-free** (same exposure as `/malla` and `/exportar`; ADR-0011).
  The tail must fully match `[A-Za-z0-9_-]{1,64}\.(stl|3mf)` and the real
  `scope["path"]` must end exactly with it; the `Content-Disposition` name
  comes from the document's own name (`_slug_descarga`: ASCII, at most 64
  characters, `modelo` when empty), never from the request. Statuses: 404 for
  a bad tail, any non-empty query string, an unknown or over-long id (at most
  128 characters and 128 UTF-8 bytes) or a missing file; 400 for a document
  without geometry (a corrupt STEP imports as an empty shape instead of
  raising) or on-disk drift, with the same message `exportar` gives for a
  stale `solidos.json`; 413 above `MAX_TRIANGULOS_DESCARGA = 1_000_000`
  triangles (measured: the largest real document is 646,158); 500, logged with
  its traceback and with no exception text in the body, only for a real
  defect. The id is validated before the per-document lock is taken and the
  registry is re-checked inside it. Headers: `attachment; filename="<slug>.<ext>"`
  (quoted ASCII), `model/stl` / `model/3mf`, `Cache-Control: no-store`.
  A bare trailing `?` (`x.stl?`) is invisible to the application under uvicorn
  (identical scopes under httptools and h11), so it cannot be rejected; the
  link builders never emit one.
- `app/export.py` gained `objetos_3mf` and `bytes_3mf` so the route and
  `exportar` share one object-building function and the 3MF can be produced
  in memory; `escribir_3mf` keeps its signature and output.
- **Viewer**: three anchors after the existing toolbar buttons, **Abrir en
  Orca** (`orcaslicer://open?file=` + the URL percent-encoded once),
  **Descargar 3MF** and **Descargar STL** (`app/web/static/js/orca.js`, built
  from `window.location.origin`, the slug rule mirrors the server's and is
  tested against 33 names derived from the real function). Clicking "Abrir en
  Orca" shows a Spanish hint in `#fj-feedback` (Firefox's one-time prompt
  for the protocol, Orca's «Objeto multipieza detectado» question: Sí = un solo
  objeto con piezas, No = objetos separados, and the «Descargar 3MF» fallback)
  and never calls `preventDefault`.
- **MCP `exportar`** (no new tool, still 19) returns `descarga`, `url_descarga`
  and `enlace_orca` for a single-file `stl`/`3mf` (absent for `step`,
  `por_solido` and errors), composed in `mcp_server/client.py` from
  `FORJA_PUBLIC_URL` (read at call time, default `http://localhost:8710`,
  `http|https://host[:port]` only; an invalid value falls back to the default
  without raising and without logging the value). The agent opens the link
  from the host shell with `xdg-open '<enlace_orca>'`; the container launches
  nothing. `docker-compose.yml` gained the `FORJA_PUBLIC_URL` line.
- Real acceptance launch on this machine (2026-10-02): `xdg-open` started the
  Flatpak from cold, Orca's own log shows the download from the route, the
  file landed in `~/Descargas`, the 3MF declared millimetres with three named
  objects, and Orca opened with the user's own profile selected.
- Tests: Python **459 passed** (293 before this phase + 88 in
  `tests/test_descarga_orca.py` + 78 in `tests/test_enlace_orca.py`); JS
  **21 passed** with `node --test tests/web/*.test.mjs` (13 in the new
  `tests/web/orca.test.mjs`; the literal `node --test tests/web` fails on
  Node v22 with MODULE_NOT_FOUND, use the glob).
- ADR-0011 records the downloader contract, the token-free route, the error
  and query-string policies, the no-host-launch decision and the rejected
  safeguard below.

### Changed (Phase 10)

- `exportar` responses are additive: `descarga` (the relative download path) on
  the single-file `stl` and `3mf` responses of the backend, plus
  `url_descarga` and `enlace_orca` on the MCP side. No existing key changed.
- Module cache-bust chain for the viewer (the static server sends no
  `Cache-Control`, so a heuristically cached module is never refetched):
  relative to `main`, `tabs.js` imported `orca.js` unversioned and now imports
  `orca.js?v=2`; `app.js` imported `tabs.js?v=8` and now imports `tabs.js?v=10`;
  `index.html` loaded `app.js?v=9` and now loads `app.js?v=11`.
- One CSS rule, `a.fj-btn { text-decoration: none }`, so the anchors look like
  the buttons.

### Not shipped / known gaps (Phase 10)

- **`aviso_volumen` was built and dropped.** A non-failing warning on
  `exportar` when the mesh volume disagreed with the recorded volume was
  implemented in `2440c09` and reverted in `6c5fd30`. The `solidos.json`
  volumes are measured on the re-imported STEP on every path that writes them,
  so a sidecar-versus-reimport check cannot see a STEP round-trip loss (exact
  0 relative difference on all 6 real STEP documents); the mesh check was
  silenced by its own watertight guard on 5 of the 6 (per-face tessellation
  leaves T-junction cracks); and the real 37% `casa_del_arbol` loss could not
  be reproduced with the current script, and may have happened inside
  OpenCascade's fuse. **`valido: true` does not detect geometry loss, and Forja
  currently has no detector for it**; a proper one needs a pre-STEP volume
  recorded in the script subprocess plus an independent estimate (a Monte Carlo
  of the bounding box against the B-rep volume): its own phase.
- The link works only where Orca can reach the origin (`localhost` only on the
  PC that runs Orca). The scheme carries only `file=`, so **no machine,
  process or filament profile can be preloaded**.
- Orca shows a modal «Objeto multipieza detectado» question for multi-solid
  assemblies whose solids are not all on the bed and holds the model until it
  is answered (observed in the real launch); the viewer hint explains it.
- The 3MF is not byte-deterministic (zip members carry their creation time; the
  content of each member is identical). Backlog: pin `ZipInfo.date_time`.
- The triangle cap bounds serialisation, not STEP tessellation: a token-free GET
  of the largest document can cost about 15 s and ~760 MB while holding the
  document lock (`/malla` tessellates identically with no cap). `HEAD` builds
  and discards the full body, deliberately, so `curl -I` works.
- Found on the way, pre-existing and not fixed here: `exportar` writes to disk
  through a token-free GET; on a corrupt STEP the new route answers 400, while
  (measured on 2026-10-02 on a corrupt STEP: `/malla` answers 200 (an empty mesh) with or without a sidecar; `exportar` with `formato=stl` answers 200 in both cases; `exportar` with `formato=3mf` answers 400 (sidecar mismatch) when a `solidos.json` exists and 200 when it does not); uploading an open (non-watertight) STL fails
  with 422 "No module named 'networkx'" on the live container; a zero-area-
  triangle STL passes the face-count emptiness check.

### Added (Phase 9 — document gallery)

- Independent gallery with searchable preview cards; quick document list retained.
- Confirmed, token-protected deletion from both views, closing deleted tabs.
- Automatically open the most recently modified STEP unless an explicit document URL is supplied.

### Added (Phase 8 — named component tree)

- Searchable piece tree with selection highlighting, per-piece visibility,
  frame selection, reversible isolation and show-all controls per document.
- Picking a visible component in the 3D view selects the corresponding tree
  entry. Hidden components are excluded from note raycasts.
- Optional `/malla?componentes=true` binary envelope carries the canonical
  STL and validated per-piece triangle ranges together. Default STL transport
  remains unchanged; shared vertices avoid duplicating geometry per piece.
- STEP names are checked against imported solids; uncertain names and
  ambiguous face ownership are surfaced. STL documents show one complete
  mesh instead of invented component names.

### Added (Phase 7 — CAD workspace usability)

- Actionable welcome screen, searchable document picker and opening/import
  progress with non-blocking errors.
- Per-document camera presets, framing, grid and wireframe controls; mouse
  navigation hints and keyboard-accessible document tabs.
- Responsive inspector and compact XYZ dimensions in the status bar.

### Fixed (Phase 7)

- Hidden empty-state and parameter controls no longer override the HTML
  hidden attribute. Closing the last tab also closes its inspector.
- Assembly panel opens from its inspector tab and `?panel=ensamble`.
- Failed document initialization removes its partial tab so opening can retry.

### Added (Phase 6 — assemblies, local-service bridges, 19 MCP tools)

- **Assemblies** (`app/assemblies.py`, `app/assembly_routes.py`): the named
  solids of a STEP document are linked by `articulaciones` of `tipo` `fijo`
  (fixed), `giro` (revolute) or `deslizamiento` (prismatic). A joint carries
  `padre`/`hijo` (solid names), `origen` and `eje` given in the document's
  **rest frame** — the world frame of the immutable rest STEP, before any
  ancestor motion — plus `limites` and a `valor`; a child's motion is applied
  in rest coordinates and every ancestor's motion is inherited on top. A
  joint may also be pinned to a face or an edge of its parent/child with a
  Phase-4 reference (`referencia_padre`/`referencia_hijo`): the reference is
  re-resolved against the live shape when the assembly is defined and
  refused when it is lost or ambiguous, never silently rebound. Validation
  refuses a repeated joint id, a solid with two parents, a parent cycle, a
  null axis, a value outside `limites`, and unknown keys (so no English key
  can be persisted).
- `GET /documentos/{id}/ensamble` (compact read), `POST …/ensamble` (token;
  definition only — initial values must be zero, defining never moves the
  geometry), `POST …/ensamble/pose` (token; an **absolute** pose
  `{articulación: valor}`, snapshotted before and committed under the same
  document id) and `DELETE …/ensamble` (token; removes the joints keeping
  the current position). A geometry that changed since the rest pose marks
  the assembly `obsoleto` (the rest STEP's digest is stored) and a pose then
  answers 409 `la geometria cambio; revisar y redefinir las articulaciones`.
- **Assembly state persists** under `documentos_data/.ensambles/{doc_id}/`
  (`state.json` + `base.step`, the rest STEP a pose is applied to), and is
  carried *through* `.historial/`: `documents.py` gained
  `_con_ensamble_snapshot` (mirroring `_con_parametros_snapshot`), so every
  geometry snapshot it builds carries `ensamble.json`/`ensamble_base.step`
  and a restore routes them back through `assemblies.restore_files` —
  restoring a snapshot older than the assembly removes it (a later pose then
  answers 422), restoring a geometry one brings it back with its values, and
  a notes-only snapshot leaves the live assembly untouched. `DELETE
  /documentos/{id}` clears it too. No restore writes `ensamble.json` or
  `ensamble_base.step` into the documents root any more — they used to land
  there, where `recargar_documentos()` would register `ensamble_base` as a
  phantom document after a restart.
- **State schema marker `version: 2`** (`assemblies.FORMAT_VERSION`): the
  Spanish rename below changed the persisted vocabulary, so a `state.json`
  written by an older build is refused explicitly (`EstadoIncompatible`, a
  `ValueError` subclass raised by `_estado_compatible` inside `load`)
  instead of blowing up later as `KeyError: 'articulaciones'` — every read
  path answers **409** `ensamble en formato anterior; quitar las
  articulaciones y redefinirlas`, and `restore_files` refuses to plant one
  (pre-flighted in `_restaurar_documento_bloqueado`, so the STEP is not
  already replaced when the refusal arrives). `DELETE …/ensamble` is the
  recovery path: it never reads the state. There is nothing to migrate.
- **Viewer panel**: the per-viewport toolbar button `Ensamble` is built in
  `app/web/static/js/tabs.js` (the fourth side-panel tab in
  `app/web/index.html`), and `app/web/static/js/ensamble.js` holds the
  panel's controller and renderer — listing the document's joints, defining
  them from a picked face/edge reference and driving the pose. Both go
  through the same REST endpoint as the MCP tool.
- **Read-only bridges to local services** (`app/bridges.py`, `/puentes`):
  `GET /puentes` reports `{habilitado, disponible[, mensaje]}` per service,
  so an agent can skip a doomed call. The suspension simulator is reached
  over HTTP on `:8683` through a hard-coded (method, path) allowlist — only
  `GET /salud`, `GET /config`, `POST /pose`, `POST /punto`, all of them pure
  computation, none of the simulator's mutating routes — with the base URL
  taken from `FORJA_SUSPENSION_URL` only, redirects not followed, a 256 KB
  body cap and an 8 s timeout. The Blender JSON socket (`:9877`) and
  kybercore are feature-flagged **off** by default
  (`FORJA_{SUSPENSION,KYBERCORE}_{ENABLED,URL}` and
  `FORJA_BLENDER_{ENABLED,HOST,PORT}`). A bridge that is switched off is
  refused with **503** `puente <servicio> desactivado` *before* any
  connection is attempted; a down, oversized or malformed upstream the call
  did reach degrades instead to a Spanish **502** `no se pudo consultar
  <servicio>` — never a 500 and never a traceback, in either case.
  `docker-compose.yml` gained
  `extra_hosts: ["host.docker.internal:host-gateway"]`: without it
  `host.docker.internal` does not resolve inside the container and every
  bridge call fails.
- **MCP tools** `ensamble(id, articulaciones=None, valores=None,
  quitar=False)` (one tool with mutually exclusive modes — read → `{id,
  piezas, articulaciones, valores, obsoleto}`, define, pose, remove; two
  modes at once is refused without touching the document),
  `suspension(subida_mm, cabeceo_deg, articulacion_deg, crudo=False)`
  (omitted milestones filled from `/config`, never persisted; exception-
  first; `fuera_de_carrera` derived from the observed `L` against `limites`
  **or** the upstream flag, so a wrong flag can only add a row, never hide
  one; a non-finite number or an unexpected shape from the upstream is
  refused as `{error, mensaje}`, and `signo_correcto_al_subir` is omitted
  rather than invented when nobody measured it) and `puentes()`. The surface
  is now **19 tools**, matching `crear_servidor()`,
  `~/.claude/skills/forja/SKILL.md`, `README.md` and
  `.claude/project-context.md` (Phase 3's no-drift contract).
- **Spanish wire vocabulary** for the assembly payload (`{id, piezas,
  articulaciones, valores, obsoleto}`; joint `{id, tipo, padre, hijo,
  origen, eje, limites, valor}`; reference `{tipo, id, punto}`) — the only
  payload in the project that still mixed English keys (`joints`, `values`,
  `stale`) with a Spanish one. The internal `huella` fingerprint is still
  required and validated when a joint is defined (it is how a reference is
  proven to belong to that solid) but is no longer echoed in any read
  response — on the fixture the rename was measured against, a joint
  carrying a face reference went from 528 to 376 characters (a like-for-like
  re-measurement gives 531 → 389; the pair is fixture-dependent).
- `docs/decisions/0010-assembly-persistence-and-bridges.md` (Nygard): rest
  pose + absolute joint values + stale-by-digest; assembly state outside
  `.historial/` but carried *through* snapshots; the bridge allowlist, the
  feature flags and the envelope limits; the per-document lock now shared by
  `documents.py`, `notes.py` and `assembly_routes.py` with its
  head-of-line-blocking residue; and why a `version: 1` state is refused
  rather than migrated. ADRs 0001–0009 untouched.
- Tests: `tests/test_assemblies.py`, `tests/test_assembly_routes.py`
  (definition/validation/pose plus the restore, delete and token-guard
  lifecycle), `tests/test_bridges.py` (allowlist, degradation, envelope) and
  `tests/test_colisiones_casquillos.py` (the committed twin of the
  mostertruck pilot: a bushing pressed into a housing, its cylindrical
  regression, two prisms that tile the same box, and the nut resting on an
  end face). Suite at the close of the phase: **285 passed / 0 failed**,
  from `6 failed / 227 passed` at its start.

### Fixed (Phase 6)

- **`check_colisiones` answered `{"choques": [], "ok": true}` for a real
  overlap** (`app/checks/colisiones.py:83`, and the same gate at
  `app/percepcion.py:537`). Pairs were skipped with
  `if separacion > 0.0`, but OpenCascade does not return an exact zero for
  solids that genuinely intersect: a cylindrical pin pressed straight
  through a block measures **3.0678e-16 mm**, so a **2261.9467 mm³**
  intersection was read as a gap by `check_colisiones` and stayed hidden
  from `percibir`'s `CHOQUE` lines. Both gates now share a single
  `TOLERANCIA_DISTANCIA_CERO_MM = 1e-9` mm (defined once in `colisiones.py`,
  imported by `percepcion.py`) and the exact boolean plus `tolerancia_mm3`
  remain the only classifier: the real contacts measured while reproducing
  the pilot (2.3867e-12 mm, 0 mm³) sit 419× below the epsilon and stay
  contacts, and two prisms that tile the same box (identical bounding boxes,
  zero intersection) stay unflagged. Found while reproducing the
  mostertruck pilot — a pre-existing bug in shipped Phase-5 code, pinned
  first as a strict `xfail` and fixed in the same branch.
- **Unauthenticated memory growth in `app/notes.py`**: the notes/trazos
  routes are token-free by design, and `parametros.bloqueo()` creates and
  keeps one `threading.Lock` per document id it is asked about, so an id
  reached the lock table *before* being validated — 300 anonymous requests
  with attacker-chosen ids added 300 permanent entries (an over-long id also
  made the filesystem probe raise `OSError` → 500 with a logged traceback; a
  63-emoji id was a live reproduction). The document is now validated before
  the lock is taken or created (`_doc_id_valido`: at most 128 **characters**
  and 128 **UTF-8 bytes**, then a fail-safe probe), so an unknown or
  over-long id answers 404. The lock table is still never evicted on delete —
  handing a fresh lock to a document another live request still holds would
  destroy the mutual exclusion these routes rely on.
- `DELETE /documentos/{id}` now removes the per-solid export directory
  `exportados/{doc_id}/` (`_borrar_directorio_exportado`, with a
  resolved-parent guard): the cleanup glob only matched `{doc_id}.*`, so
  that directory outlived every document that ever exported `por_solido`.
- A non-finite number in an upstream bridge body (`Infinity`/`NaN`, or a
  literal such as `1e999` that overflows to one) is now refused while
  parsing, so the bridge's own `except … → 502` catches it: the API layer
  cannot serialise an infinite float and used to answer 500 with a traceback
  where the bridge promises a 502 `no se pudo consultar suspension`.

### Changed (Phase 6)

- The **Phase-5 final review is closed**. Its fixes (`7f186c5`) had reached
  `main` unreviewed — the reviewer died on the weekly rate limit — so a
  review-only round ran first: every functional claim was CONFIRMED under
  adversarial probes (the notes-only-restore gate, the obsolete timeout
  cleared on script replacement, the document lock genuinely shared by
  script/params/notes/restore/delete, the MCP image wire,
  `FORJA_HEAVY_TIMEOUT` without retry), and the open lock question was
  answered with a re-run rather than an opinion — `parametros.bloqueo`
  **cannot self-deadlock** (it is acquired only at route entry and every
  helper reachable while holding it is lock-free; it is not reentrant, so
  safety rests on that call-graph property) and the residual risk is
  head-of-line blocking bounded by the script timeout, not a hang. Its
  **three** required changes are the notes/trazos id validated before the
  lock (the lock-table growth and the over-long-id 500) and the export
  directory removed with its document — the two `Fixed` entries above. The
  optional item it also raised, the export sidecars written outside the
  document lock, was not taken: `exportar` still runs without the lock. The
  same finding's residual in the token-guarded routes — the id was still
  checked *after* taking the lock in the three `assembly_routes.py`
  mutations — was closed in 6.3.
- The Phase-6 routers are mounted in `app/main.py`; `/mcp` stays POST-only
  (405 on `GET`/`DELETE`) and is appended last.

### Fixed (Phase 5 final review — 2026-09-28)

- Notes-only restores preserve the current parametric script and named solids;
  complete geometry snapshots still restore their own parameter state.
- Script replacement, parameter changes, annotation writes, restore and deletion
  share the document lock. Replacing a script clears obsolete timeout overrides.
- MCP captures now return image content without attempting JSON serialization
  of the SDK Image wrapper. Heavy read operations use `FORJA_HEAVY_TIMEOUT`
  (180 seconds by default) and report timeouts without retrying operations.
- Targeted restore/concurrency/image-wire/3MF regressions: 14 tests collected
  at `7f186c5` (`tests/test_phase5_regressions.py`; 15 at HEAD).

### Added (Phase 5C — parametric scripts, params UI, FDM checks, 3MF)

- **Parametric scripts** (`app/parametros.py`): a top-level literal
  `PARAMETROS = {"alto": {"valor", "min", "max", "paso", "unidad", "desc"}}`
  (short form `{"alto": 30}` accepted), read via `ast.literal_eval` —
  never executed in the web process. Values reach the script as a
  `construir(params)` call (new in the sandbox wrapper) and/or the injected
  `PARAMS` global. Script source (or `ruta`, re-read on each change) +
  current values persist in `{doc_id}.meta.json`.
- `GET /documentos/{id}/parametros` (token-free) → `{esquema, valores}`;
  `POST /documentos/{id}/parametros` (token) re-runs the stored script in
  place through the same update path as `ejecutar_script(documento_id=…)`
  (pre-change snapshot `parametros: alto=40, …`, named solids and note
  re-resolution intact), validates min/max first (422, nothing run), and
  returns the summary + `valores` + `ms`. Per-document lock.
- MCP tool `parametros(id, valores=None)` (None = read, dict = apply).
- **Params panel** in the web viewer (`app/web/static/js/parametros.js`):
  "🎚 Parámetros" toolbar button + side-panel tab with sliders/number inputs,
  300 ms debounce, coalesced in-flight changes, keeps the camera, shows
  server/mesh/total latency; token via `api.js`'s now-exported
  `obtenerTokenSesion()`. `?abrir=…&panel=parametros` opens it directly.
- **FDM checks** (`app/checks/fdm.py`, `GET /documentos/{id}/fdm`, MCP
  `check_fdm`): per named solid, only problems — bed fit (+ "rotar 90 en Z"
  / "tumbar" suggestion), overhang area beyond `angulo_max` excluding the
  bed face (worst connected region bbox + angle), sampled thin walls
  < 2×boquilla via trimesh's pure-numpy ray intersector, first-layer
  contact area, sub-nozzle features. Capped, ≤ ~600 tokens.
- **3MF export without new dependencies** (`export.escribir_3mf`): zip +
  XML written by hand, one named `<object>` per named solid (merged
  vertices → watertight), `[Content_Types].xml`, `_rels/.rels`. Both
  `por_solido` values give ONE multi-object 3MF (what OrcaSlicer wants).
- ADR-0009: `/mcp` is POST-only; note on the shared anyio threadpool (40).
- Tests: `tests/test_parametros_fdm_3mf.py` (schema read/apply, min/max/
  unknown/non-number 422 with document untouched, 401, named solids +
  face note survive, latency, `PARAMS` style, FDM bed/rotation/60° vs 30°/
  0.5 vs 2 mm wall/base/tiny, 3MF names + volume round-trip within 1e-3);
  `GET /mcp` → 405; MCP tool list == 16.

### Changed (Phase 5C)

- `GET`/`DELETE /mcp` now answer 405 (route is POST-only); the live-backend
  probe in `tests/test_mcp_http.py` uses "POST without token → 401".
- `exportar(formato="3mf")` no longer returns 501; its response is
  `{ruta, tamano_bytes, objetos[, objetos_mas][, nombres_inciertos]}`.
- `ForjaViewer.cargarMallaSTL(buf, {conservarVista})`.
- MCP server version 0.7.0, instructions mention the params → check_fdm →
  3mf workflow.

### Added (Phase 5D — MCP transport that survives container restarts)

- **MCP over Streamable HTTP at `/mcp`** (ADR-0008), served in-process by
  the existing FastAPI app on port 8710 — no new port, no second process.
  Stateless with JSON responses: no session id to lose, so a
  `docker compose restart forja` costs at most the one call in flight
  (verified from the host: initialize + `estado`, restart, new initialize +
  `estado` OK). The stdio transport stays as a fallback.
- `/mcp` requires the shared token (`X-Forja-Token` or
  `Authorization: Bearer`), 401 otherwise; new `auth.token_coincide()`
  shared with `requiere_token`.
- `tests/test_mcp_http.py`: 401 without/with a wrong token, initialize +
  `tools/list` = 14 tools with both header styles, no `mcp-session-id`,
  live `estado` and `ejecutar_script` + `percibir` roundtrips.

### Changed (Phase 5D)

- `mcp_server/server.py`: `crear_servidor()` is the single source of the
  server metadata + 14 tool registrations, used by stdio and HTTP.
- `app/main.py`: `@app.on_event("startup")` replaced by a `lifespan` that
  reloads documents and runs the MCP session manager.
- `percibir` docstring documents its limits (`cortes_z` <= 6,
  `proximidad_mm` <= 50).

### Fixed (Phase 5D)

- `percepcion._celdas_de_seccion`: a scanline row with an odd crossing
  count (open polyline) no longer pairs the wrong crossings and fills a
  bogus span; only its edge cells are marked.

### Fixed (Phase 5E fix-review — `percibir`)

- **`cortes` lost whole thin pieces.** One `is_inside` sample per cell
  centre let features thinner than a cell vanish from the grid AND the
  legend (casa_v2 at Z=8: `mesa`, `silla_2`, `silla_4` — 2 mm legs in
  5.4 mm cells — plus `mesa_centro` and `lampara`). Coverage is now
  conservative: each solid's real planar section is computed once (new
  `kernel.b123d_kernel.seccion_plana`, OCCT `BRepAlgoAPI_Section`, the only
  OCP code, per ADR-0001) and rasterized so a cell gets a solid's letter if
  ANY of its section intersects the cell (Liang-Barsky edge/cell test + an
  even-odd scanline fill); `#` still marks 2+ solids — note that now
  includes cells where a piece merely touches a wall. Point sampling stays
  only as a per-fragment fallback if OCCT's section fails. casa_v2 cut
  request: ~5 s -> ~0.5 s. Evidence: `plans/evidencia/5e/*_v2.txt`.
- **Support detection could report a false `SIN APOYO`.** It probed only
  the footprint centre + 4 corners inset 10%, missing legs at the true
  corners and any single off-centre or line contact. Now: the same 5
  probes at a 2% inset as a fast path, then an exact fallback (the
  candidate's planar section just below the piece, intersected with the
  piece's inset footprint rectangle; sections cached per call). casa_v2
  `contactos` still ~4.2 s (< 5 s budget), same output as before.
- **Security — `/percibir` input bounds**: `cortes_z` capped at 6 values
  (`percepcion.CORTES_MAX`, 422 above), `proximidad_mm` at 50 mm
  (`percepcion.PROXIMIDAD_MM_MAX`, 422 above).
- **matplotlib is still installed in the image** even after the Phase 5E
  unpin and a clean rebuild: it is a transitive dependency of `vtk` 9.6.2,
  required by `cadquery-ocp`. No Forja code imports it (`import main` does
  not load it). Removing it would need an explicit `pip uninstall` in the
  Dockerfile — left for a decision, not done here.
- 6 new tests (155 total).

### Added (Phase 5E — `percibir`: spatial perception as text)

- **`percibir(id, capas="contactos", cortes_z=None, eje="z", resolucion=40,
  solidos=None, proximidad_mm=5.0)`**: the model perceives a scene as
  measured spatial relations instead of interpreting a picture — the user's
  framing, "como el agua que rodea el objeto" (what touches what, at what
  distance). Real motivation: a `captura` render of `casa_v2.py` (Phase 5B)
  made a coffee table look like it was resting on a rug at a glance, while
  the EXACT distance found a genuine 0.4 mm air gap underneath it
  (`check_colisiones`'s `flotantes` finding). New `app/percepcion.py` +
  `GET /documentos/{id}/percibir` (read-only, token-free like
  `/colisiones`) + MCP tool. Returns `{texto, tokens_aprox[,
  nombres_inciertos]}` — compact TEXT, one relation per line, not verbose
  JSON, targeting 300-800 tokens for a real multi-room scene.
  - **`contactos`** (default layer): per NAMED piece, a support relation
    (what it rests on and the gap `h` in mm — `h=0.0` means it genuinely
    touches, e.g. `mesa_centro  sobre alfombra  h=0.4 (NO toca)`); the
    scene's lowest/largest-footprint piece is exempt as `base` rather than
    wrongly flagged unsupported; side neighbours within `proximidad_mm`
    (capped, sorted by gap); unsupported pieces (`SIN APOYO`, reporting
    whatever IS nearest in any direction as a fallback); penetrations
    (`CHOQUE` lines) computed from the SAME pairwise sweep this layer
    already runs, sharing `check_colisiones`'s exact intersection-volume
    definition/threshold via new `checks/distancia.volumen_interseccion`
    rather than calling `check_colisiones.verificar` a second time — that
    would have meant walking casa_v2.py's 21 solids (including one large,
    many-faced house shell) TWICE with an exact OpenCascade distance call,
    which alone pushed `percibir` past its 5 s budget in an earlier version
    of this layer; `colisiones.py` itself now also calls the same shared
    helper (pure refactor, its own behaviour/tests unchanged). Support
    classification samples real material via
    `Shape.is_inside` at the candidate piece's OWN footprint (bbox centre
    + four inset corners), `d` mm below its lowest point, instead of
    trusting OCP's raw closest-points pair — that pair degenerates to
    IDENTICAL coordinates for a genuinely touching pair (verified: a box
    resting exactly on another) and, worse, can land exactly on a curved
    silhouette edge (verified against a real `casa_v2.py` cylindrical
    piece, a toilet, misclassified as `SIN APOYO` by an earlier
    closest-points-probing version even though it was resting correctly on
    the floor — no small axis-aligned nudge crosses into a cylinder's own
    wall from a point already sitting on it). The bbox-centre-based
    fallback tried in between was also rejected: it put the direction from
    a ground-floor sofa to the whole house shell as "upward", since a
    large, irregular multi-storey shell's overall bbox centre is nowhere
    near where a piece actually touches its floor.
  - **`cortes`**: one ASCII occupancy grid per value in `cortes_z`, a
    section perpendicular to `eje` (`x`\|`y`\|`z`, default `z` — a
    horizontal "water level" cut); `resolucion`-wide (10-100, default 40),
    height by the scene's own aspect ratio (capped at 30 rows); one letter
    per solid present in that cut (legend included), `.` empty, `#` where
    two or more overlap. Built by point-in-solid classification
    (`Shape.is_inside`) restricted to each candidate solid's own
    screen-space bbox window (not a full canvas scan per solid) — a
    genuine performance/coverage trade-off: at the default resolution a
    very thin feature (e.g. a 2-3 mm chair leg) can fall between sample
    points and be absent from the legend; raise `resolucion` to catch it.
  - **`huecos`**: not implemented this phase (thin walls, enclosed
    cavities) — returns a clear "no implementado aun" text; backlog item.
  - Shares its bbox-prefiltered exact-OCP-distance engine with
    `checks/colisiones.py` via new `app/checks/distancia.py`
    (`bbox_se_acercan`/`con_tope`/`distancia_y_puntos`/
    `volumen_interseccion`) rather than duplicating it — `colisiones.py`
    itself now delegates to these helpers (pure refactor, its own
    behaviour/tests unchanged).
  - Verified against the real 21-solid `casa_v2.py` dollhouse (same
    wrapper-script technique as the Phase 5B fix-review's named-solids
    demo, `casa_v2.py` itself untouched): `contactos` lists every one of
    the 21 furniture pieces with a real support (`casa` as `base`,
    everything else `sobre casa h=0.0` except `mesa_centro  sobre
    alfombra  h=0.4 (NO toca)`, the same real gap `check_colisiones`
    already found) — no false `SIN APOYO` — in ~1000 characters (~250
    tokens) and under 4 s (performance budget: < 5 s); a ground-floor and
    an upstairs Z cut both render a recognisable floor plan in ~2400
    characters combined. See `plans/evidencia/5e/`.
- **Folded in from the Phase 5B review**:
  - `captura`'s default size dropped from 800x800 to 512x512 px (MCP tool,
    HTTP route, and `mcp_server/client.py` defaults; existing tests that
    asserted an explicit size are unaffected, a new test pins the new
    default).
  - `app/render.py` triangle ceiling (`LIMITE_TRIANGULOS = 150_000`): a
    STEP document whose tessellation exceeds it gets ONE retry at a 3x
    coarser tolerance (a render only needs to look right on screen), and
    refuses with a clear Spanish message (documenting `solidos=[...]` as
    the way around it) if that still isn't enough; an STL mesh over the
    same ceiling is refused outright (no tessellation tolerance to
    coarsen — it is already a fixed mesh).
  - `matplotlib` removed from `requirements.txt`: `grep` confirmed nothing
    in `app/`/`mcp_server/` imports it any more (Phase 5B fix-review 2's
    z-buffer rewrite of `app/render.py` was the last user); comments in
    `app/export.py`/`app/documents.py`/`mcp_server/tools.py` that cited it
    as the "one new Phase 5B dependency" (explaining why `networkx`/3mf
    isn't also authorized) updated to note it was removed again in this
    phase.
- `~/.claude/skills/forja/SKILL.md` and the MCP server's `instructions`
  string: `percibir` added to the tool table (14 tools total, matching
  `mcp_server/server.py`); recommended workflow now `ejecutar_script ->
  percibir (contactos) -> cortes only for a shape question -> check_colisiones
  for choque detail -> exportar`, with `captura` demoted to "aesthetics or
  showing the user" only.

### Fixed (Phase 5B fix-review 2)

- **Real occlusion in `captura`, replacing the sorted-`PolyCollection`
  painter's algorithm entirely**: even after fix-review's fixes (subdivide
  oversized triangles, cull back-faces, sort by nearest-vertex depth), a
  real render of `casa_v2.py` still showed sawtooth artifacts — rows of
  small furniture-coloured triangles poking through the front-right and
  lower-right walls, a magenta shard over the wardrobe face — because ANY
  single per-triangle depth key (centroid, average, nearest-vertex: all
  three were tried across Phase 5B) is fundamentally unsound whenever two
  triangles' depth *ranges* overlap or cross: a whole triangle only ever
  gets ONE position in a draw order, so a sort can be locally wrong for
  part of a long or interpenetrating triangle while being right for
  another part. `app/render.py` now rasterizes with a real per-pixel
  numpy software **z-buffer**: project every triangle to pixel coordinates
  + a scalar depth with the same hand-built orthographic camera as before,
  flat-shade it once, then for every triangle walk only its own screen
  bounding box, compute barycentric coordinates for that sub-grid
  (vectorized per triangle), and overwrite the z-buffer/colour buffer only
  where a pixel is inside the triangle AND nearer than whatever is already
  there. This is an EXACT, order-independent visibility test — draw order
  never matters, unlike a sort. Rendered at 2x2 supersampled resolution
  then box-downscaled for anti-aliasing (no more per-polygon edge
  antialiasing to fight, since the previous subdivision step — no longer
  needed — is gone entirely, along with `_subdividir_triangulo` and its
  thresholds). Back-face culling is also gone: every triangle is
  rasterized two-sided now, because a single-walled shell like casa_v2's
  exterior walls (cut open by door/window boolean subtractions) needs its
  INSIDE face drawable too — exactly what's visible looking into the house
  through an opening — and a z-buffer resolves the resulting visibility
  correctly regardless of which side faces the camera; culling was only
  ever a painter's-algorithm triangle-count optimization, never a
  correctness requirement for a per-pixel test. A cheap depth-discontinuity
  outline pass (a couple of numpy diffs over the z-buffer) now darkens
  silhouette/crease pixels for readability at negligible cost. Shading
  changed from `0.25 + 0.75*|n.luz|` to `0.35 + 0.65*|n.luz|`
  (`_AMBIENTE`/`_DIFUSO`) per this round's plan. Legend moved from
  matplotlib's `Axes.legend()` to Pillow `ImageDraw` straight onto the
  final image (same dark translucent panel, same `TOPE_LEYENDA` cap) —
  matplotlib is no longer imported anywhere in `app/render.py`, though it
  stays a pinned dependency (still referenced as the one authorized
  dependency precedent by other phases' comments). Verified against the
  real `casa_v2.py` (21 solids, ~15k triangles): full house ~2.3-2.7 s,
  interior (all but the shell) ~2.3 s, both well under the ~3 s budget and
  visibly artifact-free (see `plans/evidencia/5b/*_v3.png`).
- **Tests**: `tests/test_render_fix_review.py`'s painter's-algorithm section
  replaced with real z-buffer occlusion tests — an object correctly BEHIND
  an opaque panel must never show through (the previous version only ever
  tested the opposite direction, an object in front staying visible), two
  long slabs tilted in opposite directions crossing each other's depth
  range like an "X" must resolve the correct winner independently on each
  side of the crossing line, and a piece of "furniture" fully hidden behind
  a thin wall must never show through (the exact reported sawtooth
  artifact). Palette and `solidos_por_indice` safeguard tests untouched and
  still green.

### Fixed (Phase 5B fix-review)

- **Painter's-algorithm artifacts in `captura`** (a streak through the roof
  right of the chimney, a magenta sliver across the floor, a yellow shard
  outside the house's footprint, all in `casa_v2.py`'s render): root cause
  was `mpl_toolkits.mplot3d.art3d.Poly3DCollection` — its
  `do_3d_projection()` (`zsort='average'`, the default) recomputes its OWN
  face draw order every single draw from `np.argsort` over each face's
  *average projected z*, silently discarding whatever order the caller
  passed in (confirmed straight from Matplotlib's own source). The
  original Phase 5B render's manual pre-sort was therefore never actually
  in effect. A flat OCC face (a wall, the roof, the floor) also tessellates
  to as few as TWO huge triangles regardless of its physical size, so even
  Matplotlib's own average-z heuristic is locally meaningless for it. Fix
  (`app/render.py`): drop `Poly3DCollection`/`mplot3d` entirely — build a
  plain orthographic camera by hand from azimut/elevacion, subdivide any
  triangle whose longest edge exceeds ~5% of the scene's own bounding-box
  diagonal (down to a small grid via recursive edge-midpoint quadrisection),
  cull back-facing triangles (`normal . vista <= 0`, which also roughly
  halves the triangle count for free), sort the survivors by NEAREST-vertex
  depth (not centroid/average), project to 2D by hand, and draw a plain 2D
  `matplotlib.collections.PolyCollection` in that exact order — a 2D
  collection has no re-sorting logic of its own to fight. Also disabled
  `antialiased` on that collection: with hundreds of adjacent co-planar
  sub-triangles from the subdivision, per-polygon edge antialiasing was
  blending each one's border toward the BACKGROUND colour rather than its
  identical-colour neighbour, showing up as a visible hairline mesh over
  every subdivided flat surface (caught while manually verifying the fix,
  see the two new evidence renders below). Render time actually DROPPED
  after this fix (casa_v2.py: ~2.5 s full house / ~1.4 s furniture-only
  subset, well under the ~5 s budget) — the old code paid for an entire
  unused `Axes3D`/mplot3d machinery for nothing.
- **Colour collision in the per-solid palette**: hashing a name's digest
  (even reduced via exact integer modulo, the Phase 5B fix for the ORIGINAL
  "first byte only" collision) still only spreads hues well in the
  statistical average, not for any specific small N — nothing guaranteed
  two real names couldn't land close together for a given document.
  Replaced with `_color_por_indice`: hue assigned by the solid's POSITION
  in the document's own natural order via golden-ratio spacing (`hue_n =
  (n * phi_conjugate) mod 1`, the classic sequential-distinct-colour trick),
  with saturation/value alternating on a period-4 pattern tied to the index
  so that immediate NEIGHBOURS (index i vs i+1 — typically related pieces
  in a script's dict order, e.g. a bed next to its bedside table, or four
  chairs in a row) always differ in saturation at minimum. Deterministic
  per document ORDER, not per name — the same name in two different
  documents (or the same document rebuilt with a different dict order) can
  legitimately get a different colour; a `solidos=[...]` subset render
  still reuses the FULL document's colour assignment, never a re-indexed
  palette of just the kept names.
- **`solidos_por_indice` reimport-mismatch safeguard** (`app/solids.py`):
  previously trusted that `shape.solids()` returns solids in the same
  order every time the same STEP bytes are reimported, purely by
  positional `indice`. Every call now cross-checks each entry's stored
  volume/bbox-centre against the solid its `indice` actually points at
  (1% relative volume tolerance, 0.5 mm bbox-centre tolerance); on a
  mismatch, attempts to re-match every entry to the geometrically closest
  candidate solid, but ONLY accepts the result if every match is an
  unambiguous winner (no other candidate also falls within tolerance for
  the same entry) — otherwise falls all the way back to flat `solido_N`
  naming (`shape.solids()`'s own order) and sets `nombres_inciertos: true`,
  now surfaced by `check_colisiones`, `exportar(..., por_solido=true)`
  (both as a top-level key), and `captura` (appended to its caption) —
  never a silent mislabel of one piece's geometry as another's.
- **CHANGELOG wording**: the `check_colisiones` entry below previously
  attributed the one `casa_v2.py` finding to `mesa_centro` being
  "intentionally placed 0.8 mm above the floor" — that piece IS
  `mesa_centro`, but the real cause is a genuine 0.4 mm air gap: its own
  script places it 0.8 mm above the floor, directly on top of `alfombra`
  (a rug modelled as a picture frame — solid rim up to z=0.8, hollowed to
  z=0.4 in its 38x22 mm inner area), and the table's legs land inside that
  hollow, 0.4 mm above the recessed inner floor beneath them, not on the
  rug's own top surface. Corrected below; also demonstrated the
  named-solids synergy `check_colisiones`/`captura` are meant to unlock —
  see that entry.

### Added (Phase 5B — visual + verification tools)

- **`captura` (real render, replaces the Phase 3 stub)**: headless PNG render
  via matplotlib's `Agg` backend (new pinned dependency, no GPU/OpenGL
  needed) — `app/render.py`. Every named solid is tessellated separately;
  one stable colour per solid (index-order golden-ratio palette, see the
  fix-review entry above), shading by `|normal . light|`. Dark background,
  equal aspect (exact: both screen axes are the same orthographic
  projection), axes off, capped legend (20 names). Optional `solidos=[...]`
  renders a subset — the cheap way to see the interior of an assembly. PNG
  capped at ~150 KB (palette-quantized, then downscaled, only if it doesn't
  fit). Also exposed via `GET /documentos/{id}/captura` (raw `image/png`,
  caption in the `X-Forja-Captura` header). Verified against the real
  21-solid `casa_v2.py` dollhouse: both the full house and a furniture-only
  subset render correctly (see the fix-review entry above for the
  painter's-algorithm bug this shipped with initially, and its fix) in
  ~2.5 s / ~1.4 s respectively (budget ~5 s).
- **`check_colisiones(id, tolerancia_mm3=0.5)`**: pairwise collision / split
  piece / floating piece check over a document's named solids —
  `app/checks/colisiones.py`. A bbox pre-filter (small margin) keeps the
  O(n^2) exact OpenCascade calls (`Shape.distance`/`Shape.intersect`, via
  build123d) down to genuinely close candidate pairs. Reports ONLY
  problems: `choques` (pairs of DIFFERENT-named solids whose boolean common
  volume exceeds the tolerance), `partidas` (a name that produced more than
  one disconnected solid — the same grouping `resumen_documento`'s astilla
  rule (1) already used), `flotantes` (solids not touching/within ~0.05 mm
  of anything else); `ok: true` when all three are empty. Long lists get a
  `_mas` tail; a boolean/distance failure on one specific pair is reported
  under `errores` without aborting the rest. STL-only (mesh) documents get
  a clear 400 rather than attempting a check with no named solids. Verified
  against `casa_v2.py` (flat `solido_N` naming — its `resultado` is a plain
  `Compound`, not a dict; see the named-solids demo below): 0 choques, 0
  partidas, 1 flotante (`solido_4`) — a REAL 0.4 mm air gap, not a tool bug:
  `alfombra()` is a picture-frame shape (solid rim up to z=0.8, hollowed to
  z=0.4 in its 38x22 mm inner area), and `mesa_centro`'s legs sit inside
  that hollow at z=Z1+0.8, 0.4 mm above the recessed rug floor beneath them.
- **Named-solids demo, without touching `casa_v2.py`** (fix-review task):
  `casa_v2.py`'s own `resultado` (escena mode) is a plain `Compound`, not a
  dict, so a plain run of it only ever gets flat `solido_N` names. A small
  wrapper script (passed as `codigo`, never written into
  ``) `exec`s the original file's source
  from its real read-only path and rebuilds `resultado = {"casa": casa(),
  **{nombre: colocar(pieza, x, y, z, g) for nombre, pieza, x, y, z, g in
  MUEBLES}}` from its own already-defined `casa()`/`colocar()`/`MUEBLES` —
  identical geometry (same bbox/volume/solid count), but every tool now
  speaks in real furniture names: `check_colisiones` reports
  `flotantes: ["mesa_centro"]` (same finding as above, now legible without
  cross-referencing bboxes by hand) and `captura`'s legend lists
  `casa, alfombra, sofa, mesa_centro, lampara, ...` instead of
  `solido_1, solido_2, ...`. See
  `plans/evidencia/5b/casa_v2_nombrado.png`/`colisiones_casa_v2_nombrado.json`.
- **`exportar(id, formato, por_solido=true)`**: one file per named solid
  instead of one file for the whole document — `app/export.py`.
  Geometrically-identical pieces (e.g. 4 chairs from the same script
  function, even rotated/moved) dedupe into ONE file via a
  placement-invariant signature (volume + surface area + sorted bounding-box
  extents); the response maps each written filename to every solid name it
  represents. A name that produced several disconnected solids (see
  `partidas` above) is unioned first and exported as one file. Filenames
  are built only from names that already pass `solids.validar_nombres`
  (Phase 5A fix-review's path-injection concern) AND are additionally
  confined to the export directory via a `realpath` check (defense in
  depth). `3mf` is now accepted as a format (whole-document and
  per-solid): it needs `networkx`, not installed in this container (no new
  dependency beyond `matplotlib` was authorized this phase), so it returns
  a clear 501 instead of a raw import error.
- **New dependency**: `matplotlib==3.11.2` (pinned, `Agg` backend only —
  headless, no new system packages needed beyond the existing OpenGL/GLU
  libs already in the `Dockerfile`). `Pillow` (already a transitive
  dependency of `matplotlib`) is used for the PNG size-cap re-encode.
- `.dockerignore` (new file): the build context previously included
  `documentos_data/.forja_token` (created inside the container as root,
  `600` permissions), which made a plain `docker build`/`docker compose
  build` fail with a permission error reading the build context — this was
  never hit before Phase 5B because no earlier phase happened to trigger a
  rebuild after that file existed with those permissions. Excludes
  `documentos_data/`, `.git/`, and the usual Python caches from the build
  context (mirrors `.gitignore`); does not change what gets copied into the
  image (`Dockerfile`'s `COPY app/`/`COPY mcp_server/` were already scoped).
- `mcp_server/`: `captura` now returns real MCP image content plus a
  one-line caption (`[Image, caption]`); new `check_colisiones` tool
  (13 tools total); `exportar` gained `por_solido`. `~/.claude/skills/
  forja/SKILL.md` tool table, "Captura real"/"Verificar colisiones"/
  "Exportar por solido" sections, and a non-blocking note that the
  astilla/`partidas` "split piece" rule only ever fires for a `resultado`
  dict (flat `solido_N` names are unique by construction) added to match.

### Fixed (Phase 5A review fix — astilla rule, side-channel trust)

- `app/solids.py`: the astilla ("sliver") rule no longer compares a solid's
  volume against the largest solid in the whole document — that rule
  flagged 14 of 21 legitimate furniture pieces in a real dollhouse scene
  (a chair is tiny next to a house; that alone is not a defect). Replaced
  with three independent per-solid checks (any match flags it): (1) a
  named piece that produced more than one disconnected solid — every
  fragment except the largest of that name; (2) an absolute floor,
  `volumen < umbral_astilla_mm3` (default 1.0 mm3, unchanged); (3)
  thinness — the solid fills under 2% of its OWN bounding box volume AND
  its smallest bbox dimension is under 1 mm (both required). A legitimate
  thin plate (a 2 mm shelf, a rug modelled as a hollowed picture-frame)
  stays mostly solid within its own thin bounding box and is never
  flagged; only a near-degenerate boolean-tolerance residue trips both
  conditions. `mcp_server/tools.py` and `~/.claude/skills/forja/SKILL.md`
  updated to match. Re-ran the `casa_v2.py` smoke test: 0/21 solids
  flagged (down from 14/21), all correctly not-a-defect.
- **Security**: `app/scripts_runner.py`'s explicit names side-channel
  (`.nombres.json`, read after a successful script run) is now
  re-validated before being trusted at all — the sandboxed script runs
  arbitrary code with the exact same filesystem access as the trusted
  wrapper, so nothing previously stopped it from planting its own crafted
  sidecar (e.g. a path-traversal name like `"../evil"`, a real risk once a
  later phase uses these names as per-solid export filenames) at the exact
  path the wrapper would otherwise write to, timed so the wrapper's own
  conditional (dict-only) write never overwrites it. New
  `_leer_nombres_confiables()` requires a `list[str]` where every entry
  passes `solids.validar_nombres` (safe chars, non-empty, length cap,
  unique); anything else falls back to `None` (flat `solido_N` naming),
  never partially trusted. `combinar_nombrados`'s docstring
  (`app/kernel/b123d_kernel.py`) now matches `app/solids.py`'s on exactly
  what a re-imported STEP label does/doesn't guarantee. Non-blocking:
  documented that `__FORJA_ERROR__` is a best-effort diagnostic marker, not
  a security boundary (it can only ever affect the short error message
  shown back to the same caller who ran that script).

### Added (Phase 5A — named solids + script ergonomics)

- **Named solids**: a script's `resultado` may now be a `{nombre: Shape}`
  dict (besides a plain Shape/Compound). `kernel.b123d_kernel.
  combinar_nombrados()` tags each value's `.label` and merges them into one
  Compound — the exported STEP genuinely carries those names as STEP
  PRODUCT names. New `app/solids.py` persists the derived per-solid
  breakdown (`nombre`, `bbox`, `volumen`, `indice`) in a
  `{doc_id}.solidos.json` sidecar; names themselves come from an explicit
  side channel `scripts_runner`'s sandboxed subprocess passes back to
  `app/documents.py` (the ordered dict-key list), **never** re-derived from
  re-imported STEP labels — a real multi-part script with no explicit
  naming showed every single child coming back labelled the generic OCCT
  placeholder `"COMPOUND"` after a round trip (build123d 0.13's
  `export_step` hardcodes `auto_naming=True`), which would have made "was
  this really named by the user" undecidable from labels alone. Unnamed
  solids get `solido_N`. Names are validated (`[A-Za-z0-9_-áéíóúñ]`, unique,
  max 64 chars) — invalid → 422 with a Spanish message. `solidos.json` is
  now included in the snapshot/restore file set alongside `notas.json`, so
  `restaurar` brings back the matching names for that point in history, and
  a container restart (`recargar_documentos`) reuses the existing sidecar
  as-is instead of recomputing it (a STEP predating this phase gets a flat
  `solido_N` breakdown bootstrapped once, on first reload).
- `GET /documentos/{id}` (`resumen_documento`): when `solidos > 1`, adds
  `solidos_detalle: {lista, mas, astillas}` — `lista` is `[{nombre, bbox,
  vol}]` capped at `tope_solidos` (30 default, remainder in `mas`);
  `astillas` lists solid names whose volume is under 1% of the largest
  solid OR under `umbral_astilla_mm3` mm3 (default 1.0) — scanned over
  every solid, not just the capped list, so a shard past row 30 is never
  hidden again (the original feedback: an 800 mm3 shard hid behind a plain
  "solidos: 2"). A single-solid document's response is byte-for-byte the
  same as before (no new keys at all).
- `ejecutar_script` (MCP tool + `POST /documentos/script`) gains `ruta`
  (run a `.py` already on disk — `/data/documentos` or the read-only
  `/data/fuentes` mount, same path-traversal/symlink-escape guard as
  `abrir_archivo`), `variables` (a JSON dict injected as module globals
  before the script runs, so a parameter can change without re-pasting the
  script), and exposes `documento_id` through MCP for the first time (the
  HTTP route already accepted it since Phase 4 — feedback item 5). Exactly
  one of `codigo`/`ruta` is required (400 otherwise).
- **Error line numbers**: a script exception now reports `linea` (line
  number inside the user's own script) and `codigo_linea` (that source
  line, stripped) alongside the existing short `mensaje`, still without a
  full traceback — frames are filtered to the script's own compiled
  filename (`<forja-script>`, fixed regardless of `codigo`/`ruta`) so a
  failure inside build123d/kernel itself is never misattributed to a
  script line. Failures with no specific script line (kernel export
  rejecting the result, missing `resultado`, invalid solid names) keep the
  plain string `detail`/`mensaje` shape.
- `app/notes.py`: `resumen()`'s nested `referencia` sub-dict now filters
  the full `{"huella", "referencia_perdida", "ambigua"}` set, not only
  `"huella"` (Phase 4 review follow-up — defensive: no current write path
  actually nests the other two, but any stored/legacy data that does must
  never leak them back out).
- `~/.claude/skills/forja/SKILL.md`: documents the named-solids dict,
  `ruta`/`variables`/`documento_id`, the new error fields, the HTTP
  fallback (`POST /documentos/script` with `X-Forja-Token`) for when the
  stdio MCP dies after a container restart, and the build123d gotcha
  `extrude(Plane.YZ * perfil, amount=L)` extrudes toward -X (use
  `dir=(1, 0, 0)`); fixed the stale "no hay edicion in-place todavia"
  sentence. Tool table unchanged (12 tools, no new ones this phase).
- Real-data smoke test: `casa_v2.py`
  (225 primitives, no explicit naming) run via `ruta` in ~4.7s → 21 solids,
  matching the original feedback session; confirmed the flat `solido_N`
  fallback (not the `"COMPOUND"` bug) is what a real, un-managed script
  gets.

### Fixed (Phase 4 review fix — naming ambiguity, restaurar auth, test isolation)

- `app/naming.py`: `resolver()` no longer picks the nearest of two-or-more
  tied candidates by centroid distance alone — that pick was effectively
  order/floating-point-noise dependent, the exact "silent wrong match"
  ADR-0003 forbids, just one level removed. It now only accepts a match
  when it is a *clear winner* over the runner-up (see new
  `docs/decisions/0007-naming-ambiguity-policy.md`); otherwise the
  reference is treated as unresolved, same as the zero-candidate case, with
  a new `ambigua: true` flag (via `resolver_detallado()`) so a caller can
  tell "nothing close enough" apart from "more than one thing close
  enough" for free. `tests/test_naming.py` gained order-independence
  regression tests (two near-tied candidates, both list orders) and a
  realistic two-symmetric-holes case confirming a pin never swaps to the
  mirrored hole after an unrelated rebuild.
- `app/notes.py`/`app/documents.py`: `ambigua` is surfaced through
  `GET /documentos/{id}/notas`'s `resumen()` (default `false`, additive —
  no existing field removed); `_actualizar_documento_desde_script` no
  longer duplicates `referencia_perdida` inside the nested `referencia`
  dict it persists (a latent bug: the sub-dict `resolver_referencia()`
  returns always carries its own copy of the flag) — the top-level
  `item["referencia_perdida"]`/`item["ambigua"]` stay the single source of
  truth for a note/stroke's state.
- **Security**: `POST /documentos/{id}/restaurar` now requires the shared
  `X-Forja-Token` (`Depends(auth.requiere_token)`) — the Phase 4 review
  flagged that ADR-0005's "every write/exec route" consequence had not
  actually been applied to this route, even though restoring silently
  overwrites the live file/notes exactly like the two originally-protected
  routes. `mcp_server/client.py`'s `restaurar()` sends the token now;
  the web viewer (`historial.js`'s "Restaurar" button, `api.js`) prompts
  once per browser tab for the token (stored in `sessionStorage` only) with
  a Spanish message pointing at `documentos_data/.forja_token` on the host.
  New `DELETE /documentos/{id}` (also token-protected) permanently removes
  a document's file/meta/notes/exported-artifact/history — added for test
  cleanup (below), not exposed as an MCP tool.
- **Test isolation**: `DOCUMENTOS_DIR`/`HISTORIAL_DIR`/`TOKEN_FILE` (`app/
  documents.py`, `app/notes.py`, `app/versioning.py`, `app/auth.py`) are
  now overridable via env `FORJA_DATA_DIR` (default `/data/documentos`,
  unchanged). New `tests/conftest.py` points it at a throwaway tmp
  directory before any test module imports those modules, so the
  in-process test suite (`TestClient`-based tests) no longer writes into
  the user's real `documentos_data/`. `tests/test_mcp_tools.py` (which
  exercises the MCP tools against the **live** `uvicorn` process — a
  separate OS process the tmp-dir override can never reach) gained an
  autouse fixture that diffs the document list before/after each test and
  deletes whatever it created via the new `DELETE` route, instead of
  leaving throwaway documents behind. `versioning.py` gained
  `borrar_historial()`; `documents.py`/`mcp_server/client.py` gained
  `eliminar_documento()`. Verified `ls documentos_data | wc -l` stays
  constant (855) across repeated full `pytest /tests -q` runs. Pre-existing
  leftovers from before this fix (827 files / ~34 MB, plus a couple of
  untracked manual smoke-test documents) were left untouched, per policy —
  see the phase report for the exact count/size to ask the user about.
- `tests/test_update_in_place.py` (new): the `documento_id` update-in-place
  path (`documents._actualizar_documento_desde_script`) previously had no
  dedicated coverage — success (same id, replaced geometry), a note's
  reference surviving an unrelated rebuild that shifts face indices
  end-to-end over the HTTP API, and a kernel-rejected script (0 solids)
  leaving the STEP file and notes byte-identical plus exactly one new
  ("before the attempt") history entry, never a partial write.

### Added (Phase 4 — notes/pins/whiteboard + versioning + stable naming)

- `app/naming.py`: geometry-derived stable ids ("huellas") for faces/edges
  — type + centroid + normal/direction + area/length, rounded — re-resolved
  after every rebuild by nearest-fingerprint match within tolerance
  (`resolver`/`resolver_referencia`), never by raw kernel index. A
  previously-tagged element that no longer has a geometric match is marked
  `referencia_perdida: true`, never silently dropped or rebound to the
  wrong element (ADR-0003). `tests/test_naming.py`: rebuild-and-resolve
  acceptance (tag a box face, add an unrelated hole elsewhere so kernel
  face indices shift, the tag still resolves to the same face) plus a
  genuinely-lost-reference case and a malformed-fingerprint defensive case.
- `app/versioning.py`: plain-directory snapshot store under
  `/data/documentos/.historial/{doc_id}/` (`crear_snapshot`,
  `listar_historial`, `leer_snapshot`) — no new dependency, see new
  `docs/decisions/0006-snapshot-storage.md` (chose this over git-backed).
  Every accepted mutation snapshots *before* applying; `tests/
  test_versioning.py` covers a forced invalid edit staying recoverable.
- `app/notes.py`: pins ("notas") and whiteboard strokes ("trazos"), schema
  ported field-for-field from the FreeCAD `ClaudeNotas` add-on
  (`ClaudeNota.FCMacro`'s `comentario`/`referencia` for pins, `pizarra.py`'s
  `tipo`/`comentario`/`puntos`/`plano_origen`/`plano_normal`/`referencia`
  for strokes). Persisted as `{doc_id}.notas.json` under `DOCUMENTOS_DIR`
  (survives a container restart). CRUD routes stay open (no
  `X-Forja-Token`, like the upload route) but are size/length-validated
  (Pydantic: max comment length, max points per stroke). Every mutation
  snapshots first via `versioning`. `tests/test_notes.py` covers CRUD,
  validation limits, persistence, and history-entry creation.
- `app/documents.py`: startup reload of the registry from files already on
  `DOCUMENTOS_DIR` (a container restart no longer orphans notes — a small
  `{doc_id}.meta.json` sidecar preserves the original upload filename
  across restarts); `GET /documentos/{id}/caras` + `GET /documentos/{id}/
  caras_triangulos` (binary `uint32` per-triangle face index) so the web
  viewer can turn a three.js raycast hit into a stable face id without any
  raw geometry over JSON; `GET /documentos/{id}/historial` + `POST
  /documentos/{id}/restaurar` (restoring snapshots the current state first
  too — never a one-way door); `POST /documentos/script` gained an optional
  `documento_id` to update an existing STEP document in place (snapshot
  before, rollback the file on script/kernel failure, notes re-resolved
  against the rebuilt shape via `naming.resolver_referencia`).
- `app/kernel/mesh.py`: `tessellate_to_trimesh_con_caras` tessellates
  face-by-face (rather than the whole shape at once) and returns a parallel
  per-triangle face-index array; `tessellate_to_trimesh` now delegates to
  it (same signature/behavior, volume unaffected — confirmed exact-volume
  match in manual verification).
- Web viewer (Spanish UI): per-tab toolbar (📌 Nota, ✏ Pizarra, 🕘 Panel) in
  `app/web/static/js/{notes,historial}.js`; `viewer.js` gained raycasting
  (`raycastearMalla`), pin/stroke rendering (`mostrarAnotaciones`), and
  whiteboard-plane placement/projection (cara seleccionada / vista actual /
  XY / XZ / YZ + offset, matching `pizarra.py`'s panel). Side panel
  (`#fj-panel-lateral`) lists notes/strokes with visibility toggle + delete,
  and history with "Restaurar". Fixed a latent bug in `cssVarColor()` (used
  since Phase 2): `getComputedStyle(...).color` does not convert `oklch()`
  tokens to `rgb()` in this Chromium build, and three.js's color parser
  doesn't understand `oklch()` either — it silently fell back to white.
  Routing the resolved value through an offscreen `<canvas>` (`fillStyle`
  fully supports CSS Color 4) fixes it for every token, not just Phase 4's
  new pin/stroke colors; confirmed via a headless-Chrome screenshot showing
  a genuinely orange pin and green stroke instead of white.
- `mcp_server/{tools,client,server}.py`: five new tools — `leer_notas`,
  `crear_nota`, `borrar_nota`, `leer_historial`, `restaurar` — all thin
  HTTP wrappers over the new backend endpoints (ADR-0005: no in-process
  calls into `app/versioning.py`/`app/naming.py` from the MCP process).
  `~/.claude/skills/forja/SKILL.md` tool table updated to match exactly
  (`tests/test_mcp_tools.py::test_tool_names_match_skill_table`); verified
  end-to-end over the real stdio transport (`ejecutar_script` ->
  `crear_nota` -> `leer_notas` -> `leer_historial`), not just direct
  Python calls.
- `docs/decisions/0006-snapshot-storage.md` (new): justifies the
  plain-directory snapshot store over a git-backed one (no new dependency,
  documents are a handful of small binary files, not a source tree).

### Deviations (Phase 4)

- Edge ("arista") picking is implemented end-to-end in the backend
  (`app/naming.py` fingerprints edges exactly like faces; `app/notes.py`'s
  `referencia.tipo` accepts `"arista"`) but the web UI's 📌/✏ toolbars only
  offer face and free-point picking via the raycaster — clicking to select
  an *edge* specifically is not implemented in this phase. An `"arista"`
  reference can still be created (e.g. by a future UI iteration or directly
  via the MCP `crear_nota` tool with a hand-built `referencia`).
- The MCP tool set matches the phase contract exactly (`leer_notas`,
  `crear_nota`, `borrar_nota`, `leer_historial`, `restaurar`); there is no
  MCP `crear_trazo` tool — whiteboard strokes are created from the web UI
  only in this phase (documented in SKILL.md's "Proximamente").
- `POST /documentos/script`'s `documento_id` (update-in-place) parameter
  is an addition beyond the phase's explicit file list, required to give
  concrete meaning to the contract's "script re-run... updating a
  document" wording and to exercise a real kernel-failure rollback + a
  real naming re-resolution at the same hook (`app/documents.py`'s
  `_actualizar_documento_desde_script`).

### Security (Phase 3 review fix)

- `app/auth.py`: new `requiere_token` FastAPI dependency guards
  `POST /documentos/script` (arbitrary script execution) and
  `POST /documentos/desde_ruta` (arbitrary path read) with a shared
  `X-Forja-Token` header, compared with `hmac.compare_digest`. Token comes
  from env `FORJA_TOKEN` or, if unset, a `secrets.token_urlsafe(32)`
  generated on first use and persisted to
  `/data/documentos/.forja_token` (mode 0600, inside the already-gitignored
  `documentos_data` volume). `mcp_server/client.py` reuses the same module
  to send the header on `ejecutar_script`/`abrir_archivo`. Every GET route,
  `/salud`, static files, and the web UI's `POST /documentos` upload stay
  open with no token — the `forja` service binds `0.0.0.0:8710` so the web
  viewer stays reachable from the LAN (the user's phone), so loopback-only
  binding was rejected in favor of guarding just the two dangerous routes
  (see ADR-0005). Missing/wrong token -> `401 {"detail": "token
  requerido"}`.
- `tests/test_auth.py`: 401 without a token, 401 with a wrong token, 200
  with the correct token on both protected routes; confirms `/documentos`
  upload, `/salud` and `GET /documentos` stay token-free.

### Changed (Phase 3 review fix)

- ADR-0004 gains a "Superseded in part by ADR-0005" status line (body
  unchanged, per the project's ADR-immutability convention).
  `docs/decisions/0005-mcp-backend-http-boundary.md` (new) documents the
  real process model clarified while building Phase 3: one container, two
  OS processes (the `uvicorn` PID-1 process owning the in-memory registry;
  the MCP stdio server started per-session via `docker exec -i`)
  communicating over loopback HTTP with the new `X-Forja-Token` header, and
  the Phase 4 consequence that snapshot/rollback/history logic must live
  server-side in the backend, with MCP tools as thin wrappers over new
  compact endpoints — never in-process calls from the MCP server into
  `app/versioning.py`.

### Added (Phase 3 — MCP server + skill)

- `mcp_server/` package: a compact-response MCP server (`mcp` 2.2.0,
  `MCPServer`/stdio transport) running **inside** the `forja` container as a
  separate entry point (`python -m mcp_server.server`), registered with
  Claude Code as `claude mcp add -s user forja -- docker exec -i forja
  python -m mcp_server.server` (ADR-0004). Tools: `estado`,
  `listar_documentos`, `abrir_archivo`, `resumen_documento`,
  `ejecutar_script`, `exportar`, `captura` (stub — see Deviations below).
  Every response is a small dict (ids, numbers, booleans, short Spanish
  messages); a box's `resumen_documento` measures ~180-225 bytes end-to-end
  over stdio, well under the ~800 byte / ~200 token target.
- `mcp_server/client.py`: the tools talk to the FastAPI backend over
  `http://localhost:8000` (loopback, inside the container) rather than
  calling `app/documents.py` in-process, so documents created via MCP stay
  visible in `GET /documentos` and the web viewer (see Deviations).
- New backend endpoints in `app/documents.py`: `POST /documentos/desde_ruta`
  (open a file already on disk — the path must resolve inside
  `/data/documentos` or the new read-only `/data/fuentes` mount; traversal
  and out-of-root paths are rejected with 400), `POST /documentos/script`
  (build and register a document from a build123d script), `GET
  /documentos/{id}/exportar?formato=stl|step` (materialize the document
  under `/data/documentos/exportados` and return `{ruta, tamano_bytes}` —
  never the file bytes; `step` is rejected for documents that only have a
  mesh, i.e. were imported as STL).
- `app/scripts_runner.py`: runs a user-provided build123d script (must
  assign the built shape to a variable named `resultado`) in an isolated,
  timed-out subprocess (`timeout` seconds, default 30) — a broken or
  looping script can never hang or crash the FastAPI process; only the last
  traceback line ever crosses back to the caller, never a full traceback.
- `docker-compose.yml`: bind-mounts `./mcp_server` into `/app/mcp_server`
  and `<tu carpeta de modelos>` into `/data/fuentes` (read-only,
  never written to). `Dockerfile` also bakes `mcp_server/` into the image
  (`COPY mcp_server/ /app/mcp_server/`) for a from-scratch build.
  `requirements.txt` gained `mcp==2.2.0`.
- `tests/test_mcp_tools.py`: exercises `mcp_server.tools` against the
  live `uvicorn` process (the container's own entrypoint) — tool-name
  parity with the skill table, path-traversal rejection, script success/
  error/timeout, export (stl/step, and the STL-origin/STEP-export
  rejection), and the `captura` stub. A manual stdio smoke test
  (`initialize` → `tools/list` → `tools/call estado/ejecutar_script/
  resumen_documento`) confirmed the server over `docker exec -i`.
- `~/.claude/skills/forja/SKILL.md`: Spanish skill documenting the tool
  table, the `abrir_archivo -> resumen_documento -> ejecutar_script ->
  exportar` workflow, the token-economy rule (prefer one whole-script
  `ejecutar_script` over many granular calls — ~13x cheaper, per the
  fusion360 skill lesson), and a "proximamente" section for
  notes/checks/params (Phases 4-5).

### Fixed

- `POST /documentos`: a `.step`/`.stp` upload that imports as a shape with no
  solids (e.g. garbage bytes with a STEP extension) now responds `422
  {"detail": "archivo invalido: ..."}` instead of a fake `200
  {"solidos": 0, "valido": true}` — symmetric with the existing STL error
  path (Phase 1 review finding).
- `POST /documentos` now enforces a maximum upload size, configurable via
  `FORJA_MAX_UPLOAD_MB` (default `100`): rejects with `413 {"detail":
  "archivo demasiado grande"}` when `Content-Length` exceeds the cap, and
  independently enforces the same cap against bytes actually read while
  streaming the upload (`Content-Length` is never trusted alone).
- `POST /documentos` now deletes the file it just wrote under
  `documentos_data` whenever a later step (STEP/STL validation) responds with
  a 4xx, so no orphaned files accumulate under `/data/documentos`.

### Added

- Dockerized FastAPI backend skeleton (`forja` service, port 8710→8000,
  `network: host` build, bind-mounted `./app` and `./tests`).
- `GET /salud` reporting installed versions (via `importlib.metadata`) of
  `build123d`, `cadquery-ocp` (OCP), `manifold3d`, `trimesh` and
  `python-fcl`.
- `app/kernel/b123d_kernel.py` (build123d/OpenCascade adapter: box
  construction, analytic volume/bbox/solid-count/validity, STEP
  export/import) and `app/kernel/mesh.py` (OCC tessellation → trimesh,
  binary STL bytes, STL-only analysis) — the only two modules allowed to
  import `build123d`/`OCP` (ADR-0001).
- `POST /documentos` (multipart STEP/STL upload) returning
  `{id, nombre, bbox, volumen, solidos, valido}` — never raw vertices — with
  an in-memory registry and file storage under `./documentos_data`
  (gitignored bind mount at `/data/documentos`).
- Kernel smoke tests (`tests/test_kernel_smoke.py`): dependency imports,
  analytic vs. trimesh-tessellated volume within 1e-3 relative, STEP
  round-trip volume preservation within 1e-3 relative.
- API tests (`tests/test_api.py`) via FastAPI `TestClient`: `/salud` and
  `POST /documentos` for both STEP and STL uploads.
- Web viewer (`app/web/`): browser UI at `/` opening multiple documents as
  independent tabs (own camera/selection per tab), rendered with vendored
  three.js (`app/web/static/vendor/three/`, no CDN) — module build,
  `OrbitControls`, `STLLoader`. Dark "Forge" theme ported to `--fj-*` tokens
  (`forja-tokens.css`, `forja-base.css`), UI strings in Spanish. Render is
  event-driven (load/resize/`controls change`), not a bare `requestAnimationFrame`
  loop, so static captures (headless Chrome) show geometry.
- `GET /documentos` (compact `{id, nombre, volumen, solidos}` list) and
  `GET /documentos/{id}/malla` (binary STL, `model/stl`, always re-exported
  through `mesh.to_stl_bytes` for a guaranteed binary transport — never JSON
  vertex arrays) added to `app/documents.py`; `app/kernel/mesh.py` gained a
  shared `load_stl` helper. `app/main.py` now mounts `/static` and serves
  `index.html` at `/`.
- API tests for the new list/`malla` endpoints and for `GET /` serving the
  viewer shell.

## [0.0.0-phase0] - 2026-09-27

### Added

- Repository bootstrap: `README.md`, `AGENTS.md`, `.gitignore`.
- `.claude/project-context.md` with stack, port (8710), language rule,
  Docker-only runtime, and links to ADRs.
- ADR 0001 (geometry kernel), 0002 (frontend stack), 0003 (topological
  naming and versioning), 0004 (MCP process model).
- Approved implementation plan and phase detail committed under `plans/`.
