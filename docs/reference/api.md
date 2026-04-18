<!-- generated-by: gsd-doc-writer -->
# API Reference

All endpoints are served by the FastAPI application. Browser-facing routes return HTML responses; JSON API endpoints are prefixed `/api/`.

**Authentication:** All endpoints (except `/setup`, `/auth/login`, `/auth/logout`, `/health`) require a valid `session` cookie issued at login. Unauthenticated requests to `/api/*` paths receive `401 Not authenticated`. Unauthenticated requests to browser paths receive `307 → /auth/login`. Insufficient role returns `403`.

**Session cookie:** `HttpOnly`, `SameSite=lax`. Set `auth.secure_cookie: true` in `config.yaml` for HTTPS deployments.

---

## Table of Contents

- [Authentication & Setup](#authentication--setup)
- [Dashboard](#dashboard)
- [App Management (UI)](#app-management-ui)
- [Runs](#runs)
- [App Definitions (API)](#app-definitions-api)
- [Results](#results)
- [Export](#export)
- [Users](#users)
- [Secrets](#secrets)
- [Admin / LDAP](#admin--ldap)

---

## Health

### `GET /health`

Returns `{"status": "ok"}`. No authentication required. Used by Docker healthchecks, load balancers, and uptime monitors.

**Auth required:** None

**Response (200):**
```json
{"status": "ok"}
```

---

## Authentication & Setup

### `GET /setup`

Renders the first-run admin account creation form. Returns `404` if any users already exist.

**Auth required:** None  
**Role required:** None (only accessible when zero users exist)

---

### `POST /setup`

Creates the initial admin account and sets a session cookie.

**Auth required:** None  
**Role required:** None (only accessible when zero users exist)

**Form fields:**

| Field | Required | Description |
|---|---|---|
| `username` | Yes | Username for the admin account |
| `password` | Yes | Password (minimum 8 characters) |
| `confirm` | Yes | Password confirmation |
| `display_name` | No | Human-readable display name (defaults to username) |

**Responses:**

| Status | Description |
|---|---|
| `303` | Account created; redirects to `/` with `session` cookie set |
| `404` | Users already exist |
| `422` | Passwords do not match or are fewer than 8 characters |

---

### `GET /auth/login`

Renders the login form.

**Auth required:** None

---

### `POST /auth/login`

Authenticates a user and sets a session cookie.

**Auth required:** None

**Form fields:**

| Field | Required | Description |
|---|---|---|
| `username` | Yes | Username |
| `password` | Yes | Password |

**Auth sequence:**
1. Attempt local password verification (`bcrypt.checkpw`).
2. If no local user found and `auth.ldap.enabled` is true, attempt LDAP bind and upsert the user.
3. On success, set `session` cookie and redirect to `/`.

**Responses:**

| Status | Description |
|---|---|
| `303` | Authenticated; redirects to `/` with `session` cookie set |
| `401` | Invalid username or password |

---

### `GET /auth/oauth/github/login`

Initiates GitHub OAuth2 login. Redirects to GitHub's authorization page. Only available when `auth.github.client_id` is set in `config.yaml`.

**Auth required:** None

**Responses:**

| Status | Description |
|---|---|
| `302` | Redirect to `https://github.com/login/oauth/authorize` |
| `404` | GitHub OAuth not configured |

---

### `GET /auth/oauth/github/callback`

OAuth2 callback. Exchanges the authorization code for a token, fetches the GitHub user profile, upserts the user in the database, sets a session cookie, and redirects to `/`.

**Auth required:** None

**Query params:** `code`, `state` (set by GitHub)

**Responses:**

| Status | Description |
|---|---|
| `302` | Authenticated; redirects to `/` |
| `400` | Invalid or missing OAuth state (CSRF protection) |

New GitHub users are assigned the role from `auth.github.default_role` (default: `read_only`).

---

### `POST /auth/logout`

Clears the session cookie and redirects to the login page.

**Auth required:** None (cookie is deleted regardless)

**Responses:**

| Status | Description |
|---|---|
| `303` | Redirects to `/auth/login` with `session` cookie deleted |

---

## Dashboard

### `GET /`

Renders the main dashboard showing a summary of all apps for the selected environment.

**Auth required:** Yes (any role)  
**Query params:**

| Param | Default | Description |
|---|---|---|
| `environment` | `config.default_environment` | Environment to display summary for |

**Response:** HTML. Each app row includes `app`, `total`, `passing`, `failing`, `unknown`, `last_run`, `last_run_id`, and `active_run_id`. Apps configured but never run are included with zero counts.

---

### `GET /app/{app}/{environment}`

Renders the detail view for a specific app and environment, showing recent runs and test-level results.

**Auth required:** Yes (any role)  
**Path params:**

| Param | Description |
|---|---|
| `app` | App name as defined in the YAML config |
| `environment` | Environment name (e.g. `production`, `staging`) |

**Query params:**

| Param | Default | Description |
|---|---|---|
| `run_id` | Most recent run | UUID of a specific run to display |

**Response:** HTML. Includes run list, per-test results with step logs, pass/fail history per test, and live-polling state if a run is pending or running.

---

## App Management (UI)

### `GET /apps`

Lists all active and archived app definitions.

**Auth required:** Yes (any role)

**Response:** HTML. Two sections: active apps (from `apps/` directory) and archived apps (from `apps/_archive/`).

---

### `GET /apps/new`

Renders the app creation form.

**Auth required:** Yes  
**Role required:** `admin`, `runner`

---

### `GET /apps/{app_name}/edit`

Renders the app edit form pre-populated with the existing YAML definition.

**Auth required:** Yes  
**Role required:** `admin`, `runner`

**Path params:**

| Param | Description |
|---|---|
| `app_name` | App name to edit |

**Responses:**

| Status | Description |
|---|---|
| `200` | HTML form with current YAML |
| `404` | App not found |

---

## Runs

### `POST /api/runs`

Triggers a test run for one app or all apps. Responds immediately with run IDs; execution happens in a background task.

**Auth required:** Yes  
**Role required:** `admin`, `runner`

**Request body (JSON):**

```json
{
  "app": "my-app",
  "environment": "production",
  "triggered_by": "api"
}
```

| Field | Required | Description |
|---|---|---|
| `app` | No | App name to run. Omit to run all configured apps. |
| `environment` | Yes | Environment name (e.g. `production`, `staging`) |
| `triggered_by` | No | Label recorded on the run (default: `"api"`) |

**Response (202):**

```json
{
  "run_id": "a1b2c3d4-...",
  "run_ids": ["a1b2c3d4-...", "e5f6..."],
  "apps": ["my-app"]
}
```

`run_id` is the first queued run ID (convenience field). `run_ids` lists all queued IDs. If all targeted apps already have an active run and `app` was not specified, `run_id` will be `null` and `run_ids` will be empty.

**Error responses:**

| Status | Description |
|---|---|
| `404` | Named `app` not found in configuration |
| `409` | A run for the named app + environment is already in progress |

---

### `GET /api/runs/{run_id}`

Returns the run record and all test results for a completed or in-progress run.

**Auth required:** Yes (any role)

**Path params:**

| Param | Description |
|---|---|
| `run_id` | UUID of the run |

**Response (200):**

```json
{
  "id": "a1b2c3d4-...",
  "app": "my-app",
  "environment": "production",
  "status": "complete",
  "triggered_by": "api",
  "started_at": "2026-04-14T10:00:00+00:00",
  "finished_at": "2026-04-14T10:01:23+00:00",
  "results": [
    {
      "test_name": "Homepage loads",
      "status": "pass",
      "duration_ms": 842,
      "step_log": "..."
    }
  ]
}
```

**Error responses:**

| Status | Description |
|---|---|
| `404` | Run not found |

---

## App Definitions (API)

### `GET /api/apps`

Returns a summary of all apps and their last-run status for a given environment.

**Auth required:** Yes (any role)  
**Query params:**

| Param | Default | Description |
|---|---|---|
| `environment` | `"production"` | Environment to summarise |

**Response (200):** Array of app summary objects (same shape as the dashboard summary rows).

---

### `POST /api/apps`

Creates a new app definition by writing a YAML file to the `apps/` directory.

**Auth required:** Yes  
**Role required:** `admin`, `runner`

**Request body (JSON):**

```json
{
  "app_def": {
    "app": "my-app",
    "tests": [
      { "name": "Homepage loads", "url": "https://example.com", "type": "browser" }
    ]
  }
}
```

**Response (201):**

```json
{
  "app": "my-app",
  "file": "apps/my-app.yaml"
}
```

**Error responses:**

| Status | Description |
|---|---|
| `409` | An app with this name already exists |

---

### `PUT /api/apps/{app_name}`

Replaces an existing app definition.

**Auth required:** Yes  
**Role required:** `admin`, `runner`

**Path params:**

| Param | Description |
|---|---|
| `app_name` | App name to update |

**Request body (JSON):** Same shape as `POST /api/apps`. If `app_def.app` is present it must match `app_name`.

**Response (200):**

```json
{
  "app": "my-app",
  "file": "apps/my-app.yaml"
}
```

**Error responses:**

| Status | Description |
|---|---|
| `404` | App not found |
| `422` | `app_def.app` does not match the URL `app_name` |

---

### `DELETE /api/apps/{app_name}`

Archives an app (moves its YAML to `apps/_archive/`). The app will no longer appear in run targets.

**Auth required:** Yes  
**Role required:** `admin`, `runner`

**Path params:**

| Param | Description |
|---|---|
| `app_name` | App name to archive |

**Response (200):**

```json
{
  "archived": "apps/_archive/my-app.yaml"
}
```

**Error responses:**

| Status | Description |
|---|---|
| `404` | App not found or already archived |

---

### `POST /api/apps/{app_name}/restore`

Restores an archived app back to the active `apps/` directory.

**Auth required:** Yes  
**Role required:** `admin`, `runner`

**Path params:**

| Param | Description |
|---|---|
| `app_name` | Archived app name to restore |

**Response (200):**

```json
{
  "app": "my-app",
  "file": "apps/my-app.yaml"
}
```

**Error responses:**

| Status | Description |
|---|---|
| `404` | App not found in archive |

---

### `DELETE /api/apps/{app_name}/permanent`

Permanently deletes an archived app's YAML file. The app must already be archived.

**Auth required:** Yes  
**Role required:** `admin`, `runner`

**Path params:**

| Param | Description |
|---|---|
| `app_name` | Archived app name to permanently delete |

**Response:** `204 No Content`

**Error responses:**

| Status | Description |
|---|---|
| `404` | App not found in archive |

---

## Results

### `GET /api/results/{app}/{environment}`

Returns recent test results for an app and environment.

**Auth required:** Yes (any role)

**Path params:**

| Param | Description |
|---|---|
| `app` | App name |
| `environment` | Environment name |

**Query params:**

| Param | Default | Description |
|---|---|---|
| `limit` | `20` | Maximum number of results to return |
| `offset` | `0` | Number of results to skip (for pagination) |

**Response (200):** Array of test result objects.

---

## Vars

### `GET /api/vars`

Returns all `$VAR` names referenced in app YAML files. Never returns values.

**Auth required:** Yes (any role)

**Response (200):**

```json
{
  "vars": ["API_KEY", "DB_PASSWORD", "BASE_URL"]
}
```

---

## Export

### `GET /api/runs/{run_id}/export`

Downloads a PDF or DOCX report for a completed run.

**Auth required:** Yes  
**Role required:** `admin`, `runner`, `reporting`

**Path params:**

| Param | Description |
|---|---|
| `run_id` | UUID of the run to export |

**Query params:**

| Param | Default | Description |
|---|---|---|
| `format` | `"pdf"` | Export format: `pdf`, `docx`, or `csv` |

**Response:** Binary file download.

| Format | Content-Type |
|---|---|
| `pdf` | `application/pdf` |
| `docx` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` |
| `csv` | `text/csv` |

`Content-Disposition` header: `attachment; filename="run-{run_id[:8]}-{app}.{ext}"`

**Error responses:**

| Status | Description |
|---|---|
| `404` | Run not found |
| `422` | `format` is not `pdf` or `docx` |
| `500` | Export generation failed |

---

## Users

All user management routes are restricted to `admin`. They return HTML responses (form-based UI).

### `GET /users`

Lists all users.

**Auth required:** Yes  
**Role required:** `admin`

---

### `GET /users/new`

Renders the new user creation form.

**Auth required:** Yes  
**Role required:** `admin`

---

### `POST /users/new`

Creates a new local user account.

**Auth required:** Yes  
**Role required:** `admin`

**Form fields:**

| Field | Required | Description |
|---|---|---|
| `username` | Yes | Unique username |
| `password` | Yes | Password (hashed with bcrypt before storage) |
| `display_name` | No | Human-readable name (defaults to username) |
| `email` | No | Email address |
| `role` | No | One of `admin`, `runner`, `reporting`, `read_only` (default: `read_only`) |

**Responses:**

| Status | Description |
|---|---|
| `303` | User created; redirects to `/users` |
| `409` | Username already exists |
| `422` | Username or password missing |

---

### `GET /users/{user_id}/edit`

Renders the edit form for an existing user. Password hash is never included in the form.

**Auth required:** Yes  
**Role required:** `admin`

**Responses:**

| Status | Description |
|---|---|
| `200` | HTML edit form |
| `404` | User not found |

---

### `POST /users/{user_id}/edit`

Updates a user's profile. Optionally resets the password if `password` is provided.

**Auth required:** Yes  
**Role required:** `admin`

**Form fields:**

| Field | Description |
|---|---|
| `display_name` | Human-readable name |
| `email` | Email address |
| `role` | One of `admin`, `runner`, `reporting`, `read_only` |
| `role_override` | `1` to pin role locally (skip LDAP group sync on login); `0` to sync |
| `is_active` | `1` to enable; `0` to disable (immediately invalidates sessions) |
| `password` | If non-empty, replaces the current password hash |

**Responses:**

| Status | Description |
|---|---|
| `303` | Updated; redirects to `/users` |
| `404` | User not found |

---

### `POST /users/{user_id}/delete`

Deletes a user account. Cannot delete the currently logged-in user.

**Auth required:** Yes  
**Role required:** `admin`

**Responses:**

| Status | Description |
|---|---|
| `303` | Deleted; redirects to `/users` |
| `400` | Attempt to delete own account |
| `404` | User not found |

---

## Secrets

Secrets are stored encrypted in `data/harness.db` and injected into `os.environ` at startup and on creation.

### `GET /secrets`

Lists all stored secrets (names, descriptions, and metadata only — values are never returned to the UI) and shows which `$VAR` references from app YAML files are currently set in the environment.

**Auth required:** Yes  
**Role required:** `admin`, `runner`

---

### `POST /secrets`

Creates or updates a named secret. The name is uppercased automatically.

**Auth required:** Yes  
**Role required:** `admin`, `runner`

**Form fields:**

| Field | Required | Description |
|---|---|---|
| `name` | Yes | Variable name (uppercased; must match `[A-Z_][A-Z0-9_]*`) |
| `value` | Yes | Secret value (encrypted before storage) |
| `description` | No | Human-readable description |

**Responses:**

| Status | Description |
|---|---|
| `303` | Secret saved; redirects to `/secrets` |
| `422` | Name is empty |

---

### `POST /secrets/{name}/delete`

Deletes a named secret and removes it from `os.environ`.

**Auth required:** Yes  
**Role required:** `admin`

**Responses:**

| Status | Description |
|---|---|
| `303` | Deleted; redirects to `/secrets` |

---

## Admin / LDAP

### `GET /admin/ldap`

Renders the LDAP configuration page showing the current `auth.ldap` settings from `config.yaml`.

**Auth required:** Yes  
**Role required:** `admin`

---

### `POST /admin/ldap/test`

AJAX endpoint. Tests LDAP connectivity and authentication using the current `config.yaml` LDAP settings. Used by the test-connection widget on the LDAP admin page.

**Auth required:** Yes  
**Role required:** `admin`

**Request body (JSON):**

```json
{
  "username": "testuser",
  "password": "testpassword"
}
```

**Response (200) — success:**

```json
{
  "ok": true,
  "role": "runner",
  "display_name": "Test User"
}
```

**Response (200) — failure:**

```json
{
  "ok": false,
  "error": "Bind failed — invalid credentials."
}
```

Note: this endpoint always returns HTTP 200; the `ok` field indicates success or failure. An `"error"` key is present on failure. If LDAP is not enabled in `config.yaml`, `ok` will be `false` with `error: "LDAP is not enabled in config."`.
