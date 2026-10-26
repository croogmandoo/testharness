# Deploying with Docker

The harness ships with a `Dockerfile` and `docker-compose.yml` that let you run it on any machine with Docker installed. The container includes Playwright's Chromium browser so browser tests work out of the box.

---

## Prerequisites

- Docker 24+ and Docker Compose v2 (`docker compose` not `docker-compose`)
- A `.env` file in the project root with any secrets your app YAML files reference

---

## Quick Start

```bash
# 1. Clone the repo
git clone <repo-url>
cd webtestingharness

# 2. Create your .env file
cp .env.example .env   # or create it manually
# Edit .env — add SLACK_WEBHOOK_URL, ADMIN_PASSWORD, etc.

# 3. Start the harness
docker compose up -d

# 4. Check it's healthy
curl http://localhost:9552/health
# → {"status": "ok"}
```

Open `http://localhost:9552` in your browser. On first run you'll be prompted to create an admin account.

The setup form asks for a **first-run setup token**, so nobody else who can reach the port can claim the admin account before you do. Either read the generated token from the logs (it is printed once, when `/setup` is first opened):

```bash
docker logs harness 2>&1 | grep "setup token"
# → ... WARNING ... First-run setup token: 3q2-...
```

or choose your own by setting `HARNESS_SETUP_TOKEN` in `.env` before the first start. The generated token changes on every restart until the admin account exists, and differs per process, so set `HARNESS_SETUP_TOKEN` if you run more than one worker process.

With no `allowed_users`/`allowed_orgs` configured, a pre-provisioned `github:<login>` account binds to whichever GitHub account first signs in while owning that login.

---

## File Layout

```
.
├── Dockerfile
├── docker-compose.yml
├── .env                  # secrets — never commit this
├── apps/                 # your app YAML definitions (bind-mounted)
├── config.yaml           # harness config (bind-mounted)
└── data/                 # SQLite DB, screenshots, CA bundle (bind-mounted)
    └── screenshots/
```

The three bind-mounts mean **all state lives outside the container**. You can destroy and recreate the container without losing data.

---

## Configuration

### `config.yaml`

The config file is bind-mounted from `./config.yaml` into the container. Create it in the project root before starting:

```yaml
default_environment: production

environments:
  production:
    label: Production
  staging:
    label: Staging

browser:
  headless: true
  timeout_ms: 30000

alerts:
  slack:
    webhook_url: "$SLACK_WEBHOOK_URL"
  discord:
    webhook_url: "$DISCORD_WEBHOOK_URL"
  webhook:
    url: "$WEBHOOK_URL"
    secret: "$WEBHOOK_SECRET"   # optional HMAC-SHA256 signing key

auth:
  session_hours: 8
  secure_cookie: true           # set true when behind HTTPS
  github:
    client_id: "$GITHUB_CLIENT_ID"
    client_secret: "$GITHUB_CLIENT_SECRET"
    default_role: read_only
    # New GitHub sign-ins are refused unless one of these matches.
    # When either list is set it is checked on every login.
    allowed_users: [octocat]
    allowed_orgs: [my-company]
    auto_provision: false        # true = any GitHub account may sign in
```

### `.env` file

Environment variables are passed into the container via `env_file: .env` in `docker-compose.yml`. Any `$VAR` reference in `config.yaml` or app YAML files is resolved from this file.

```bash
# .env
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
WEBHOOK_URL=https://ci.example.com/harness-hook
WEBHOOK_SECRET=mysupersecret

GITHUB_CLIENT_ID=abc123
GITHUB_CLIENT_SECRET=xyz456

# App-specific secrets
SONARR_PASSWORD=admin
RADARR_API_KEY=...

# Per-environment secrets (optional — falls back to the base var if not set)
SONARR_PASSWORD#staging=staging-pass
```

---

## Port

The harness listens on **port 9552** inside the container. The `docker-compose.yml` maps it to the same port on the host:

```yaml
ports:
  - "9552:9552"
```

Change the left side to use a different host port:

```yaml
ports:
  - "8080:9552"
```

---

## Healthcheck

The compose file includes a healthcheck that polls `/health` every 30 seconds:

```yaml
healthcheck:
  test: ["CMD", "python", "-c",
         "import urllib.request; urllib.request.urlopen('http://localhost:9552/health')"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 15s
```

Check health status:

```bash
docker compose ps
```

---

## Common Commands

```bash
# Start in background
docker compose up -d

# View logs (follow)
docker compose logs -f

# Stop
docker compose down

# Rebuild after code changes
docker compose build
docker compose up -d

# Open a shell inside the container
docker compose exec app bash
```

---

## Reverse Proxy (HTTPS)

For production, put the harness behind nginx or Caddy. Set `auth.secure_cookie: true` in `config.yaml` when serving over HTTPS so the session cookie is marked `Secure`.

**Example nginx snippet:**

```nginx
server {
    listen 443 ssl;
    server_name harness.example.com;

    # ... ssl_certificate, ssl_certificate_key ...

    location / {
        proxy_pass         http://localhost:9552;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-Proto https;
    }
}
```

---

## v2 config knobs

Two new optional sections shipped on 2026-05-13. Both have safe defaults
and can be omitted entirely.

```yaml
# Structured logging — root + harness.* loggers go through a rotating
# file handler plus a console handler (systemd / docker journal).
logging:
  path: data/logs/harness.log    # rotating log file (created if missing)
  level: INFO                    # DEBUG | INFO | WARNING | ERROR | CRITICAL
  max_bytes: 10000000            # rotate when file exceeds ~10 MB
  backup_count: 5                # keep N rotated files (.1 .. .N)
  console: true                  # also emit to stderr (set false in containers if journal is enough)

# Remote-worker server-side tunables. Set only if you run remote workers.
worker:
  job_timeout_seconds: 600         # stuck-job reaper threshold
  reaper_interval_seconds: 60      # how often the reaper scans
  max_retries: 3                   # requeue this many times before failing
  version_skew_warning_minors: 2   # minor-version drift before flagging 'stale'
                                   # (different MAJOR is always 'incompatible'
                                   # and refused dispatch — not a knob)
  mtls:                            # optional — see remote-workers.md § "mTLS recipe"
    enabled: false
    client_ca_path: /etc/harness/client-ca.pem
```

The full worker env-var reference (worker-side `HARNESS_*` variables) lives
in [`remote-workers.md`](remote-workers.md); the column-by-column
`/admin/runners` reference lives in [`admin-runners.md`](admin-runners.md).
The section above is the only **central-side** worker config block.

---

## Upgrading

```bash
git pull
docker compose build
docker compose up -d
```

The SQLite database in `data/` is backward-compatible — the harness runs schema migrations automatically on startup.

---

## Troubleshooting

**Container exits immediately:**
```bash
docker compose logs app
```
Common causes: missing `config.yaml`, malformed YAML, or a required env var not set.

**`/health` returns connection refused:**
The container may still be starting. Browser tests start Playwright on the first run, which takes a few seconds. Wait for the healthcheck to pass (`docker compose ps`).

**Browser tests fail inside Docker:**
All required Chromium system dependencies are installed in the `Dockerfile`. If you add custom Playwright flags, make sure `headless: true` is set in `config.yaml` — a display server is not available inside the container.

**Screenshots not saving:**
Ensure `./data` is writable by the process running inside the container.
The container runs as **uid 10001 (harness user)** for security. See the
[Permissions](#permissions) section below for the supported approaches.

<a id="permissions"></a>

### Permissions on bind-mounted volumes

The container image runs the application as **uid 10001 (harness)**. When
you bind-mount a host directory into `/app/data` or `/app/apps`, Docker
preserves the host's ownership. If your host directory is owned by your
regular user (typically uid 1000), the harness user can't write to it —
you'll see `sqlite3.OperationalError: unable to open database file` on
startup.

The image's `entrypoint.sh` handles this automatically when the container
starts as root (the default):

1. Container boots as root
2. Entrypoint chowns `/app/data`, `/app/data/screenshots`, and `/app/apps`
   to uid 10001 (skipped if already correct, so warm restarts are fast)
3. Entrypoint `gosu`-drops privileges to uid 10001 and exec's the app
4. Application runs unprivileged for its entire lifetime

If you override the container user with `user:` in compose, the entrypoint
detects the non-root start, skips the chown, and exec's directly — you've
taken responsibility for permissions:

```yaml
services:
  harness:
    image: ghcr.io/croogmandoo/testharness:latest
    user: "1000:1000"          # match your host uid:gid
    volumes:
      - ./data:/app/data
      - ./apps:/app/apps
```

**Manual fix on an older image** (before the entrypoint was added):

```bash
sudo chown -R 10001:10001 ./data ./apps
docker restart harness
```

**Diagnostic check:**

```bash
docker inspect harness --format '{{range .Mounts}}{{.Source}} -> {{.Destination}} ({{.Mode}}){{println}}{{end}}'
stat -c '%u:%g %n' ./data ./apps
docker exec harness id
```

The three uids should match: host directory owner, container effective
user, and the harness uid in the image (10001 by default).

**`apps/` mounted read-only causes 500 on save:**
The form-based app editor at `/apps/new` writes YAML files into the
`apps/` directory.  Make sure the bind-mount is **rw** (no `:ro` suffix).

---

## Scheduling, Retention, Metrics

The harness runs an in-process APScheduler so no external cron is needed.

### In-process cron

```yaml
# config.yaml
environments:
  production:
    schedule: "*/15 * * * *"          # all apps in this env every 15 min
```

Per-app override (in any `apps/*.yaml`):

```yaml
schedule: "0 */6 * * *"               # this app every 6 hours
```

Day-of-week follows **standard cron** (`0 = Sunday`).  The harness
internally translates to APScheduler's `0 = Monday` convention.

### Run history retention

```yaml
retention_days: 90                    # daily 03:00 UTC prune; null = keep forever
```

The job deletes runs whose `finished_at` is older than the cutoff, plus
their orphaned screenshot files.  In-flight runs are preserved
regardless of age.

### Prometheus integration

```yaml
# prometheus.yml
scrape_configs:
  - job_name: harness
    metrics_path: /metrics
    static_configs:
      - targets: ['harness.internal:9552']
    bearer_token_file: /etc/prometheus/harness-token   # if metrics.bearer_token set
```

Useful queries:

```promql
# Failing tests right now
sum by (app, environment) (harness_test_status == 0)

# Tests that haven't run in over an hour
time() - harness_app_last_run_timestamp_seconds > 3600

# Run failure rate, 1h window
rate(harness_runs_total{status="fail"}[1h])
```

### Public status page

`/status` is unauthenticated and shows aggregate counts (no error
messages).  Disable in `config.yaml` with `status_page.enabled: false`.

---

## Maintenance windows & stale alerts

Suppress alerts during planned downtime:

```yaml
alerts:
  maintenance_windows:
    - name: "Weekly maintenance"
      cron: "0 2 * * 6"               # Sat 02:00 UTC, standard cron
      duration_minutes: 120
    - name: "Holiday freeze"
      start: "2026-12-23T00:00:00Z"
      end:   "2026-12-27T23:59:59Z"

  stale_threshold_minutes: 1440       # 24h — no run in this long -> alert
  stale_realert_hours: 24             # don't re-page within this window
  stale_check_enabled: true           # default true
```

See [`alerting.md`](alerting.md) for full details.

---

## Database backup

Admins can take a consistent online snapshot at any time:

```bash
curl -fO -H "Cookie: session=$ADMIN_SESSION" \
  http://localhost:9552/admin/backup
# saves harness-20260507T184200Z.db
```

Or click **⤓ Download DB Backup** on the audit log page.  The backup
uses SQLite's online `BACKUP` API, so the harness keeps serving requests
during the snapshot.

---

## Container hardening notes

- Runs as non-root **uid 10001 (harness)** — system-account UID range
  preserved to prevent collisions with packaged Linux users
- HEALTHCHECK probes `/health` every 30s
- `.dockerignore` excludes tests, `.git`, `.claude/`, `.swarm/`, etc. —
  the production image has no test code or developer tooling
- Playwright browsers live at `/opt/playwright` (world-readable) so the
  harness uid can launch Chromium without owning the install
