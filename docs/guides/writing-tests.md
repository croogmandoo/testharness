# Writing Tests

This guide covers every way to define tests in the harness — from simple uptime checks to
multi-step browser flows and custom Python scripts.

---

## Table of Contents

1. [App Definition Structure](#1-app-definition-structure)
2. [Test Types](#2-test-types)
   - [availability](#availability)
   - [api](#api)
   - [browser](#browser)
3. [Referencing Page Content](#3-referencing-page-content)
   - [assert_text](#assert_text--check-visible-text)
   - [assert_url_contains](#assert_url_contains--check-the-address-bar)
   - [Selectors reference](#selectors-reference)
4. [All Browser Steps](#4-all-browser-steps)
5. [Environment Variables](#5-environment-variables)
6. [Python Scripts](#6-python-scripts)
   - [When to use a script](#when-to-use-a-script)
   - [AppTest base class](#apptest-base-class)
   - [Full example script](#full-example-script)
7. [Config Reference](#7-config-reference)

---

## 1. App Definition Structure

Every app lives in a single YAML file inside `apps/`.

```yaml
app: My Application           # Display name (required)
url: https://myapp.example.com  # Default base URL

environments:                 # Per-environment base URLs (optional)
  staging: https://staging.myapp.example.com
  production: https://myapp.example.com

tests:
  - name: "Test name"
    type: availability | api | browser
    # ... type-specific fields
```

| Field | Required | Description |
|-------|----------|-------------|
| `app` | Yes | Name shown in the dashboard |
| `url` | Yes* | Default base URL — used when no environment matches |
| `environments` | No | Map of environment names to base URLs |
| `tests` | No | Array of test definitions |

\* Required unless every test uses absolute URLs.

---

## 2. Test Types

### availability

The simplest check. Makes a single GET request to the app's base URL and
verifies the HTTP status code. No browser, no content inspection.

```yaml
- name: "Site is up"
  type: availability
  expect_status: 200     # default is 200
```

**Use when** you just need to know the app is reachable.

**Examples:**

```yaml
# Expect a 200
- name: "Homepage is reachable"
  type: availability
  expect_status: 200

# Accept a redirect (301 or 302 from http → https)
- name: "HTTP redirects"
  type: availability
  expect_status: 301

# Maintenance mode — expect 503
- name: "App is in maintenance"
  type: availability
  expect_status: 503
```

---

### api

Makes a single HTTP request to an endpoint and can check both the status code
and key/value pairs inside a JSON response body.

```yaml
- name: "Health check"
  type: api
  method: GET            # default is GET
  endpoint: /health      # appended to base URL
  expect_status: 200
  expect_json:           # optional — check JSON response body
    status: "ok"
```

| Field | Default | Description |
|-------|---------|-------------|
| `method` | `GET` | HTTP verb — GET, POST, PUT, PATCH, DELETE |
| `endpoint` | `/` | Path appended to the base URL |
| `expect_status` | `200` | Expected HTTP status code |
| `expect_json` | — | Key-value pairs that must match exactly in the JSON body |
| `headers` | — | Map of custom HTTP headers; values support `$VAR_NAME` substitution |

**Examples:**

```yaml
# Simple GET health check
- name: "Health endpoint"
  type: api
  endpoint: /api/health
  expect_status: 200

# Assert specific JSON fields
- name: "Health reports ok"
  type: api
  endpoint: /api/health
  expect_status: 200
  expect_json:
    status: "ok"
    database: "connected"

# POST to an endpoint
- name: "Create resource returns 201"
  type: api
  method: POST
  endpoint: /api/items
  expect_status: 201

# Verify version info
- name: "API version"
  type: api
  endpoint: /api/version
  expect_status: 200
  expect_json:
    major: 2
    deprecated: false

# Expect 401 when unauthenticated
- name: "Auth is required"
  type: api
  endpoint: /api/me
  expect_status: 401

# API with custom headers (Bearer token authentication)
- name: "Authenticated API call with token"
  type: api
  method: POST
  endpoint: /api/runs
  headers:
    Authorization: "Bearer $API_KEY"
    Content-Type: "application/json"
  expect_status: 202

# Nested JSON: only top-level keys are checked
- name: "Feature flags present"
  type: api
  endpoint: /api/config
  expect_status: 200
  expect_json:
    environment: "production"
    maintenance_mode: false
```

#### Headers

Add custom HTTP headers to any API request using the `headers` field, which accepts a map of header names to values.
Header values support `$VAR_NAME` substitution, making it easy to inject secrets (like API keys or auth tokens).

```yaml
- name: "Call protected endpoint"
  type: api
  endpoint: /api/secure
  headers:
    Authorization: "Bearer $API_TOKEN"
    X-Custom-Header: "value"
  expect_status: 200
```

**Common use cases:**
- Bearer token authentication: `Authorization: "Bearer $API_KEY"`
- API keys: `X-API-Key: "$API_KEY_SECRET"`
- Custom headers: `X-My-Header: "custom-value"`

> **Note:** `expect_json` matches top-level keys only. Each key must equal the
> expected value exactly (type-aware comparison). Missing or extra keys in the
> response do not cause a failure — only mismatches on the keys you specify.

---

### browser

Launches a headless Chromium browser, runs a sequence of steps, and passes
only if every step succeeds.

```yaml
- name: "Login flow"
  type: browser
  steps:
    - navigate: /login
    - fill:
        field: input[name=username]
        value: admin
    - fill:
        field: input[name=password]
        value: $ADMIN_PASSWORD
    - click: "button:has-text('Sign in')"
    - wait_for_selector: .dashboard-header
    - assert_text: "Welcome back"
    - screenshot: null
```

Steps execute in order. If any step fails or errors, the test stops immediately
and saves an automatic failure screenshot.

---

## 3. Referencing Page Content

### `assert_text` — check visible text

Reads all text from the `<body>` element and checks that the given string
appears somewhere in it. Case-sensitive, substring match.

```yaml
- assert_text: "Welcome"
```

**What it checks:** the full plain-text content of the page body, including
text inside buttons, headings, paragraphs, labels, table cells, and so on.

**What it does NOT check:** HTML attributes, CSS content, invisible elements,
or content inside `<script>`/`<style>` tags.

**Examples:**

```yaml
# Check a heading appeared after login
- assert_text: "Dashboard"

# Check an error message is shown
- assert_text: "Invalid credentials"

# Check a specific user's name appears
- assert_text: "Welcome, Craig"

# Check a dynamic label
- assert_text: "3 items pending"

# Check content that only appears when loaded
- wait_for_selector: .results-table
- assert_text: "Total: 42"

# Verify logout worked
- click: "#logout-btn"
- assert_text: "You have been signed out"
```

---

### `assert_url_contains` — check the address bar

Checks that the current page URL contains the given substring.
Case-sensitive.

```yaml
- assert_url_contains: /dashboard
```

**Examples:**

```yaml
# Verify redirect after form submit
- click: "button[type=submit]"
- assert_url_contains: /success

# Check query param appeared
- assert_url_contains: "?tab=settings"

# Verify you landed on the right section
- assert_url_contains: /admin/users

# Check login redirect happened
- assert_url_contains: /home

# Useful after OAuth / SSO flows
- assert_url_contains: callback
```

---

### Selectors reference

`fill`, `click`, and `wait_for_selector` all accept any Playwright selector.

#### CSS selectors

The most common approach — use standard CSS syntax.

```yaml
# By ID
- click: "#submit-btn"
- wait_for_selector: "#modal"
- fill:
    field: "#email-input"
    value: user@example.com

# By class
- click: ".primary-button"
- wait_for_selector: ".loading-spinner"

# By element and class
- wait_for_selector: button.danger

# By attribute
- fill:
    field: input[name=username]
    value: admin
- fill:
    field: input[type=email]
    value: user@example.com
- click: input[type=submit]

# By data attribute (common in React/Vue apps)
- click: "[data-testid='login-btn']"
- wait_for_selector: "[data-cy='results-table']"

# Nested selector
- fill:
    field: form.login-form input[name=password]
    value: $PASSWORD
```

#### Text-based locators

Use Playwright's `:has-text()` pseudo-class to target by visible label.
Ideal when IDs are unstable or missing.

```yaml
# Click a button by its label
- click: "button:has-text('Login')"
- click: "button:has-text('Save changes')"
- click: "button:has-text('Delete')"

# Click a link by text
- click: "a:has-text('View report')"
- click: "a:has-text('Sign out')"

# Wait for a specific button to appear
- wait_for_selector: "button:has-text('Continue')"

# Works on any element
- wait_for_selector: "span:has-text('Loaded')"
```

> `:has-text()` is a substring match, so `'Login'` matches "Login now" and "Login".

#### XPath

Use when CSS alone is not enough — e.g., selecting a parent by child content.

```yaml
# By ID
- click: "//button[@id='submit']"

# Parent of a known text node
- click: "//label[contains(text(),'Remember me')]"

# Nth element
- fill:
    field: "(//input[@type='text'])[2]"
    value: "Second field"

# Any element containing text
- wait_for_selector: "//div[contains(text(),'Error')]"
```

#### Role selectors

Playwright supports ARIA role selectors which are accessibility-friendly.

```yaml
- click: "role=button[name='Submit']"
- wait_for_selector: "role=dialog"
- click: "role=menuitem[name='Settings']"
```

---

## 4. All Browser Steps

### `navigate` — go to a URL

```yaml
- navigate: /login              # relative — appended to base URL
- navigate: /admin/users/42
- navigate: https://other.example.com/page   # absolute
```

---

### `fill` — type into a form field

```yaml
- fill:
    field: <selector>
    value: <text>
```

Clears the field first, then types the value.

```yaml
- fill:
    field: input[name=username]
    value: admin

- fill:
    field: "#search-box"
    value: "quarterly report"

- fill:
    field: textarea[name=notes]
    value: "Multi-line notes go here"

# Use environment variables for secrets
- fill:
    field: input[type=password]
    value: $ADMIN_PASSWORD

- fill:
    field: input[name=email]
    value: $TEST_USER_EMAIL
```

---

### `click` — click an element

```yaml
- click: <selector>
```

```yaml
- click: "button[type=submit]"
- click: "#logout-link"
- click: ".modal-close"
- click: "button:has-text('Confirm')"
- click: "a:has-text('Next page')"
- click: "[data-testid='save-btn']"
```

---

### `wait` — pause for a fixed time

```yaml
- wait: <milliseconds>
```

```yaml
- wait: 500      # 0.5 seconds
- wait: 1000     # 1 second
- wait: 2000     # 2 seconds (use sparingly)
```

> Prefer `wait_for_selector` or `wait_for_url` over `wait` wherever possible —
> they are more reliable and faster.

---

### `wait_for_selector` — wait for an element to appear

```yaml
- wait_for_selector: <selector>
```

Blocks until the element is attached to the DOM and visible. Times out after
`browser.timeout_ms` (default 30 s).

```yaml
# After a page load
- navigate: /dashboard
- wait_for_selector: .sidebar

# After an async action
- click: "#refresh-btn"
- wait_for_selector: "[data-loaded='true']"

# After a form submit
- click: "button:has-text('Submit')"
- wait_for_selector: ".success-banner"

# Wait for a modal to open
- click: "#open-dialog"
- wait_for_selector: "role=dialog"

# Wait for a table to populate
- wait_for_selector: "table tbody tr"
```

---

### `wait_for_url` — wait for the address bar to change

```yaml
- wait_for_url: <substring>
```

Wraps the value in glob syntax (`**substring**`) and waits for the URL to match.

```yaml
# After a form submit that redirects
- click: "button[type=submit]"
- wait_for_url: /confirmation

# After login redirect
- wait_for_url: /home

# After OAuth callback
- wait_for_url: callback

# After tab/section change that updates the URL
- click: "a:has-text('Settings')"
- wait_for_url: /settings
```

---

### `assert_text` — assert body contains text

```yaml
- assert_text: <substring>
```

See [Referencing Page Content](#assert_text--check-visible-text) for full examples.

---

### `assert_url_contains` — assert URL contains substring

```yaml
- assert_url_contains: <substring>
```

See [Referencing Page Content](#assert_url_contains--check-the-address-bar) for full examples.

---

### `screenshot` — capture the current page

```yaml
- screenshot: null
```

Saves a screenshot to `data/screenshots/<app>/<env>/<run_id>/<test-slug>-step-N.png`.
The image is linked in the dashboard on the test result detail view.

```yaml
# Take a screenshot at a key moment
- navigate: /reports
- wait_for_selector: .report-chart
- screenshot: null

# Screenshot before a destructive action (for audit purposes)
- screenshot: null
- click: "button:has-text('Delete all')"
- wait_for_selector: ".empty-state"
- screenshot: null
```

> The harness also takes an **automatic failure screenshot** whenever any step
> fails, so you don't need to add `screenshot: null` for failure debugging —
> it's already done for you.

---

## 5. Environment Variables

Any YAML string value that starts with `$` is resolved from the process
environment at startup. If the variable is not set, the harness refuses to
start and logs a `ConfigError`.

### Per-environment secrets

Use `$VAR#env` to prefer an environment-specific value with automatic fallback
to the global `VAR`:

```yaml
tests:
  - name: Login
    type: browser
    steps:
      - fill:
          field: input[name=password]
          value: $SONARR_PASSWORD#staging   # uses SONARR_PASSWORD#staging if set, else SONARR_PASSWORD
```

Set the scoped variable in your `.env` file or environment:

```bash
SONARR_PASSWORD=global-password
SONARR_PASSWORD#staging=staging-only-password
```

If neither `$VAR#env` nor `$VAR` is set, the harness raises a `ConfigError` at startup.

```yaml
url: $MYAPP_BASE_URL

environments:
  staging: $MYAPP_STAGING_URL
  production: $MYAPP_PROD_URL

tests:
  - name: "Login"
    type: browser
    steps:
      - fill:
          field: input[name=username]
          value: $MYAPP_USERNAME
      - fill:
          field: input[type=password]
          value: $MYAPP_PASSWORD
```

Put secrets in a `.env` file (already in `.gitignore`) or set them in your
shell/CI environment:

```
# .env
MYAPP_BASE_URL=https://myapp.example.com
MYAPP_USERNAME=testuser
MYAPP_PASSWORD=supersecret
TEAMS_WEBHOOK_URL=https://...
```

---

## 6. Python Scripts

### When to use a script

YAML is enough for the majority of tests. Reach for a Python script when you
need something YAML cannot express:

| Scenario | Use |
|----------|-----|
| Simple availability / status check | YAML `availability` |
| JSON field assertions | YAML `api` |
| Click-through a UI flow | YAML `browser` |
| Dynamic test data (generate IDs, timestamps) | Python |
| Multi-step API flows (create → read → delete) | Python |
| Custom authentication headers or signed requests | Python |
| Database / queue assertions | Python |
| Conditional logic — skip if env is staging | Python |
| Reusable helpers shared across many tests | Python |

### AppTest base class

Subclass `AppTest` from `harness` and add `test_*` methods. Each method is
discovered and run as an individual test, exactly like a pytest test.

```python
from harness import AppTest

class MyAppTest(AppTest):
    name = "My Application"               # shown in dashboard
    base_url = "https://myapp.example.com"

    environments = {                       # optional
        "staging":    "https://staging.myapp.example.com",
        "production": "https://myapp.example.com",
    }

    def test_something(self):
        ...
```

**Helpers available in every method:**

| Helper | Description |
|--------|-------------|
| `self.env("VAR_NAME")` | Read an environment variable; raises `ConfigError` if missing |
| `self.resolve_base_url("production")` | Return the base URL for an environment |
| `self.name` | App display name |

---

### Full example script

The file below demonstrates the main patterns. Save it as `apps/my_api_test.py`.

```python
"""
apps/my_api_test.py

Example Python test script for the Web Testing Harness.

Demonstrates:
- Multi-step API flows (create → read → update → delete)
- Reading secrets from environment variables
- Custom request headers
- Dynamic test data
- Conditional assertions
"""

import httpx
import uuid
from datetime import datetime
from harness import AppTest


class MyApiTest(AppTest):
    name = "My API"
    base_url = "https://api.example.com"

    environments = {
        "staging":    "https://staging.api.example.com",
        "production": "https://api.example.com",
    }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict:
        """Build auth headers using an environment variable secret."""
        api_key = self.env("MY_API_KEY")
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
        }

    def _base(self) -> str:
        """Resolve the base URL for the current run environment."""
        # Defaults to production if no environment is matched.
        try:
            return self.resolve_base_url("production")
        except Exception:
            return self.base_url

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def test_health_check(self):
        """GET /health returns 200 with status ok."""
        r = httpx.get(f"{self._base()}/health", timeout=10)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"

        body = r.json()
        assert body.get("status") == "ok", (
            f"Expected status='ok', got {body.get('status')!r}"
        )

    def test_unauthenticated_request_rejected(self):
        """Requests without a token must return 401."""
        r = httpx.get(f"{self._base()}/api/me", timeout=10)
        assert r.status_code == 401, (
            f"Expected 401 Unauthorized, got {r.status_code}"
        )

    def test_create_and_delete_item(self):
        """
        Full lifecycle: create an item, verify it exists, then delete it.

        This tests the happy path for the core CRUD API.
        Uses a unique name so parallel runs don't collide.
        """
        base = self._base()
        headers = self._headers()
        unique_name = f"harness-test-{uuid.uuid4().hex[:8]}"

        # 1. Create
        create_resp = httpx.post(
            f"{base}/api/items",
            headers=headers,
            json={"name": unique_name, "active": True},
            timeout=10,
        )
        assert create_resp.status_code == 201, (
            f"Create failed: {create_resp.status_code} — {create_resp.text}"
        )
        item_id = create_resp.json()["id"]

        # 2. Read back
        get_resp = httpx.get(f"{base}/api/items/{item_id}", headers=headers, timeout=10)
        assert get_resp.status_code == 200, (
            f"Read failed: {get_resp.status_code}"
        )
        assert get_resp.json()["name"] == unique_name, "Name mismatch after create"

        # 3. Delete
        del_resp = httpx.delete(f"{base}/api/items/{item_id}", headers=headers, timeout=10)
        assert del_resp.status_code in (200, 204), (
            f"Delete failed: {del_resp.status_code}"
        )

        # 4. Confirm gone
        gone_resp = httpx.get(f"{base}/api/items/{item_id}", headers=headers, timeout=10)
        assert gone_resp.status_code == 404, (
            f"Item still exists after delete: {gone_resp.status_code}"
        )

    def test_pagination(self):
        """
        List endpoint returns paginated results with correct metadata.
        """
        base = self._base()
        headers = self._headers()

        r = httpx.get(
            f"{base}/api/items",
            headers=headers,
            params={"page": 1, "per_page": 5},
            timeout=10,
        )
        assert r.status_code == 200, f"List failed: {r.status_code}"

        body = r.json()

        # Verify the pagination envelope is present
        assert "items" in body,      "Response missing 'items' key"
        assert "total" in body,      "Response missing 'total' key"
        assert "page" in body,       "Response missing 'page' key"
        assert "per_page" in body,   "Response missing 'per_page' key"

        # Verify the slice size is within bounds
        assert len(body["items"]) <= 5, (
            f"Got {len(body['items'])} items but requested per_page=5"
        )

    def test_response_contains_timestamp(self):
        """
        Every item in the list must have an ISO 8601 `created_at` timestamp.
        """
        r = httpx.get(
            f"{self._base()}/api/items",
            headers=self._headers(),
            timeout=10,
        )
        assert r.status_code == 200

        for item in r.json().get("items", []):
            assert "created_at" in item, f"Item {item.get('id')} missing 'created_at'"
            # Validate it parses as ISO 8601
            try:
                datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
            except ValueError:
                raise AssertionError(
                    f"Invalid created_at for item {item.get('id')}: {item['created_at']!r}"
                )

    def test_staging_only_feature_flag(self):
        """
        The /api/config endpoint should expose a 'beta_features' flag.
        On staging it must be true; on production it may be false.

        This test demonstrates conditional logic based on environment.
        """
        env_name = "staging"   # adjust as needed
        base = self.resolve_base_url(env_name)

        r = httpx.get(f"{base}/api/config", headers=self._headers(), timeout=10)
        assert r.status_code == 200

        flags = r.json().get("feature_flags", {})

        if env_name == "staging":
            assert flags.get("beta_features") is True, (
                "Expected beta_features=True on staging"
            )
        else:
            # Production — just assert the key exists, don't enforce value
            assert "beta_features" in flags, (
                "Expected beta_features key to exist in production config"
            )
```

---

### Tips for Python scripts

**Keep each `test_*` method independent.** The harness runs methods in
definition order but does not guarantee a shared state between them. If
`test_b` needs a resource created by `test_a`, put the whole flow in a single
method (like `test_create_and_delete_item` above).

**Always clean up.** If a test creates a resource (user, item, record), delete
it in the same method — even on assertion failure. A `try/finally` block is
the safest pattern:

```python
def test_resource_lifecycle(self):
    item_id = None
    try:
        r = httpx.post(...)
        item_id = r.json()["id"]
        # ... assertions ...
    finally:
        if item_id:
            httpx.delete(f"{self._base()}/api/items/{item_id}",
                         headers=self._headers(), timeout=10)
```

**Use `self.env()` for every secret.** Never hard-code credentials. The method
raises a clear `ConfigError` at startup if the variable is missing, rather than
failing silently mid-test.

**Use specific assertion messages.** When an `assert` fails, the harness shows
the message in the dashboard. `assert r.status_code == 200` shows only the
expression; `assert r.status_code == 200, f"Got {r.status_code}: {r.text}"` shows
exactly what went wrong.

---

## 7. Per-Test Options

The following fields can be added to any test definition regardless of type:

| Field | Default | Description |
|-------|---------|-------------|
| `timeout_ms` | `browser.timeout_ms` | Override the global timeout for this test only |
| `retry` | `0` | Number of additional attempts on failure (e.g. `retry: 2` = 3 total attempts) |

```yaml
tests:
  - name: Slow export
    type: api
    endpoint: /export
    timeout_ms: 60000    # 60 s for this test only

  - name: Flaky background job
    type: api
    endpoint: /status
    retry: 2             # retry up to 2 times on failure
    timeout_ms: 10000

  - name: Login flow
    type: browser
    timeout_ms: 45000    # browser steps allowed 45 s
    retry: 1
    steps:
      - navigate: /login
      - fill:
          field: input[name=username]
          value: admin
```

Retry stops as soon as a passing result is returned. The last result (pass or fail) is the one stored.

---

## 8. App Tags

Add a `tags` list to any app YAML to group and filter apps on the dashboard:

```yaml
app: Sonarr
tags: [media, production-critical]
environments:
  production: https://sonarr.example.com
tests:
  - name: Homepage
    type: availability
```

Tags appear as chips on the dashboard app tile. The search box filters by tag as well as by app name.

---

## 9. Config Reference

Global settings live in `config.yaml` in the project root.

```yaml
default_environment: production

environments:
  staging:
    label: "Staging"
  production:
    label: "Production"

browser:
  headless: true         # set to false to watch the browser during local dev
  timeout_ms: 30000      # 30 s default for all waits

alerts:
  teams:
    webhook_url: "$TEAMS_WEBHOOK_URL"

  email:
    smtp_host: mail.example.com
    smtp_port: 587
    from: harness@example.com
    to:
      - ops@example.com
      - oncall@example.com
    username: "$SMTP_USERNAME"
    password: "$SMTP_PASSWORD"
```

Alerts fire only on **state transitions** — a test going from passing to
failing (or unknown to failing) sends one alert; subsequent failures do not
repeat it. Recovery sends a separate "resolved" alert.

| Transition | Alert sent |
|------------|------------|
| unknown → fail/error | FAIL |
| passing → fail/error | FAIL |
| failing → pass | RESOLVED |
| same state | none |
