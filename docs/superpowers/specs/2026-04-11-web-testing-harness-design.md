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
  ├── Scheduler management
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
      - fill: {field: "#username", value: "$USERNAME"}
      - fill: {field: "#password", value: "$PASSWORD"}
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
        await page.fill("#username", self.env("USERNAME"))
        await page.fill("#password", self.env("PASSWORD"))
        await page.click("button[type=submit]")
        await page.wait_for_url("**/dashboard")
        assert "Welcome" in await page.text_content("body")
```

---

## Web Dashboard

A FastAPI-served web interface accessible to all staff.

### Main View — Application Status Table

- Columns: Application name, Status (Pass/Fail), Tests passed (e.g. 3/3), Last run time, Run button
- Environment switcher (Production / Staging) at the top
- "Run All" button to trigger all tests for selected environment
- Search/filter by app name
- Rows highlighted red on failure

### Failure Detail View

Accessible by clicking any failed row. Shows:

- **Error message** — what assertion or step failed
- **Screenshot** — browser screenshot captured at the moment of failure
- **Step log** — each test step with pass/fail status and timing
- **Run history** — coloured pass/fail blocks for recent runs (highlights recurring vs new failures)
- **Re-run button** — trigger just this test on demand

---

## Alerts

- Configured in `config.yaml` (Teams webhook URL, SMTP settings)
- Fires on **state transition only**: pass→fail triggers alert, fail→pass triggers resolution notice
- No repeated alerts for every failing run — avoids noise
- Alert content: app name, test name, environment, error summary, link to detail view
- Credentials stored in environment variables or `.env` — never in YAML or config files

---

## Scheduling

- Scheduled runs configured in `config.yaml` (cron expressions)
- Windows Task Scheduler calls the REST API endpoint to trigger a run
- Each schedule can target a specific environment and/or subset of apps
- Results stored and visible in dashboard like any manual run

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
│   ├── browser.py           # Playwright wrapper
│   ├── api.py               # httpx wrapper
│   ├── scheduler.py         # Scheduled run management
│   └── alerts.py            # Teams/email dispatch
├── web/                     # FastAPI application
│   ├── main.py
│   ├── templates/           # Jinja2 HTML templates
│   └── static/              # CSS, JS
├── data/                    # SQLite DB + failure screenshots
├── config.yaml              # Environments, alert settings, schedules
├── .env                     # Credentials (not committed to git)
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
| `apscheduler` | In-process scheduling (backs Windows Task Scheduler) |
| `pyyaml` | YAML test definition parsing |
| `jinja2` | Dashboard HTML templating |
| `python-dotenv` | `.env` credential loading |

---

## Out of Scope (for now)

- Authentication on the dashboard itself (internal tool, network-restricted)
- Parallel test execution across apps (runs sequentially initially)
- Video recording of browser sessions (screenshots only)
- CI/CD pipeline integration (REST API endpoint exists but pipeline hookup is future work)
