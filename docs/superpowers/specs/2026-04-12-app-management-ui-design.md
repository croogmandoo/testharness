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

**Important:** The edit form reads raw YAML from disk (via `app_manager`), not from `get_apps()`. The `_apps` list holds resolved data (env vars substituted), which is wrong for editing.

---

## API Endpoints

### Existing endpoint (unchanged)

`GET /api/apps?environment=...` already exists in `web/routes/api.py` and returns database-backed app summary rows (pass/fail counts, last run). It is **not modified** by this feature.

### New mutation endpoints

Added to `web/routes/api.py`:

| Method | Path | Body | Response |
|--------|------|------|----------|
| `POST` | `/api/apps` | `{app_def: dict}` | 201 `{app: str, file: str}` |
| `PUT` | `/api/apps/{app_name}` | `{app_def: dict}` | 200 `{app: str, file: str}` |
| `DELETE` | `/api/apps/{app_name}` | — | 200 `{archived: str}` |
| `POST` | `/api/apps/{app_name}/restore` | — | 200 `{app: str, file: str}` |
| `DELETE` | `/api/apps/{app_name}/permanent` | — | 204 (no body) |

All mutation endpoints call `reload_apps()` after the filesystem operation succeeds.

Error responses: 404 if app not found, 409 if duplicate on create, 422 for validation errors.

---

## Pages

### `/apps` — App Management

Lists all active apps and archived apps. Linked from the nav bar.

**Active apps table columns:** App name, URL, number of tests, Edit button, Archive button.

**Archived apps section:** Collapsed by default. Shows app name, Restore button, Permanently Delete button.

### `/apps/new` — Create App

Form for creating a new app. On save, calls `POST /api/apps` and redirects to `/apps`.

### `/apps/{app_name}/edit` — Edit App

Same form as create, pre-filled with the **raw YAML file contents** read from disk (not from `get_apps()`). On save, calls `PUT /api/apps/{app_name}` and redirects to `/apps`.

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

A `<textarea>` pre-filled with the YAML generated from the Simple mode fields (on create) or the raw file contents read from disk (on edit). Saved as-is to disk. YAML parse errors are shown inline before saving.

Switching from Simple → Advanced pre-fills the textarea with the current form state serialised to YAML. Switching from Advanced → Simple is not supported (a warning is shown).

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

Owns all filesystem operations. Raises `AppManagerError` on failure.

**No concurrency locking is implemented.** Concurrent writes (e.g. a UI save racing with a background test reload) may produce inconsistent reads. This is an accepted limitation for a single-operator tool.

`list_apps()` and `list_archived()` delegate to `harness.loader.load_apps()` for consistency — they return the same dict shape (including `_source`, `_type`, and resolved env vars). The edit form does **not** use these — it reads the raw file from disk directly to preserve unresolved `$VAR` placeholders.

```python
class AppManagerError(Exception):
    pass

def slugify_app_name(name: str) -> str
    # "My API" → "my-api"

def app_file_path(name: str, apps_dir: str = "apps") -> Path
    # returns Path("apps/my-api.yaml")

def list_apps(apps_dir: str = "apps") -> list[dict]
    # delegates to load_apps(apps_dir); excludes apps/archived/

def list_archived(apps_dir: str = "apps") -> list[dict]
    # delegates to load_apps(apps_dir + "/archived")

def write_app(app_def: dict, apps_dir: str = "apps") -> Path
    # validates no conflict, writes YAML file, returns path
    # (named write_app to avoid collision with web.main.create_app)

def update_app(app_name: str, app_def: dict, apps_dir: str = "apps") -> Path
    # overwrites existing file, raises AppManagerError if not found

def read_app_raw(app_name: str, apps_dir: str = "apps") -> str
    # returns raw YAML string from disk (unresolved env vars preserved)

def archive_app(app_name: str, apps_dir: str = "apps") -> Path
    # moves file to apps/archived/, returns new path

def restore_app(app_name: str, apps_dir: str = "apps") -> Path
    # moves file from apps/archived/ back to apps/

def delete_archived_app(app_name: str, apps_dir: str = "apps") -> None
    # permanently deletes from apps/archived/
```

---

## Reload Behaviour

After any create, update, archive, or restore operation, the API route calls `reload_apps()` from `web/main.py` to refresh `_apps`.

```python
# web/main.py
def reload_apps(apps_dir: str = "apps") -> None:
    global _apps
    _apps = load_apps(apps_dir) if os.path.isdir(apps_dir) else []
```

**Module identity note:** `web/main.py` already registers itself as `sys.modules["web.main"]` when run as `__main__` (see existing code). Routes in `web/routes/api.py` must import `reload_apps` as:

```python
from web.main import reload_apps
```

This works correctly because the module-aliasing trick in `main()` ensures `sys.modules["web.main"]` always points to the live module object with the real `_apps` global.

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
│   ├── apps.py             ← new: HTML routes (GET /apps, /apps/new, /apps/{name}/edit)
│   └── api.py              ← update: add mutation endpoints
├── templates/
│   ├── base.html           ← update: Apps nav link
│   ├── apps.html           ← new: active + archived app tables
│   └── app_form.html       ← new: shared create/edit form
└── main.py                 ← update: register apps router, add reload_apps()

apps/
└── archived/               ← created on first archive operation

tests/
├── test_app_manager.py     ← new
└── test_web_apps.py        ← new
```

---

## Testing

`tests/test_app_manager.py` — unit tests using `tmp_path`:
- `write_app` writes correct YAML file
- `write_app` raises `AppManagerError` on duplicate name
- `read_app_raw` returns raw YAML with unresolved `$VAR` placeholders
- `update_app` overwrites existing file
- `update_app` raises `AppManagerError` if app not found
- `archive_app` moves file to `archived/` subdirectory
- `restore_app` moves file back from `archived/`
- `delete_archived_app` permanently removes file
- All error cases raise `AppManagerError`

`tests/test_web_apps.py` — FastAPI `TestClient` tests:
- `GET /apps` returns 200 HTML
- `GET /apps/new` returns 200 HTML
- `GET /apps/{name}/edit` returns 200 HTML with app data pre-filled
- `POST /api/apps` creates file and returns 201 with `{app, file}`
- `POST /api/apps` returns 409 on duplicate name
- `PUT /api/apps/{name}` updates file and returns 200
- `DELETE /api/apps/{name}` archives app and returns 200
- `POST /api/apps/{name}/restore` restores app and returns 200
- `DELETE /api/apps/{name}/permanent` permanently deletes and returns 204
