# Web Testing Harness — Design Spec

**Date:** 2026-04-11
**Status:** Approved

---

## Overview

A web/application testing harness for verifying that internal applications are available, functional, and returning correct results before and after changes reach production. Built with Python, Playwright, and FastAPI. Designed for teams with mixed technical skill — developers configure tests, ops/QA staff trigger runs and view results.

---

## Context & Requirements

- **Users:** Developers (configure tests) and non-technical ops/QA staff (trigger runs, view results)
- **Applications under test:** Mix of browser-based web UIs and REST APIs; 5–20 apps across multiple environments
- **Environments:** Primarily staging and production; each app defines its own URLs per environment
- **Triggers:** Manual on-demand via web UI, scheduled automated runs, future CI/CD via REST API
- **On failure:** Alert team via Teams webhook and/or email on state transitions (pass→fail, fail→pass); log all results to database
- **Infrastructure:** Primarily Windows on-prem servers; some Azure (App Service / VM) for future hosting
- **Dashboard auth:** None — internal tool assumed to be network-restricted (VPN or trusted subnet). This assumption must be validated at deployment time; if broader network access is required, authentication must be added before going live.

---

## Architecture

```
Triggers
  ├── Web UI (ops staff click "Run")
  ├── Windows Task Scheduler → REST API (scheduled)
  └── REST API (future CI/CD)
        │
        ▼
  FastAPI Application (core)
  ├── Test runner orchestration
  ├── Result storage (SQLite)
  └── Alert dispatch
        │
        ├── Playwright (browser tests)
        └── httpx (API tests)
              │
              ├── SQLite (result history + logs)
              ├── Dashboard (pass/fail status per app)
              └── Alerts (Teams / email on state transition)
```

---

## Test Definition Format

Two formats, used together:

### YAML (default — covers ~80% of cases)

Each app under test has a `.yaml` file in `apps/`. Readable by non-developers.

```yaml
app: "Customer Portal"
url: "https://portal.company.com"
environments:
  staging: "https://staging.portal.company.com"
  production: "https://portal.company.com"

tests:
  - name: "Page is reachable"
    type: availability
    expect_status: 200

  - name: "Login works"
    type: browser
    steps:
      - navigate: /login
      - fill: {field: "#username", value: "$PORTAL_USERNAME"}
      - fill: {field: "#password", value: "$PORTAL_PASSWORD"}
      - click: "button[type=submit]"
      - assert_url_contains: /dashboard
      - assert_text: "Welcome"

  - name: "API health check"
    type: api
    endpoint: /api/health
    method: GET
    expect_status: 200
    expect_json:
      status: "ok"
```

**Credential substitution:** Any `$VARIABLE_NAME` value in YAML is resolved from the process environment at runtime. The `.env` file in the project root is loaded automatically on startup via `python-dotenv`. Variables are never stored in YAML or committed to git. Each app should use namespaced variable names (e.g. `PORTAL_USERNAME`, `HR_USERNAME`) to avoid collisions.

**YAML base URL resolution:** For browser and API tests, the runner resolves the base URL as follows:
1. Look up the target environment key (e.g. `staging`) in the app's `environments` map
2. If found, use that URL as the base (e.g. `https://staging.portal.company.com`)
3. If the key is not present in `environments`, fall back to the top-level `url` value
4. If neither exists, fail the run with a `ConfigError` before executing any tests
5. Relative paths in `navigate` steps (e.g. `/login`) are prepended with the resolved base URL; absolute URLs are used as-is

### Python (escape hatch — complex flows)

Apps with unusual flows get a `.py` file instead. Full Playwright API available.

```python
from harness import AppTest, browser_test, api_test

class CustomerPortalTest(AppTest):
    name = "Customer Portal"
    base_url = "https://portal.company.com"

    @api_test
    async def test_health(self, client):
        r = await client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    @browser_test
    async def test_login(self, page):
        await page.goto("/login")
        await page.fill("#username", self.env("PORTAL_USERNAME"))
        await page.fill("#password", self.env("PORTAL_PASSWORD"))
        await page.click("button[type=submit]")
        await page.wait_for_url("**/dashboard")
        assert "Welcome" in await page.text_content("body")
```

**`AppTest` base class contract:**
- `self.env(key)` — resolves a variable from the process environment; raises `ConfigError` if missing
- `self.base_url` — class attribute; the default URL used when no environment-specific override is defined
- `self.environments` — optional class attribute dict mapping environment key to URL (e.g. `{"staging": "https://staging.portal.company.com", "production": "https://portal.company.com"}`). If defined, the runner uses the entry matching the target environment; falls back to `self.base_url` if the key is absent. If neither is defined, raises `ConfigError`. This mirrors the YAML base URL resolution algorithm exactly.
- `@browser_test` — decorator that injects a Playwright `page` object; handles browser lifecycle, captures screenshot on failure, records step timing. Screenshot path uses the same sanitised test name as YAML tests (see Screenshot Storage).
- `@api_test` — decorator that injects an `httpx.AsyncClient` pre-configured with the resolved `base_url` and any auth headers defined on the class
- Test methods are discovered automatically by name prefix `test_`

---

## Web Dashboard

A FastAPI-served web interface accessible to all staff.

### Main View — Application Status Table

- Columns: Application name, Status (Pass/Fail), Tests passed (e.g. 3/3), Last run time, Run button
- Environment switcher (Production / Staging) at the top
- "Run All" button to trigger all tests for selected environment
- Search/filter by app name
- Rows highlighted on failure
- If a run is already in progress for an app, its Run button shows "Running..." and is disabled; a second trigger is rejected with a clear message

### Failure Detail View

Accessible by clicking any failed row. Shows:

- **Error message** — what assertion or step failed
- **Screenshot** — browser screenshot captured at the moment of failure
- **Step log** — each test step with pass/fail status and timing
- **Run history** — coloured pass/fail blocks for recent runs (highlights recurring vs new failures)
- **Re-run button** — trigger just this test on demand

---

## REST API

The FastAPI app exposes the following endpoints (used by the dashboard, scheduler, and future CI/CD):

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/runs` | Trigger a run. Body: `{"app": "customer-portal", "environment": "production", "triggered_by": "api"}`. Omit `app` to run all. `triggered_by` is optional; defaults to `"api"`. The dashboard passes `"ui"`. Returns `run_id`. |
| `GET` | `/api/runs/{run_id}` | Poll run status. Returns status (`pending`, `running`, `complete`), results summary, and per-test outcomes. |
| `GET` | `/api/apps` | List all configured apps with their current state per environment. |
| `GET` | `/api/results/{app}/{environment}` | Full result history for an app+environment combination. |

No authentication on these endpoints (internal network assumption — see dashboard auth note above).

---

## Scheduling

Scheduling is handled **externally** by Windows Task Scheduler. The harness does not manage its own schedule internally — it only responds to API calls.

Setup: Windows Task Scheduler is configured to call `POST /api/runs` on the desired interval (e.g. every hour for production, every 15 minutes for staging). This keeps the harness stateless with respect to scheduling and avoids a background thread that must stay alive inside the process.

`harness/scheduler.py` is not a scheduling engine. It is a thin utility module that generates the Windows Task Scheduler XML configuration from `config.yaml`, so developers can set up scheduled runs without manually editing the Task Scheduler UI.

---

## Concurrent Run Handling

Runs are tracked per `(app, environment)` pair. If a run is triggered for a combination that is already `running`:

- The API returns HTTP 409 with message: `"A run for {app} ({environment}) is already in progress"`
- The dashboard disables the Run button and shows "Running..." for that row
- "Run All" skips any app currently running and triggers the rest

Runs for different apps execute concurrently. Runs for the same app+environment are serialised.

---

## State Tracking & Alert Logic

State is tracked per `(test_name, app, environment)` triple. This means a test can pass in staging and fail in production independently.

- **Initial state:** `unknown`. A first-run failure transitions `unknown → failing` and **does trigger an alert**, since the intent is to catch problems immediately.
- **Authoritative state:** the result of the most recent completed run (not a rolling average).
- **Alert fires on:** `passing → failing` or `unknown → failing`
- **Resolution fires on:** `failing → passing`
- No alert fires when state does not change (e.g. a test that has been failing continues to fail).

---

## Database Schema

SQLite database at `data/harness.db`.

```sql
-- One row per triggered run
CREATE TABLE runs (
    id          TEXT PRIMARY KEY,      -- UUID
    app         TEXT NOT NULL,
    environment TEXT NOT NULL,
    triggered_by TEXT NOT NULL,        -- 'ui' (dashboard), 'api' (all external callers including scheduler and CI/CD)
    status      TEXT NOT NULL,         -- 'pending', 'running', 'complete'
    started_at  TEXT,                  -- ISO8601
    finished_at TEXT
);

-- One row per individual test within a run
-- app and environment are denormalised from runs for simpler state queries
CREATE TABLE test_results (
    id          TEXT PRIMARY KEY,      -- UUID
    run_id      TEXT NOT NULL REFERENCES runs(id),
    app         TEXT NOT NULL,         -- denormalised from runs.app
    environment TEXT NOT NULL,         -- denormalised from runs.environment
    test_name   TEXT NOT NULL,
    status      TEXT NOT NULL,         -- 'pass', 'fail', 'error'
    error_msg   TEXT,
    step_log    TEXT,                  -- JSON array of {step, status, duration_ms}
    screenshot  TEXT,                  -- path relative to data/screenshots/, or NULL
    duration_ms INTEGER,
    finished_at TEXT
);

-- Index to support state queries and history lookups without joins
CREATE INDEX idx_test_results_lookup ON test_results(app, environment, test_name, finished_at);

-- Current state per (test, app, environment) — used for alert logic
CREATE TABLE app_state (
    app         TEXT NOT NULL,
    environment TEXT NOT NULL,
    test_name   TEXT NOT NULL,
    state       TEXT NOT NULL,         -- 'unknown', 'passing', 'failing'
    since       TEXT NOT NULL,         -- ISO8601 of last state change
    PRIMARY KEY (app, environment, test_name)
);
```

---

## Screenshot Storage

- Stored at: `data/screenshots/{app}/{environment}/{run_id}/{test_name_slug}.png`
- `{run_id}` is the UUID value from `runs.id` for that run
- `{test_name_slug}` is the test name normalised to a filesystem-safe string: lowercased, whitespace replaced with hyphens, all characters not in `[a-z0-9-]` removed. Example: `"Login works!"` → `"login-works"`
- Captured by the `@browser_test` decorator at the moment of failure (no screenshot on pass)
- `test_results.screenshot` stores the path **relative to `data/screenshots/`** — e.g. `customer-portal/production/{run_id}/login-works.png`
- Served by FastAPI with a static mount at `/screenshots` pointing to `data/screenshots/`, giving URLs of the form: `GET /screenshots/customer-portal/production/{run_id}/login-works.png`
- No automated retention policy in v1 — manual cleanup. A future task can add pruning.

---

## Alert Configuration

Alerts configured in `config.yaml`:

```yaml
alerts:
  teams:
    webhook_url: "$TEAMS_WEBHOOK_URL"   # resolved from environment
  email:
    smtp_host: "mail.company.com"
    smtp_port: 587
    from: "harness@company.com"
    to:
      - "ops-team@company.com"
    username: "$SMTP_USERNAME"
    password: "$SMTP_PASSWORD"
```

Either `teams` or `email` (or both) can be omitted to disable that channel.

---

## Global Config Schema

`config.yaml` at the project root:

```yaml
# Default environment for dashboard on load
default_environment: production

# Named environments (apps reference these by key)
environments:
  staging:
    label: "Staging"
  production:
    label: "Production"

# Alert configuration (see Alert Configuration section)
alerts:
  teams:
    webhook_url: "$TEAMS_WEBHOOK_URL"
  email:
    smtp_host: "mail.company.com"
    smtp_port: 587
    from: "harness@company.com"
    to: ["ops-team@company.com"]
    username: "$SMTP_USERNAME"
    password: "$SMTP_PASSWORD"

# Browser settings
browser:
  headless: true
  timeout_ms: 30000
```

---

## Project Structure

```
webtestingharness/
├── apps/                    # One file per application under test
│   ├── customer-portal.yaml
│   ├── hr-system.yaml
│   └── inventory-api.py     # Python for complex flows
├── harness/                 # Core framework
│   ├── runner.py            # Test orchestration
│   ├── browser.py           # Playwright wrapper + @browser_test decorator
│   ├── api.py               # httpx wrapper + @api_test decorator
│   ├── loader.py            # Discovers and loads apps/ (YAML + Python)
│   ├── alerts.py            # Teams/email dispatch
│   └── scheduler.py         # Generates Windows Task Scheduler XML from config
├── web/                     # FastAPI application
│   ├── main.py              # App entrypoint, route registration
│   ├── routes/
│   │   ├── dashboard.py     # HTML dashboard routes
│   │   └── api.py           # REST API routes
│   ├── templates/           # Jinja2 HTML templates
│   └── static/              # CSS, JS
├── data/                    # SQLite DB + failure screenshots (gitignored)
│   ├── harness.db
│   └── screenshots/
├── config.yaml              # Environments, alert settings, browser config
├── .env                     # Credentials (not committed to git)
├── .gitignore
└── requirements.txt
```

---

## Key Dependencies

| Package | Purpose |
|---|---|
| `playwright` | Browser automation |
| `httpx` | Async HTTP client for API tests |
| `fastapi` | Web framework for dashboard + REST API |
| `uvicorn` | ASGI server |
| `pyyaml` | YAML test definition parsing |
| `jinja2` | Dashboard HTML templating |
| `python-dotenv` | `.env` credential loading |

---

## Out of Scope (for now)

- Authentication on the dashboard (internal tool, network-restricted — validate assumption at deployment)
- Parallel test execution across apps (runs sequentially within an app; concurrent across apps)
- Video recording of browser sessions (screenshots only)
- CI/CD pipeline integration (REST API endpoint exists; pipeline hookup is future work)
- Automated screenshot retention/pruning
- In-process scheduling (Windows Task Scheduler is the scheduler in v1)
