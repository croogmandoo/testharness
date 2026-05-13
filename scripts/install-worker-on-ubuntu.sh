#!/usr/bin/env bash
#
# install-worker-on-ubuntu.sh — turnkey installer for a native-Linux worker.
#
# For a Docker-based deployment, see worker/Dockerfile + worker/README.md.
# This script is for hosts that can't / don't run Docker — it sets up a
# system Python venv, installs the worker, and registers a systemd unit.
#
# Required env vars (pass via sudo -E or inline):
#   HARNESS_URL              e.g. https://harness.example.com
#   HARNESS_API_KEY          e.g. hth_<your-key>
#   HARNESS_WORKER_NAME      unique name for this worker
#
# Optional:
#   HARNESS_WORKER_TAGS      comma-separated, e.g. "eu,media"
#   HARNESS_CAPABILITIES     default: "api,availability,browser"
#   HARNESS_CAPACITY         default: 4
#   HARNESS_POLL_INTERVAL    default: 10
#   HARNESS_VERIFY_TLS       default: true
#   HARNESS_REF              git ref to install (default: master)
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/croogmandoo/testharness/master/scripts/install-worker-on-ubuntu.sh \
#     | sudo HARNESS_URL=https://harness.example.com \
#            HARNESS_API_KEY=hth_xxx \
#            HARNESS_WORKER_NAME=eu-worker-1 \
#            HARNESS_WORKER_TAGS=eu,media \
#            bash

set -euo pipefail

INSTALL_DIR="${HARNESS_WORKER_INSTALL_DIR:-/opt/harness-worker}"
REF="${HARNESS_REF:-master}"
SERVICE_NAME=harness-worker
HARNESS_UID=10002
HARNESS_GID=10002

log() { printf "\033[1;36m[harness-worker-install]\033[0m %s\n" "$*"; }
die() { printf "\033[1;31m[harness-worker-install]\033[0m %s\n" "$*" >&2; exit 1; }

require_root() {
    [ "$(id -u)" = "0" ] || die "run as root (sudo bash $0)"
}
require_env() {
    for v in HARNESS_URL HARNESS_API_KEY HARNESS_WORKER_NAME; do
        if [ -z "${!v:-}" ]; then
            die "required env var $v is not set"
        fi
    done
}

install_system_deps() {
    log "installing system deps..."
    apt-get update -y
    apt-get install -y --no-install-recommends \
        python3 python3-venv python3-pip git curl \
        ca-certificates fonts-liberation \
        libasound2 libatk-bridge2.0-0 libatk1.0-0 libcups2 libdbus-1-3 \
        libdrm2 libgbm1 libgtk-3-0 libnspr4 libnss3 libx11-6 \
        libxcomposite1 libxdamage1 libxext6 libxfixes3 libxrandr2 \
        libxshmfence1 libxss1 libxtst6 xdg-utils
}

create_user() {
    if id -u "$HARNESS_UID" >/dev/null 2>&1; then
        log "uid $HARNESS_UID already present"
        return
    fi
    log "creating system user harness-worker (uid $HARNESS_UID)..."
    groupadd --system --gid "$HARNESS_GID" harness-worker || true
    useradd --system --uid "$HARNESS_UID" --gid "$HARNESS_GID" \
            --home-dir "$INSTALL_DIR" --no-create-home --shell /usr/sbin/nologin \
            harness-worker
}

install_code() {
    log "fetching worker code to $INSTALL_DIR (ref=$REF)..."
    if [ -d "$INSTALL_DIR/.git" ]; then
        git -C "$INSTALL_DIR" fetch --tags origin
        git -C "$INSTALL_DIR" reset --hard "origin/${REF}" 2>/dev/null \
            || git -C "$INSTALL_DIR" reset --hard "$REF"
    else
        git clone --depth 1 --branch "$REF" \
            https://github.com/croogmandoo/testharness.git "$INSTALL_DIR"
    fi
    chown -R "$HARNESS_UID:$HARNESS_GID" "$INSTALL_DIR"
}

setup_venv() {
    log "creating Python venv + installing requirements..."
    sudo -u harness-worker python3 -m venv "$INSTALL_DIR/.venv"
    sudo -u harness-worker "$INSTALL_DIR/.venv/bin/pip" install --quiet --upgrade pip
    sudo -u harness-worker "$INSTALL_DIR/.venv/bin/pip" install --quiet \
        -r "$INSTALL_DIR/requirements.txt"
    log "installing Playwright chromium (may take a few minutes)..."
    sudo -u harness-worker "$INSTALL_DIR/.venv/bin/python" -m playwright install chromium
}

write_env_file() {
    log "writing $INSTALL_DIR/.env (mode 0600)..."
    cat > "$INSTALL_DIR/.env" <<EOF
HARNESS_URL=${HARNESS_URL}
HARNESS_API_KEY=${HARNESS_API_KEY}
HARNESS_WORKER_NAME=${HARNESS_WORKER_NAME}
HARNESS_WORKER_TAGS=${HARNESS_WORKER_TAGS:-}
HARNESS_CAPABILITIES=${HARNESS_CAPABILITIES:-api,availability,browser}
HARNESS_CAPACITY=${HARNESS_CAPACITY:-4}
HARNESS_POLL_INTERVAL=${HARNESS_POLL_INTERVAL:-10}
HARNESS_HEARTBEAT_INTERVAL=${HARNESS_HEARTBEAT_INTERVAL:-30}
HARNESS_VERIFY_TLS=${HARNESS_VERIFY_TLS:-true}
EOF
    chown "$HARNESS_UID:$HARNESS_GID" "$INSTALL_DIR/.env"
    chmod 0600 "$INSTALL_DIR/.env"
}

write_systemd_unit() {
    log "writing systemd unit /etc/systemd/system/${SERVICE_NAME}.service..."
    cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=Harness Remote Worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=harness-worker
Group=harness-worker
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$INSTALL_DIR/.env
ExecStart=$INSTALL_DIR/.venv/bin/python -m worker.worker
Restart=on-failure
RestartSec=10
TimeoutStopSec=70

# Hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=$INSTALL_DIR

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
}

start_and_summary() {
    systemctl restart "$SERVICE_NAME"
    sleep 2
    log "service status:"
    systemctl --no-pager --lines=10 status "$SERVICE_NAME" || true
    cat <<EOF

────────────────────────────────────────────────────────────────────────
  Harness worker installed.
────────────────────────────────────────────────────────────────────────

  Service:   systemctl status $SERVICE_NAME
  Logs:      journalctl -u $SERVICE_NAME -f
  Restart:   systemctl restart $SERVICE_NAME
  Env:       $INSTALL_DIR/.env   (edit + restart to apply)
  Source:    $INSTALL_DIR        (git clone at ref=$REF)

  Verify registration:
    Open https://${HARNESS_URL#https://}/admin/runners  in the central UI
    The worker '${HARNESS_WORKER_NAME}' should appear online within 30s.

  To pin apps to this worker, add to the app YAML:
    run_on: ${HARNESS_WORKER_NAME}

  Or by tag (load-balanced across all workers with that tag):
    run_on:
      tag: <one of: ${HARNESS_WORKER_TAGS:-(no tags set)}>

EOF
}

# ── main ───────────────────────────────────────────────────────────────────
require_root
require_env

log "starting worker install (ref=$REF, name=$HARNESS_WORKER_NAME)..."
install_system_deps
create_user
install_code
setup_venv
write_env_file
write_systemd_unit
start_and_summary
