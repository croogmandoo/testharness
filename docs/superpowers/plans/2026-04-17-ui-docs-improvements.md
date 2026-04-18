# UI & Docs Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a light/dark mode toggle, reorganize documentation into a structured folder hierarchy, add an API quickstart guide, serve `graphify-out/` as static files, and update `.gitignore`.

**Architecture:** Light mode is pure CSS (custom property overrides under `[data-theme="light"]`) with a localStorage toggle — zero backend changes. Docs are file moves and renames. The graph visualization is served via a new FastAPI `StaticFiles` mount. All five tasks are independent and can be committed separately.

**Tech Stack:** CSS custom properties, vanilla JS (`localStorage`), FastAPI `StaticFiles`, Markdown.

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `web/static/style.css` | Add `[data-theme="light"]` variable overrides, hide scanline in light mode |
| Modify | `web/templates/base.html` | Inline anti-flash script in `<head>`, add toggle button in nav |
| Modify | `web/main.py` | Mount `graphify-out/` as StaticFiles at `/graphify-out` |
| Modify | `.gitignore` | Add `graphify-out/cache/` |
| Move | `docs/writing-tests.md` → `docs/guides/writing-tests.md` | |
| Move | `docs/troubleshooting-stuck-runs.md` → `docs/guides/troubleshooting.md` | |
| Move | `docs/api-reference.md` → `docs/reference/api.md` | |
| Move | `docs/auth-architecture.md` → `docs/reference/auth.md` | |
| Move | `docs/approach-recommendations.md` → `docs/architecture/decisions.md` | |
| Create | `docs/guides/api-quickstart.md` | Step-by-step API key + curl examples |
| Create | `docs/architecture/decisions.md` | (rename target — content unchanged) |

---

### Task 1: `.gitignore` additions

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Add entries**

Open `.gitignore` and append:

```
graphify-out/cache/
```

(`.superpowers/` is already present — confirmed from current file.)

- [ ] **Step 2: Verify**

```bash
cat .gitignore
```

Confirm `graphify-out/cache/` appears and `.superpowers/` is already there.

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore: add graphify cache to gitignore"
```

---

### Task 2: Serve `graphify-out/` as static files

**Files:**
- Modify: `web/main.py` (lines 134–138)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_web_apps.py` (or any existing web test file that has a `client` fixture):

```python
def test_graphify_out_route_exists(client):
    """graphify-out static mount responds — 404 for missing file, not 500."""
    r = client.get("/graphify-out/nonexistent.html", follow_redirects=False)
    assert r.status_code == 404
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
pytest tests/test_web_apps.py::test_graphify_out_route_exists -v
```

Expected: FAIL — `/graphify-out/nonexistent.html` returns 404 from the app router (route doesn't exist yet, so likely 404 from FastAPI itself — but confirm it's not a 500).

Actually the test may pass trivially if FastAPI returns 404 for unknown routes. The real check is that the mount exists. Change the assertion:

```python
def test_graphify_out_mount_serves_files(tmp_path, monkeypatch):
    """graphify-out StaticFiles mount is registered when the directory exists."""
    import os
    from web.main import create_app
    from harness.db import Database

    # Create a real graphify-out dir with a test file
    gout = tmp_path / "graphify-out"
    gout.mkdir()
    (gout / "test.txt").write_text("hello")

    monkeypatch.chdir(tmp_path)
    db = Database(str(tmp_path / "test.db"))
    db.init_schema()
    app = create_app(db=db, config={})

    from fastapi.testclient import TestClient
    c = TestClient(app)
    r = c.get("/graphify-out/test.txt")
    assert r.status_code == 200
    assert r.text == "hello"
```

- [ ] **Step 3: Run test to confirm it fails**

```bash
pytest tests/test_web_apps.py::test_graphify_out_mount_serves_files -v
```

Expected: FAIL — 404 (mount not registered yet).

- [ ] **Step 4: Add the mount to `web/main.py`**

After line 138 (`app.mount("/static", ...)`), add:

```python
    graphify_dir = "graphify-out"
    if os.path.isdir(graphify_dir):
        app.mount("/graphify-out", StaticFiles(directory=graphify_dir), name="graphify-out")
```

- [ ] **Step 5: Run test to confirm it passes**

```bash
pytest tests/test_web_apps.py::test_graphify_out_mount_serves_files -v
```

Expected: PASS.

- [ ] **Step 6: Run full suite**

```bash
pytest -v
```

Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add web/main.py tests/test_web_apps.py
git commit -m "feat: serve graphify-out/ as static files at /graphify-out"
```

---

### Task 3: Light mode — CSS variables

**Files:**
- Modify: `web/static/style.css`

- [ ] **Step 1: Append the light theme block to `web/static/style.css`**

At the very end of the file, add:

```css
/* ─── LIGHT THEME ──────────────────────────────────── */
[data-theme="light"] {
  --bg:       #f5f5f4;
  --bg-1:     #ffffff;
  --bg-2:     #f0f0ef;
  --bg-3:     #e8e8e7;
  --bg-4:     #dcdcdb;
  --border:   #d4d4d3;
  --border-2: #c0c0bf;
  --text:     #1c1b1c;
  --text-2:   #5a5959;
  --text-3:   #909090;
  --amber:    #d11f44;
  --amber-hi: #b81a38;
  --amber-lo: #fde8ec;
  --pass:     #15803d;
  --pass-dim: #dcfce7;
  --fail:     #b91c1c;
  --fail-dim: #fee2e2;
  --run:      #1d4ed8;
  --run-dim:  #dbeafe;
}

[data-theme="light"] body::after {
  display: none;
}
```

- [ ] **Step 2: Manually verify**

Open any page in a browser. In DevTools console run:

```js
document.documentElement.setAttribute('data-theme', 'light')
```

Confirm the page switches to light colors with no flash or broken elements. Run again with `'dark'` to restore.

- [ ] **Step 3: Commit**

```bash
git add web/static/style.css
git commit -m "feat: add light theme CSS variables"
```

---

### Task 4: Light mode — nav toggle + anti-flash script

**Files:**
- Modify: `web/templates/base.html`

- [ ] **Step 1: Add anti-flash inline script to `<head>`**

In `web/templates/base.html`, insert immediately after `<meta name="viewport" ...>` and before `<title>`:

```html
  <script>
    (function(){
      var t = localStorage.getItem('theme');
      if (t) document.documentElement.setAttribute('data-theme', t);
    })();
  </script>
```

- [ ] **Step 2: Add toggle button to nav**

In `web/templates/base.html`, in the `.nav-right` div, insert the toggle button as the **first** child (before the env-switcher block):

```html
      <button id="theme-toggle" onclick="(function(){
        var cur = document.documentElement.getAttribute('data-theme') || 'dark';
        var next = cur === 'dark' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', next);
        localStorage.setItem('theme', next);
        document.getElementById('theme-toggle').textContent = next === 'dark' ? '☀' : '☾';
      })()" style="background:none;border:none;cursor:pointer;font-size:1rem;color:var(--text-2);padding:.25rem .4rem;line-height:1;" title="Toggle light/dark mode">☀</button>
```

Note: The button shows `☀` when in dark mode (click to switch to light) and `☾` when in light mode (click to switch to dark). On page load the icon is set by the inline script below.

- [ ] **Step 3: Update anti-flash script to also set button icon**

Replace the `<script>` added in Step 1 with:

```html
  <script>
    (function(){
      var t = localStorage.getItem('theme') || 'dark';
      document.documentElement.setAttribute('data-theme', t);
      document.addEventListener('DOMContentLoaded', function(){
        var btn = document.getElementById('theme-toggle');
        if (btn) btn.textContent = t === 'dark' ? '☀' : '☾';
      });
    })();
  </script>
```

- [ ] **Step 4: Write an integration test**

In `tests/test_web_apps.py`, append:

```python
def test_theme_toggle_button_in_nav(client):
    """Nav contains the theme toggle button."""
    from web.auth import get_current_user
    # Use an existing admin fixture or create inline
    import uuid
    from datetime import datetime, timezone
    from harness.db import Database
    from web.main import create_app, get_db
    db = get_db()
    r = client.get("/", follow_redirects=False)
    # Unauthenticated redirects to login — check login page has the toggle too
    # (base.html is shared, toggle always present)
    r2 = client.get("/auth/login")
    assert b'theme-toggle' in r2.content
```

- [ ] **Step 5: Run test**

```bash
pytest tests/test_web_apps.py::test_theme_toggle_button_in_nav -v
```

Expected: PASS.

- [ ] **Step 6: Run full suite**

```bash
pytest -v
```

Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add web/templates/base.html tests/test_web_apps.py
git commit -m "feat: add light/dark mode toggle to nav with localStorage persistence"
```

---

### Task 5: Docs reorganization

**Files:** All moves are `git mv` to preserve history.

- [ ] **Step 1: Create new directories**

```bash
mkdir -p docs/guides docs/reference docs/architecture
```

- [ ] **Step 2: Move files**

```bash
git mv docs/writing-tests.md docs/guides/writing-tests.md
git mv docs/troubleshooting-stuck-runs.md docs/guides/troubleshooting.md
git mv docs/api-reference.md docs/reference/api.md
git mv docs/auth-architecture.md docs/reference/auth.md
git mv docs/approach-recommendations.md docs/architecture/decisions.md
```

- [ ] **Step 3: Update internal cross-references**

Open each moved file and update any relative links between them:

- `docs/reference/api.md` — check for links to auth doc; update to `../reference/auth.md` if present.
- `docs/guides/writing-tests.md` — check for links to api-reference; update to `../reference/api.md`.
- `docs/architecture/decisions.md` — check for links; update paths as needed.

- [ ] **Step 4: Add knowledge graph reference to `docs/architecture/decisions.md`**

Insert at the top of the file, after the H1 heading (or as the first section if there is none):

```markdown
## Knowledge Graph

An interactive graph of this codebase is available at [`/graphify-out/graph.html`](/graphify-out/graph.html) when the app is running. Run `/graphify` in Claude Code to rebuild it after significant changes.
```

- [ ] **Step 5: Update README.md doc links**

Open `README.md`. Find any links to the moved files and update them to their new paths:

| Old path | New path |
|---|---|
| `docs/writing-tests.md` | `docs/guides/writing-tests.md` |
| `docs/troubleshooting-stuck-runs.md` | `docs/guides/troubleshooting.md` |
| `docs/api-reference.md` | `docs/reference/api.md` |
| `docs/auth-architecture.md` | `docs/reference/auth.md` |
| `docs/approach-recommendations.md` | `docs/architecture/decisions.md` |

- [ ] **Step 6: Commit**

```bash
git add docs/ README.md
git commit -m "docs: reorganize into guides/, reference/, architecture/ structure"
```

---

### Task 6: API quickstart guide

**Files:**
- Create: `docs/guides/api-quickstart.md`

- [ ] **Step 1: Create the guide**

Create `docs/guides/api-quickstart.md` with the following content:

````markdown
# API Quickstart

The Testing Harness exposes a REST API that accepts API key authentication — useful for CI pipelines, scripts, and external tooling.

## 1. Generate an API Key

1. Sign in to the harness web UI.
2. Click **API Keys** in the top navigation.
3. Enter a name (e.g. `CI pipeline`) and choose an expiry.
4. Click **Create Key**.
5. **Copy the key immediately** — it is shown only once. It starts with `hth_`.

## 2. Authenticate

Every request needs either:

```
Authorization: Bearer hth_your_key_here
```

or:

```
X-API-Key: hth_your_key_here
```

Both headers work on all `/api/*` endpoints.

## 3. List Apps

```bash
curl -s \
  -H "Authorization: Bearer hth_your_key_here" \
  http://localhost:9552/api/apps | jq .
```

Response:

```json
[
  {
    "name": "Sonarr",
    "environments": ["production", "staging"],
    "last_run_id": "abc123",
    "summary": { "pass": 2, "fail": 0, "error": 0 }
  }
]
```

## 4. Trigger a Test Run

```bash
curl -s -X POST \
  -H "Authorization: Bearer hth_your_key_here" \
  -H "Content-Type: application/json" \
  -d '{"app": "Sonarr", "environment": "production"}' \
  http://localhost:9552/api/runs | jq .
```

Response:

```json
{ "run_id": "abc123def456" }
```

## 5. Poll for Results

```bash
curl -s \
  -H "Authorization: Bearer hth_your_key_here" \
  http://localhost:9552/api/runs/abc123def456 | jq .
```

Response when complete:

```json
{
  "id": "abc123def456",
  "app": "Sonarr",
  "environment": "production",
  "status": "complete",
  "results": [
    { "test_name": "login-flow", "status": "pass", "duration_ms": 3201 },
    { "test_name": "homepage-loads", "status": "pass", "duration_ms": 812 }
  ]
}
```

Poll until `status` is `"complete"` or `"error"`. A run in progress returns `"running"`.

## 6. Revoking a Key

Go to **API Keys** in the UI and click **Revoke** next to the key. Revoked keys return `401` immediately.

## Full API Reference

See [`../reference/api.md`](../reference/api.md) for all endpoints, query parameters, and response schemas.
````

- [ ] **Step 2: Commit**

```bash
git add docs/guides/api-quickstart.md
git commit -m "docs: add API quickstart guide with key generation and curl examples"
```

---

## Self-Review

### Spec coverage

| Requirement | Task |
|---|---|
| Light mode toggle in nav | Tasks 3, 4 |
| localStorage persistence, no flash | Task 4 |
| Light palette (exact colors) | Task 3 |
| Scanline hidden in light mode | Task 3 |
| graphify-out/ served as static files | Task 2 |
| Conditional mount (no-dir safe) | Task 2 |
| graph.html linked from architecture docs | Task 5 step 4 |
| .gitignore: graphify-out/cache/ | Task 1 |
| .gitignore: .superpowers/ already present | Task 1 verified |
| Docs into guides/, reference/, architecture/ | Task 5 |
| API quickstart guide | Task 6 |
| Key generation steps | Task 6 |
| curl examples (list, trigger, poll) | Task 6 |
| Both auth header formats shown | Task 6 |
| Expiry / revocation covered | Task 6 |

### Placeholder scan

No TBDs, TODOs, or "similar to Task N" references. All code is complete.

### Type consistency

No shared types across tasks — each task is isolated. CSS variable names match exactly between Task 3 (defined) and Task 4 (toggled via `data-theme` attribute). `StaticFiles` import already present in `web/main.py`.
