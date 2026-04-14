# Secrets / Environment Variable Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow the app form to suggest known `$VAR` references as autocomplete options for value fields (fill step values, URLs), and add a `/secrets` page showing the env-var dependency map across all apps — without ever displaying actual secret values.

**Architecture:** A new `get_known_vars()` function in `harness/app_manager.py` scans all app YAML files for `$VAR` patterns and returns their names. A `GET /api/vars` endpoint exposes this list. The form fetches it on load and attaches a `<datalist>` to every value input that might hold a secret reference. The `/secrets` page shows which apps reference which vars and whether each var is present in the current OS environment (name-only — no values shown).

**Tech Stack:** Python 3, FastAPI, Jinja2 templates, vanilla JS, HTML `<datalist>` for autocomplete, `os.environ` for presence checks (not value reads)

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `harness/app_manager.py` | Modify | Add `get_known_vars()` |
| `web/routes/api.py` | Modify | Add `GET /api/vars` endpoint |
| `web/routes/apps.py` | Modify | Add `GET /secrets` HTML route |
| `web/templates/app_form.html` | Modify | Add `<datalist>` + JS fetch for var autocomplete |
| `web/templates/secrets.html` | Create | Env var dependency table |
| `tests/test_app_manager.py` | Modify | Tests for `get_known_vars()` |
| `tests/test_web_apps.py` | Modify | Tests for `GET /api/vars` and `GET /secrets` |

---

## Task 1: `get_known_vars()` in app_manager.py

**Files:**
- Modify: `harness/app_manager.py`
- Modify: `tests/test_app_manager.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_app_manager.py`:

```python
def test_get_known_vars_returns_var_names(tmp_path):
    """Scans YAML files and returns sorted unique $VAR names."""
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    (apps_dir / "myapp.yaml").write_text(
        "app: myapp\nurl: https://example.com\ntests:\n"
        "  - name: login\n    type: browser\n    steps:\n"
        "      - fill:\n          field: '#user'\n          value: $MY_USERNAME\n"
        "      - fill:\n          field: '#pass'\n          value: $MY_PASSWORD\n"
    )
    from harness.app_manager import get_known_vars
    result = get_known_vars(apps_dir=str(apps_dir))
    assert result == ["$MY_PASSWORD", "$MY_USERNAME"]


def test_get_known_vars_deduplicates(tmp_path):
    """Same var used in multiple files appears once."""
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    (apps_dir / "a.yaml").write_text("url: $SHARED_URL\n")
    (apps_dir / "b.yaml").write_text("url: $SHARED_URL\nother: $OTHER_VAR\n")
    from harness.app_manager import get_known_vars
    result = get_known_vars(apps_dir=str(apps_dir))
    assert result == ["$OTHER_VAR", "$SHARED_URL"]


def test_get_known_vars_includes_archived(tmp_path):
    """Also scans apps/archived/ so users can reuse vars from archived apps."""
    apps_dir = tmp_path / "apps"
    (apps_dir / "archived").mkdir(parents=True)
    (apps_dir / "archived" / "old.yaml").write_text("url: $ARCHIVED_SECRET\n")
    from harness.app_manager import get_known_vars
    result = get_known_vars(apps_dir=str(apps_dir))
    assert "$ARCHIVED_SECRET" in result


def test_get_known_vars_empty_dir(tmp_path):
    """Returns empty list when no YAML files exist."""
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    from harness.app_manager import get_known_vars
    result = get_known_vars(apps_dir=str(apps_dir))
    assert result == []


def test_get_known_vars_missing_dir(tmp_path):
    """Returns empty list when apps_dir does not exist."""
    from harness.app_manager import get_known_vars
    result = get_known_vars(apps_dir=str(tmp_path / "nonexistent"))
    assert result == []
```

- [ ] **Step 2: Run tests to confirm they fail**

```
python -m pytest tests/test_app_manager.py::test_get_known_vars_returns_var_names tests/test_app_manager.py::test_get_known_vars_deduplicates tests/test_app_manager.py::test_get_known_vars_includes_archived tests/test_app_manager.py::test_get_known_vars_empty_dir tests/test_app_manager.py::test_get_known_vars_missing_dir -v
```

Expected: 5 failures — `ImportError: cannot import name 'get_known_vars'`

- [ ] **Step 3: Implement `get_known_vars()` in `harness/app_manager.py`**

Add after the `list_archived` function (end of file):

```python
def get_known_vars(apps_dir: str = "apps") -> list:
    """Scan all YAML files (including archived/) for $VAR references.

    Returns a sorted list of unique variable names like ['$MY_PASSWORD', '$MY_TOKEN'].
    Never reads actual env var values — only discovers names used in YAML files.
    """
    apps_path = Path(apps_dir)
    if not apps_path.is_dir():
        return []
    pattern = re.compile(r'\$([A-Z_][A-Z0-9_]*)')
    found = set()
    for yaml_file in apps_path.rglob("*.yaml"):
        content = yaml_file.read_text(encoding="utf-8")
        for match in pattern.finditer(content):
            found.add(f"${match.group(1)}")
    for yaml_file in apps_path.rglob("*.yml"):
        content = yaml_file.read_text(encoding="utf-8")
        for match in pattern.finditer(content):
            found.add(f"${match.group(1)}")
    return sorted(found)
```

- [ ] **Step 4: Run tests to confirm they pass**

```
python -m pytest tests/test_app_manager.py::test_get_known_vars_returns_var_names tests/test_app_manager.py::test_get_known_vars_deduplicates tests/test_app_manager.py::test_get_known_vars_includes_archived tests/test_app_manager.py::test_get_known_vars_empty_dir tests/test_app_manager.py::test_get_known_vars_missing_dir -v
```

Expected: 5 passing

- [ ] **Step 5: Run full test suite to confirm no regressions**

```
python -m pytest tests/ -q
```

Expected: all passing

- [ ] **Step 6: Commit**

```bash
git add harness/app_manager.py tests/test_app_manager.py
git commit -m "feat: add get_known_vars() to scan app YAMLs for \$VAR references"
```

---

## Task 2: `GET /api/vars` endpoint

**Files:**
- Modify: `web/routes/api.py`
- Modify: `tests/test_web_apps.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_web_apps.py`:

```python
def test_get_vars_returns_list(tmp_path, monkeypatch):
    """GET /api/vars returns sorted list of $VAR names found in app YAMLs."""
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    (apps_dir / "myapp.yaml").write_text(
        "app: myapp\nurl: https://example.com\ntests:\n"
        "  - name: login\n    type: browser\n    steps:\n"
        "      - fill:\n          field: '#p'\n          value: $SECRET_PASS\n"
    )
    monkeypatch.chdir(tmp_path)
    import web.main as main_mod
    monkeypatch.setattr(main_mod, "_apps", [])
    monkeypatch.setattr(main_mod, "_apps_dir", str(apps_dir))
    from fastapi.testclient import TestClient
    client = TestClient(main_mod.app)
    resp = client.get("/api/vars")
    assert resp.status_code == 200
    data = resp.json()
    assert "vars" in data
    assert "$SECRET_PASS" in data["vars"]


def test_get_vars_returns_empty_when_no_apps(tmp_path, monkeypatch):
    """GET /api/vars returns empty list when no app YAMLs exist."""
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    monkeypatch.chdir(tmp_path)
    import web.main as main_mod
    monkeypatch.setattr(main_mod, "_apps", [])
    monkeypatch.setattr(main_mod, "_apps_dir", str(apps_dir))
    from fastapi.testclient import TestClient
    client = TestClient(main_mod.app)
    resp = client.get("/api/vars")
    assert resp.status_code == 200
    assert resp.json() == {"vars": []}
```

- [ ] **Step 2: Run tests to confirm they fail**

```
python -m pytest tests/test_web_apps.py::test_get_vars_returns_list tests/test_web_apps.py::test_get_vars_returns_empty_when_no_apps -v
```

Expected: 2 failures — 404 or attribute errors

- [ ] **Step 3: Add the endpoint to `web/routes/api.py`**

Look at the existing imports at the top of `web/routes/api.py`. Add `get_known_vars` to the `harness.app_manager` import line:

```python
from harness.app_manager import (
    AppManagerError,
    write_app,
    update_app,
    archive_app,
    restore_app,
    delete_archived_app,
    get_known_vars,
)
```

Then add this route after the existing `list_apps` GET route (around line 70):

```python
@router.get("/vars")
async def list_vars():
    """Return all $VAR names referenced in app YAML files. Never returns values."""
    return {"vars": get_known_vars(apps_dir=get_apps_dir())}
```

- [ ] **Step 4: Run tests to confirm they pass**

```
python -m pytest tests/test_web_apps.py::test_get_vars_returns_list tests/test_web_apps.py::test_get_vars_returns_empty_when_no_apps -v
```

Expected: 2 passing

- [ ] **Step 5: Run full test suite**

```
python -m pytest tests/ -q
```

Expected: all passing

- [ ] **Step 6: Commit**

```bash
git add web/routes/api.py tests/test_web_apps.py
git commit -m "feat: add GET /api/vars endpoint returning known \$VAR references"
```

---

## Task 3: Env var autocomplete in app_form.html

**Files:**
- Modify: `web/templates/app_form.html`

The form currently renders fill step value fields as plain `<input>` elements. We'll add a `<datalist>` populated from `GET /api/vars`, then wire it to every value field (fill values, env URLs, base URL). This uses the native HTML `<datalist>` element — no JS library needed.

- [ ] **Step 1: Add the datalist and fetch to `app_form.html`**

Add the following block **before** the closing `</script>` tag (the one near the end of the file):

```javascript
// Populate var autocomplete datalist from /api/vars
const varDatalist = document.createElement('datalist');
varDatalist.id = 'var-options';
document.body.appendChild(varDatalist);

fetch('/api/vars')
  .then(function(r) { return r.json(); })
  .then(function(data) {
    data.vars.forEach(function(v) {
      const opt = document.createElement('option');
      opt.value = v;
      varDatalist.appendChild(opt);
    });
    // Wire datalist to all existing value inputs
    applyVarDatalist();
  });

function applyVarDatalist() {
  // Fill step value fields
  document.querySelectorAll('.step-fill-value, .step-value').forEach(function(el) {
    el.setAttribute('list', 'var-options');
  });
  // Environment URL fields
  document.querySelectorAll('.env-url').forEach(function(el) {
    el.setAttribute('list', 'var-options');
  });
}
```

- [ ] **Step 2: Wire datalist to dynamically-added fields**

In `app_form.html`, find the `addStepRow` function. At the end of the function, before the closing `}`, add:

```javascript
  applyVarDatalist();
```

Also add `applyVarDatalist()` at the end of `addEnvRow()` and `addTestBlock()`.

Here is how `addStepRow` should look after the edit (the existing function — locate it and add the call):

```javascript
function addStepRow(btn) {
  // ... existing row-building code unchanged ...
  applyVarDatalist();  // ← ADD THIS LINE at the end
}
```

And similarly for `addEnvRow`:

```javascript
function addEnvRow() {
  // ... existing row-building code unchanged ...
  applyVarDatalist();  // ← ADD THIS LINE at the end
}
```

And `addTestBlock`:

```javascript
function addTestBlock() {
  // ... existing block-building code unchanged ...
  applyVarDatalist();  // ← ADD THIS LINE at the end
}
```

- [ ] **Step 3: Wire datalist to fill step value inputs rendered server-side**

The `app_form.html` Jinja2 template renders existing fill steps as `<input class="search step-fill-value">`. Add `list="var-options"` to these elements so they get the autocomplete immediately even before the fetch completes:

```html
<input class="search step-fill-value" type="text" placeholder="value"
       value="{{ step.fill.value | default('') | e }}"
       list="var-options"
       style="flex:1; margin-bottom:0;">
```

And for the single-value step input:

```html
<input class="search step-value" type="text" placeholder="value"
       value="{{ step[action] | default('') | e }}"
       list="var-options"
       style="flex:1; margin-bottom:0;">
```

And for env-url inputs in the Jinja2 loop:

```html
<input class="search env-url" type="text" placeholder="https://staging.example.com"
       value="{{ url | e }}"
       list="var-options"
       style="flex:1; margin-bottom:0;">
```

- [ ] **Step 4: Manual smoke test**

Start the server:

```
python -m web.main
```

Open http://localhost:8000/apps/new in Chrome.

1. Click "+ Add Test", choose type `browser`, click "+ Add Step", select `fill`
2. Click in the "value" field and type `$` — you should see a dropdown with known `$VAR` names from your apps
3. Select one — it populates the field
4. Also check the Environments section: click "+ Add Environment" and verify the URL field also shows `$VAR` suggestions

- [ ] **Step 5: Commit**

```bash
git add web/templates/app_form.html
git commit -m "feat: add \$VAR autocomplete datalist to form value fields"
```

---

## Task 4: `/secrets` page — env var dependency map

**Files:**
- Create: `web/templates/secrets.html`
- Modify: `web/routes/apps.py`
- Modify: `web/templates/base.html`
- Modify: `tests/test_web_apps.py`

This page shows which env vars are referenced across apps, and whether each var is currently set in the server's environment (green tick / red cross). **No values are ever shown.** This gives operators a "what do I need to configure" overview.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_web_apps.py`:

```python
def test_get_secrets_returns_200_html(tmp_path, monkeypatch):
    """GET /secrets returns 200 HTML with the secrets dependency table."""
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    (apps_dir / "myapp.yaml").write_text(
        "app: myapp\nurl: https://example.com\ntests:\n"
        "  - name: t\n    type: browser\n    steps:\n"
        "      - fill:\n          field: '#p'\n          value: $MY_SECRET\n"
    )
    monkeypatch.chdir(tmp_path)
    import web.main as main_mod
    monkeypatch.setattr(main_mod, "_apps", [])
    monkeypatch.setattr(main_mod, "_apps_dir", str(apps_dir))
    from fastapi.testclient import TestClient
    client = TestClient(main_mod.app)
    resp = client.get("/secrets")
    assert resp.status_code == 200
    assert b"$MY_SECRET" in resp.content
```

- [ ] **Step 2: Run test to confirm it fails**

```
python -m pytest tests/test_web_apps.py::test_get_secrets_returns_200_html -v
```

Expected: FAIL — 404

- [ ] **Step 3: Create `web/templates/secrets.html`**

```html
{% extends "base.html" %}
{% block content %}
<h1 style="margin-bottom:1.5rem;">Environment Variables</h1>
<p style="color:#a0aec0; font-size:.875rem; margin-bottom:1.5rem;">
  These environment variables are referenced in your app YAML files.
  A <span style="color:#68d391;">&#10003;</span> means the variable is present
  in the server process; <span style="color:#f87171;">&#10007;</span> means it
  is not set. Values are never shown.
</p>

{% if not vars %}
<p style="color:#718096;">No <code>$VAR</code> references found in app files.</p>
{% else %}
<table style="width:100%; border-collapse:collapse;">
  <thead>
    <tr style="text-align:left; border-bottom:1px solid #2d3748; color:#a0aec0; font-size:.8rem;">
      <th style="padding:.5rem .75rem;">Variable</th>
      <th style="padding:.5rem .75rem;">Status</th>
    </tr>
  </thead>
  <tbody>
    {% for var, is_set in vars %}
    <tr style="border-bottom:1px solid #1a202c;">
      <td style="padding:.5rem .75rem; font-family:monospace; font-size:.875rem;">{{ var }}</td>
      <td style="padding:.5rem .75rem;">
        {% if is_set %}
          <span style="color:#68d391; font-weight:600;">&#10003; Set</span>
        {% else %}
          <span style="color:#f87171; font-weight:600;">&#10007; Not set</span>
        {% endif %}
      </td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endif %}

<div style="margin-top:1.5rem;">
  <a href="/apps" class="btn">&#8592; Back to Apps</a>
</div>
{% endblock %}
```

- [ ] **Step 4: Add the route to `web/routes/apps.py`**

Add this import at the top of `web/routes/apps.py` (with existing imports):

```python
import os
from harness.app_manager import get_known_vars
```

Then add this route after the existing `apps_edit` route:

```python
@router.get("/secrets", response_class=HTMLResponse)
async def secrets_page(request: Request):
    apps_dir = get_apps_dir()
    var_names = get_known_vars(apps_dir=apps_dir)
    # Check presence only — never read values
    vars_with_status = [(v, os.environ.get(v[1:]) is not None) for v in var_names]
    return templates.TemplateResponse(request, "secrets.html", {
        "vars": vars_with_status,
        **_nav_ctx(request),
    })
```

`v[1:]` strips the leading `$` to look up the env var name (e.g. `$MY_SECRET` → `MY_SECRET`).

- [ ] **Step 5: Add nav link in `base.html`**

Find the Apps nav link in `web/templates/base.html`:

```html
<a href="/apps" style="color:#a0aec0; font-size:.875rem;">Apps</a>
```

Add a Secrets link after it:

```html
<a href="/apps" style="color:#a0aec0; font-size:.875rem;">Apps</a>
<a href="/secrets" style="color:#a0aec0; font-size:.875rem;">Secrets</a>
```

- [ ] **Step 6: Run test to confirm it passes**

```
python -m pytest tests/test_web_apps.py::test_get_secrets_returns_200_html -v
```

Expected: PASS

- [ ] **Step 7: Run full test suite**

```
python -m pytest tests/ -q
```

Expected: all passing

- [ ] **Step 8: Manual smoke test**

Navigate to http://localhost:8000/secrets.

Confirm:
- Each `$VAR` from your app YAMLs appears
- `$SONARR_USERNAME` and `$SONARR_PASSWORD` show with a green tick (if set) or red cross (if not)
- No values are shown anywhere on the page

- [ ] **Step 9: Commit**

```bash
git add web/templates/secrets.html web/routes/apps.py web/templates/base.html tests/test_web_apps.py
git commit -m "feat: add /secrets page showing env var dependency map"
```

---

## Self-Review Checklist

- [x] **Spec coverage:** User asked for (a) dropdown/autocomplete for `$VAR` references in form fields — covered by Task 3. (b) secure handling — no values ever shown, only names. (c) optional dedicated page — covered by Task 4.
- [x] **No placeholders:** All steps have actual code.
- [x] **Type consistency:** `get_known_vars()` returns `list[str]` in Task 1, consumed as `list[str]` in Task 2 and Task 4 route. `vars_with_status` is `list[tuple[str, bool]]` — matches template loop `for var, is_set in vars`.
- [x] **Security:** `os.environ.get(v[1:]) is not None` performs a presence check — the value is never returned or stored.
- [x] **Import check:** `get_known_vars` added to import in both `api.py` (Task 2) and `apps.py` (Task 4). `re` is already imported in `app_manager.py` (used by `slugify_app_name`).
