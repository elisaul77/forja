#!/bin/sh
# Forja server entrypoint (F0.2, ADR-0013, plans/maestro/secuencias/07).
#
# Runs as root only long enough to:
#   1. move the legacy token into the private secret volume (0400, uid 1000)
#      without ever printing it;
#   2. migrate documentos_data to uid 1000 ONCE (marker .migrado-uid):
#      list what is not 1000:1000, chown -h exactly that list, never -R,
#      never touch file contents;
#   3. prepare the staging volume shared with forja-sandbox;
# then drops to uid 1000 with setpriv (never a Python pre-exec hook; with
# no_new_privs and an empty capability bounding set) and execs the server
# command.
#
# Every path below lives in a volume the server (uid 1000) can write to, so
# root never follows a symlink here: chown -h, symlinks refused, temp files
# created with mktemp in the destination directory (never a fixed name).
#
#   entrypoint.sh --dry-run   only prints what the migration would change.
#
# Never add `set -x` here: the token path is handled in this script.
set -eu

UID_SERVIDOR=1000
GID_SERVIDOR=1000
DOCS="${FORJA_DATA_DIR:-/data/documentos}"
MARCADOR="$DOCS/.migrado-uid"
LEGADO="$DOCS/.forja_token"
SECRETO="${FORJA_SECRET_FILE:-/run/secrets/forja_token}"
STAGING="${FORJA_STAGING_DIR:-/staging}"

log() { echo "[entrypoint] $*"; }

listar_migracion() {
    # NUL-separated list of every path under $DOCS (same filesystem, no
    # symlink following) whose owner or group is not the server's.
    find "$DOCS" -xdev \( -path "$LEGADO" -o -path "$MARCADOR" \) -prune -o \
        \( ! -uid "$UID_SERVIDOR" -o ! -gid "$GID_SERVIDOR" \) -print0
}

resumen_migracion() {
    lista="$1"
    total=$(tr -cd '\0' < "$lista" | wc -c)
    log "migracion de uid: $total rutas a cambiar a $UID_SERVIDOR:$GID_SERVIDOR (chown -h, sin -R)"
    if [ "$total" -gt 0 ]; then
        log "por dueño actual (uid:gid tipo):"
        xargs -0 -r stat -c '%u:%g %F' -- < "$lista" | sort | uniq -c | sed 's/^/[entrypoint]   /'
        log "por entrada de primer nivel:"
        tr '\0' '\n' < "$lista" | sed "s#^$DOCS/\{0,1\}##" | cut -d/ -f1 | sed 's/^$/(raiz)/' \
            | sort | uniq -c | sort -rn | head -n 25 | sed 's/^/[entrypoint]   /'
        enlaces=$(xargs -0 -r stat -c '%h %F' -- < "$lista" | awk '$1 > 1 && $2 != "directory"' | wc -l)
        log "archivos con mas de un enlace duro: $enlaces"
        log "primeras rutas:"
        tr '\0' '\n' < "$lista" | head -n 15 | sed 's/^/[entrypoint]   /'
    fi
}

if [ "${1:-}" = "--dry-run" ]; then
    lista=$(mktemp)
    listar_migracion > "$lista"
    if [ -e "$MARCADOR" ]; then
        log "dry-run: marcador $MARCADOR presente; el arranque real no migraria nada"
    fi
    resumen_migracion "$lista"
    if [ -f "$LEGADO" ]; then
        log "dry-run: el token legado se trasladaria a $SECRETO (0400, uid $UID_SERVIDOR)"
    fi
    rm -f "$lista"
    exit 0
fi

if [ "$(id -u)" != "0" ]; then
    exec "$@"
fi

rechazar_enlace() {
    if [ -L "$1" ]; then
        log "error: $1 es un enlace simbolico; se rechaza el arranque"
        exit 1
    fi
}

# 1. Token -> secret volume, never printed (redirect/cmp/rm only).
DIR_SECRETO="$(dirname "$SECRETO")"
rechazar_enlace "$DIR_SECRETO"
mkdir -p "$DIR_SECRETO"
chown -h "$UID_SERVIDOR:$GID_SERVIDOR" "$DIR_SECRETO"
chmod 0700 "$DIR_SECRETO"
rechazar_enlace "$SECRETO"
if [ -f "$LEGADO" ] && [ ! -L "$LEGADO" ]; then
    if [ ! -e "$SECRETO" ]; then
        tmp=$(mktemp "$DIR_SECRETO/.forja_token.XXXXXX")
        cat -- "$LEGADO" > "$tmp"
        chown -h "$UID_SERVIDOR:$GID_SERVIDOR" "$tmp"
        chmod 0400 "$tmp"
        mv -f -- "$tmp" "$SECRETO"
    fi
    if cmp -s "$LEGADO" "$SECRETO"; then
        rm -f "$LEGADO"
        log "token trasladado a $SECRETO"
    else
        log "aviso: el token legado difiere del secreto; se conserva el legado sin tocar"
    fi
fi
if [ -e "$SECRETO" ]; then
    rechazar_enlace "$SECRETO"
    chown -h "$UID_SERVIDOR:$GID_SERVIDOR" "$SECRETO"
    chmod 0400 "$SECRETO"
fi

# 2. One-time uid migration of documentos_data.
if [ -d "$DOCS" ] && [ ! -e "$MARCADOR" ]; then
    lista=$(mktemp)
    listar_migracion > "$lista"
    resumen_migracion "$lista"
    xargs -0 -r chown -h "$UID_SERVIDOR:$GID_SERVIDOR" -- < "$lista"
    total=$(tr -cd '\0' < "$lista" | wc -c)
    rm -f "$lista"
    tmp=$(mktemp "$DOCS/.migrado-uid.XXXXXX")
    printf 'migrado %s rutas=%s uid=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$total" "$UID_SERVIDOR" > "$tmp"
    chown -h "$UID_SERVIDOR:$GID_SERVIDOR" "$tmp"
    chmod 0644 "$tmp"
    mv -f -- "$tmp" "$MARCADOR"
    log "migracion de uid terminada ($total rutas); marcador $MARCADOR"
fi

# 3. Staging shared with forja-sandbox (see app/sandbox/protocolo.py).
for ruta in "$STAGING" "$STAGING/trabajos" "$STAGING/ejecutor"; do
    rechazar_enlace "$ruta"
done
mkdir -p "$STAGING/trabajos" "$STAGING/ejecutor"
chown -h "$UID_SERVIDOR:$GID_SERVIDOR" "$STAGING" "$STAGING/trabajos"
chmod 0711 "$STAGING" "$STAGING/trabajos"
chown -h 65534:65534 "$STAGING/ejecutor"
chmod 0755 "$STAGING/ejecutor"
find "$STAGING/trabajos" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +

export HOME=/tmp
exec setpriv --reuid="$UID_SERVIDOR" --regid="$GID_SERVIDOR" --clear-groups --inh-caps=-all \
    --bounding-set=-all --no-new-privs -- "$@"
