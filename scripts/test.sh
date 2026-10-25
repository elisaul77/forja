#!/bin/sh
# Unico camino para correr los tests de Forja (ver AGENTS.md y README).
#
#   scripts/test.sh                          suite Python completa, hermetica
#   scripts/test.sh test_papelera.py -k algo  argumentos de pytest, relativos a /tests
#   scripts/test.sh web                      tests de la UI (node:22-bookworm-slim)
#
# NUNCA `docker exec forja python -m pytest`: ese contenedor sirve datos reales
# y la guarda de tests/conftest.py lo rechaza.
set -eu
cd "$(dirname "$0")/.."

if [ "${1:-}" = "web" ]; then
    shift
    exec docker run --rm -v "$PWD:/repo:ro" -w /repo node:22-bookworm-slim \
        sh -c 'node --test tests/web/*.test.mjs' "$@"
fi

exec docker compose --profile test run --rm --build test "$@"
