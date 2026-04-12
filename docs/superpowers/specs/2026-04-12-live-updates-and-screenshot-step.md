# Live Run Updates & Screenshot Step — Design Spec

**Date:** 2026-04-12
**Status:** Approved

---

## Goal

Two independent improvements to the test harness UI:

1. **Live updates** — the dashboard and detail page auto-refresh as runs execute, so the user never has to manually reload.
2. **Screenshot step** — browser tests can explicitly capture a screenshot at any point (not just on failure), stored and displayed inline in the step log.

---

## Feature 1: Live Run Updates

### Strategy

Polling, not SSE/WebSockets. The existing `GET /api/runs/{run_id}` endpoint already returns run status and results. Both pages poll it every 2 seconds while a run is active and reload once when the run reaches `complete`. This keeps the server-rendered HTML as the authoritative view — no client-side templating.

### 1a. DB change — `active_run_id` in app summary

`db.get_app_summary(environment)` currently omits active run state. Add a third query to the method that finds any `pending` or `running` run per app, and merge the result into each summary row:

```python
# New field in each summary dict:
"active_run_id": str | None   # None when no run is in progress
```

SQL to add inside `get_app_summary`:

```sql
SELECT app, id FROM runs
WHERE environment = ? AND status IN ('pending', 'running')
GROUP BY app
```

(One active run per app is enforced by the existing `is_run_active` guard on the POST endpoint.)

### 1b. Dashboard — live row updates

**Server side** (`dashboard.html`): add `data-run-id="{{ row.active_run_id or '' }}"` to each `<tr>`. This seeds the JS with which runs are already active on page load.

**Client side** — two entry points:

1. **On trigger** (`triggerRun` / `runAll`): after the POST returns `run_ids`, store them in a JS set (`activeRunIds`) and start the poll loop.
2. **On page load**: scan all rows for `data-run-id` attributes; seed `activeRunIds` from any non-empty values and start polling if any exist.

**Poll loop** (2-second interval):

```
while activeRunIds is not empty:
  for each run_id in activeRunIds:
    GET /api/runs/{run_id}
    if response.status == 'complete':
      remove from activeRunIds
      update that app's row: badge, test count, last_run
  if activeRunIds empty: location.reload()   ← final reload for clean state
  else: sleep 2s
```

Row updates (in-place, no full reload while running):
- **Badge**: update class and text based on results in the polling response
- **Tests column**: `passing_count / total_count` derived from results
- **Last Run**: update to formatted `started_at`
- **Run button**: disable / show "Running…" while active; re-enable on complete

The final `location.reload()` when all runs finish gives a clean server-rendered state.

### 1c. Detail page — progress + auto-reload

**Server side** (`dashboard.py` `app_detail` route): when `selected_run.status in ('pending', 'running')`:
- Look up the app definition via `get_apps()` to get the complete test list
- Compute `pending_test_names`: test names in the app def that have no completed result in `test_results`
- Pass `pending_test_names` and `is_live: True` to the template

**Template additions** (`detail.html`):

1. **Progress bar** (shown only when `is_live`):
   ```
   ██████░░░░ 3 / 5 tests complete  •  elapsed: 00:12
   ```
   The elapsed counter ticks via `setInterval` in JS.

2. **Pending test cards**: for each name in `pending_test_names`, render a grey placeholder card:
   ```html
   <div class="test-card test-card--pending">
     <div class="test-card-header">
       <span class="test-name">{{ name }}</span>
       <span class="test-status" style="color:#718096;">⏳ Pending</span>
     </div>
   </div>
   ```

3. **Run history panel**: the `runs` list is already passed to the template but unused. Render it as a compact strip above the test cards:
   ```
   [run 1: ✓ Complete  12s  Apr 12 14:03]  [run 2: ✗ Fail  8s  Apr 12 13:47]  …
   ```
   Each entry is a link to `?run_id=xxx`. The selected run is highlighted. Shows last 5 runs.

**Poll loop** (JS, only when `is_live`):

```
every 2s:
  GET /api/runs/{{ selected_run.id }}
  update progress counter with results.length
  if response.status == 'complete': location.reload()
```

No in-place DOM card updates — always a clean server reload at completion.

### 1d. `GET /api/runs/{run_id}` — no change needed

The existing endpoint already returns `{status, started_at, finished_at, results: [...]}`. The dashboard uses this for badge updates; the detail page uses `status` only (checking for `complete`).

---

## Feature 2: Screenshot Step

### 2a. `StepResult` model

Add one field to `harness/models.py`:

```python
@dataclass
class StepResult:
    step: str
    status: str
    duration_ms: int
    error: Optional[str] = None
    screenshot: Optional[str] = None   # ← new: relative path, same root as TestResult.screenshot
```

### 2b. `execute_step` in `harness/browser.py`

Add a `screenshot_path: Optional[str] = None` parameter. When the step is `{"screenshot": ...}` (any value, or `null` — the value is unused), save the page screenshot to `screenshot_path` and return a `StepResult` with the relative path stored in `screenshot`.

```python
async def execute_step(page: Page, step: dict, base_url: str,
                       screenshot_path: Optional[str] = None) -> StepResult:
    ...
    if "screenshot" in step:
        if not screenshot_path:
            return StepResult(step="screenshot", status="error", duration_ms=_elapsed(),
                              error="No screenshot path provided")
        os.makedirs(os.path.dirname(screenshot_path), exist_ok=True)
        await page.screenshot(path=screenshot_path)
        # Return the relative path from the screenshots root for URL construction
        rel = os.path.relpath(screenshot_path, "data/screenshots")
        return StepResult(step="screenshot", status="pass", duration_ms=_elapsed(),
                          screenshot=rel.replace("\\", "/"))
    ...
```

`run_browser_test` computes `slug` once before the loop (moving it out of the failure handler where it currently lives), then passes a unique path per step:

```python
slug = slugify_test_name(test_def["name"])   # ← move before loop
for i, step in enumerate(steps):
    step_shot = os.path.join(screenshot_dir, app, environment, run_id,
                             f"{slug}-step-{i}.png") if "screenshot" in step else None
    step_result = await execute_step(page, step, base_url, screenshot_path=step_shot)
```

The existing failure screenshot path construction (`f"{slug}.png"`) already uses `slug`, so moving the variable up is a safe refactor.

The failure screenshot path (already computed when a step fails) is unaffected.

### 2c. DB serialization

`db.insert_test_result` already serializes `step_log` as JSON. Add `"screenshot": s.screenshot` to the dict:

```python
step_log_json = json.dumps([
    {"step": s.step, "status": s.status, "duration_ms": s.duration_ms,
     "error": s.error, "screenshot": s.screenshot}
    for s in tr.step_log
])
```

### 2d. Detail page — step screenshot thumbnails

In `detail.html`, step rendering currently shows step name, status, duration, and error. When `step.screenshot` is present, add an inline thumbnail:

```html
{% if step.screenshot %}
<div class="step-screenshot">
  <img src="/screenshots/{{ step.screenshot }}" class="screenshot" style="max-width:300px; margin-top:.5rem;">
</div>
{% endif %}
```

### 2e. App form — screenshot step option

In `app_form.html`, the step action dropdown lists `navigate`, `fill`, `click`, `assert_text`, etc. Add `screenshot` to this list.

When `screenshot` is selected, hide the value input (no value needed). Add handling to `onStepActionChange`:

```javascript
if (sel.value === 'screenshot') {
    // No inputs needed — just the remove button
    const sp = document.createElement('span'); sp.style.flex = '1'; row.insertBefore(sp, rm);
    const sp2 = document.createElement('span'); sp2.style.flex = '1'; row.insertBefore(sp2, rm);
} else if (sel.value === 'fill') { ...
```

`collectSimpleFormData` already handles this correctly: `test.steps.push({ [action]: val })` where `val` is `""` — `{screenshot: ""}` is a valid step that the runner ignores the value of.

---

## File Map

| File | Change |
|------|--------|
| `harness/models.py` | Add `screenshot: Optional[str] = None` to `StepResult` |
| `harness/browser.py` | Add `screenshot_path` param to `execute_step`; pass per-step path from `run_browser_test`; handle `screenshot` step |
| `harness/db.py` | Add `active_run_id` to `get_app_summary`; add `screenshot` to step_log JSON |
| `web/routes/dashboard.py` | Pass `pending_test_names` and `is_live` when run is active |
| `web/templates/dashboard.html` | `data-run-id` on rows; live update JS loop |
| `web/templates/detail.html` | Run history strip; pending cards; progress bar + timer; live poll JS; step screenshot thumbnails |
| `web/templates/app_form.html` | Add `screenshot` to step action dropdown; hide value input for it |

---

## Polling Behaviour Summary

| Page | Trigger | Interval | Stop condition |
|------|---------|----------|---------------|
| Dashboard | Page load (active run detected) OR after triggering | 2s | All run IDs complete → `location.reload()` |
| Detail | Page load when `is_live == True` | 2s | `status == 'complete'` → `location.reload()` |

---

## What This Is Not

- No SSE, no WebSocket
- No in-place DOM card rendering (always server-reload at completion)
- No visual diffing for `assert_screenshot`
- No run deletion or cleanup UI

---

## Testing

**`tests/test_db.py`** (new or update existing):
- `get_app_summary` includes `active_run_id` when a run is pending/running
- `get_app_summary` returns `active_run_id: None` when no run is active

**`tests/test_browser.py`** (new):
- `execute_step` with `screenshot` step saves file and returns `StepResult` with `screenshot` set
- `execute_step` with `screenshot` step and no `screenshot_path` returns `error` status

**`tests/test_web_detail.py`** (new or add to `test_web_apps.py`):
- `GET /app/{app}/{env}` with active run returns 200 and includes pending test card content
- `GET /app/{app}/{env}` with complete run returns 200 and does not include pending indicators
