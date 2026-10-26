# Web Testing Harness

A self-hosted synthetic-monitoring tool: define tests in YAML, run them on
demand or on a schedule, get alerts on failures, and view results in a
modern web dashboard with sparklines and a public status page.

**At a glance**

- 🧪 Three test types: HTTP availability, API (JSON/REST), browser (Playwright)
- 🔗 Variable extraction & chaining — log in, capture token, exercise authenticated endpoints
- ⏱ In-process cron scheduling (no Windows Task Scheduler dependency)
- 🚨 Alerts to Teams / Slack / Discord / email / generic webhook
- 🛌 Maintenance windows + stale-run detection
- 📊 Prometheus `/metrics` endpoint + public `/status` page
- 🛡 Auth: local accounts + LDAP/AD + GitHub OAuth + API keys + 4-role RBAC
- 🔐 Encrypted secrets store + custom CA certificate management
- 📜 Audit log of every admin action
- 🎨 Cyberpunk-tinted dark UI (with light theme), Inter / JetBrains Mono

---

## Prerequisites

- Python 3.11+ (or Docker)

## Quick start (Docker — recommended)

```bash
git clone https://github.com/croogmandoo/testharness && cd testharness
cp .env.example .env                # then edit values you actually use
docker compose up -d
```

Open <http://localhost:9552>.  First load redirects to `/setup` so you can
create the admin account.

## Quick start (Ubuntu Server — turnkey)

For a single-VM deployment without a checkout:

```bash
curl -fsSL https://raw.githubusercontent.com/croogmandoo/testharness/master/scripts/install-on-ubuntu.sh \
  | sudo bash
```

Installs Docker, lays out `/opt/harness/{data,apps,config.yaml}`, registers a
systemd unit, and starts the container. Used as the basis for OVA/OVF
appliance builds — see [`docs/guides/deployment-vmware-ova.md`](docs/guides/deployment-vmware-ova.md).

## Quick start (native Python)

```bash
python -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -r requirements.lock    # reproducible — falls back to requirements.txt
python -m playwright install chromium
cp .env.example .env
python -m web.main                  # serves on :9552
```

---

## Defining apps

Drop YAML files into the `apps/` directory — one per application — or use
the form-based editor at `/apps/new` once the dashboard is running.

### API test

```yaml
# apps/my-api.yaml
app: "My API"
url: "https://api.example.com"
tags: [critical]
environments:
  staging:    "https://staging-api.example.com"
  production: "https://api.example.com"
schedule: "*/15 * * * *"            # optional cron — runs every 15 min
tests:
  - name: "Health check"
    type: api
    endpoint: /health
    expect_status: 200
    max_duration_ms: 500            # SLO — fails the test if too slow

  - name: "Returns JSON"
    type: api
    endpoint: /status
    expect_json:
      status: "ok"
```

### Multi-step API flow (capture token, reuse it)

```yaml
sequential: true                    # forces ordered execution (auto-on with extract:)
tests:
  - name: Login
    type: api
    endpoint: /auth/login
    method: POST
    json: { username: alice, password: $ALICE_PW }
    extract:
      AUTH_TOKEN: $.token           # JSONPath dot-walk
      USER_ID:    $.user.id

  - name: Fetch profile
    type: api
    endpoint: /users/$USER_ID
    headers: { Authorization: "Bearer $AUTH_TOKEN" }
    expect_json: { username: alice }
```

### Browser test

```yaml
- name: "Login flow"
  type: browser
  steps:
    - navigate: /login
    - fill:    { field: "#username", value: $APP_USERNAME }
    - fill:    { field: "#password", value: $APP_PASSWORD }
    - press_key: { field: "#password", key: "Enter" }
    - assert_url_contains: /dashboard
    - hover:    ".profile-menu"
    - select_option: { field: "select#lang", label: "English" }
    - wait_for_function: "() => window.dataLoaded === true"
    - screenshot: null              # captured on assertion failure too
```

### Availability check (HTTP, no parsing)

```yaml
- name: "Site is up"
  type: availability
  expect_status: 200
```

See **[docs/guides/writing-tests.md](docs/guides/writing-tests.md)** for the
full step reference, retry/SLO/extract semantics, and Python-defined tests.

---

## Configuration

Edit `config.yaml`:

```yaml
default_environment: production

environments:
  staging:
    label: "Staging"
    schedule: "0 */1 * * *"                  # hourly — env-default cron
  production:
    label: "Production"
    schedule: "*/15 * * * *"                 # every 15 min

retention_days: 90                           # daily 03:00 UTC prune of old runs

scheduling:
  enabled: true                              # master switch (default true)

metrics:
  enabled: true
  bearer_token: "$METRICS_TOKEN"             # optional — restrict /metrics

status_page:
  enabled: true                              # public /status

alerts:
  stale_threshold_minutes: 1440              # alert if no run in 24h
  stale_realert_hours: 24

  teams:
    webhook_url: "$TEAMS_WEBHOOK_URL"
  slack:
    webhook_url: "$SLACK_WEBHOOK_URL"
  discord:
    webhook_url: "$DISCORD_WEBHOOK_URL"
  email:
    smtp_host: "mail.example.com"
    smtp_port: 587
    from:     "harness@example.com"
    to:       ["ops@example.com"]
    username: "$SMTP_USERNAME"
    password: "$SMTP_PASSWORD"
  webhook:
    url: "$GENERIC_WEBHOOK_URL"              # generic JSON POST per run

  maintenance_windows:                       # alerts suppressed during these
    - name: "Weekly maintenance"
      cron: "0 2 * * 6"                      # standard cron — Sat 02:00 UTC
      duration_minutes: 120
    - name: "Holiday freeze"
      start: "2026-12-23T00:00:00Z"
      end:   "2026-12-27T23:59:59Z"

browser:
  headless: true
  timeout_ms: 30000
  screenshot_diff_threshold: 0.05            # visual regression threshold
```

`$VAR` references in `config.yaml` resolve from `.env` and `os.environ`.
`$VAR#staging` lets you provide environment-specific values; the resolver also
accepts **inline** substitution (`"Bearer $AUTH_TOKEN"`).

`$VAR` references in **app YAML** resolve from the encrypted secrets store,
and from process env only for names listed in `apps_env_allowlist`
(exact names or globs, e.g. `["MYAPP_*"]`). See
[docs/guides/writing-tests.md](docs/guides/writing-tests.md#5-environment-variables).

> **Upgrade note:** app YAML no longer reads arbitrary process environment
> variables. If your apps used `$VAR`s from `.env`/the environment, either move
> them into the secrets store (`/secrets`) or list them in
> `apps_env_allowlist` in `config.yaml`. The harness logs a warning at startup
> for each affected variable; `apps_env_allowlist: ["*"]` restores the old
> behaviour (not recommended).

---

## Endpoints

| Path            | Auth        | Purpose |
|-----------------|-------------|---------|
| `/`             | session     | Dashboard — app tiles with sparklines, tag filters |
| `/app/<a>/<e>`  | session     | App detail — run history, step logs, screenshots |
| `/apps`, `/apps/new` | admin/runner | YAML-free app editor |
| `/secrets`      | admin/runner | Manage encrypted secrets |
| `/api-keys`     | session     | Generate API keys for CI |
| `/users`        | admin       | User CRUD + role/active toggles |
| `/admin/audit`  | admin       | Audit log + DB backup download |
| `/admin/ldap`, `/admin/ca-certs` | admin | Auth provider + CA bundle config |
| `/status`       | **public**  | Aggregate health page (auto-refresh) |
| `/metrics`      | **public**\* | Prometheus exposition format |
| `/docs`, `/redoc` | **public** | OpenAPI / Swagger UI |
| `/health`       | **public**  | Healthcheck (used by Docker `HEALTHCHECK`) |

\* `/metrics` accepts an optional `Authorization: Bearer <token>` if
`metrics.bearer_token` is configured.

---

## REST API

Trigger a run for one app:

```bash
curl -X POST http://localhost:9552/api/runs \
  -H "Content-Type: application/json" \
  -d '{"app": "My API", "environment": "production"}'
```

Trigger every app with a tag (for "run all critical"-style operations):

```bash
curl -X POST http://localhost:9552/api/runs \
  -H "Content-Type: application/json" \
  -d '{"tag": "critical", "environment": "production"}'
```

Trigger every app:

```bash
curl -X POST http://localhost:9552/api/runs \
  -H "Content-Type: application/json" \
  -d '{"environment": "production"}'
```

Authenticate either via session cookie or with an API key:

```bash
curl http://localhost:9552/api/runs/<run_id> \
  -H "Authorization: Bearer hth_<your-key>"
```

See **[docs/reference/api.md](docs/reference/api.md)** for full reference,
**[docs/guides/api-quickstart.md](docs/guides/api-quickstart.md)** for
end-to-end examples.

---

## Operations

### Scheduling

Use `schedule:` (standard 5-field cron, `0=Sunday` for day-of-week) at
either the **environment** or **per-app** level — the per-app override
wins.  Schedules run inside the harness process via APScheduler; no
external cron is required.

### Metrics & status

Point Prometheus at `http://harness:9552/metrics`.  The exposition
includes:

- `harness_test_status{app,environment,test}` (1 = passing, 0 = failing, -1 = unknown)
- `harness_test_last_duration_seconds{...}`
- `harness_app_last_run_timestamp_seconds{app,environment}`
- `harness_runs_total{app,environment,status}` (counter)

The public `/status` page is unauthenticated, auto-refreshes every 60s,
and exposes only aggregate up/down counts — never error messages.

### DB backup

Admins can take a consistent online snapshot via `GET /admin/backup` (or
the button on `/admin/audit`).  The endpoint streams a SQLite DB file
that's safe to copy while the harness is still running.

### Retention

`retention_days: 90` enables a daily 03:00 UTC job that deletes runs and
their test results older than 90 days, plus orphaned screenshot files.
Active runs are preserved regardless of age.

### Maintenance windows

Define recurring (cron) or fixed (start/end ISO) windows in
`alerts.maintenance_windows`.  Run records, dashboards, audit log, and
metrics still see what happens during the window — only the alert
**dispatch** is suppressed.

### Stale-run alerts

If an app's most-recent completed run is older than
`alerts.stale_threshold_minutes` (default 1440 = 24h), the harness
dispatches a STALE alert through your configured channels.  Re-alerts
honor `alerts.stale_realert_hours` (default 24h) per app/env to avoid
hourly noise; a fresh run clears the dedup automatically.

See **[docs/guides/alerting.md](docs/guides/alerting.md)** for the full
alerting story.

---

## Project structure

```
testharness/
├── apps/                     ← your app YAML files (rw — UI writes here)
├── harness/                  ← core engine
│   ├── api.py                ← API test runner (extract: lives here)
│   ├── auto_scheduler.py     ← APScheduler integration + cron translator
│   ├── browser.py            ← Playwright steps (select_option, hover, …)
│   ├── db.py                 ← SQLite layer
│   ├── maintenance.py        ← maintenance-window predicate
│   ├── runner.py             ← orchestrates a single run
│   ├── retention.py          ← old-run pruning
│   ├── staleness.py          ← stale-run detection
│   └── alerts.py             ← Teams / Slack / Discord / email / webhook
├── web/                      ← FastAPI app
│   ├── main.py               ← create_app + lifespan + middleware
│   ├── routes/               ← dashboard / api / admin / etc.
│   ├── templates/            ← Jinja2 HTML
│   └── static/style.css      ← design system (Inter + JetBrains Mono)
├── tests/                    ← pytest (377 tests)
├── data/                     ← SQLite DB + screenshots (gitignored)
├── config.yaml               ← environments + alerts + scheduling
├── docker-compose.yml
├── Dockerfile                ← multi-stage, runs as uid 10001
└── .env                      ← credentials (gitignored, never commit)
```

---

## Running the test suite

```bash
pytest                            # 377 tests, ~30 seconds
```

Pytest defaults: see `pytest.ini`.  Browser-engine tests use mocks (no
real Playwright launch); the screenshot-diff test uses a tiny generated
PNG.

---

## Documentation

- **[Writing tests](docs/guides/writing-tests.md)** — full step reference,
  variable chaining, SLO, retry, sequential mode
- **[Alerting](docs/guides/alerting.md)** — channels, maintenance windows,
  stale-run detection, generic webhook payload
- **[API quickstart](docs/guides/api-quickstart.md)** — common curl recipes
- **[Deployment](docs/guides/deployment.md)** — Docker, native, reverse
  proxy, Prometheus integration, retention, hardening
- **[Troubleshooting](docs/guides/troubleshooting.md)**
- **[API reference](docs/reference/api.md)**
- **[Auth reference](docs/reference/auth.md)** — RBAC roles, audit log,
  LDAP setup, API keys, OAuth
- **[Architecture decisions](docs/architecture/decisions.md)**

---

## License

See `LICENSE`.
