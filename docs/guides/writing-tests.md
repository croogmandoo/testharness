# Writing Tests

This guide covers every way to define tests in the harness — from simple uptime checks to
multi-step browser flows. (A Python-script test type is reserved for a future release; see §6.)

---

## Table of Contents

1. [App Definition Structure](#1-app-definition-structure)
2. [Test Types](#2-test-types)
   - [availability](#availability)
   - [api](#api)
   - [tls](#tls)
   - [browser](#browser)
3. [Referencing Page Content](#3-referencing-page-content)
   - [assert_text](#assert_text--check-visible-text)
   - [assert_url_contains](#assert_url_contains--check-the-address-bar)
   - [Selectors reference](#selectors-reference)
4. [All Browser Steps](#4-all-browser-steps)
5. [Environment Variables](#5-environment-variables)
6. [Python Scripts](#6-python-scripts) — *not yet implemented*
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
| `json` | — | Object — sent as the JSON request body (sets `Content-Type: application/json`) |
| `body` | — | Raw string — sent as the request body when `json:` isn't appropriate |
| `extract` | — | Map of `VAR_NAME: $.json.path` — pulls values from the response for use in subsequent tests (forces sequential execution) |
| `max_duration_ms` | — | SLO ceiling — fails an otherwise-passing test that takes longer than this |
| `retry` | `0` | Re-run the test on `fail`/`error` up to this many times before recording the result |

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
Header values support `$VAR_NAME` substitution, making it easy to inject secrets (like API keys or auth tokens) from the secrets store (see [Environment Variables](#5-environment-variables)).

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

#### JSON request bodies

```yaml
- name: "Create user"
  type: api
  method: POST
  endpoint: /users
  json: { username: alice, role: admin }    # auto-serializes + Content-Type
  expect_status: 201
```

For non-JSON payloads, use `body:` with a raw string instead.

#### Variable extraction & chaining

Capture values from a response and reference them in later tests using
`$VAR_NAME`.  The harness uses a JSONPath dot-walk (covers ~95% of real
needs without a JSONPath dependency):

```yaml
sequential: true        # explicit; auto-on whenever any test uses extract:
tests:
  - name: Login
    type: api
    endpoint: /auth/login
    method: POST
    json: { username: alice, password: $ALICE_PW }
    extract:
      AUTH_TOKEN: $.token
      USER_ID:    $.user.id
      FIRST_ITEM: $.items.0.name      # array index walk

  - name: Fetch profile
    type: api
    endpoint: /users/$USER_ID         # path interpolation
    headers:
      Authorization: "Bearer $AUTH_TOKEN"   # inline substitution
    expect_json: { username: alice }
```

Notes:

- **Sequential execution** is forced whenever any test in the app has
  `extract:`.  Otherwise tests run in parallel via `asyncio.gather`.
- Set `sequential: true` at app level to force ordered execution even
  without extracts (useful for "create / verify / delete" flows).
- Dot-walk supports objects (`$.user.id`) and array indices (`$.items.0`).
- A missing path or non-matching field fails the test with a clear
  "Extract failed: VAR_NAME (path '$.x')" message.
- Subsequent tests use `$AUTH_TOKEN` either as a whole-string value
  (`Authorization: $AUTH_TOKEN`) or **inline** within a larger string
  (`Authorization: "Bearer $AUTH_TOKEN"`).  Both work.
- Captured values are NOT persisted to the database, exposed via
  `/api/runs/<id>`, or sent in webhooks — they exist only for chaining
  within a single run.

#### Per-test SLO threshold

```yaml
- name: "Search must respond in 500ms"
  type: api
  endpoint: /search?q=test
  max_duration_ms: 500
```

A test that returns the expected status but exceeds the SLO is marked
**fail** with `error_msg: "SLO violation: 612ms exceeds max_duration_ms=500"`.

The SLO check runs *after* the retry budget — a flaky-but-fast test that
eventually succeeds isn't penalized; only consistently-slow successes
trip it.

---

### tls

Inspects remote TLS/SSL certificates and security headers without running a browser.
Validates certificate expiration, Subject Alternative Names (SANs), minimum TLS version, and HTTPS response headers.

```yaml
- name: "Production SSL and Security Headers"
  type: tls
  min_days_valid: 30            # Fail if cert expires in fewer than 30 days (default: 30)
  min_tls_version: "TLSv1.2"    # Require at least TLSv1.2 or TLSv1.3
  check_security_headers: true  # Validate HSTS, CSP, and X-Content-Type-Options
```

**What it validates:**
- **Certificate Expiration**: Verifies `notAfter` date has not passed and is at least `min_days_valid` days in the future.
- **Hostname / SAN Matching**: Verifies the domain matches the Common Name (CN) or Subject Alternative Names (SANs).
- **Protocol Security**: Verifies the negotiated cipher/protocol meets `min_tls_version`.
- **Security Headers**: Ensures `Strict-Transport-Security` (HSTS), `X-Content-Type-Options: nosniff`, and `Content-Security-Policy` are present.

---

### browser

Launches a headless Chromium browser, runs a sequence of steps, and passes
only if every step succeeds.

```yaml
- name: "Login flow"
  type: browser
  diff_threshold: 0.05          # Alert if screenshot differs from baseline by > 5%
  max_lcp_ms: 2500              # Core Web Vitals: Largest Contentful Paint under 2.5s
  max_fcp_ms: 1500              # First Contentful Paint under 1.5s
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

#### Visual Regression Testing & Diff Heatmaps

When `diff_threshold` (e.g. `0.02` for 2% pixel tolerance) is set on a browser test with a `screenshot` step:
1. The harness compares the newly captured screenshot against the most recent passing baseline.
2. If pixels changed exceed the threshold, the test fails with `Visual regression: X.X% pixels changed`.
3. A visual diff heatmap (`<slug>-diff.png`) is automatically generated, highlighting changed pixels in vibrant magenta (`#FF007F`) on a dimmed background.
4. An operator with `admin` or `runner` role can review the diff in the test detail view and click **"Approve as New Baseline"** to promote the new screenshot.

#### Core Web Vitals & Frontend Performance

Browser tests can capture and enforce Web Vitals metrics extracted from Chromium's Performance API:
- `max_lcp_ms`: Largest Contentful Paint (ms) — recommended `< 2500`
- `max_fcp_ms`: First Contentful Paint (ms) — recommended `< 1800`
- `max_ttfb_ms`: Time to First Byte (ms) — recommended `< 800`
- `max_cls`: Cumulative Layout Shift (score) — recommended `< 0.1`
- `max_load_ms`: Full page load event duration (ms)
- `capture_web_vitals: true`: Records metrics into the step log without asserting failure thresholds.

#### Guided Journey Creator: URL Recording & Step Generation

The browser test engine includes automatic URL recording capabilities via `harness.url_recorder.URLRecorder` and the `record_url_steps` flag in `run_browser_test`. When enabled:
- The recorder hooks into Playwright navigation events on the main page frame.
- Captured URLs are converted into standardized step definitions (`{"navigate": "/path"}`).
- Consecutive duplicate navigations are collapsed, while non-consecutive revisits are preserved.
- URLs matching the app's `base_url` are normalized to clean relative paths, while external redirects (such as OAuth identity providers) retain their full URLs.
- Recorded steps are prepended directly into the test result's `step_log` as passing navigation steps, allowing users to automatically capture and generate synthetic journey tests from interactive runs.

Thresholds can also be declared as discrete steps:
```yaml
steps:
  - navigate: /products
  - assert_performance:
      max_lcp_ms: 2000
      max_ttfb_ms: 600
```

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

# Use secrets ($VAR from the secrets store) for credentials
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

### `select_option` — pick from a `<select>` dropdown

```yaml
- select_option: { field: "select#country", value: "US" }
- select_option: { field: "select#country", label: "United States" }
- select_option: { field: "select#country", index: 0 }
```

Exactly one of `value`, `label`, or `index` is required.

### `press_key` — keyboard input

Either fire a key globally:

```yaml
- press_key: "Enter"
- press_key: "Escape"
```

…or target a specific field (focuses it first):

```yaml
- press_key: { field: "#search", key: "Enter" }
```

Useful for forms that submit on Enter rather than via a button click.

### `hover` — hover an element

```yaml
- hover: ".tooltip-target"
```

Triggers `:hover` styles and any JS-bound `mouseenter` handlers.  Pair
with `wait_for_selector` to test menus / tooltips that appear on hover.

### `wait_for_function` — wait for arbitrary JS to return truthy

```yaml
- wait_for_function: "() => window.dataLoaded === true"
- wait_for_function: "() => document.querySelectorAll('.row').length > 10"
```

For SPAs that don't expose deterministic DOM signals — when neither
`wait_for_selector` nor `wait_for_url` will do, drop into a small
predicate evaluated in the page context.

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

### Screenshot diff (visual regression detection)

When a browser test produces a screenshot and a previous screenshot exists for the same app/environment/test, the harness computes a pixel-level diff. If the fraction of changed pixels exceeds `browser.screenshot_diff_threshold`, the test is marked `fail` and a detail step is added to the step log.

Configure the threshold in `config.yaml`:

```yaml
browser:
  screenshot_diff_threshold: 0.05   # fail if >5% of pixels changed (default)
```

Set a tighter threshold for pixel-perfect UIs; loosen it for pages with animated content. Set `1.0` to disable diffs entirely.

The diff result appears in the test detail page as a step entry:

```
Screenshot diff: 12.3% pixels changed (threshold 5%)   ✗ fail
```

The diff is skipped gracefully when:
- No previous screenshot exists for the test (first run always passes)
- Pillow is not installed (degrades to no-op, no error)
- Either screenshot file cannot be read

---

## 5. Environment Variables

Any YAML string value that is (or contains) a `$VAR` reference is resolved in
this order:

1. **The secrets store** (`/secrets` in the UI) — the recommended place for
   every credential, token and per-app URL.
2. **Process environment, but only names allowlisted** in `config.yaml` via
   `apps_env_allowlist` (exact names or `fnmatch` globs):

   ```yaml
   # config.yaml
   apps_env_allowlist:
     - MYAPP_BASE_URL
     - "MYAPP_*"
   ```

App definitions can be edited by the `runner` role, so they can **not** read
arbitrary process environment by default — otherwise any app could exfiltrate
server secrets such as `$HARNESS_API_KEY` or cloud credentials. With no
allowlist (the default), app YAML resolves from the secrets store only.
`apps_env_allowlist: ["*"]` (or any wildcard-only pattern) restores the old
"any env var" behaviour and logs a warning; avoid it outside single-user
setups. Keep entries narrow — do **not** use broad prefixes such as
`HARNESS_*`, which also match server credentials like the worker's
`HARNESS_API_KEY`. The harness logs a warning naming any allowlisted variable
that looks like a credential (`*KEY*`, `*SECRET*`, `*PASSWORD*`, `*TOKEN*`).

URLs (`url:` and `environments.*`) must be `http://` or `https://`. A
whole-value `$VAR` URL is accepted when the app is saved and checked again
after resolution, right before each run or dispatch; a non-http(s) value
fails the run with a setup error.

If a referenced variable is neither a secret nor allowlisted, the run fails
with a `ConfigError` that says to add it as a secret or list it in
`apps_env_allowlist`. At startup the harness logs a warning for every `$VAR`
that exists in the process env but is no longer visible to app YAML, to help
with migration.

`config.yaml` itself is unaffected: its `$VAR` references (webhook URLs, SMTP
passwords, ...) still resolve from the full process environment.

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

Create both secrets in the secrets store (or allowlist both names if you keep
them in the environment):

```
SONARR_PASSWORD          = global-password
SONARR_PASSWORD#staging  = staging-only-password
```

If neither `$VAR#env` nor `$VAR` resolves, the run fails with a `ConfigError`.

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

Store these as secrets (`/secrets`). If you prefer to keep some of them in a
`.env` file or the shell/CI environment, list them in `apps_env_allowlist`:

```
# .env
MYAPP_BASE_URL=https://myapp.example.com
MYAPP_USERNAME=testuser
MYAPP_PASSWORD=supersecret
```

```yaml
# config.yaml
apps_env_allowlist: ["MYAPP_*"]
```

---

## 6. Python Scripts

> **Status: Not yet implemented.**
>
> The Python-script test type (subclassing `AppTest` from the `harness`
> package) is reserved for a future release. The class exists in
> `harness/__init__.py` as a stable public surface, but the harness
> loader does not currently discover or execute `AppTest` subclasses —
> defining one will not produce any test results yet.
>
> For now, use the YAML test types — `availability`, `api`, and
> `browser` — documented above. The `api` type with
> [variable extraction & chaining](#variable-extraction--chaining) can
> express most multi-step flows that would otherwise need a script.
>
> This section will be expanded with full documentation and examples
> once the runner support lands.

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
