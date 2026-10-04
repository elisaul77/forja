"""Constants shared by `scripts_runner` (in `forja`) and the executor (in
`forja-sandbox`). Plain values only, no imports beyond `os`/`pathlib`.

Layout of the `staging` volume (prepared by the image and re-applied by
`app/entrypoint.sh`):

    /staging                 uid 1000, 0711  (no listing by the sandbox)
    /staging/trabajos        uid 1000, 0711
    /staging/trabajos/<id>   uid of the server, 0711, created per job
        peticion.json        0644  {codigo, variables, timeout}
        salida/              0777  the only place the child writes outputs
    /staging/ejecutor        uid 65534, 0755
        ejecutor.sock        0666  the executor's Unix socket

The job id (32 random hex chars) is NOT what isolates jobs from each other:
it reaches the child only through a pipe on stdin (never argv), but every
child runs as the same uid 65534. Isolation comes from (ADR-0013):

- the executor runs ONE job at a time (no sibling job is alive to spy on);
- each child confines itself with Landlock before the user's code: it may
  write only under its own ``salida/`` and its private ``HOME`` (so not in
  ``/staging/ejecutor`` nor in another job's ``salida/``) and may not signal
  processes outside its domain (PID 1 included). Landlock does not cover
  ``chmod``: the executor restores the socket's and its directory's modes;
- the child is non-dumpable, so its ``/proc`` entries (cwd, fds, environ)
  are not readable by another process of the same uid;
- the executor only takes ``ejecutar`` from a peer whose uid is not its
  own (``SO_PEERCRED``): a child cannot queue jobs.
"""
from __future__ import annotations

import os
from pathlib import Path

STAGING_DIR = Path(os.environ.get("FORJA_STAGING_DIR", "/staging"))
TRABAJOS_DIR = STAGING_DIR / "trabajos"
SOCKET_PATH = Path(os.environ.get("FORJA_SANDBOX_SOCKET", str(STAGING_DIR / "ejecutor" / "ejecutor.sock")))

PETICION = "peticion.json"
SALIDA_DIR = "salida"
RESULTADO = "resultado.step"
NOMBRES = "resultado.nombres.json"
SALIDAS_ESPERADAS = (RESULTADO, NOMBRES)

# Caps on what the server accepts back (the child's RLIMIT_FSIZE is the
# same 512 MB, so a bigger STEP never even gets written).
MAX_RESULTADO_BYTES = 512 * 1024 * 1024
MAX_NOMBRES_BYTES = 1024 * 1024
MAX_STDERR_BYTES = 64 * 1024
MAX_MENSAJE_BYTES = 1024 * 1024

# Child limits (ADR-0013; measured in revision-tecnica T3: 1.5 GB needs
# BLAS/OMP at 1 thread). No RLIMIT_NPROC: the container's pids_limit covers it.
RLIMIT_DATA_BYTES = 1536 * 1024 * 1024
RLIMIT_FSIZE_BYTES = MAX_RESULTADO_BYTES
RLIMIT_CPU_MARGEN_S = 10

# Per-job write cap enforced by the executor's watcher thread: the job's
# process group is killed if its `salida/` grows past this, or if free space
# on the staging volume falls below the floor (the host disk is nearly full).
MAX_SALIDA_TRABAJO_BYTES = 600 * 1024 * 1024
MIN_LIBRE_STAGING_BYTES = 2 * 1024 * 1024 * 1024
MAX_ENTRADAS_SALIDA = 10000

# Server side: how long an `ejecutar_script` call waits for the previous one
# (jobs run one at a time) before giving up with "sandbox ocupado".
MAX_ESPERA_COLA_S = 300.0
