# Web Testing Harness

A Python-based web and API testing harness with a browser dashboard, Teams/email alerts, and Windows Task Scheduler integration.

Define tests in YAML, run them on demand or on a schedule, and view results in a web dashboard.

---

## Prerequisites

- Python 3.11 or later
- pip

---

## Installation

### 1. Create and activate a virtual environment

```bash
python -m venv .venv
.venv\Scripts\activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

### 3. Copy the example env file

```bash
copy .env.example .env
```

Edit `.env` and fill in your values. At minimum, set `TEAMS_WEBHOOK_URL` if you want alert notifications. If you don't use Teams, comment it out in `config.yaml` under `alerts:`.

### 4. Review `config.yaml`

```yaml
default_environment: production

environments:
  staging:
    label: "Staging"
  production:
    label: "Production"

alerts:
  teams:
    webhook_url: "$TEAMS_WEBHOOK_URL"   # reads from .env

browser:
  headless: true
  timeout_ms: 30000
```

Add or remove environments as needed. Each environment gets a button in the dashboard nav.

---

## Define Your Apps

Create YAML files in the `apps/` directory — one file per application.

### API test

```yaml
# apps/my-api.yaml
app: "My API"
url: "https://api.mycompany.com"
environments:
  staging: "https://staging-api.mycompany.com"
  production: "https://api.mycompany.com"
tests:
  - name: "Health check"
    type: api
    endpoint: /health
    method: GET
    expect_status: 200

  - name: "Returns JSON"
    type: api
    endpoint: /status
    method: GET
    expect_status: 200
    expect_json:
      status: "ok"
```

### Browser test

```yaml
# apps/my-site.yaml
app: "My Site"
url: "https://mycompany.com"
tests:
  - name: "Homepage loads"
    type: browser
    steps:
      - navigate: /
      - assert_text: "Welcome"

  - name: "Login flow"
    type: browser
    steps:
      - navigate: /login
      - fill:
          field: "#username"
          value: "$APP_USERNAME"      # reads from .env
      - fill:
          field: "#password"
          value: "$APP_PASSWORD"
      - click: "button[type=submit]"
      - assert_url_contains: /dashboard
```

### Availability check (no browser, no API — just HTTP status)

```yaml
tests:
  - name: "Site is up"
    type: availability
    expect_status: 200
```

### Supported browser step actions

| Action | Example |
|--------|---------|
| Navigate to URL | `navigate: /login` |
| Fill input | `fill: {field: "#email", value: "foo@bar.com"}` |
| Click element | `click: "button.submit"` |
| Assert page text | `assert_text: "Dashboard"` |
| Assert URL contains | `assert_url_contains: /dashboard` |
| Wait for selector | `wait_for_selector: ".loaded"` |

---

## Start the Dashboard

```bash
.venv\Scripts\activate
python -m web.main
```

Open **http://localhost:8000** in a browser.

- The dashboard shows all apps and their current pass/fail status.
- Click **Run** next to any app to trigger a test run immediately.
- Click an app name to see the detail page: step logs, error messages, screenshots on failure, and recent run history.
- Use the environment buttons (Staging / Production) to switch views.

---

## Run Tests via the API

Trigger a run for a specific app:

```bash
curl -X POST http://localhost:8000/api/runs \
  -H "Content-Type: application/json" \
  -d '{"app": "My API", "environment": "production"}'
```

Trigger all apps:

```bash
curl -X POST http://localhost:8000/api/runs \
  -H "Content-Type: application/json" \
  -d '{"environment": "production"}'
```

Check run status:

```bash
curl http://localhost:8000/api/runs/<run_id>
```

List app summary:

```bash
curl http://localhost:8000/api/apps?environment=production
```

---

## Schedule Automatic Runs (Windows Task Scheduler)

Generate a Task Scheduler XML file:

```python
from harness.scheduler import print_setup_instructions
print_setup_instructions("http://localhost:8000", "production", interval_minutes=60)
```

This writes `harness-task-production.xml` and prints the install command:

```
schtasks /create /xml harness-task-production.xml /tn Harness-production
```

Run that command in an elevated terminal to register the scheduled task. It will call `POST /api/runs` every 60 minutes.

---

## Python-Defined Tests (Advanced)

For tests that need custom logic, subclass `AppTest` in a `.py` file in `apps/`:

```python
# apps/my_app.py
from harness import AppTest

class MyApp(AppTest):
    name = "My App"
    environments = {
        "staging": "https://staging.mycompany.com",
        "production": "https://mycompany.com",
    }

    async def test_login(self, page, environment):
        await page.goto(self.resolve_base_url(environment) + "/login")
        await page.fill("#user", self.env("APP_USERNAME"))
        await page.fill("#pass", self.env("APP_PASSWORD"))
        await page.click("button[type=submit]")
        assert "/dashboard" in page.url
```

`self.env("VAR")` reads from environment variables (set in `.env`). `self.resolve_base_url(environment)` returns the correct base URL for the given environment.

---

## Alerts

When a test transitions from passing → failing, a Teams message is sent. When it recovers, a resolve notification is sent. Configure in `config.yaml`:

**Teams:**
```yaml
alerts:
  teams:
    webhook_url: "$TEAMS_WEBHOOK_URL"
```

**Email (SMTP):**
```yaml
alerts:
  email:
    smtp_host: "mail.company.com"
    smtp_port: 587
    from: "harness@company.com"
    to: ["ops-team@company.com"]
    username: "$SMTP_USERNAME"
    password: "$SMTP_PASSWORD"
```

---

## Project Structure

```
webtestingharness/
├── apps/               ← your app YAML files go here
├── harness/            ← core engine (loader, runner, browser, api, alerts)
├── web/                ← FastAPI dashboard and REST API
├── tests/              ← test suite (run with pytest)
├── data/               ← SQLite database + screenshots (gitignored)
├── config.yaml         ← environments and alert config
└── .env                ← credentials (gitignored, never commit)
```

---

## Running the Test Suite

```bash
pytest tests/ --ignore=tests/test_browser_engine.py
```

To include browser engine tests (requires Playwright):

```bash
pytest tests/
```
