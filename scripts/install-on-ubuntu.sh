#!/usr/bin/env bash
#
# install-on-ubuntu.sh — turnkey installer for a fresh Ubuntu Server VM.
#
# Bring up an Ubuntu Server 22.04 / 24.04 VM, then run:
#
#     curl -fsSL https://raw.githubusercontent.com/croogmandoo/testharness/master/scripts/install-on-ubuntu.sh | sudo bash
#
# The script:
#   - installs Docker Engine + compose plugin (idempotent)
#   - creates /opt/harness/{data,apps} owned by uid 10001
#   - writes a docker-compose.yml that pulls ghcr.io/croogmandoo/testharness:latest
#   - writes a minimal config.yaml (operators edit afterward)
#   - creates a systemd unit so the harness restarts on boot
#   - launches the container; container is reachable on tcp/9552
#
# Once the VM is up + the harness is running, shut it down and use your
# hypervisor's "Export OVF Template" / "Export Appliance" to produce an
# .ova or .ovf you can distribute. See docs/guides/deployment-vmware-ova.md.

set -euo pipefail

# ── tunables ───────────────────────────────────────────────────────────────
INSTALL_DIR="${HARNESS_INSTALL_DIR:-/opt/harness}"
IMAGE="${HARNESS_IMAGE:-ghcr.io/croogmandoo/testharness:latest}"
HARNESS_UID=10001
HARNESS_GID=10001
SERVICE_NAME=harness

# ── helpers ────────────────────────────────────────────────────────────────
log() { printf "\033[1;36m[harness-install]\033[0m %s\n" "$*"; }
die() { printf "\033[1;31m[harness-install]\033[0m %s\n" "$*" >&2; exit 1; }
require_root() {
    if [ "$(id -u)" != "0" ]; then
        die "Run as root (try: sudo bash $0)"
    fi
}
require_ubuntu() {
    if [ ! -f /etc/os-release ]; then
        die "Cannot determine OS; this installer targets Ubuntu Server."
    fi
    # shellcheck disable=SC1091
    . /etc/os-release
    case "${ID:-}" in
        ubuntu|debian) ;;
        *) die "Unsupported OS: ${ID:-unknown}. Tested on Ubuntu 22.04/24.04." ;;
    esac
}

# ── steps ──────────────────────────────────────────────────────────────────

install_docker() {
    if command -v docker >/dev/null 2>&1; then
        log "docker already installed: $(docker --version)"
        return
    fi
    log "installing docker engine + compose plugin..."
    apt-get update -y
    apt-get install -y ca-certificates curl gnupg lsb-release
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    . /etc/os-release
    echo \
        "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
        | tee /etc/apt/sources.list.d/docker.list > /dev/null
    apt-get update -y
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable --now docker
}

prepare_dirs() {
    log "preparing $INSTALL_DIR (owned by uid:$HARNESS_UID)..."
    install -d -m 0755 -o "$HARNESS_UID" -g "$HARNESS_GID" \
        "$INSTALL_DIR/data" "$INSTALL_DIR/data/screenshots" "$INSTALL_DIR/apps"
    # config.yaml stays root-owned (read-only mount in compose)
    install -d -m 0755 "$INSTALL_DIR"
}

write_config() {
    if [ -f "$INSTALL_DIR/config.yaml" ]; then
        log "config.yaml already exists, leaving as-is"
        return
    fi
    log "writing $INSTALL_DIR/config.yaml (minimal default)..."
    cat > "$INSTALL_DIR/config.yaml" <<'EOF'
default_environment: production

auth:
  session_hours: 8
  secure_cookie: false      # set true when behind HTTPS reverse proxy

environments:
  production:
    label: Production
  staging:
    label: Staging

browser:
  headless: true
  timeout_ms: 30000

# Daily Parquet export to Azure Blob/ADLS Gen2 — see docs/guides/analytics.md
# analytics:
#   enabled: true
#   target:
#     type: azure_blob
#     container: synthetics
#     prefix: harness/
#     connection_string: "$AZURE_STORAGE_CONNECTION_STRING"
EOF
}

write_compose() {
    log "writing $INSTALL_DIR/docker-compose.yml..."
    cat > "$INSTALL_DIR/docker-compose.yml" <<EOF
services:
  harness:
    image: $IMAGE
    container_name: harness
    restart: unless-stopped
    ports:
      - "9552:9552"
    volumes:
      - ./data:/app/data
      - ./apps:/app/apps
      - ./config.yaml:/app/config.yaml:ro
    env_file:
      - .env
    healthcheck:
      test: ["CMD", "curl", "--fail", "--silent", "--show-error", "http://127.0.0.1:9552/health"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
EOF
    touch "$INSTALL_DIR/.env"
    chmod 0600 "$INSTALL_DIR/.env"
}

write_systemd_unit() {
    log "writing systemd unit /etc/systemd/system/${SERVICE_NAME}.service..."
    cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=Web Testing Harness (Docker)
Requires=docker.service
After=docker.service network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$INSTALL_DIR
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
}

pull_and_start() {
    log "pulling image $IMAGE..."
    docker pull "$IMAGE"
    log "starting via systemd..."
    systemctl start "$SERVICE_NAME"
}

print_summary() {
    local ip
    ip=$(hostname -I | awk '{print $1}')
    cat <<EOF

────────────────────────────────────────────────────────────────────────
  Harness installed and running.
────────────────────────────────────────────────────────────────────────

  Container:     docker ps
  Logs:          docker logs -f harness
  Restart:       systemctl restart $SERVICE_NAME
  Status:        systemctl status $SERVICE_NAME
  Health probe:  curl http://localhost:9552/health

  Web UI:        http://${ip:-<this-host>}:9552
                 First load redirects to /setup to create admin account.

  Install dir:   $INSTALL_DIR
    config.yaml  $INSTALL_DIR/config.yaml      (edit + restart to apply)
    apps/        $INSTALL_DIR/apps             (drop app YAML files here)
    data/        $INSTALL_DIR/data             (SQLite DB + screenshots)
    .env         $INSTALL_DIR/.env             (secrets — never commit)

  To build an OVA appliance from this VM:
    1. (optional) shut down: sudo poweroff
    2. From your hypervisor (vSphere/Workstation/VirtualBox), use
       "Export OVF Template" with the VM selected.
    3. See docs/guides/deployment-vmware-ova.md for distribution notes.

EOF
}

# ── main ───────────────────────────────────────────────────────────────────
require_root
require_ubuntu

log "starting install..."
install_docker
prepare_dirs
write_config
write_compose
write_systemd_unit
pull_and_start
print_summary
