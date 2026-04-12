# App Management UI — Design Spec

**Date:** 2026-04-12
**Status:** Approved

---

## Goal

Add a browser-based UI for managing app definitions (YAML files in `apps/`). Users can create, edit, and archive apps without touching the filesystem directly.

---

## Architecture

YAML files in `apps/` remain the single source of truth. The UI reads and writes them directly via a new `harness/app_manager.py` module that owns all file operations. The web layer stays thin — routes call the manager, the manager touches the filesystem.

Archived apps are moved to `apps/archived/` and can be restored. The in-memory `_apps` snapshot in `web/main.py` is reloaded after any mutation so the dashboard reflects changes immediately without a server restart.

---

## API Endpoints

Three new REST endpoints added to `web/routes/api.py`:

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/apps` | Create a new app — writes YAML file to `apps/` |
| `PUT` | `/api/apps/{app_name}` | Update an existing app — overwrites its YAML file |
| `DELETE` | `/api/apps/{app_name}` | Archive an app — moves file to `apps/archived/` |

Additional endpoints for archive management:

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/apps/{app_name}/restore` | Restore an archived app — moves file back to `apps/` |
| `DELETE` | `/api/apps/{app_name}/permanent` | Permanently delete an archived app |

---

## Pages

### `/apps` — App Management

Lists all active apps and archived apps. Linked from the nav bar.

**Active apps table columns:** App name, URL, number of tests, Edit button, Archive button.

**Archived apps section:** Collapsed by default. Shows app name, Restore button, Permanently Delete button.

### `/apps/new` — Create App

Form for creating a new app. On save, writes a new YAML file and redirects to `/apps`.

### `/apps/{app_name}/edit` — Edit App

Same form as create, pre-filled with the existing app's data. On save, overwrites the YAML file and redirects to `/apps`.

---

## Form Design

The form has two modes toggled by a tab/button:

### Simple Mode

Fields rendered as HTML form inputs:

- **App name** (text, required) — slugified for the filename on create
- **URL** (text, required) — fallback base URL for all environments
- **Environments** (dynamic rows) — each row has an environment key and URL; rows can be added and removed
- **Tests** (dynamic list) — each test has:
  - Name (text, required)
  - Type (dropdown: `availability` / `api` / `browser`)
  - Type-specific fields:
    - `availability`: expect_status (number, default 200)
    - `api`: endpoint (text), method (dropdown: GET/POST/PUT/DELETE), expect_status (number), expect_json (optional textarea)
    - `browser`: steps (dynamic list — each step has an action dropdown and a value field)

Browser step actions available: `navigate`, `fill`, `click`, `assert_text`, `assert_url_contains`, `wait_for_selector`, `wait_for_url`, `wait`

Fill steps have two value fields: `field` (CSS selector) and `value`.

### Advanced Mode

A `<textarea>` pre-filled with the YAML generated from the Simple mode fields (or the raw file contents on edit). Saved as-is to disk. YAML parse errors are shown inline before saving.

Switching from Simple → Advanced pre-fills the textarea. Switching from Advanced → Simple is not supported (data loss warning shown).

---

## Validation

Errors shown inline above the form field or as a banner.

| Rule | Error message |
|------|--------------|
| App name is blank | "App name is required" |
| App name already exists (on create) | "An app with this name already exists" |
| Filename conflict after slugification | "This name conflicts with an existing app file" |
| URL is blank | "URL is required" |
| URL doesn't start with http:// or https:// | "URL must start with http:// or https://" |
| Test has no name | "Each test must have a name" |
| Test has no type | "Each test must have a type" |
| Browser test has no steps | "Browser tests must have at least one step" |
| API test has no endpoint | "API tests must have an endpoint" |
| Advanced mode YAML parse error | "Invalid YAML: {error message}" |

---

## `harness/app_manager.py`

Owns all filesystem operations. Functions:

```python
def slugify_app_name(name: str) -> str
    # "My API" → "my-api"

def app_file_path(name: str, apps_dir: str = "apps") -> Path
    # returns apps/my-api.yaml

def list_apps(apps_dir: str = "apps") -> list[dict]
    # returns parsed YAML for all .yaml/.yml in apps/ (not archived/)

def list_archived(apps_dir: str = "apps") -> list[dict]
    # returns parsed YAML for all files in apps/archived/

def create_app(app_def: dict, apps_dir: str = "apps") -> Path
    # validates no conflict, writes YAML file, returns path

def update_app(app_name: str, app_def: dict, apps_dir: str = "apps") -> Path
    # overwrites existing file, raises if not found

def archive_app(app_name: str, apps_dir: str = "apps") -> Path
    # moves file to apps/archived/, returns new path

def restore_app(app_name: str, apps_dir: str = "apps") -> Path
    # moves file from apps/archived/ back to apps/

def delete_archived_app(app_name: str, apps_dir: str = "apps") -> None
    # permanently deletes from apps/archived/
```

All functions raise `AppManagerError` (a new exception class) on failure.

---

## Navigation

`base.html` nav updated to include an "Apps" link:

```
Testing Harness    [Apps]    [Staging] [Production]
```

---

## File Structure

```
harness/
└── app_manager.py          ← new

web/
├── routes/
│   ├── apps.py             ← new: HTML routes
│   └── api.py              ← update: CRUD endpoints
├── templates/
│   ├── base.html           ← update: Apps nav link
│   ├── apps.html           ← new: management page
│   └── app_form.html       ← new: create/edit form
└── main.py                 ← update: register router, reload _apps after mutations

apps/
└── archived/               ← new directory (created on first archive)

tests/
├── test_app_manager.py     ← new
└── test_web_apps.py        ← new
```

---

## Reload Behaviour

After any create, update, archive, or restore operation, `web/main.py`'s `_apps` list is refreshed by calling `load_apps()` again. This keeps the dashboard in sync without requiring a server restart.

A `reload_apps()` helper function is added to `web/main.py`:

```python
def reload_apps(apps_dir: str = "apps") -> None:
    global _apps
    _apps = load_apps(apps_dir) if os.path.isdir(apps_dir) else []
```

---

## Testing

`tests/test_app_manager.py` — unit tests using `tmp_path`:
- Create app writes correct YAML
- Create app raises on duplicate name
- Update app overwrites file
- Archive moves file to `archived/`
- Restore moves file back
- Permanent delete removes file
- All error cases raise `AppManagerError`

`tests/test_web_apps.py` — FastAPI `TestClient` tests:
- `POST /api/apps` creates file and returns 201
- `POST /api/apps` returns 409 on duplicate
- `PUT /api/apps/{name}` updates file
- `DELETE /api/apps/{name}` archives app
- `POST /api/apps/{name}/restore` restores app
- GET `/apps` returns 200 HTML
