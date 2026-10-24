#!/bin/sh
# Suite hermetica de Forja: el unico camino soportado para correr los tests.
# Lo lanza `scripts/test.sh`, dentro del servicio `test` de docker-compose.yml
# (perfil `test`): contenedor propio, SIN documentos_data ni secretos montados.
#
# Levanta SU backend uvicorn en 127.0.0.1:8001 con datos temporales. Los tests
# en vivo (MCP/HTTP) hablan con ese backend, y la guarda de tests/conftest.py
# se niega a correr contra cualquier otro. El sandbox es el forja-sandbox vivo,
# via el volumen forja_staging (no se arranca otro ejecutor).
#
# Argumentos: rutas y opciones de pytest, relativas a /tests
# (p. ej. `scripts/test.sh test_papelera.py -k algo`). Sin argumentos, toda la suite.
set -eu

PUERTO=8001
DATOS=/work/datos-backend
TOKEN_BACKEND="forja-hermetico-$(python -c 'import secrets; print(secrets.token_urlsafe(24))')"
export FORJA_TOKEN="$TOKEN_BACKEND" FORJA_TOKEN_VIVO="$TOKEN_BACKEND"
export FORJA_TEST_BACKEND=hermetico FORJA_TEST_BACKEND_DATOS="$DATOS"
export FORJA_HTTP_BASE_URL="http://127.0.0.1:$PUERTO"
export FORJA_DATA_DIR="$DATOS" FORJA_SECRET_FILE=/work/sin-secreto/forja_token
export PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app
mkdir -p "$DATOS" /work/sin-secreto

cd /app
uvicorn main:app --host 127.0.0.1 --port "$PUERTO" >/work/uvicorn.log 2>&1 &
BACKEND=$!
trap 'rc=$?; if [ "$rc" -ne 0 ]; then echo "hermetico: fallo (rc=$rc). Ultimas lineas del backend de prueba:" >&2; tail -n 40 /work/uvicorn.log >&2; fi; kill "$BACKEND" 2>/dev/null || true' EXIT

i=0
until python -c "import urllib.request; urllib.request.urlopen('$FORJA_HTTP_BASE_URL/salud', timeout=2)" 2>/dev/null; do
    i=$((i + 1))
    if [ "$i" -gt 60 ] || ! kill -0 "$BACKEND" 2>/dev/null; then
        echo "hermetico: el backend de prueba no arranco. Ultimas lineas del log:" >&2
        tail -n 20 /work/uvicorn.log >&2
        exit 9
    fi
    sleep 1
done

if [ "$#" -eq 0 ]; then
    set -- /tests
fi
cd /tests
python -m pytest -q -p no:cacheprovider "$@"
