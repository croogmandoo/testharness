# Web Testing Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python-based web/application testing harness that lets developers define tests in YAML or Python, runs them on demand or on a schedule, and shows results in a web dashboard with Teams/email alerts on failure.

**Architecture:** FastAPI serves the dashboard and REST API. The harness core (loader, runner, browser engine, API engine) is a separate Python package. Tests are defined as YAML files or Python `AppTest` subclasses in an `apps/` directory. Results are stored in SQLite; screenshots are saved to disk.

**Tech Stack:** Python 3.11+, Playwright, httpx, FastAPI, uvicorn, SQLite (stdlib), PyYAML, Jinja2, python-dotenv, pytest, pytest-asyncio

---

## File Map

```
webtestingharness/
├── harness/
│   ├── __init__.py          # exports AppTest, browser_test, api_test
│   ├── models.py            # dataclasses: Run, TestResult, AppState, StepResult
│   ├── config.py            # load config.yaml, resolve $VAR from env
│   ├── db.py                # SQLite schema init + CRUD helpers
│   ├── loader.py            # discover and parse apps/ (YAML + Python)
│   ├── runner.py            # orchestrate test execution, update state + alerts
│   ├── browser.py           # Playwright wrapper + @browser_test decorator
│   ├── api.py               # httpx wrapper + @api_test decorator
│   ├── alerts.py            # Teams webhook + SMTP email dispatch
│   └── scheduler.py         # generate Windows Task Scheduler XML from config
├── web/
│   ├── __init__.py
│   ├── main.py              # FastAPI app, route registration, static mounts
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── dashboard.py     # GET / and GET /app/{app}/{env} (HTML)
│   │   └── api.py           # REST: /api/runs, /api/apps, /api/results
│   ├── templates/
│   │   ├── base.html        # shared layout, env switcher, nav
│   │   ├── dashboard.html   # app status table
│   │   └── detail.html      # failure detail: error, screenshot, step log, history
│   └── static/
│       └── style.css
├── apps/                    # user-created app definitions (starts empty)
│   ├── example-web.yaml     # sample browser test
│   └── example-api.yaml     # sample API test
├── tests/
│   ├── conftest.py          # shared fixtures
│   ├── test_config.py
│   ├── test_db.py
│   ├── test_loader.py
│   ├── test_api_engine.py
│   ├── test_browser_engine.py
│   ├── test_runner.py
│   ├── test_alerts.py
│   └── test_web_api.py
├── data/                    # gitignored — harness.db + screenshots/
├── config.yaml
├── .env.example
├── .gitignore
└── requirements.txt
```

---

## Task 1: Project Scaffold

**Files:**
- Create: `requirements.txt`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `config.yaml`
- Create: `harness/__init__.py`
- Create: `web/__init__.py`
- Create: `web/routes/__init__.py`

- [ ] **Step 1: Create `requirements.txt`**

```
playwright==1.44.0
httpx==0.27.0
fastapi==0.111.0
uvicorn[standard]==0.29.0
pyyaml==6.0.1
jinja2==3.1.4
python-dotenv==1.0.1
pytest==8.2.0
pytest-asyncio==0.23.7
pytest-httpx==0.30.0
anyio==4.3.0
```

- [ ] **Step 2: Create `.gitignore`**

```
.env
data/
__pycache__/
*.pyc
.pytest_cache/
.superpowers/
```

- [ ] **Step 3: Create `.env.example`**

```bash
# Copy to .env and fill in values. Never commit .env.
# Add one entry per credential used in your app YAML files.
EXAMPLE_USERNAME=your-username
EXAMPLE_PASSWORD=your-password
TEAMS_WEBHOOK_URL=https://outlook.office.com/webhook/...
SMTP_USERNAME=harness@company.com
SMTP_PASSWORD=your-smtp-password
```

- [ ] **Step 4: Create `config.yaml`**

```yaml
default_environment: production

environments:
  staging:
    label: "Staging"
  production:
    label: "Production"

alerts:
  teams:
    webhook_url: "$TEAMS_WEBHOOK_URL"
  # email:
  #   smtp_host: "mail.company.com"
  #   smtp_port: 587
  #   from: "harness@company.com"
  #   to: ["ops-team@company.com"]
  #   username: "$SMTP_USERNAME"
  #   password: "$SMTP_PASSWORD"

browser:
  headless: true
  timeout_ms: 30000
```

- [ ] **Step 5: Create empty `__init__.py` files**

```bash
mkdir -p harness web/routes web/templates web/static apps tests data/screenshots
touch harness/__init__.py web/__init__.py web/routes/__init__.py
```

- [ ] **Step 6: Install dependencies**

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

- [ ] **Step 7: Commit**

```bash
git add requirements.txt .gitignore .env.example config.yaml harness/__init__.py web/__init__.py web/routes/__init__.py
git commit -m "feat: project scaffold"
```

---

## Task 2: Data Models

**Files:**
- Create: `harness/models.py`
- Create: `tests/conftest.py` (partial — extended in later tasks)

- [ ] **Step 1: Write the test**

Create `tests/test_models.py`:

```python
from harness.models import Run, TestResult, AppState, StepResult
import uuid

def test_run_has_uuid_by_default():
    run = Run(app="myapp", environment="production", triggered_by="ui")
    assert len(run.id) == 36  # UUID format
    assert run.status == "pending"

def test_test_result_defaults():
    tr = TestResult(run_id="abc", app="myapp", environment="production", test_name="login")
    assert tr.status == ""
    assert tr.screenshot is None
    assert tr.step_log == []

def test_app_state_fields():
    state = AppState(app="myapp", environment="staging", test_name="login",
                     state="failing", since="2026-04-11T10:00:00")
    assert state.state == "failing"

def test_step_result():
    step = StepResult(step="navigate /login", status="pass", duration_ms=120)
    assert step.error is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_models.py -v
```
Expected: `ImportError` or `ModuleNotFoundError` — `harness.models` doesn't exist yet.

- [ ] **Step 3: Create `harness/models.py`**

```python
from dataclasses import dataclass, field
from typing import Optional, List
import uuid


def _new_id() -> str:
    return str(uuid.uuid4())


@dataclass
class StepResult:
    step: str
    status: str          # 'pass', 'fail', 'error'
    duration_ms: int
    error: Optional[str] = None


@dataclass
class TestResult:
    run_id: str
    app: str
    environment: str
    test_name: str
    id: str = field(default_factory=_new_id)
    status: str = ""     # 'pass', 'fail', 'error'
    error_msg: Optional[str] = None
    step_log: List[StepResult] = field(default_factory=list)
    screenshot: Optional[str] = None
    duration_ms: Optional[int] = None
    finished_at: Optional[str] = None


@dataclass
class Run:
    app: str
    environment: str
    triggered_by: str    # 'ui', 'api'
    id: str = field(default_factory=_new_id)
    status: str = "pending"   # 'pending', 'running', 'complete'
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


@dataclass
class AppState:
    app: str
    environment: str
    test_name: str
    state: str           # 'unknown', 'passing', 'failing'
    since: str           # ISO8601
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_models.py -v
```
Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/models.py tests/test_models.py
git commit -m "feat: add data models"
```

---

## Task 3: Config Loader

**Files:**
- Create: `harness/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write the tests**

Create `tests/test_config.py`:

```python
import os
import pytest
import yaml
from harness.config import load_config, resolve_env_vars, ConfigError


def test_resolve_env_vars_substitutes_dollar_vars(monkeypatch):
    monkeypatch.setenv("MY_SECRET", "hunter2")
    result = resolve_env_vars({"key": "$MY_SECRET", "other": "plain"})
    assert result["key"] == "hunter2"
    assert result["other"] == "plain"


def test_resolve_env_vars_raises_on_missing(monkeypatch):
    monkeypatch.delenv("MISSING_VAR", raising=False)
    with pytest.raises(ConfigError, match="MISSING_VAR"):
        resolve_env_vars({"key": "$MISSING_VAR"})


def test_resolve_env_vars_nested(monkeypatch):
    monkeypatch.setenv("WEBHOOK", "https://example.com")
    result = resolve_env_vars({"alerts": {"teams": {"webhook_url": "$WEBHOOK"}}})
    assert result["alerts"]["teams"]["webhook_url"] == "https://example.com"


def test_load_config_reads_yaml(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.dump({
        "default_environment": "production",
        "environments": {"production": {"label": "Production"}},
        "browser": {"headless": True, "timeout_ms": 30000},
    }))
    config = load_config(str(cfg_file))
    assert config["default_environment"] == "production"
    assert config["browser"]["timeout_ms"] == 30000


def test_load_config_missing_file():
    with pytest.raises(ConfigError, match="not found"):
        load_config("/nonexistent/config.yaml")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_config.py -v
```
Expected: `ImportError` — module doesn't exist.

- [ ] **Step 3: Create `harness/config.py`**

```python
import os
import yaml
from typing import Any


class ConfigError(Exception):
    pass


def resolve_env_vars(obj: Any) -> Any:
    """Recursively replace '$VAR_NAME' strings with environment variable values."""
    if isinstance(obj, dict):
        return {k: resolve_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_env_vars(v) for v in obj]
    if isinstance(obj, str) and obj.startswith("$"):
        var = obj[1:]
        val = os.environ.get(var)
        if val is None:
            raise ConfigError(f"Environment variable '{var}' is not set")
        return val
    return obj


def load_config(path: str = "config.yaml") -> dict:
    if not os.path.exists(path):
        raise ConfigError(f"Config file not found: {path}")
    with open(path) as f:
        raw = yaml.safe_load(f)
    return resolve_env_vars(raw)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_config.py -v
```
Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/config.py tests/test_config.py
git commit -m "feat: add config loader with env var resolution"
```

---

## Task 4: Database Layer

**Files:**
- Create: `harness/db.py`
- Create: `tests/test_db.py`

- [ ] **Step 1: Write the tests**

Create `tests/test_db.py`:

```python
import pytest
from harness.db import Database
from harness.models import Run, TestResult, AppState


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    return d


def test_insert_and_get_run(db):
    run = Run(app="myapp", environment="production", triggered_by="ui")
    db.insert_run(run)
    fetched = db.get_run(run.id)
    assert fetched["app"] == "myapp"
    assert fetched["status"] == "pending"


def test_update_run_status(db):
    run = Run(app="myapp", environment="production", triggered_by="api")
    db.insert_run(run)
    db.update_run_status(run.id, "running", started_at="2026-04-11T10:00:00")
    fetched = db.get_run(run.id)
    assert fetched["status"] == "running"
    assert fetched["started_at"] == "2026-04-11T10:00:00"


def test_insert_test_result(db):
    run = Run(app="myapp", environment="production", triggered_by="api")
    db.insert_run(run)
    tr = TestResult(run_id=run.id, app="myapp", environment="production",
                    test_name="login", status="fail", error_msg="Timeout")
    db.insert_test_result(tr)
    results = db.get_results_for_run(run.id)
    assert len(results) == 1
    assert results[0]["test_name"] == "login"


def test_upsert_and_get_app_state(db):
    state = AppState(app="myapp", environment="production", test_name="login",
                     state="failing", since="2026-04-11T10:00:00")
    db.upsert_app_state(state)
    fetched = db.get_app_state("myapp", "production", "login")
    assert fetched["state"] == "failing"
    # Upsert again with new state
    state.state = "passing"
    state.since = "2026-04-11T11:00:00"
    db.upsert_app_state(state)
    fetched = db.get_app_state("myapp", "production", "login")
    assert fetched["state"] == "passing"


def test_is_run_active(db):
    run = Run(app="myapp", environment="production", triggered_by="api")
    db.insert_run(run)
    assert not db.is_run_active("myapp", "production")
    db.update_run_status(run.id, "running")
    assert db.is_run_active("myapp", "production")
    db.update_run_status(run.id, "complete")
    assert not db.is_run_active("myapp", "production")


def test_get_app_summary_returns_all_states(db):
    for test_name, state in [("login", "passing"), ("health", "failing")]:
        db.upsert_app_state(AppState(
            app="myapp", environment="production",
            test_name=test_name, state=state, since="2026-04-11T10:00:00"
        ))
    summary = db.get_app_summary("production")
    myapp = next(s for s in summary if s["app"] == "myapp")
    assert myapp["total"] == 2
    assert myapp["passing"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_db.py -v
```
Expected: `ImportError`.

- [ ] **Step 3: Create `harness/db.py`**

```python
import sqlite3
import json
from typing import Optional
from harness.models import Run, TestResult, AppState


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id           TEXT PRIMARY KEY,
    app          TEXT NOT NULL,
    environment  TEXT NOT NULL,
    triggered_by TEXT NOT NULL,
    status       TEXT NOT NULL,
    started_at   TEXT,
    finished_at  TEXT
);

CREATE TABLE IF NOT EXISTS test_results (
    id          TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL REFERENCES runs(id),
    app         TEXT NOT NULL,
    environment TEXT NOT NULL,
    test_name   TEXT NOT NULL,
    status      TEXT NOT NULL,
    error_msg   TEXT,
    step_log    TEXT,
    screenshot  TEXT,
    duration_ms INTEGER,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_test_results_lookup
    ON test_results(app, environment, test_name, finished_at);

CREATE TABLE IF NOT EXISTS app_state (
    app         TEXT NOT NULL,
    environment TEXT NOT NULL,
    test_name   TEXT NOT NULL,
    state       TEXT NOT NULL,
    since       TEXT NOT NULL,
    PRIMARY KEY (app, environment, test_name)
);
"""


class Database:
    def __init__(self, path: str = "data/harness.db"):
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def insert_run(self, run: Run) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO runs (id, app, environment, triggered_by, status) VALUES (?,?,?,?,?)",
                (run.id, run.app, run.environment, run.triggered_by, run.status)
            )

    def update_run_status(self, run_id: str, status: str, *,
                          started_at: Optional[str] = None,
                          finished_at: Optional[str] = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE runs SET status=?, started_at=COALESCE(?,started_at), "
                "finished_at=COALESCE(?,finished_at) WHERE id=?",
                (status, started_at, finished_at, run_id)
            )

    def get_run(self, run_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            return dict(row) if row else None

    def insert_test_result(self, tr: TestResult) -> None:
        step_log_json = json.dumps([
            {"step": s.step, "status": s.status, "duration_ms": s.duration_ms, "error": s.error}
            for s in tr.step_log
        ])
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO test_results "
                "(id, run_id, app, environment, test_name, status, error_msg, "
                "step_log, screenshot, duration_ms, finished_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (tr.id, tr.run_id, tr.app, tr.environment, tr.test_name,
                 tr.status, tr.error_msg, step_log_json,
                 tr.screenshot, tr.duration_ms, tr.finished_at)
            )

    def get_results_for_run(self, run_id: str) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM test_results WHERE run_id=?", (run_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def upsert_app_state(self, state: AppState) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO app_state (app, environment, test_name, state, since) "
                "VALUES (?,?,?,?,?) ON CONFLICT(app,environment,test_name) "
                "DO UPDATE SET state=excluded.state, since=excluded.since",
                (state.app, state.environment, state.test_name, state.state, state.since)
            )

    def get_app_state(self, app: str, environment: str, test_name: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM app_state WHERE app=? AND environment=? AND test_name=?",
                (app, environment, test_name)
            ).fetchone()
            return dict(row) if row else None

    def is_run_active(self, app: str, environment: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM runs WHERE app=? AND environment=? AND status='running'",
                (app, environment)
            ).fetchone()
            return row is not None

    def get_app_summary(self, environment: str) -> list:
        """Return list of {app, total, passing, failing, unknown, last_run} per app."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT app, state, COUNT(*) as cnt FROM app_state "
                "WHERE environment=? GROUP BY app, state",
                (environment,)
            ).fetchall()
        apps: dict = {}
        for row in rows:
            app = row["app"]
            if app not in apps:
                apps[app] = {"app": app, "total": 0, "passing": 0, "failing": 0, "unknown": 0}
            apps[app]["total"] += row["cnt"]
            apps[app][row["state"]] += row["cnt"]
        # Attach last run time
        with self._connect() as conn:
            last_runs = conn.execute(
                "SELECT app, MAX(finished_at) as last_run FROM runs "
                "WHERE environment=? AND status='complete' GROUP BY app",
                (environment,)
            ).fetchall()
        for row in last_runs:
            if row["app"] in apps:
                apps[row["app"]]["last_run"] = row["last_run"]
        return list(apps.values())
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_db.py -v
```
Expected: 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/db.py tests/test_db.py
git commit -m "feat: add SQLite database layer"
```

---

## Task 5: App Loader — YAML

**Files:**
- Create: `harness/loader.py`
- Create: `tests/test_loader.py`
- Create: `apps/example-web.yaml`
- Create: `apps/example-api.yaml`

- [ ] **Step 1: Write the tests**

Create `tests/test_loader.py`:

```python
import pytest
import yaml
from harness.loader import load_apps, resolve_base_url, slugify_test_name
from harness.config import ConfigError


@pytest.fixture
def apps_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("EXAMPLE_USER", "admin")
    monkeypatch.setenv("EXAMPLE_PASS", "secret")
    (tmp_path / "portal.yaml").write_text(yaml.dump({
        "app": "Customer Portal",
        "url": "https://portal.example.com",
        "environments": {
            "staging": "https://staging.portal.example.com",
            "production": "https://portal.example.com",
        },
        "tests": [
            {"name": "Page is reachable", "type": "availability", "expect_status": 200},
            {"name": "Login works", "type": "browser", "steps": [
                {"navigate": "/login"},
                {"fill": {"field": "#user", "value": "$EXAMPLE_USER"}},
                {"click": "button[type=submit]"},
                {"assert_url_contains": "/dashboard"},
            ]},
        ]
    }))
    return str(tmp_path)


def test_load_apps_returns_list(apps_dir):
    apps = load_apps(apps_dir)
    assert len(apps) == 1
    assert apps[0]["app"] == "Customer Portal"


def test_load_apps_resolves_env_vars(apps_dir):
    apps = load_apps(apps_dir)
    login_test = next(t for t in apps[0]["tests"] if t["name"] == "Login works")
    fill_step = login_test["steps"][1]
    assert fill_step["fill"]["value"] == "admin"


def test_resolve_base_url_uses_environment_key():
    app = {"url": "https://default.com", "environments": {"staging": "https://staging.com"}}
    assert resolve_base_url(app, "staging") == "https://staging.com"


def test_resolve_base_url_falls_back_to_url():
    app = {"url": "https://default.com", "environments": {"production": "https://prod.com"}}
    assert resolve_base_url(app, "staging") == "https://default.com"


def test_resolve_base_url_raises_when_no_url():
    app = {"environments": {}}
    with pytest.raises(ConfigError, match="No base URL"):
        resolve_base_url(app, "staging")


def test_slugify_test_name():
    assert slugify_test_name("Login works!") == "login-works"
    assert slugify_test_name("API health check") == "api-health-check"
    assert slugify_test_name("  spaces  ") == "spaces"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_loader.py -v
```
Expected: `ImportError`.

- [ ] **Step 3: Create `harness/loader.py`**

```python
import os
import re
import glob
import yaml
from harness.config import resolve_env_vars, ConfigError


def slugify_test_name(name: str) -> str:
    """Normalise a test name to a filesystem-safe slug: lowercase, hyphens, [a-z0-9-] only."""
    name = name.strip().lower()
    name = re.sub(r"\s+", "-", name)
    name = re.sub(r"[^a-z0-9-]", "", name)
    return name


def resolve_base_url(app: dict, environment: str) -> str:
    """Resolve the base URL for an app given a target environment."""
    envs = app.get("environments", {})
    if environment in envs:
        return envs[environment]
    if "url" in app:
        return app["url"]
    raise ConfigError(
        f"No base URL found for app '{app.get('app', '?')}' in environment '{environment}'"
    )


def load_apps(apps_dir: str = "apps") -> list:
    """Discover and return all YAML app definitions from apps_dir."""
    pattern = os.path.join(apps_dir, "*.yaml")
    apps = []
    for path in sorted(glob.glob(pattern)):
        with open(path) as f:
            raw = yaml.safe_load(f)
        resolved = resolve_env_vars(raw)
        resolved["_source"] = path
        resolved["_type"] = "yaml"
        apps.append(resolved)
    return apps
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_loader.py -v
```
Expected: 7 tests PASS.

- [ ] **Step 5: Create sample app definitions**

Create `apps/example-web.yaml`:

```yaml
app: "Example Web App"
url: "https://example.com"
environments:
  staging: "https://staging.example.com"
  production: "https://example.com"

tests:
  - name: "Page is reachable"
    type: availability
    expect_status: 200

  - name: "Homepage loads"
    type: browser
    steps:
      - navigate: /
      - assert_text: "Example"
```

Create `apps/example-api.yaml`:

```yaml
app: "Example API"
url: "https://api.example.com"
environments:
  staging: "https://staging-api.example.com"
  production: "https://api.example.com"

tests:
  - name: "Health check"
    type: api
    endpoint: /health
    method: GET
    expect_status: 200
```

- [ ] **Step 6: Commit**

```bash
git add harness/loader.py tests/test_loader.py apps/
git commit -m "feat: add YAML app loader"
```

---

## Task 6: API Test Engine

**Files:**
- Create: `harness/api.py`
- Create: `tests/test_api_engine.py`

- [ ] **Step 1: Write the tests**

Create `tests/test_api_engine.py`:

```python
import pytest
import httpx
from pytest_httpx import HTTPXMock
from harness.api import run_api_test
from harness.models import TestResult


@pytest.mark.asyncio
async def test_api_test_passes_on_expected_status(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url="https://api.example.com/health", status_code=200,
                            json={"status": "ok"})
    test_def = {
        "name": "Health check",
        "type": "api",
        "endpoint": "/health",
        "method": "GET",
        "expect_status": 200,
    }
    result = await run_api_test("run-1", "myapi", "production",
                                "https://api.example.com", test_def)
    assert result.status == "pass"
    assert result.error_msg is None


@pytest.mark.asyncio
async def test_api_test_fails_on_wrong_status(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url="https://api.example.com/health", status_code=500)
    test_def = {
        "name": "Health check",
        "type": "api",
        "endpoint": "/health",
        "method": "GET",
        "expect_status": 200,
    }
    result = await run_api_test("run-1", "myapi", "production",
                                "https://api.example.com", test_def)
    assert result.status == "fail"
    assert "500" in result.error_msg


@pytest.mark.asyncio
async def test_api_test_checks_expect_json(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url="https://api.example.com/health", status_code=200,
                            json={"status": "degraded"})
    test_def = {
        "name": "Health check",
        "type": "api",
        "endpoint": "/health",
        "method": "GET",
        "expect_status": 200,
        "expect_json": {"status": "ok"},
    }
    result = await run_api_test("run-1", "myapi", "production",
                                "https://api.example.com", test_def)
    assert result.status == "fail"
    assert "status" in result.error_msg
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_api_engine.py -v
```
Expected: `ImportError`.

- [ ] **Step 3: Create `harness/api.py`**

```python
import time
import httpx
from datetime import datetime, timezone
from harness.models import TestResult, StepResult


async def run_api_test(run_id: str, app: str, environment: str,
                       base_url: str, test_def: dict) -> TestResult:
    """Execute a single API test definition and return a TestResult."""
    result = TestResult(
        run_id=run_id, app=app, environment=environment,
        test_name=test_def["name"]
    )
    start = time.monotonic()

    try:
        method = test_def.get("method", "GET").upper()
        endpoint = test_def.get("endpoint", "/")
        url = base_url.rstrip("/") + endpoint
        expect_status = test_def.get("expect_status", 200)
        expect_json = test_def.get("expect_json")

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.request(method, url)

        step = StepResult(step=f"{method} {endpoint}", status="pass",
                          duration_ms=int((time.monotonic() - start) * 1000))

        if response.status_code != expect_status:
            step.status = "fail"
            step.error = f"Expected status {expect_status}, got {response.status_code}"
            result.status = "fail"
            result.error_msg = step.error
            result.step_log = [step]
            result.duration_ms = step.duration_ms
            result.finished_at = datetime.now(timezone.utc).isoformat()
            return result

        if expect_json:
            try:
                body = response.json()
            except Exception:
                body = {}
            mismatches = {k: f"expected {v!r}, got {body.get(k)!r}"
                          for k, v in expect_json.items() if body.get(k) != v}
            if mismatches:
                step.status = "fail"
                step.error = "JSON mismatch: " + ", ".join(
                    f"{k}: {m}" for k, m in mismatches.items()
                )
                result.status = "fail"
                result.error_msg = step.error
                result.step_log = [step]
                result.duration_ms = step.duration_ms
                result.finished_at = datetime.now(timezone.utc).isoformat()
                return result

        result.status = "pass"
        result.step_log = [step]
        result.duration_ms = int((time.monotonic() - start) * 1000)
        result.finished_at = datetime.now(timezone.utc).isoformat()

    except Exception as e:
        result.status = "error"
        result.error_msg = str(e)
        result.duration_ms = int((time.monotonic() - start) * 1000)
        result.finished_at = datetime.now(timezone.utc).isoformat()

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_api_engine.py -v
```
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/api.py tests/test_api_engine.py
git commit -m "feat: add API test engine"
```

---

## Task 7: Browser Test Engine

**Files:**
- Create: `harness/browser.py`
- Create: `tests/test_browser_engine.py`

The browser engine is tested with mocked Playwright objects to avoid needing a real browser in unit tests.

- [ ] **Step 1: Write the tests**

Create `tests/test_browser_engine.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from harness.browser import run_browser_test, execute_step
from harness.models import StepResult


@pytest.mark.asyncio
async def test_execute_navigate_step():
    page = AsyncMock()
    result = await execute_step(page, {"navigate": "/login"}, "https://example.com")
    page.goto.assert_called_once_with("https://example.com/login")
    assert result.status == "pass"


@pytest.mark.asyncio
async def test_execute_navigate_absolute_url():
    page = AsyncMock()
    result = await execute_step(page, {"navigate": "https://other.com/page"}, "https://example.com")
    page.goto.assert_called_once_with("https://other.com/page")


@pytest.mark.asyncio
async def test_execute_fill_step():
    page = AsyncMock()
    result = await execute_step(page, {"fill": {"field": "#user", "value": "admin"}}, "https://x.com")
    page.fill.assert_called_once_with("#user", "admin")
    assert result.status == "pass"


@pytest.mark.asyncio
async def test_execute_click_step():
    page = AsyncMock()
    result = await execute_step(page, {"click": "button"}, "https://x.com")
    page.click.assert_called_once_with("button")
    assert result.status == "pass"


@pytest.mark.asyncio
async def test_execute_assert_url_contains_pass():
    page = AsyncMock()
    page.url = "https://example.com/dashboard"
    result = await execute_step(page, {"assert_url_contains": "/dashboard"}, "https://example.com")
    assert result.status == "pass"


@pytest.mark.asyncio
async def test_execute_assert_url_contains_fail():
    page = AsyncMock()
    page.url = "https://example.com/login?error=1"
    result = await execute_step(page, {"assert_url_contains": "/dashboard"}, "https://example.com")
    assert result.status == "fail"
    assert "/dashboard" in result.error


@pytest.mark.asyncio
async def test_execute_assert_text_pass():
    page = AsyncMock()
    page.text_content = AsyncMock(return_value="Welcome, Admin")
    result = await execute_step(page, {"assert_text": "Welcome"}, "https://x.com")
    assert result.status == "pass"


@pytest.mark.asyncio
async def test_execute_assert_text_fail():
    page = AsyncMock()
    page.text_content = AsyncMock(return_value="Error: not found")
    result = await execute_step(page, {"assert_text": "Welcome"}, "https://x.com")
    assert result.status == "fail"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_browser_engine.py -v
```
Expected: `ImportError`.

- [ ] **Step 3: Create `harness/browser.py`**

```python
import os
import time
import re
from datetime import datetime, timezone
from typing import Optional
from playwright.async_api import async_playwright, Page
from harness.models import TestResult, StepResult
from harness.loader import slugify_test_name


async def execute_step(page: Page, step: dict, base_url: str) -> StepResult:
    """Execute one YAML browser step. Returns a StepResult."""
    start = time.monotonic()

    def _elapsed() -> int:
        return int((time.monotonic() - start) * 1000)

    try:
        if "navigate" in step:
            target = step["navigate"]
            url = target if target.startswith("http") else base_url.rstrip("/") + target
            await page.goto(url)
            return StepResult(step=f"navigate {target}", status="pass", duration_ms=_elapsed())

        if "fill" in step:
            s = step["fill"]
            await page.fill(s["field"], s["value"])
            return StepResult(step=f"fill {s['field']}", status="pass", duration_ms=_elapsed())

        if "click" in step:
            await page.click(step["click"])
            return StepResult(step=f"click {step['click']}", status="pass", duration_ms=_elapsed())

        if "assert_url_contains" in step:
            expected = step["assert_url_contains"]
            current = page.url
            if expected not in current:
                err = f"Expected URL to contain '{expected}' but got '{current}'"
                return StepResult(step=f"assert_url_contains {expected}",
                                  status="fail", duration_ms=_elapsed(), error=err)
            return StepResult(step=f"assert_url_contains {expected}",
                              status="pass", duration_ms=_elapsed())

        if "assert_text" in step:
            expected = step["assert_text"]
            body = await page.text_content("body")
            if expected not in (body or ""):
                err = f"Expected page to contain text '{expected}'"
                return StepResult(step=f"assert_text {expected}",
                                  status="fail", duration_ms=_elapsed(), error=err)
            return StepResult(step=f"assert_text {expected}",
                              status="pass", duration_ms=_elapsed())

        return StepResult(step=str(step), status="error", duration_ms=_elapsed(),
                          error=f"Unknown step type: {list(step.keys())}")

    except Exception as e:
        return StepResult(step=str(step), status="error", duration_ms=_elapsed(), error=str(e))


async def run_browser_test(run_id: str, app: str, environment: str,
                           base_url: str, test_def: dict,
                           screenshot_dir: str = "data/screenshots",
                           headless: bool = True, timeout_ms: int = 30000) -> TestResult:
    """Execute a browser test and return a TestResult with step log and optional screenshot."""
    result = TestResult(run_id=run_id, app=app, environment=environment,
                        test_name=test_def["name"])
    step_log = []
    start = time.monotonic()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        page = await browser.new_page()
        page.set_default_timeout(timeout_ms)

        steps = test_def.get("steps", [])
        failed = False

        for step in steps:
            step_result = await execute_step(page, step, base_url)
            step_log.append(step_result)
            if step_result.status in ("fail", "error"):
                failed = True
                # Capture screenshot at point of failure
                slug = slugify_test_name(test_def["name"])
                screenshot_path = os.path.join(
                    screenshot_dir, app, environment, run_id, f"{slug}.png"
                )
                os.makedirs(os.path.dirname(screenshot_path), exist_ok=True)
                await page.screenshot(path=screenshot_path)
                # Store as relative path from screenshot_dir
                result.screenshot = os.path.join(app, environment, run_id, f"{slug}.png")
                result.error_msg = step_result.error
                break

        await browser.close()

    result.status = "fail" if failed else "pass"
    result.step_log = step_log
    result.duration_ms = int((time.monotonic() - start) * 1000)
    result.finished_at = datetime.now(timezone.utc).isoformat()
    return result


async def run_availability_test(run_id: str, app: str, environment: str,
                                base_url: str, test_def: dict) -> TestResult:
    """Simple HTTP availability check (no browser needed)."""
    import httpx
    result = TestResult(run_id=run_id, app=app, environment=environment,
                        test_name=test_def["name"])
    start = time.monotonic()
    expect_status = test_def.get("expect_status", 200)

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(base_url)
        elapsed = int((time.monotonic() - start) * 1000)
        if resp.status_code == expect_status:
            result.status = "pass"
        else:
            result.status = "fail"
            result.error_msg = f"Expected status {expect_status}, got {resp.status_code}"
        result.step_log = [StepResult(step=f"GET {base_url}", status=result.status,
                                      duration_ms=elapsed, error=result.error_msg)]
    except Exception as e:
        result.status = "error"
        result.error_msg = str(e)

    result.duration_ms = int((time.monotonic() - start) * 1000)
    result.finished_at = datetime.now(timezone.utc).isoformat()
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_browser_engine.py -v
```
Expected: 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/browser.py tests/test_browser_engine.py
git commit -m "feat: add browser test engine with Playwright"
```

---

## Task 8: Test Runner + State Tracking

**Files:**
- Create: `harness/runner.py`
- Create: `tests/test_runner.py`

- [ ] **Step 1: Write the tests**

Create `tests/test_runner.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from harness.runner import run_app, determine_alert, AlertType
from harness.models import TestResult, AppState
from harness.db import Database


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    return d


def make_result(status: str, test_name: str = "login") -> TestResult:
    return TestResult(run_id="r1", app="myapp", environment="production",
                      test_name=test_name, status=status)


def test_determine_alert_unknown_to_failing():
    assert determine_alert("unknown", "fail") == AlertType.FAIL


def test_determine_alert_passing_to_failing():
    assert determine_alert("passing", "fail") == AlertType.FAIL


def test_determine_alert_failing_to_passing():
    assert determine_alert("failing", "pass") == AlertType.RESOLVE


def test_determine_alert_no_change_passing():
    assert determine_alert("passing", "pass") is None


def test_determine_alert_no_change_failing():
    assert determine_alert("failing", "fail") is None


@pytest.mark.asyncio
async def test_run_app_updates_db_and_state(db):
    app_def = {
        "app": "myapp",
        "url": "https://example.com",
        "environments": {"production": "https://example.com"},
        "_type": "yaml",
        "tests": [{"name": "Health check", "type": "api", "endpoint": "/health",
                   "method": "GET", "expect_status": 200}]
    }

    mock_result = make_result("pass", "Health check")

    with patch("harness.runner.run_api_test", new=AsyncMock(return_value=mock_result)), \
         patch("harness.runner.dispatch_alerts", new=AsyncMock()):
        run_id = await run_app(app_def, "production", "api", db, config={})

    run = db.get_run(run_id)
    assert run["status"] == "complete"
    state = db.get_app_state("myapp", "production", "Health check")
    assert state["state"] == "passing"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_runner.py -v
```
Expected: `ImportError`.

- [ ] **Step 3: Create `harness/runner.py`**

```python
import asyncio
from enum import Enum
from datetime import datetime, timezone
from typing import Optional
from harness.db import Database
from harness.models import Run, AppState
from harness.loader import resolve_base_url
from harness.api import run_api_test
from harness.browser import run_browser_test, run_availability_test
from harness.alerts import dispatch_alerts


class AlertType(Enum):
    FAIL = "fail"
    RESOLVE = "resolve"


def determine_alert(previous_state: str, new_status: str) -> Optional[AlertType]:
    """Return AlertType if a notification should be sent, else None."""
    is_fail = new_status in ("fail", "error")
    if is_fail and previous_state in ("unknown", "passing"):
        return AlertType.FAIL
    if not is_fail and previous_state == "failing":
        return AlertType.RESOLVE
    return None


async def run_app(app_def: dict, environment: str, triggered_by: str,
                  db: Database, config: dict, run_id: str = None) -> str:
    """Run all tests for one app+environment. Returns the run_id.

    If run_id is provided, reuses an existing Run record (pre-inserted by the API
    so callers get a real ID immediately). Otherwise creates a new one.
    """
    if run_id:
        run = Run(id=run_id, app=app_def["app"], environment=environment, triggered_by=triggered_by)
    else:
        run = Run(app=app_def["app"], environment=environment, triggered_by=triggered_by)
        db.insert_run(run)
    db.update_run_status(run.id, "running",
                         started_at=datetime.now(timezone.utc).isoformat())

    base_url = resolve_base_url(app_def, environment)
    browser_cfg = config.get("browser", {})
    headless = browser_cfg.get("headless", True)
    timeout_ms = browser_cfg.get("timeout_ms", 30000)
    alerts_cfg = config.get("alerts", {})

    alerts_to_send = []

    for test_def in app_def.get("tests", []):
        test_type = test_def.get("type", "availability")

        if test_type == "api":
            result = await run_api_test(run.id, run.app, environment, base_url, test_def)
        elif test_type == "browser":
            result = await run_browser_test(run.id, run.app, environment, base_url,
                                            test_def, headless=headless, timeout_ms=timeout_ms)
        else:  # availability
            result = await run_availability_test(run.id, run.app, environment, base_url, test_def)

        db.insert_test_result(result)

        # Determine state transition
        prev = db.get_app_state(run.app, environment, test_def["name"])
        prev_state = prev["state"] if prev else "unknown"
        new_state_str = "passing" if result.status == "pass" else "failing"
        new_state = AppState(app=run.app, environment=environment,
                             test_name=test_def["name"], state=new_state_str,
                             since=result.finished_at or datetime.now(timezone.utc).isoformat())
        db.upsert_app_state(new_state)

        alert_type = determine_alert(prev_state, result.status)
        if alert_type:
            alerts_to_send.append((alert_type, run.app, environment, test_def["name"],
                                   result.error_msg))

    db.update_run_status(run.id, "complete",
                         finished_at=datetime.now(timezone.utc).isoformat())

    if alerts_to_send:
        await dispatch_alerts(alerts_to_send, alerts_cfg)

    return run.id
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_runner.py -v
```
Expected: 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/runner.py tests/test_runner.py
git commit -m "feat: add test runner with state tracking"
```

---

## Task 9: Alert Dispatch

**Files:**
- Create: `harness/alerts.py`
- Create: `tests/test_alerts.py`

- [ ] **Step 1: Write the tests**

Create `tests/test_alerts.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from harness.alerts import dispatch_alerts, format_alert_message
from harness.runner import AlertType


def test_format_alert_message_fail():
    msg = format_alert_message(AlertType.FAIL, "Customer Portal", "production",
                               "Login works", "Timeout waiting for selector")
    assert "FAIL" in msg or "failed" in msg.lower()
    assert "Customer Portal" in msg
    assert "Login works" in msg
    assert "production" in msg


def test_format_alert_message_resolve():
    msg = format_alert_message(AlertType.RESOLVE, "Customer Portal", "production",
                               "Login works", None)
    assert "resolv" in msg.lower() or "pass" in msg.lower() or "recover" in msg.lower()
    assert "Customer Portal" in msg


@pytest.mark.asyncio
async def test_dispatch_sends_teams_webhook(httpx_mock):
    from pytest_httpx import HTTPXMock
    alerts = [(AlertType.FAIL, "myapp", "production", "login", "Timeout")]
    config = {"teams": {"webhook_url": "https://teams.example.com/webhook"}}
    httpx_mock.add_response(url="https://teams.example.com/webhook", status_code=200)
    await dispatch_alerts(alerts, config)  # should not raise


@pytest.mark.asyncio
async def test_dispatch_skips_missing_config():
    alerts = [(AlertType.FAIL, "myapp", "production", "login", "Timeout")]
    # No alerts config — should silently do nothing
    await dispatch_alerts(alerts, {})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_alerts.py -v
```
Expected: `ImportError`.

- [ ] **Step 3: Create `harness/alerts.py`**

```python
import httpx
import smtplib
from email.message import EmailMessage
from harness.runner import AlertType


def format_alert_message(alert_type: AlertType, app: str, environment: str,
                          test_name: str, error_msg: str | None) -> str:
    if alert_type == AlertType.FAIL:
        msg = f"[FAIL] {app} ({environment}) — {test_name} failed"
        if error_msg:
            msg += f"\n\nError: {error_msg}"
        return msg
    else:
        return f"[RESOLVED] {app} ({environment}) — {test_name} is now passing"


async def _send_teams(webhook_url: str, text: str) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(webhook_url, json={"text": text})


def _send_email(smtp_config: dict, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp_config["from"]
    msg["To"] = ", ".join(smtp_config["to"])
    msg.set_content(body)
    with smtplib.SMTP(smtp_config["smtp_host"], smtp_config.get("smtp_port", 587)) as s:
        s.starttls()
        s.login(smtp_config["username"], smtp_config["password"])
        s.send_message(msg)


async def dispatch_alerts(alerts: list, alerts_config: dict) -> None:
    """Send alerts for all (AlertType, app, environment, test_name, error_msg) tuples."""
    teams_cfg = alerts_config.get("teams")
    email_cfg = alerts_config.get("email")

    for alert_type, app, environment, test_name, error_msg in alerts:
        text = format_alert_message(alert_type, app, environment, test_name, error_msg)

        if teams_cfg and teams_cfg.get("webhook_url"):
            try:
                await _send_teams(teams_cfg["webhook_url"], text)
            except Exception as e:
                print(f"[alerts] Teams webhook failed: {e}")

        if email_cfg:
            subject = f"[Harness] {app} — {test_name} {'FAILED' if alert_type == AlertType.FAIL else 'RESOLVED'}"
            try:
                _send_email(email_cfg, subject, text)
            except Exception as e:
                print(f"[alerts] Email failed: {e}")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_alerts.py -v
```
Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/alerts.py tests/test_alerts.py
git commit -m "feat: add Teams and email alert dispatch"
```

---

## Task 10: FastAPI REST API

**Files:**
- Create: `web/routes/api.py`
- Create: `tests/test_web_api.py`
- Update: `web/main.py` (create it)

- [ ] **Step 1: Write the tests**

Create `tests/test_web_api.py`:

```python
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock, MagicMock
from web.main import create_app
from harness.db import Database


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    return d


@pytest.fixture
def client(db, tmp_path):
    config = {"default_environment": "production", "environments": {"production": {"label": "Production"}}}
    app = create_app(db=db, config=config, apps_dir=str(tmp_path / "apps"))
    (tmp_path / "apps").mkdir()
    return TestClient(app)


def test_get_apps_empty(client):
    resp = client.get("/api/apps?environment=production")
    assert resp.status_code == 200
    assert resp.json() == []


def test_trigger_run_returns_run_id(client, db, tmp_path):
    # Create a minimal YAML app
    import yaml
    apps_dir = tmp_path / "apps"
    (apps_dir / "myapp.yaml").write_text(yaml.dump({
        "app": "myapp", "url": "https://example.com",
        "tests": [{"name": "health", "type": "api", "endpoint": "/h",
                   "method": "GET", "expect_status": 200}]
    }))
    with patch("web.routes.api.run_app", new=AsyncMock(return_value="run-123")):
        resp = client.post("/api/runs", json={"app": "myapp", "environment": "production"})
    assert resp.status_code == 202
    assert "run_id" in resp.json()


def test_trigger_run_rejects_conflict(client, db):
    from harness.models import Run
    run = Run(app="myapp", environment="production", triggered_by="ui", status="running")
    db.insert_run(run)
    db.update_run_status(run.id, "running")
    resp = client.post("/api/runs", json={"app": "myapp", "environment": "production"})
    assert resp.status_code == 409


def test_get_run_status(client, db):
    from harness.models import Run
    run = Run(app="myapp", environment="production", triggered_by="api")
    db.insert_run(run)
    resp = client.get(f"/api/runs/{run.id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"


def test_get_run_not_found(client):
    resp = client.get("/api/runs/nonexistent")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_web_api.py -v
```
Expected: `ImportError`.

- [ ] **Step 3: Create `web/routes/api.py`**

```python
import asyncio
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
from harness.models import Run

router = APIRouter(prefix="/api")


class RunRequest(BaseModel):
    app: Optional[str] = None
    environment: str
    triggered_by: str = "api"


@router.post("/runs", status_code=202)
async def trigger_run(req: RunRequest, background_tasks: BackgroundTasks):
    from web.main import get_db, get_config, get_apps
    from harness.runner import run_app
    db = get_db()
    config = get_config()
    all_apps = get_apps()

    target_apps = [a for a in all_apps if req.app is None or a["app"] == req.app]
    if req.app and not target_apps:
        raise HTTPException(status_code=404, detail=f"App '{req.app}' not found")

    # Pre-insert Run records so callers get real IDs back immediately.
    # The background task updates status from 'pending' → 'running' → 'complete'.
    queued = []
    for app_def in target_apps:
        if db.is_run_active(app_def["app"], req.environment):
            if req.app:
                raise HTTPException(
                    status_code=409,
                    detail=f"A run for {app_def['app']} ({req.environment}) is already in progress"
                )
            continue  # skip already-running apps in "run all" mode
        run = Run(app=app_def["app"], environment=req.environment, triggered_by=req.triggered_by)
        db.insert_run(run)
        background_tasks.add_task(run_app, app_def, req.environment, req.triggered_by, db, config,
                                  run_id=run.id)
        queued.append(run.id)

    return {"run_ids": queued, "apps": [a["app"] for a in target_apps]}


@router.get("/runs/{run_id}")
async def get_run(run_id: str):
    from web.main import get_db
    db = get_db()
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    results = db.get_results_for_run(run_id)
    return {**run, "results": results}


@router.get("/apps")
async def list_apps(environment: str = "production"):
    from web.main import get_db
    db = get_db()
    return db.get_app_summary(environment)


@router.get("/results/{app}/{environment}")
async def get_results(app: str, environment: str, limit: int = 20):
    from web.main import get_db
    import sqlite3
    db = get_db()
    conn = sqlite3.connect(db.path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM test_results WHERE app=? AND environment=? "
        "ORDER BY finished_at DESC LIMIT ?",
        (app, environment, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
```

- [ ] **Step 4: Create `web/main.py`**

```python
import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from harness.db import Database
from harness.loader import load_apps

_db: Database = None
_config: dict = {}
_apps: list = []


def get_db() -> Database:
    return _db


def get_config() -> dict:
    return _config


def get_apps() -> list:
    return _apps


def create_app(db: Database = None, config: dict = None, apps_dir: str = "apps") -> FastAPI:
    global _db, _config, _apps
    _config = config or {}
    _apps = load_apps(apps_dir) if os.path.isdir(apps_dir) else []

    if db is None:
        os.makedirs("data", exist_ok=True)
        _db = Database("data/harness.db")
        _db.init_schema()
    else:
        _db = db

    app = FastAPI(title="Web Testing Harness")

    from web.routes.api import router as api_router
    app.include_router(api_router)

    from web.routes.dashboard import router as dashboard_router
    app.include_router(dashboard_router)

    screenshots_dir = "data/screenshots"
    os.makedirs(screenshots_dir, exist_ok=True)
    app.mount("/screenshots", StaticFiles(directory=screenshots_dir), name="screenshots")

    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.isdir(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    return app


def main():
    import uvicorn
    from harness.config import load_config
    config = load_config("config.yaml")
    app = create_app(config=config)
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_web_api.py -v
```
Expected: 5 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add web/main.py web/routes/api.py tests/test_web_api.py
git commit -m "feat: add FastAPI REST API and app entrypoint"
```

---

## Task 11: Dashboard HTML

**Files:**
- Create: `web/routes/dashboard.py`
- Create: `web/templates/base.html`
- Create: `web/templates/dashboard.html`
- Create: `web/templates/detail.html`
- Create: `web/static/style.css`

- [ ] **Step 1: Create `web/routes/dashboard.py`**

```python
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import os

router = APIRouter()
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "..", "templates"))


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, environment: str = None):
    from web.main import get_db, get_config
    db = get_db()
    config = get_config()
    env = environment or config.get("default_environment", "production")
    envs = config.get("environments", {})
    summary = db.get_app_summary(env)
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "summary": summary,
        "environment": env,
        "environments": envs,
    })


@router.get("/app/{app}/{environment}", response_class=HTMLResponse)
async def app_detail(request: Request, app: str, environment: str, run_id: str = None):
    from web.main import get_db, get_config
    import sqlite3
    db = get_db()
    config = get_config()

    # Get recent runs for this app+environment
    conn = sqlite3.connect(db.path)
    conn.row_factory = sqlite3.Row
    runs = conn.execute(
        "SELECT * FROM runs WHERE app=? AND environment=? ORDER BY started_at DESC LIMIT 10",
        (app, environment)
    ).fetchall()
    runs = [dict(r) for r in runs]

    selected_run = None
    test_results = []
    if runs:
        selected = next((r for r in runs if r["id"] == run_id), runs[0])
        selected_run = selected
        test_results = db.get_results_for_run(selected["id"])
        import json
        for tr in test_results:
            tr["step_log"] = json.loads(tr["step_log"] or "[]")
    conn.close()

    history = {}
    conn2 = sqlite3.connect(db.path)
    conn2.row_factory = sqlite3.Row
    for tr in test_results:
        rows = conn2.execute(
            "SELECT status FROM test_results WHERE app=? AND environment=? AND test_name=? "
            "ORDER BY finished_at DESC LIMIT 10",
            (app, environment, tr["test_name"])
        ).fetchall()
        history[tr["test_name"]] = [r["status"] for r in rows]
    conn2.close()

    return templates.TemplateResponse("detail.html", {
        "request": request,
        "app": app,
        "environment": environment,
        "environments": config.get("environments", {}),
        "runs": runs,
        "selected_run": selected_run,
        "test_results": test_results,
        "history": history,
    })
```

- [ ] **Step 2: Create `web/templates/base.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Testing Harness</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <nav class="nav">
    <a href="/" class="nav-brand">Testing Harness</a>
    <div class="env-switcher">
      {% for key, env in environments.items() %}
        <a href="?environment={{ key }}"
           class="env-btn {% if environment == key %}active{% endif %}">
          {{ env.label }}
        </a>
      {% endfor %}
    </div>
  </nav>
  <main class="main">
    {% block content %}{% endblock %}
  </main>
</body>
</html>
```

- [ ] **Step 3: Create `web/templates/dashboard.html`**

```html
{% extends "base.html" %}
{% block content %}
<div class="toolbar">
  <h1>Application Status</h1>
  <button class="btn btn-primary" onclick="runAll()">Run All</button>
</div>

<input class="search" type="text" placeholder="Search apps…" oninput="filterTable(this.value)">

<table class="table" id="app-table">
  <thead>
    <tr>
      <th>Application</th>
      <th>Status</th>
      <th>Tests</th>
      <th>Last Run</th>
      <th></th>
    </tr>
  </thead>
  <tbody>
    {% for row in summary %}
    <tr class="{% if row.failing > 0 %}row-fail{% endif %}" data-app="{{ row.app }}">
      <td><a href="/app/{{ row.app }}/{{ environment }}">{{ row.app }}</a></td>
      <td>
        {% if row.failing > 0 %}
          <span class="badge badge-fail">✗ Fail</span>
        {% elif row.unknown == row.total %}
          <span class="badge badge-unknown">— Unknown</span>
        {% else %}
          <span class="badge badge-pass">✓ Pass</span>
        {% endif %}
      </td>
      <td>{{ row.passing }}/{{ row.total }}</td>
      <td>{{ row.last_run or "—" }}</td>
      <td>
        <button class="btn btn-sm" onclick="triggerRun('{{ row.app }}')">Run</button>
      </td>
    </tr>
    {% else %}
    <tr><td colspan="5" class="empty">No apps configured. Add YAML files to the apps/ directory.</td></tr>
    {% endfor %}
  </tbody>
</table>

<script>
const ENV = "{{ environment }}";

async function triggerRun(app) {
  const btn = event.target;
  btn.textContent = "Running…";
  btn.disabled = true;
  const resp = await fetch("/api/runs", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({app, environment: ENV, triggered_by: "ui"})
  });
  if (!resp.ok) {
    const err = await resp.json();
    alert(err.detail || "Failed to start run");
    btn.textContent = "Run";
    btn.disabled = false;
  } else {
    setTimeout(() => location.reload(), 3000);
  }
}

async function runAll() {
  await fetch("/api/runs", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({environment: ENV, triggered_by: "ui"})
  });
  setTimeout(() => location.reload(), 3000);
}

function filterTable(query) {
  document.querySelectorAll("#app-table tbody tr[data-app]").forEach(row => {
    row.style.display = row.dataset.app.toLowerCase().includes(query.toLowerCase()) ? "" : "none";
  });
}
</script>
{% endblock %}
```

- [ ] **Step 4: Create `web/templates/detail.html`**

```html
{% extends "base.html" %}
{% block content %}
<div class="breadcrumb">
  <a href="/?environment={{ environment }}">← Dashboard</a> / {{ app }}
</div>

{% if selected_run %}
<div class="run-meta">
  <span>Run: {{ selected_run.id[:8] }}</span>
  <span>Status: <strong>{{ selected_run.status }}</strong></span>
  <span>Started: {{ selected_run.started_at or "—" }}</span>
  <button class="btn btn-sm" onclick="triggerRun()">Re-run All</button>
</div>

{% for tr in test_results %}
<div class="test-card {% if tr.status == 'fail' or tr.status == 'error' %}test-card--fail{% elif tr.status == 'pass' %}test-card--pass{% endif %}">
  <div class="test-card-header">
    <span class="test-name">{{ tr.test_name }}</span>
    <span class="test-status">
      {% if tr.status == 'pass' %}✓ Pass{% elif tr.status == 'fail' %}✗ Fail{% else %}⚠ Error{% endif %}
    </span>
    <span class="test-duration">{{ tr.duration_ms }}ms</span>
  </div>

  {% if tr.error_msg %}
  <div class="error-box">{{ tr.error_msg }}</div>
  {% endif %}

  {% if tr.screenshot %}
  <div class="screenshot-wrap">
    <div class="screenshot-label">Screenshot at failure</div>
    <img src="/screenshots/{{ tr.screenshot }}" alt="Screenshot" class="screenshot">
  </div>
  {% endif %}

  {% if tr.step_log %}
  <div class="step-log">
    {% for step in tr.step_log %}
    <div class="step step--{{ step.status }}">
      <span class="step-icon">{% if step.status == 'pass' %}✓{% else %}✗{% endif %}</span>
      <span class="step-name">{{ step.step }}</span>
      <span class="step-duration">{{ step.duration_ms }}ms</span>
      {% if step.error %}<span class="step-error">{{ step.error }}</span>{% endif %}
    </div>
    {% endfor %}
  </div>
  {% endif %}

  {% if history[tr.test_name] is defined %}
  <div class="history">
    {% for s in history[tr.test_name] %}
      <span class="history-dot history-dot--{{ s }}" title="{{ s }}"></span>
    {% endfor %}
    <span class="history-label">recent runs</span>
  </div>
  {% endif %}
</div>
{% endfor %}

{% else %}
<p>No runs yet for {{ app }} ({{ environment }}). <button class="btn" onclick="triggerRun()">Run now</button></p>
{% endif %}

<script>
const APP = "{{ app }}";
const ENV = "{{ environment }}";
async function triggerRun() {
  await fetch("/api/runs", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({app: APP, environment: ENV, triggered_by: "ui"})
  });
  setTimeout(() => location.reload(), 3000);
}
</script>
{% endblock %}
```

- [ ] **Step 5: Create `web/static/style.css`**

```css
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: system-ui, sans-serif; background: #0f1117; color: #e2e8f0; font-size: 14px; }
a { color: #60a5fa; text-decoration: none; }
a:hover { text-decoration: underline; }

.nav { display: flex; align-items: center; justify-content: space-between;
       padding: .75rem 1.5rem; background: #1a1f2e; border-bottom: 1px solid #2d3748; }
.nav-brand { font-weight: 700; font-size: 1rem; color: #e2e8f0; }
.env-switcher { display: flex; gap: .5rem; }
.env-btn { padding: .3rem .75rem; border-radius: 4px; border: 1px solid #4a5568;
           color: #a0aec0; font-size: .8rem; cursor: pointer; }
.env-btn.active { background: #3b82f6; border-color: #3b82f6; color: #fff; }

.main { padding: 1.5rem; max-width: 1100px; margin: 0 auto; }
.toolbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 1rem; }
h1 { font-size: 1.25rem; font-weight: 600; }

.search { width: 100%; padding: .5rem .75rem; background: #1a1f2e; border: 1px solid #2d3748;
          border-radius: 6px; color: #e2e8f0; margin-bottom: 1rem; font-size: .875rem; }

.table { width: 100%; border-collapse: collapse; }
.table th { text-align: left; padding: .6rem .75rem; color: #718096;
             font-size: .75rem; text-transform: uppercase; border-bottom: 1px solid #2d3748; }
.table td { padding: .65rem .75rem; border-bottom: 1px solid #1e2535; }
.row-fail { background: rgba(239,68,68,.05); }
.empty { text-align: center; color: #718096; padding: 2rem; }

.badge { padding: .2rem .5rem; border-radius: 4px; font-size: .75rem; font-weight: 600; }
.badge-pass { background: rgba(34,197,94,.15); color: #4ade80; }
.badge-fail { background: rgba(239,68,68,.15); color: #f87171; }
.badge-unknown { background: rgba(148,163,184,.1); color: #94a3b8; }

.btn { padding: .35rem .75rem; border-radius: 5px; border: 1px solid #3b82f6;
       background: transparent; color: #60a5fa; cursor: pointer; font-size: .8rem; }
.btn:hover { background: #3b82f6; color: #fff; }
.btn-primary { background: #3b82f6; color: #fff; }
.btn-sm { padding: .2rem .5rem; font-size: .75rem; }

.breadcrumb { margin-bottom: 1rem; color: #718096; font-size: .875rem; }
.run-meta { display: flex; gap: 1.5rem; align-items: center; margin-bottom: 1rem;
            padding: .75rem; background: #1a1f2e; border-radius: 6px; font-size: .875rem; }

.test-card { border: 1px solid #2d3748; border-radius: 8px; margin-bottom: 1rem; overflow: hidden; }
.test-card--pass { border-color: #166534; }
.test-card--fail { border-color: #7f1d1d; }
.test-card-header { display: flex; align-items: center; gap: 1rem; padding: .75rem 1rem;
                     background: #1a1f2e; }
.test-name { font-weight: 600; flex: 1; }
.test-status { font-size: .8rem; }
.test-duration { color: #718096; font-size: .75rem; margin-left: auto; }

.error-box { padding: .75rem 1rem; background: #3b0f0f; border-top: 1px solid #7f1d1d;
             font-family: monospace; font-size: .8rem; color: #fca5a5; }

.screenshot-wrap { padding: .75rem 1rem; border-top: 1px solid #2d3748; }
.screenshot-label { font-size: .75rem; color: #718096; margin-bottom: .5rem; }
.screenshot { max-width: 100%; border-radius: 4px; border: 1px solid #2d3748; }

.step-log { padding: .5rem 1rem; border-top: 1px solid #2d3748; }
.step { display: flex; align-items: baseline; gap: .5rem; padding: .25rem 0;
        font-family: monospace; font-size: .78rem; }
.step--pass .step-icon { color: #4ade80; }
.step--fail .step-icon, .step--error .step-icon { color: #f87171; }
.step-name { flex: 1; }
.step-duration { color: #718096; font-size: .7rem; }
.step-error { color: #f87171; font-size: .75rem; }

.history { display: flex; align-items: center; gap: .25rem; padding: .6rem 1rem;
           border-top: 1px solid #2d3748; }
.history-dot { width: 14px; height: 14px; border-radius: 3px; display: inline-block; }
.history-dot--pass { background: #166534; }
.history-dot--fail { background: #7f1d1d; }
.history-dot--error { background: #78350f; }
.history-label { font-size: .7rem; color: #718096; margin-left: .25rem; }
```

- [ ] **Step 6: Run a quick sanity check**

```bash
pytest tests/ -v --ignore=tests/test_browser_engine.py
```
Expected: All non-browser tests PASS.

- [ ] **Step 7: Commit**

```bash
git add web/routes/dashboard.py web/templates/ web/static/
git commit -m "feat: add dashboard HTML with table view and failure detail"
```

---

## Task 12: Scheduler XML Generator + harness `__init__.py`

**Files:**
- Create: `harness/scheduler.py`
- Update: `harness/__init__.py`

- [ ] **Step 1: Create `harness/scheduler.py`**

This generates the XML file you import into Windows Task Scheduler.

```python
import xml.etree.ElementTree as ET
from datetime import datetime


def generate_task_xml(task_name: str, harness_url: str,
                      app: str | None, environment: str,
                      interval_minutes: int = 60) -> str:
    """
    Generate Windows Task Scheduler XML that calls POST /api/runs on an interval.

    Usage:
        xml = generate_task_xml("Harness-Prod", "http://localhost:8000",
                                app=None, environment="production", interval_minutes=60)
        with open("task.xml", "w") as f:
            f.write(xml)
        # Then: schtasks /create /xml task.xml /tn "Harness-Prod"
    """
    body_parts = [f'"environment": "{environment}", "triggered_by": "api"']
    if app:
        body_parts.insert(0, f'"app": "{app}"')
    body = "{" + ", ".join(body_parts) + "}"
    command = (
        f'powershell -Command "Invoke-RestMethod -Uri {harness_url}/api/runs '
        f"-Method POST -ContentType 'application/json' -Body '{body}'\""
    )

    root = ET.Element("Task", version="1.2",
                      xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task")
    triggers = ET.SubElement(root, "Triggers")
    trigger = ET.SubElement(triggers, "TimeTrigger")
    ET.SubElement(trigger, "StartBoundary").text = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    ET.SubElement(trigger, "Enabled").text = "true"
    repetition = ET.SubElement(trigger, "Repetition")
    ET.SubElement(repetition, "Interval").text = f"PT{interval_minutes}M"

    actions = ET.SubElement(root, "Actions", Context="Author")
    exec_elem = ET.SubElement(actions, "Exec")
    ET.SubElement(exec_elem, "Command").text = "cmd.exe"
    ET.SubElement(exec_elem, "Arguments").text = f"/c {command}"

    settings = ET.SubElement(root, "Settings")
    ET.SubElement(settings, "MultipleInstancesPolicy").text = "IgnoreNew"
    ET.SubElement(settings, "Enabled").text = "true"

    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def print_setup_instructions(harness_url: str, environment: str,
                              interval_minutes: int = 60) -> None:
    xml = generate_task_xml(
        task_name=f"Harness-{environment}",
        harness_url=harness_url,
        app=None,
        environment=environment,
        interval_minutes=interval_minutes,
    )
    filename = f"harness-task-{environment}.xml"
    with open(filename, "w") as f:
        f.write(xml)
    print(f"Task XML written to {filename}")
    print(f"To install: schtasks /create /xml {filename} /tn Harness-{environment}")
```

- [ ] **Step 2: Update `harness/__init__.py`** to expose the public API for Python test files

```python
from harness.models import TestResult, Run, AppState, StepResult
from harness.browser import run_browser_test
from harness.api import run_api_test
from harness.config import ConfigError

# AppTest base class for Python escape-hatch test files
import os
from typing import Optional


class AppTest:
    """Base class for Python-defined app tests. Subclass and add test_* methods."""
    name: str = ""
    base_url: str = ""
    environments: dict = {}

    def env(self, key: str) -> str:
        val = os.environ.get(key)
        if val is None:
            raise ConfigError(f"Environment variable '{key}' is not set")
        return val

    def resolve_base_url(self, environment: str) -> str:
        if self.environments and environment in self.environments:
            return self.environments[environment]
        if self.base_url:
            return self.base_url
        raise ConfigError(
            f"No base URL for app '{self.name}' in environment '{environment}'"
        )
```

- [ ] **Step 3: Commit**

```bash
git add harness/scheduler.py harness/__init__.py
git commit -m "feat: add scheduler XML generator and AppTest base class"
```

---

## Task 13: Full Test Run + Startup Verification

**Files:**
- No new files — verify everything works end-to-end

- [ ] **Step 1: Run the full test suite**

```bash
pytest tests/ -v
```
Expected: All tests PASS. Note any that fail and fix them before continuing.

- [ ] **Step 2: Start the server and verify the dashboard loads**

```bash
python -m web.main
```

Open `http://localhost:8000` in a browser. Expected: Dashboard table loads with "No apps configured" message (or sample apps if present).

- [ ] **Step 3: Verify the API works via curl**

```bash
curl -s http://localhost:8000/api/apps?environment=production
```
Expected: `[]` (empty JSON array).

```bash
curl -s -X POST http://localhost:8000/api/runs \
  -H "Content-Type: application/json" \
  -d "{\"environment\": \"production\", \"triggered_by\": \"api\"}"
```
Expected: `{"run_id": "queued", "apps": []}` (no apps configured yet).

- [ ] **Step 4: Add a real app YAML and test an actual URL**

Edit `apps/example-api.yaml` to point at a real URL your team can reach (e.g. an internal health endpoint). Then trigger a run and check the dashboard.

- [ ] **Step 5: Generate Task Scheduler XML**

```python
from harness.scheduler import print_setup_instructions
print_setup_instructions("http://localhost:8000", "production", interval_minutes=60)
```
Expected: Creates `harness-task-production.xml`. Open it in a text editor to verify it looks correct before importing.

- [ ] **Step 6: Commit**

```bash
git add harness/ web/ apps/ tests/ docs/
git commit -m "feat: complete initial implementation — all tests passing"
```

> Note: Do not commit generated `harness-task-*.xml` files — these are environment-specific and should be generated fresh per deployment.

---

## Running the Harness

**Start the server:**
```bash
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env   # fill in credentials
python -m web.main
```

**Run all tests:**
```bash
pytest tests/ -v
```

**Run a single test module:**
```bash
pytest tests/test_db.py -v
```

**Generate Windows Task Scheduler XML:**
```python
from harness.scheduler import print_setup_instructions
print_setup_instructions("http://your-server:8000", "production", interval_minutes=60)
```
Then: `schtasks /create /xml harness-task-production.xml /tn Harness-Production`
