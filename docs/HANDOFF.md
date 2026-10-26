# Web Testing Harness Session Handoff & Operational Runbook

**Generated:** 2026-10-26T17:25:00-04:00  
**Branch:** `master` (synchronized with `origin/master`)  
**Workspace:** `/home/craig/orca/workspaces/testharness/main`  
**Test Suite:** **828 / 828 tests passing** (100% green, ~40s)

---

## 1. System Snapshot & Live State

- **Application Port**: `http://localhost:9552` (Docker default) or local development via `uvicorn web.main:create_app --factory --port 9552`
- **Environment**: Python 3.12.15 in local `.venv` (managed via `uv`)
- **Database Engine**: SQLite 3 (WAL mode)
  - Test / Metrics DB: `data/harness.db`
  - Authentication DB: `data/auth.db`
- **Secrets Key**: `data/secret.key` (Fernet 32-byte key, enforced `0600` permissions)
- **Primary Dependencies**: FastAPI, Uvicorn, Playwright, SQLite3, Jinja2, Pytest, Pillow, ReportLab, Cryptography, APScheduler
- **GitHub Task Tracking**: Initial milestones (#8, #9, #10, #11, #12) and Web Proxy milestones (#14, #15, #16) closed and verified.

---

## 2. Recent Work & Hardening Accomplishments

1. **Reverse Proxy ASGI Middleware (Issue #14 - CLOSED)**:
   - Added [web/proxy_middleware.py](file:///home/craig/orca/workspaces/testharness/main/web/proxy_middleware.py) parsing `X-Forwarded-For`, `X-Forwarded-Proto`, `X-Forwarded-Host`, and `X-Forwarded-Prefix`.
   - Validates client IP against trusted CIDR ranges (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `127.0.0.1/32`, or `*`).
   - Automatically enables `Secure;` cookie attributes on incoming requests terminated with HTTPS.
   - Normalizes `request.scope["root_path"]` for subpath deployments.
   - Comprehensive unit tests in [tests/test_proxy_middleware.py](file:///home/craig/orca/workspaces/testharness/main/tests/test_proxy_middleware.py).
2. **Outbound Corporate Forward Proxy Support (Issue #15 - CLOSED)**:
   - Added [harness/proxy.py](file:///home/craig/orca/workspaces/testharness/main/harness/proxy.py) resolving forward proxy settings from `config.yaml` (`proxy.outbound`) or standard environment variables (`HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`).
   - Propagated proxy settings to Playwright Chromium launch arguments in [harness/browser.py](file:///home/craig/orca/workspaces/testharness/main/harness/browser.py).
   - Routed HTTP and API tests through configured egress proxies with credential parsing in [harness/api.py](file:///home/craig/orca/workspaces/testharness/main/harness/api.py).
   - Unit tests in [tests/test_harness_proxy.py](file:///home/craig/orca/workspaces/testharness/main/tests/test_harness_proxy.py).
3. **Admin Web Proxy Settings & Diagnostic Screen (Issue #16 - CLOSED)**:
   - Created `/admin/proxy` dashboard in [web/routes/admin_proxy.py](file:///home/craig/orca/workspaces/testharness/main/web/routes/admin_proxy.py) and [web/templates/admin_proxy.html](file:///home/craig/orca/workspaces/testharness/main/web/templates/admin_proxy.html).
   - Live inbound request diagnostics (client IP, connecting hop IP, effective scheme, headers, trusted status).
   - Interactive outbound probe endpoint (`POST /admin/proxy/test`) to test egress connectivity.
   - Dynamic reverse proxy configuration templates (Caddy, Traefik, Nginx, Apache) tailored to detected server host.
   - Added "Proxy & Gateway" card to `/admin` dashboard and command palette (`Ctrl+K`).
   - Route and permission tests in [tests/test_web_admin_proxy.py](file:///home/craig/orca/workspaces/testharness/main/tests/test_web_admin_proxy.py).
4. **Test Suite Expansion**:
   - Reached **828 passing unit and integration tests** (100% green, 0 failures, 0 regressions).

---

## 3. Key Architectural Invariants & Gotchas

- **Playwright Fallback**: `harness/browser.py` is written to be cleanly importable even on headless systems without Playwright installed; keep browser imports lazy inside test execution routines.
- **SQLite Concurrency**: Always keep `PRAGMA journal_mode=WAL;` and `PRAGMA busy_timeout=5000;` on connection pools to prevent `database is locked` during concurrent background runs and web requests.
- **Test Isolation**: `conftest.py` provides shared memory databases and test client fixtures. Never let test execution write to or mutate `data/harness.db`.
- **Bind-Mount Ownership & Test Execution**: Running `docker compose up` without `user:` pins will chown `./data` to container UID 10001. If running local pytest afterward as UID 1000, ensure host ownership of `./data` (`0600 data/secret.key`) matches the host user.

---

## 4. Prioritized Next Actions

1. **[Prometheus Metrics Hardening & Alerting Rules]**:
   - Add standard Prometheus alerting rule templates in `docs/guides/` or `deploy/` for high failure rate, worker heartbeat timeouts, and database lock stalls.
2. **[Custom Webhook Payload Templating]**:
   - Allow customizable JSON templates for alert dispatch webhooks to support custom webhook consumers and alerting platforms.
3. **[Scheduled Maintenance Window UI Enhancements]**:
   - Add recurring maintenance window schedules (e.g. weekly cron maintenance window) in addition to one-off datetime windows.
4. **[Multi-Environment Dashboard Filters]**:
   - Add persistent environment selection cookies/filters to the admin dashboard views for teams monitoring dozens of staging/production instances.

---

## 5. Kickoff Prompt for Next Session

```text
I am resuming work on Web Testing Harness (testharness).
Please read docs/HANDOFF.md, docs/OVERVIEW.md, and docs/STANDARDS.md to understand the current architecture and state.
All 808 tests are passing on master in .venv/bin/pytest. All milestone GitHub issues (#8-#12) are closed.
Our next priority is to review the new prioritized roadmap items in Section 4.
```
