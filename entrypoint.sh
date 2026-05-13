#!/bin/sh
# Container entrypoint.
#
# Bind-mounted host directories arrive with their host's ownership (often
# uid 1000), which the in-container `harness` user (uid 10001) cannot write.
# This script runs as root, normalises ownership + creates required runtime
# files, then drops privileges and exec's the application.
#
# If the operator pins a specific uid via docker's --user / compose `user:`
# field, the entrypoint detects the non-root start and skips the chown
# (the operator has taken responsibility for permissions in that case).
set -e

HARNESS_UID=10001
HARNESS_GID=10001
HARNESS_USER=harness

# Ensure a directory exists and is owned by the harness user. Best-effort:
# on a read-only or root-squashed mount, chown will fail and we'll warn
# rather than refuse to start (the preflight check inside web/main.py will
# produce a more actionable message if writes really do fail later).
ensure_owned() {
    target="$1"
    mkdir -p "$target" 2>/dev/null || true
    if [ ! -d "$target" ]; then
        return 0
    fi
    current=$(stat -c '%u:%g' "$target" 2>/dev/null || echo "?:?")
    if [ "$current" != "${HARNESS_UID}:${HARNESS_GID}" ]; then
        if ! chown -R "${HARNESS_UID}:${HARNESS_GID}" "$target" 2>/dev/null; then
            echo "[entrypoint] warning: could not chown $target (was $current);" \
                 "the container will continue but writes may fail" >&2
        fi
    fi
}

if [ "$(id -u)" = "0" ]; then
    # Running as root — normalise volumes, then drop privileges.
    ensure_owned /app/data
    ensure_owned /app/data/screenshots
    ensure_owned /app/apps

    # Pre-create the SQLite DB file so sqlite3.connect() can open it even
    # before the schema runs. The harness's init_schema() creates the file
    # on first connect, but doing it here gives us deterministic ownership.
    if [ ! -e /app/data/harness.db ]; then
        : > /app/data/harness.db
        chown "${HARNESS_UID}:${HARNESS_GID}" /app/data/harness.db
    fi

    exec gosu "${HARNESS_USER}" "$@"
fi

# Non-root start (operator set `user:` in compose, etc.) — exec directly.
# The web/main.py preflight check will produce a friendly error if the
# mounted directories aren't writable by this uid.
exec "$@"
