# Live Run Updates & Screenshot Step — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add explicit `screenshot` browser step support and make the dashboard + detail page auto-refresh as test runs execute, so users never have to manually reload.

**Architecture:** Screenshot step extends `StepResult` with an optional path field, adds a handler in `execute_step`, serialises through the existing JSON step_log column, and renders as an inline thumbnail. Live updates use 2-second JS polling against the existing `GET /api/runs/{run_id}` endpoint; `get_app_summary` gains an `active_run_id` field so page-load state is server-seeded; both pages do a single `location.reload()` when the run completes rather than patching the DOM.

**Tech Stack:** Python 3, FastAPI, SQLite, Playwright (async), vanilla JS, Jinja2

---

## File Map

| File | Action | What changes |
|------|--------|-------------|
| `harness/models.py` | Modify | Add `screenshot: Optional[str] = None` to `StepResult` |
| `harness/browser.py` | Modify | `execute_step` gains `screenshot_path` param + `screenshot` step handler; `run_browser_test` computes per-step paths |
| `harness/db.py` | Modify | Step-log JSON gains `screenshot` key; `get_app_summary` gains `active_run_id` per row |
| `web/routes/dashboard.py` | Modify | `app_detail` passes `is_live` and `pending_test_names` |
| `web/templates/detail.html` | Modify | Run history strip, progress bar, pending cards, live-poll JS, step screenshot thumbnails |
| `web/templates/dashboard.html` | Modify | `data-run-id` on rows, live-update JS replaces the crude `setTimeout(reload)` |
| `web/templates/app_form.html` | Modify | Add `screenshot` to step action list; hide value input when selected |
| `tests/test_models_and_db.py` | Create | Unit tests for `StepResult.screenshot` serialisation and `active_run_id` in summary |
| `tests/test_browser_screenshot.py` | Create | Unit tests for the `screenshot` step in `execute_step` |

---

## Task 1: Add `screenshot` field to `StepResult` and serialise it in the DB

**Files:**
- Modify: `harness/models.py`
- Modify: `harness/db.py` (lines ~80–95, `insert_test_result`)
- Create: `tests/test_models_and_db.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_models_and_db.py`:

```python
import json
from harness.models import StepResult, TestResult

def test_step_result_has_screenshot_field():
    s = StepResult(step="screenshot", status="pass", duration_ms=42, screenshot="app/prod/run1/test-step-0.png")
    assert s.screenshot == "app/prod/run1/test-step-0.png"

def test_step_result_screenshot_defaults_none():
    s = StepResult(step="navigate /", status="pass", duration_ms=10)
    assert s.screenshot is None

def test_db_serialises_screenshot_in_step_log(tmp_path):
    """screenshot field survives round-trip through insert_test_result → get_results_for_run."""
    import os
    os.environ.setdefault("HARNESS_DB", str(tmp_path / "h.db"))
    from harness.db import Database
    from harness.models import Run, TestResult, StepResult

    db = Database(str(tmp_path / "h.db"))
    db.init_schema()

    run = Run(app="myapp", environment="prod", triggered_by="test")
    db.insert_run(run)

    step_with_shot = StepResult(step="screenshot", status="pass", duration_ms=5,
                                screenshot="myapp/prod/run1/test-step-0.png")
    step_no_shot = StepResult(step="navigate /", status="pass", duration_ms=3)
    tr = TestResult(run_id=run.id, app="myapp", environment="prod",
                    test_name="login", status="pass",
                    step_log=[step_with_shot, step_no_shot])
    db.insert_test_result(tr)

    results = db.get_results_for_run(run.id)
    assert len(results) == 1
    steps = json.loads(results[0]["step_log"])
    assert steps[0]["screenshot"] == "myapp/prod/run1/test-step-0.png"
    assert steps[1]["screenshot"] is None
```

- [ ] **Step 2: Run tests to confirm they fail**

```
python -m pytest tests/test_models_and_db.py -v
```

Expected: `test_step_result_has_screenshot_field` FAIL (no such field), others depend on it.

- [ ] **Step 3: Add `screenshot` field to `StepResult` in `harness/models.py`**

Current `StepResult`:
```python
@dataclass
class StepResult:
    step: str
    status: str
    duration_ms: int
    error: Optional[str] = None
```

Replace with:
```python
@dataclass
class StepResult:
    step: str
    status: str
    duration_ms: int
    error: Optional[str] = None
    screenshot: Optional[str] = None
```

- [ ] **Step 4: Update step-log JSON serialisation in `harness/db.py`**

Find `insert_test_result` (around line 80). The current serialisation:
```python
step_log_json = json.dumps([
    {"step": s.step, "status": s.status, "duration_ms": s.duration_ms, "error": s.error}
    for s in tr.step_log
])
```

Replace with:
```python
step_log_json = json.dumps([
    {"step": s.step, "status": s.status, "duration_ms": s.duration_ms,
     "error": s.error, "screenshot": s.screenshot}
    for s in tr.step_log
])
```

- [ ] **Step 5: Run tests to confirm they pass**

```
python -m pytest tests/test_models_and_db.py -v
```

Expected: 3 passing.

- [ ] **Step 6: Run full suite to confirm no regressions**

```
python -m pytest tests/ -q
```

Expected: all passing (was 92 before this task).

- [ ] **Step 7: Commit**

```bash
git add harness/models.py harness/db.py tests/test_models_and_db.py
git commit -m "feat: add screenshot field to StepResult and db serialisation"
```

---

## Task 2: Add `screenshot` step handler to `execute_step` and `run_browser_test`

**Files:**
- Modify: `harness/browser.py`
- Create: `tests/test_browser_screenshot.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_browser_screenshot.py`:

```python
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from harness.browser import execute_step
from harness.models import StepResult

@pytest.fixture
def mock_page():
    page = MagicMock()
    page.screenshot = AsyncMock()
    return page

def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)

def test_screenshot_step_saves_file_and_returns_path(mock_page, tmp_path):
    """execute_step with screenshot step saves the file and puts relative path in StepResult."""
    shot_path = str(tmp_path / "app" / "prod" / "run1" / "test-step-0.png")
    result = run(execute_step(mock_page, {"screenshot": None}, "https://example.com",
                              screenshot_path=shot_path))
    mock_page.screenshot.assert_called_once_with(path=shot_path)
    assert result.status == "pass"
    assert result.screenshot is not None
    assert "test-step-0" in result.screenshot

def test_screenshot_step_without_path_returns_error(mock_page):
    """execute_step with screenshot step and no screenshot_path returns error status."""
    result = run(execute_step(mock_page, {"screenshot": None}, "https://example.com",
                              screenshot_path=None))
    mock_page.screenshot.assert_not_called()
    assert result.status == "error"
    assert "No screenshot path" in result.error

def test_non_screenshot_step_ignores_screenshot_path(mock_page):
    """Passing screenshot_path to a non-screenshot step has no effect."""
    mock_page.goto = AsyncMock()
    result = run(execute_step(mock_page, {"navigate": "/"}, "https://example.com",
                              screenshot_path="/some/path.png"))
    mock_page.screenshot.assert_not_called()
    assert result.status == "pass"
```

- [ ] **Step 2: Run tests to confirm they fail**

```
python -m pytest tests/test_browser_screenshot.py -v
```

Expected: FAIL — `execute_step` doesn't accept `screenshot_path`.

- [ ] **Step 3: Update `execute_step` in `harness/browser.py`**

Current signature (line 9):
```python
async def execute_step(page: Page, step: dict, base_url: str) -> StepResult:
```

New signature:
```python
async def execute_step(page: Page, step: dict, base_url: str,
                       screenshot_path: Optional[str] = None) -> StepResult:
```

Add `Optional` to the existing import at the top of the file — it should already be there from `typing`. Check line 1–6, confirm `from typing import Optional` exists (it does via `from harness.models import TestResult, StepResult` which uses Optional).

Actually `Optional` is used in the return type of `run_browser_test` — add it to the `typing` import if not there:
```python
from typing import Optional
```

Add the screenshot step handler **before** the unknown-step fallback at the end of `execute_step`. Insert this block after the `wait_for_selector` handler (currently the last `if` block):

```python
        if "screenshot" in step:
            if not screenshot_path:
                return StepResult(step="screenshot", status="error", duration_ms=_elapsed(),
                                  error="No screenshot path provided")
            os.makedirs(os.path.dirname(screenshot_path), exist_ok=True)
            await page.screenshot(path=screenshot_path)
            rel = os.path.relpath(screenshot_path, "data/screenshots").replace("\\", "/")
            return StepResult(step="screenshot", status="pass", duration_ms=_elapsed(),
                              screenshot=rel)
```

- [ ] **Step 4: Update `run_browser_test` to pass per-step screenshot paths**

In `run_browser_test` (line ~61), the loop currently is:

```python
for step in steps:
    step_result = await execute_step(page, step, base_url)
```

And `slug` is computed inside the failure block. Move `slug` before the loop and pass a step screenshot path:

```python
slug = slugify_test_name(test_def["name"])
for i, step in enumerate(steps):
    step_shot = (
        os.path.join(screenshot_dir, app, environment, run_id, f"{slug}-step-{i}.png")
        if "screenshot" in step else None
    )
    step_result = await execute_step(page, step, base_url, screenshot_path=step_shot)
    step_log.append(step_result)
    if step_result.status in ("fail", "error"):
        failed = True
        screenshot_path = os.path.join(screenshot_dir, app, environment, run_id, f"{slug}.png")
        os.makedirs(os.path.dirname(screenshot_path), exist_ok=True)
        await page.screenshot(path=screenshot_path)
        result.screenshot = os.path.join(app, environment, run_id, f"{slug}.png")
        result.error_msg = step_result.error
        break
```

(The `slug` variable in the failure block was previously computed there — now it uses the already-computed one from above the loop.)

- [ ] **Step 5: Run tests to confirm they pass**

```
python -m pytest tests/test_browser_screenshot.py -v
```

Expected: 3 passing.

- [ ] **Step 6: Run full suite**

```
python -m pytest tests/ -q
```

Expected: all passing.

- [ ] **Step 7: Commit**

```bash
git add harness/browser.py tests/test_browser_screenshot.py
git commit -m "feat: add screenshot step handler to execute_step"
```

---

## Task 3: Show step screenshot thumbnails in `detail.html`

**Files:**
- Modify: `web/templates/detail.html`

No new tests — template rendering is covered by existing `test_web_apps.py` page-load tests.

- [ ] **Step 1: Locate the step log rendering block in `detail.html`**

Find this block (around line 38–43):
```html
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
```

- [ ] **Step 2: Add the step screenshot thumbnail after the error line**

Replace the inner `<div class="step ...">` block with:

```html
  <div class="step step--{{ step.status }}">
    <span class="step-icon">{% if step.status == 'pass' %}✓{% else %}✗{% endif %}</span>
    <span class="step-name">{{ step.step }}</span>
    <span class="step-duration">{{ step.duration_ms }}ms</span>
    {% if step.error %}<span class="step-error">{{ step.error }}</span>{% endif %}
    {% if step.screenshot %}
    <div style="margin-top:.4rem;">
      <img src="/screenshots/{{ step.screenshot }}" class="screenshot"
           style="max-width:320px; border-radius:4px; display:block;">
    </div>
    {% endif %}
  </div>
```

- [ ] **Step 3: Run full suite to confirm no regressions**

```
python -m pytest tests/ -q
```

Expected: all passing.

- [ ] **Step 4: Commit**

```bash
git add web/templates/detail.html
git commit -m "feat: show step screenshot thumbnails inline in step log"
```

---

## Task 4: Add `screenshot` to the browser step form

**Files:**
- Modify: `web/templates/app_form.html`

- [ ] **Step 1: Add `screenshot` to the Jinja step-action list**

Find this list in `app_form.html` (in the `{% for a in [...] %}` loop inside the step-rendering block, around line 107):

```html
{% for a in ['navigate','fill','click','assert_text','assert_url_contains','wait_for_selector','wait_for_url','wait'] %}
```

Replace with:

```html
{% for a in ['navigate','fill','click','assert_text','assert_url_contains','wait_for_selector','wait_for_url','wait','screenshot'] %}
```

- [ ] **Step 2: Handle `screenshot` in `onStepActionChange` JS**

Find the `onStepActionChange` function. It currently has an `if (sel.value === 'fill')` branch and an `else` branch. Add a `screenshot` branch before the `else`:

```javascript
function onStepActionChange(sel) {
  const row = sel.closest('.step-row'); const rm = row.querySelector('button');
  row.querySelectorAll('.step-value,.step-fill-field,.step-fill-value,span').forEach(function(el) { el.remove(); });
  if (sel.value === 'fill') {
    row.insertBefore(makeInput('search step-fill-field', 'text', 'CSS selector', 'flex:1; margin-bottom:0;'), rm);
    row.insertBefore(makeInput('search step-fill-value', 'text', 'value', 'flex:1; margin-bottom:0;'), rm);
  } else if (sel.value === 'screenshot') {
    const sp = document.createElement('span'); sp.style.flex = '2'; row.insertBefore(sp, rm);
  } else {
    row.insertBefore(makeInput('search step-value', 'text', 'value', 'flex:1; margin-bottom:0;'), rm);
    const sp = document.createElement('span'); sp.style.flex = '1'; row.insertBefore(sp, rm);
  }
  applyVarDatalist();
}
```

- [ ] **Step 3: Handle `screenshot` in `collectSimpleFormData`**

Find this block inside `collectSimpleFormData`:

```javascript
} else if (test.type === 'browser') {
  test.steps = [];
  block.querySelectorAll('.step-row').forEach(function(stepRow) {
    const action = stepRow.querySelector('.step-action').value;
    if (action === 'fill') {
      test.steps.push({ fill: { field: stepRow.querySelector('.step-fill-field').value.trim(),
                                value: stepRow.querySelector('.step-fill-value').value.trim() }});
    } else {
      const val = stepRow.querySelector('.step-value').value.trim();
      const step = {}; step[action] = val; test.steps.push(step);
    }
  });
}
```

Add a `screenshot` branch inside the `forEach`:

```javascript
    if (action === 'fill') {
      test.steps.push({ fill: { field: stepRow.querySelector('.step-fill-field').value.trim(),
                                value: stepRow.querySelector('.step-fill-value').value.trim() }});
    } else if (action === 'screenshot') {
      test.steps.push({ screenshot: null });
    } else {
      const val = stepRow.querySelector('.step-value') ? stepRow.querySelector('.step-value').value.trim() : '';
      const step = {}; step[action] = val; test.steps.push(step);
    }
```

- [ ] **Step 4: Run full suite**

```
python -m pytest tests/ -q
```

Expected: all passing.

- [ ] **Step 5: Commit**

```bash
git add web/templates/app_form.html
git commit -m "feat: add screenshot step to browser test form"
```

---

## Task 5: Add `active_run_id` to `get_app_summary`

**Files:**
- Modify: `harness/db.py` (`get_app_summary` method, around line 160)
- Modify: `tests/test_models_and_db.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_models_and_db.py`:

```python
def test_get_app_summary_has_active_run_id_when_run_is_running(tmp_path):
    from harness.db import Database
    from harness.models import Run
    db = Database(str(tmp_path / "h.db"))
    db.init_schema()
    run = Run(app="myapp", environment="prod", triggered_by="test")
    db.insert_run(run)
    db.update_run_status(run.id, "running", started_at="2026-01-01T00:00:00")

    # Seed app_state so myapp appears in summary
    from harness.models import AppState
    db.upsert_app_state(AppState(app="myapp", environment="prod",
                                 test_name="t", state="passing",
                                 since="2026-01-01T00:00:00"))
    summary = db.get_app_summary("prod")
    row = next(r for r in summary if r["app"] == "myapp")
    assert row["active_run_id"] == run.id

def test_get_app_summary_active_run_id_is_none_when_complete(tmp_path):
    from harness.db import Database
    from harness.models import Run, AppState
    db = Database(str(tmp_path / "h.db"))
    db.init_schema()
    run = Run(app="myapp", environment="prod", triggered_by="test")
    db.insert_run(run)
    db.update_run_status(run.id, "complete",
                         started_at="2026-01-01T00:00:00",
                         finished_at="2026-01-01T00:01:00")
    db.upsert_app_state(AppState(app="myapp", environment="prod",
                                 test_name="t", state="passing",
                                 since="2026-01-01T00:00:00"))
    summary = db.get_app_summary("prod")
    row = next(r for r in summary if r["app"] == "myapp")
    assert row["active_run_id"] is None
```

- [ ] **Step 2: Run tests to confirm they fail**

```
python -m pytest tests/test_models_and_db.py::test_get_app_summary_has_active_run_id_when_run_is_running tests/test_models_and_db.py::test_get_app_summary_active_run_id_is_none_when_complete -v
```

Expected: FAIL — `KeyError: 'active_run_id'`

- [ ] **Step 3: Update `get_app_summary` in `harness/db.py`**

Find `get_app_summary` (around line 160). After the `last_runs` merge block (the one that sets `apps[row["app"]]["last_run"]`), add:

```python
        with self._connect() as conn:
            active_rows = conn.execute(
                "SELECT app, id FROM runs "
                "WHERE environment=? AND status IN ('pending','running')",
                (environment,)
            ).fetchall()
        active_map = {row["app"]: row["id"] for row in active_rows}
        for app_dict in apps.values():
            app_dict["active_run_id"] = active_map.get(app_dict["app"])
```

- [ ] **Step 4: Run tests to confirm they pass**

```
python -m pytest tests/test_models_and_db.py -v
```

Expected: all 5 tests passing.

- [ ] **Step 5: Run full suite**

```
python -m pytest tests/ -q
```

Expected: all passing.

- [ ] **Step 6: Commit**

```bash
git add harness/db.py tests/test_models_and_db.py
git commit -m "feat: add active_run_id to get_app_summary"
```

---

## Task 6: Dashboard live polling

**Files:**
- Modify: `web/templates/dashboard.html`

- [ ] **Step 1: Add `data-run-id` to each table row**

In `dashboard.html`, find the `<tr>` element inside the `{% for row in summary %}` loop:

```html
<tr class="{% if row.failing > 0 %}row-fail{% endif %}" data-app="{{ row.app }}">
```

Replace with:

```html
<tr class="{% if row.failing > 0 %}row-fail{% endif %}"
    data-app="{{ row.app }}"
    data-run-id="{{ row.active_run_id or '' }}">
```

- [ ] **Step 2: Replace the JS block in `dashboard.html`**

The current `<script>` block uses `setTimeout(() => location.reload(), 3000)` in both `triggerRun` and `runAll`. Replace the entire `<script>` block with:

```html
<script>
const ENV = {{ environment | tojson }};

const activeRunIds = new Set();
const runAppMap = {};  // run_id → app name

// Seed from server-rendered data (runs already active on page load)
document.querySelectorAll('#app-table tbody tr[data-run-id]').forEach(function(row) {
  var runId = row.dataset.runId;
  if (runId) {
    activeRunIds.add(runId);
    runAppMap[runId] = row.dataset.app;
    setRowRunning(row.dataset.app);
  }
});
if (activeRunIds.size > 0) startPolling();

async function triggerRun(app) {
  var btn = event.target;
  btn.textContent = "Running…";
  btn.disabled = true;
  var resp = await fetch("/api/runs", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({app: app, environment: ENV, triggered_by: "ui"})
  });
  if (!resp.ok) {
    var err = await resp.json();
    alert(err.detail || "Failed to start run");
    btn.textContent = "Run";
    btn.disabled = false;
    return;
  }
  var data = await resp.json();
  data.run_ids.forEach(function(id) {
    activeRunIds.add(id);
    runAppMap[id] = app;
  });
  setRowRunning(app);
  startPolling();
}

async function runAll() {
  var resp = await fetch("/api/runs", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({environment: ENV, triggered_by: "ui"})
  });
  if (!resp.ok) return;
  var data = await resp.json();
  data.run_ids.forEach(function(id, i) {
    activeRunIds.add(id);
    runAppMap[id] = data.apps[i];
    setRowRunning(data.apps[i]);
  });
  startPolling();
}

function setRowRunning(appName) {
  var row = document.querySelector('tr[data-app="' + appName + '"]');
  if (!row) return;
  var badge = row.querySelector('.badge');
  if (badge) { badge.className = 'badge badge-unknown'; badge.textContent = '⟳ Running'; }
  var btn = row.querySelector('button');
  if (btn) { btn.textContent = 'Running…'; btn.disabled = true; }
}

var polling = false;
function startPolling() {
  if (polling) return;
  polling = true;
  pollLoop();
}

async function pollLoop() {
  while (activeRunIds.size > 0) {
    await new Promise(function(r) { setTimeout(r, 2000); });
    var toRemove = [];
    for (var runId of activeRunIds) {
      var resp = await fetch('/api/runs/' + runId);
      if (!resp.ok) { toRemove.push(runId); continue; }
      var data = await resp.json();
      updateRow(runAppMap[runId], data);
      if (data.status === 'complete') toRemove.push(runId);
    }
    toRemove.forEach(function(id) { activeRunIds.delete(id); });
  }
  polling = false;
  location.reload();
}

function updateRow(appName, runData) {
  var row = document.querySelector('tr[data-app="' + appName + '"]');
  if (!row) return;
  var results = runData.results || [];
  var passing = results.filter(function(r) { return r.status === 'pass'; }).length;
  var total = results.length;
  var hasFail = results.some(function(r) { return r.status === 'fail' || r.status === 'error'; });
  var badge = row.querySelector('.badge');
  if (badge) {
    if (runData.status === 'complete') {
      if (hasFail) { badge.className = 'badge badge-fail'; badge.textContent = '✗ Fail'; }
      else { badge.className = 'badge badge-pass'; badge.textContent = '✓ Pass'; }
    }
  }
  if (row.cells[2]) row.cells[2].textContent = passing + '/' + total;
  if (row.cells[3] && runData.started_at) {
    row.cells[3].textContent = runData.started_at.slice(0, 16).replace('T', ' ');
  }
}

function filterTable(query) {
  document.querySelectorAll("#app-table tbody tr[data-app]").forEach(function(row) {
    row.style.display = row.dataset.app.toLowerCase().includes(query.toLowerCase()) ? "" : "none";
  });
}
</script>
```

- [ ] **Step 3: Run full suite**

```
python -m pytest tests/ -q
```

Expected: all passing.

- [ ] **Step 4: Manual smoke test**

Start the server: `python -m web.main`

Open http://localhost:8000. Click "Run" on an app. Confirm:
- Button shows "Running…" immediately (no 3-second delay)
- Badge updates to "⟳ Running"
- After tests finish, badge updates to Pass/Fail and last-run time updates
- Page reloads cleanly at the end

- [ ] **Step 5: Commit**

```bash
git add web/templates/dashboard.html
git commit -m "feat: dashboard live polling — badges and test counts update as runs complete"
```

---

## Task 7: Detail page — run history strip, progress bar, pending cards, live poll

**Files:**
- Modify: `web/routes/dashboard.py` (`app_detail` route)
- Modify: `web/templates/detail.html`
- Modify: `tests/test_web_apps.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_web_apps.py`:

```python
def test_detail_page_shows_run_history_strip(tmp_path, monkeypatch):
    """GET /app/{app}/{env} shows a list of recent runs as a strip."""
    import web.main as main_mod
    from harness.db import Database
    from harness.models import Run
    from fastapi.testclient import TestClient

    db = Database(str(tmp_path / "h.db"))
    db.init_schema()
    # Two completed runs
    for i in range(2):
        run = Run(app="myapp", environment="prod", triggered_by="test")
        db.insert_run(run)
        db.update_run_status(run.id, "complete",
                             started_at=f"2026-01-0{i+1}T00:00:00",
                             finished_at=f"2026-01-0{i+1}T00:01:00")

    monkeypatch.setattr(main_mod, "_db", db)
    monkeypatch.setattr(main_mod, "_apps", [{"app": "myapp", "url": "https://x.com", "tests": []}])
    monkeypatch.setattr(main_mod, "_apps_dir", str(tmp_path / "apps"))
    client = TestClient(main_mod.app)
    resp = client.get("/app/myapp/prod")
    assert resp.status_code == 200
    assert b"run-history-strip" in resp.content


def test_detail_page_shows_pending_cards_when_run_is_active(tmp_path, monkeypatch):
    """Detail page shows pending test placeholders while a run is in progress."""
    import web.main as main_mod
    from harness.db import Database
    from harness.models import Run
    from fastapi.testclient import TestClient

    db = Database(str(tmp_path / "h.db"))
    db.init_schema()
    run = Run(app="myapp", environment="prod", triggered_by="test")
    db.insert_run(run)
    db.update_run_status(run.id, "running", started_at="2026-01-01T00:00:00")

    monkeypatch.setattr(main_mod, "_db", db)
    monkeypatch.setattr(main_mod, "_apps", [
        {"app": "myapp", "url": "https://x.com",
         "tests": [{"name": "health", "type": "availability"},
                   {"name": "login", "type": "browser", "steps": []}]}
    ])
    monkeypatch.setattr(main_mod, "_apps_dir", str(tmp_path / "apps"))
    client = TestClient(main_mod.app)
    resp = client.get("/app/myapp/prod")
    assert resp.status_code == 200
    assert b"Pending" in resp.content
    assert b"progress-bar" in resp.content
```

- [ ] **Step 2: Run tests to confirm they fail**

```
python -m pytest tests/test_web_apps.py::test_detail_page_shows_run_history_strip tests/test_web_apps.py::test_detail_page_shows_pending_cards_when_run_is_active -v
```

Expected: FAIL — `AssertionError: b"run-history-strip" not in resp.content`

- [ ] **Step 3: Update `app_detail` route in `web/routes/dashboard.py`**

Current return statement:
```python
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

Replace the entire `app_detail` function with:

```python
@router.get("/app/{app}/{environment}", response_class=HTMLResponse)
async def app_detail(request: Request, app: str, environment: str, run_id: str = None):
    from web.main import get_db, get_config, get_apps
    db = get_db()
    config = get_config()

    runs = db.get_recent_runs(app, environment)

    selected_run = None
    test_results = []
    history = {}

    if runs:
        selected_run = next((r for r in runs if r["id"] == run_id), runs[0])
        test_results = db.get_results_for_run(selected_run["id"])
        for tr in test_results:
            tr["step_log"] = json.loads(tr["step_log"] or "[]")
        test_names = [tr["test_name"] for tr in test_results]
        history = db.get_run_history_batch(app, environment, test_names)

    is_live = bool(selected_run and selected_run["status"] in ("pending", "running"))
    pending_test_names = []
    if is_live:
        completed_names = {tr["test_name"] for tr in test_results}
        app_def = next((a for a in get_apps() if a["app"] == app), None)
        if app_def:
            pending_test_names = [
                t["name"] for t in app_def.get("tests", [])
                if t["name"] not in completed_names
            ]

    return templates.TemplateResponse("detail.html", {
        "request": request,
        "app": app,
        "environment": environment,
        "environments": config.get("environments", {}),
        "runs": runs,
        "selected_run": selected_run,
        "test_results": test_results,
        "history": history,
        "is_live": is_live,
        "pending_test_names": pending_test_names,
    })
```

- [ ] **Step 4: Update `detail.html`**

Replace the entire file content with:

```html
{% extends "base.html" %}
{% block content %}
<div class="breadcrumb">
  <a href="/?environment={{ environment }}">← Dashboard</a> / {{ app }}
</div>

{% if selected_run %}

{# Run history strip #}
{% if runs %}
<div id="run-history-strip" style="display:flex; gap:.4rem; flex-wrap:wrap; margin-bottom:1rem;">
  {% for run in runs[:5] %}
  <a href="/app/{{ app }}/{{ environment }}?run_id={{ run.id }}"
     style="padding:.2rem .55rem; border-radius:4px; font-size:.75rem; text-decoration:none;
            border:1px solid {{ '#3b82f6' if run.id == selected_run.id else '#2d3748' }};
            color:{{ '#e2e8f0' if run.id == selected_run.id else '#718096' }};">
    {% if run.status == 'complete' %}✓
    {% elif run.status in ('pending', 'running') %}⟳
    {% else %}✗{% endif %}
    {{ (run.started_at or '')[:10] }}
  </a>
  {% endfor %}
</div>
{% endif %}

<div class="run-meta">
  <span>Run: {{ selected_run.id[:8] }}</span>
  <span>Status: <strong>{{ selected_run.status }}</strong></span>
  <span>Started: {{ selected_run.started_at or "—" }}</span>
  <button class="btn btn-sm" onclick="triggerRun()">Re-run All</button>
</div>

{# Progress bar — only shown while run is active #}
{% if is_live %}
{% set total = test_results|length + pending_test_names|length %}
{% set done = test_results|length %}
<div id="progress-bar" style="margin-bottom:1rem;">
  <div style="display:flex; align-items:center; gap:1rem; margin-bottom:.4rem;">
    <span id="progress-text" style="font-size:.875rem; color:#a0aec0;">
      {{ done }} / {{ total }} tests complete
    </span>
    <span id="elapsed-timer" style="font-size:.8rem; color:#718096;">00:00</span>
  </div>
  <div style="height:4px; background:#2d3748; border-radius:2px;">
    <div id="progress-fill"
         style="height:4px; background:#3b82f6; border-radius:2px; transition:width .3s;
                width:{{ ((done / total * 100) | int) if total > 0 else 0 }}%;"></div>
  </div>
</div>
{% endif %}

{# Completed test cards #}
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
      {% if step.screenshot %}
      <div style="margin-top:.4rem;">
        <img src="/screenshots/{{ step.screenshot }}" class="screenshot"
             style="max-width:320px; border-radius:4px; display:block;">
      </div>
      {% endif %}
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

{# Pending test placeholders #}
{% for name in pending_test_names %}
<div class="test-card" style="opacity:.45;">
  <div class="test-card-header">
    <span class="test-name">{{ name }}</span>
    <span class="test-status" style="color:#718096;">⏳ Pending</span>
  </div>
</div>
{% endfor %}

{% else %}
<p>No runs yet for {{ app }} ({{ environment }}). <button class="btn" onclick="triggerRun()">Run now</button></p>
{% endif %}

<script>
const APP = {{ app | tojson }};
const ENV = {{ environment | tojson }};

async function triggerRun() {
  await fetch("/api/runs", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({app: APP, environment: ENV, triggered_by: "ui"})
  });
  location.reload();
}

{% if is_live %}
const RUN_ID = {{ selected_run.id | tojson }};
const TOTAL_TESTS = {{ test_results|length + pending_test_names|length }};

var elapsedSeconds = 0;
var timer = setInterval(function() {
  elapsedSeconds++;
  var m = String(Math.floor(elapsedSeconds / 60)).padStart(2, '0');
  var s = String(elapsedSeconds % 60).padStart(2, '0');
  var el = document.getElementById('elapsed-timer');
  if (el) el.textContent = m + ':' + s;
}, 1000);

(async function pollRun() {
  while (true) {
    await new Promise(function(r) { setTimeout(r, 2000); });
    var resp = await fetch('/api/runs/' + RUN_ID);
    if (!resp.ok) break;
    var data = await resp.json();
    var done = data.results ? data.results.length : 0;
    var pct = TOTAL_TESTS > 0 ? Math.round(done / TOTAL_TESTS * 100) : 0;
    var fill = document.getElementById('progress-fill');
    var text = document.getElementById('progress-text');
    if (fill) fill.style.width = pct + '%';
    if (text) text.textContent = done + ' / ' + TOTAL_TESTS + ' tests complete';
    if (data.status === 'complete') {
      clearInterval(timer);
      location.reload();
      return;
    }
  }
})();
{% endif %}
</script>
{% endblock %}
```

- [ ] **Step 5: Run tests to confirm they pass**

```
python -m pytest tests/test_web_apps.py::test_detail_page_shows_run_history_strip tests/test_web_apps.py::test_detail_page_shows_pending_cards_when_run_is_active -v
```

Expected: 2 passing.

- [ ] **Step 6: Run full suite**

```
python -m pytest tests/ -q
```

Expected: all passing.

- [ ] **Step 7: Manual smoke test**

Start server: `python -m web.main`

1. Open http://localhost:8000/app/sonarr/production — verify the run history strip shows recent runs as clickable pills, clicking one loads that run.
2. Trigger a run from the dashboard. Click into the app detail page while it's running. Confirm:
   - Progress bar shows "0 / N tests complete" with ticking elapsed timer
   - Pending test cards appear in grey
   - After each test completes, the counter updates
   - Page reloads cleanly when the run finishes with all cards showing

- [ ] **Step 8: Commit**

```bash
git add web/routes/dashboard.py web/templates/detail.html tests/test_web_apps.py
git commit -m "feat: detail page run history strip, progress bar, pending cards, live poll"
```

---

## Self-Review

**Spec coverage:**

| Spec requirement | Task |
|-----------------|------|
| `StepResult.screenshot` field | Task 1 |
| Step-log JSON includes screenshot | Task 1 |
| `execute_step` screenshot step handler | Task 2 |
| `run_browser_test` per-step paths, slug moved | Task 2 |
| Step screenshot thumbnails in detail.html | Task 3 |
| `screenshot` in form step dropdown, no value input | Task 4 |
| `collectSimpleFormData` handles screenshot | Task 4 |
| `active_run_id` in `get_app_summary` | Task 5 |
| Dashboard `data-run-id`, live polling JS | Task 6 |
| Detail page run history strip | Task 7 |
| Detail page progress bar + elapsed timer | Task 7 |
| Detail page pending test cards | Task 7 |
| Detail page live poll JS | Task 7 |
| `app_detail` route passes `is_live`, `pending_test_names` | Task 7 |

**No gaps found.**

**Type consistency check:** `StepResult.screenshot` defined in Task 1, referenced by Task 2 (set in execute_step), Task 3 (rendered in template). `active_run_id` defined in Task 5, consumed in Task 6 via `data-run-id` attribute. Consistent throughout.

**Placeholder scan:** No TBDs, no "implement later", no vague steps. All code blocks are complete.
