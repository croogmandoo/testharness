# Web Testing Harness Session Handoff & Operational Runbook

**Generated:** 2026-10-26T17:40:00-04:00  
**Branch:** `master` (synchronized with `origin/master`)  
**Commit:** `d7da698`  
**Workspace:** `/home/craig/orca/workspaces/testharness/main`  
**Test Suite:** **853 / 853 tests passing** (100% green, ~40s)

---

## 1. System Snapshot & Live State

- **Application Port**: `http://localhost:9552` (Docker default) or local development via `uvicorn web.main:create_app --factory --port 9552`
- **Environment**: Python 3.12.15 in local `.venv` (managed via `uv`)
- **Database Engine**: SQLite 3 (WAL mode)
  - Test / Metrics DB: `data/harness.db`
  - Authentication DB: `data/auth.db`
- **Secrets Key**: `data/secret.key` (Fernet 32-byte key, enforced `0600` permissions)
- **Primary Dependencies**: FastAPI, Uvicorn, Playwright, SQLite3, Jinja2, Pytest, Pillow, ReportLab, Cryptography, APScheduler
- **GitHub Task Tracking**: All 11 GitHub issues (#8, #9, #10, #11, #12, #14, #15, #16, #17, #18, #19) closed and verified on `croogmandoo/testharness`.

---

## 2. Recent Work & Hardening Accomplishments

1. **TLS Certificate Expiration & Security Headers Prober (Issue #17 - CLOSED)**:
   - Added [harness/tls_engine.py](file:///home/craig/orca/workspaces/testharness/main/harness/tls_engine.py) implementing `run_tls_test` and `inspect_cert_sync` using Python `ssl` and `socket`.
   - Validates TLS certificate expiration date against configurable threshold (`min_days_valid`, default 30 days).
   - Verifies Subject Alternative Names (SANs) match the target hostname and checks negotiated TLS protocol against `min_tls_version` (e.g. `TLSv1.2` or `TLSv1.3`).
   - Checks security headers (`Strict-Transport-Security`, `X-Content-Type-Options`, `Content-Security-Policy`) over HTTPS responses.
   - Integrated `type: tls` / `type: certificate` / `type: ssl` in [harness/runner.py](file:///home/craig/orca/workspaces/testharness/main/harness/runner.py).
   - Verified with 7 unit and mock tests in [tests/test_tls_engine.py](file:///home/craig/orca/workspaces/testharness/main/tests/test_tls_engine.py).

2. **Visual Regression Diff Viewer & Baseline Approval Workflow (Issue #18 - CLOSED)**:
   - Added `generate_diff_image` in [harness/screenshot_diff.py](file:///home/craig/orca/workspaces/testharness/main/harness/screenshot_diff.py) generating a high-contrast magenta (`#FF007F`) visual heatmap overlay on a dimmed grayscale background.
   - Integrated visual diff generation in [harness/runner.py](file:///home/craig/orca/workspaces/testharness/main/harness/runner.py), automatically attaching `<slug>-diff.png` to `StepResult.screenshot`.
   - Added `POST /api/visual/approve` endpoint in [web/routes/api.py](file:///home/craig/orca/workspaces/testharness/main/web/routes/api.py) requiring `admin` or `runner` role, marking test result `pass`, upserting `app_state` to `passing`, and recording `visual.baseline_approve` in the audit log.
   - Updated `db.get_last_screenshot` in [harness/db/_app_state.py](file:///home/craig/orca/workspaces/testharness/main/harness/db/_app_state.py) with `AND status='pass'` so regressions are not compared against broken runs.
   - Added "Approve as New Baseline" UI button and async handler in [web/templates/detail.html](file:///home/craig/orca/workspaces/testharness/main/web/templates/detail.html).
   - Verified with 9 unit and integration tests in [tests/test_visual_approval.py](file:///home/craig/orca/workspaces/testharness/main/tests/test_visual_approval.py).

3. **Core Web Vitals & Frontend Performance Timing Metrics (Issue #19 - CLOSED)**:
   - Added `extract_web_vitals` in [harness/browser.py](file:///home/craig/orca/workspaces/testharness/main/harness/browser.py) querying the Navigation Timing API and PerformanceObserver paint/layout-shift records (TTFB, DOMContentLoaded, Load Time, FCP, LCP, CLS).
   - Added `evaluate_performance_thresholds` supporting assertions on `max_lcp_ms`, `max_fcp_ms`, `max_ttfb_ms`, `max_cls`, and `max_load_ms`.
   - Supported both step-level assertions (`assert_performance`, `performance_metrics`, `capture_web_vitals`) and top-level assertions in `test_def`.
   - Formatted performance summary into step logs for display across run details and dashboard views.
   - Verified with 9 unit and mock browser tests in [tests/test_browser_performance.py](file:///home/craig/orca/workspaces/testharness/main/tests/test_browser_performance.py).

4. **Web Proxy Middleware & Settings (Issues #14, #15, #16 - CLOSED)**:
   - Added reverse proxy ASGI middleware [web/proxy_middleware.py](file:///home/craig/orca/workspaces/testharness/main/web/proxy_middleware.py) with CIDR parsing and secure cookie propagation.
   - Added outbound forward proxy client configuration [harness/proxy.py](file:///home/craig/orca/workspaces/testharness/main/harness/proxy.py).
   - Added `/admin/proxy` settings, interactive egress testing, and reverse proxy snippets for Caddy, Traefik, Nginx, and Apache in [web/routes/admin_proxy.py](file:///home/craig/orca/workspaces/testharness/main/web/routes/admin_proxy.py).

5. **Test Suite Milestone**:
   - Reached **853 passing unit and integration tests** (100% green, 0 failures, 0 regressions).

---

## 3. Key Architectural Invariants & Gotchas

- **Playwright Fallback**: `harness/browser.py` is written to be cleanly importable even on headless systems without Playwright installed; keep browser imports lazy inside test execution routines.
- **Visual Baseline Invariant**: `get_last_screenshot` only queries `status='pass'` so failures do not contaminate subsequent baseline comparisons unless explicitly approved by an operator or admin.
- **SQLite Concurrency**: Always keep `PRAGMA journal_mode=WAL;` and `PRAGMA busy_timeout=5000;` on connection pools to prevent `database is locked` during concurrent background runs and web requests.
- **Test Isolation**: `conftest.py` provides shared memory databases and test client fixtures. Never let test execution write to or mutate `data/harness.db`.
- **Bind-Mount Ownership & Test Execution**: Running `docker compose up` without `user:` pins will chown `./data` to container UID 10001. If running local pytest afterward as UID 1000, ensure host ownership of `./data` (`0600 data/secret.key`) matches the host user.

---

## 4. Prioritized Next Actions

1. **[Prometheus Alerting Rules & Grafana Dashboards]**:
   - Add standard Prometheus alerting rule templates in `deploy/prometheus/` for elevated failure rates, worker heartbeat drops, and database lock contention.
2. **[Custom Webhook Payload Templating]**:
   - Allow configurable JSON Jinja2 templates for alert dispatch webhooks to support PagerDuty, Opsgenie, and Microsoft Teams natively.
3. **[Scheduled Maintenance Window Recurrence]**:
   - Add recurring maintenance window schedules (e.g. weekly Sunday 02:00-04:00 UTC) in addition to one-off datetime windows.
4. **[Multi-Environment Dashboard Filtering]**:
   - Add persistent environment selection cookies/filters in the UI header for operators monitoring multi-region deployments.
5. **[Optional Exploration: Windows-Level Control & Desktop Automation (Linear CRO-47)]**:
   - Evaluate hybrid browser + desktop automation (`rpaframework`, `pywinauto`) for Windows-hosted remote workers testing native desktop clients and OS dialogs.

---

## 5. Kickoff Prompt for Next Session

```text
I am resuming work on Web Testing Harness (testharness).
Please read docs/HANDOFF.md, docs/OVERVIEW.md, and docs/STANDARDS.md to understand the current architecture and state.
All 853 tests are passing on master in .venv/bin/pytest. All GitHub issues (#8-#19) are closed.
Our next priority is to review the new prioritized roadmap items in Section 4.
```
