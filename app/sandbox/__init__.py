"""Untrusted-code side of Forja (F0.2, ADR-0013).

Runs only inside the `forja-sandbox` container (uid 65534, no network,
read-only root, no data/fonts/secret mounts), never inside `forja`:

- `ejecutor` — the long-lived executor (PID 1 of the sandbox container)
  listening on a Unix socket in the shared `staging` volume.
- `bootstrap` — the first code of every child: minimal env, rlimits,
  `oom_score_adj`, then the user's script.

`protocolo` holds the constants both containers share (paths, file names,
caps). It imports nothing heavy so `scripts_runner` can use it too.
"""
