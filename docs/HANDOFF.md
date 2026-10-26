# Web Testing Harness Session Handoff & Operational Runbook

**Generated:** 2026-10-26T18:50:00-04:00  
**Branch:** `master` (synchronized with `origin/master`)  
**Workspace:** `/home/craig/orca/workspaces/testharness/main`  
**Test Suite:** **868 / 868 tests passing** (100% green, ~41s)

---

## 1. System Snapshot & Live State

- **Application Port**: `http://localhost:9552` (Docker default) or local development via `uvicorn web.main:create_app --factory --port 9552`
- **Environment**: Python 3.12.15 in local `.venv` (managed via `uv`)
- **Database Engine**: SQLite 3 (WAL mode)
  - Test / Metrics DB: `data/harness.db`
  - Authentication DB: `data/auth.db`
- **Secrets Key**: `data/secret.key` (Fernet 32-byte key, enforced `0600` permissions)
- **Primary Dependencies**: FastAPI, Uvicorn, Playwright, SQLite3, Jinja2, Pytest, Pillow, ReportLab, Cryptography, APScheduler
- **GitHub Task Tracking**: Issues #8 through #21 closed and verified on `croogmandoo/testharness`.
- **Linear Project**: `Web Testing Harness (testharness)` on `Croogmando-homelab` (`CRO-47`).
- **Observability Assets**: Prometheus alerting rules in [deploy/prometheus/alerts.yml](file:///home/craig/orca/workspaces/testharness/main/deploy/prometheus/alerts.yml) and Grafana dashboard in [deploy/grafana/dashboard.json](file:///home/craig/orca/workspaces/testharness/main/deploy/grafana/dashboard.json).

---

## 2. Recent Work & Hardening Accomplishments

1. **Prometheus Alerting Rules & Grafana Dashboard**:
   - Added [deploy/prometheus/alerts.yml](file:///home/craig/orca/workspaces/testharness/main/deploy/prometheus/alerts.yml) with production Prometheus alerts (`HarnessTestFailing`, `HarnessHighFailureRate`, `HarnessAppStale`, `HarnessSlowTestExecution`, `HarnessRunnerHeartbeatMissing`).
   - Added [deploy/grafana/dashboard.json](file:///home/craig/orca/workspaces/testharness/main/deploy/grafana/dashboard.json) with fleet health stats, test duration percentiles, app status timeline matrix, run volume bar charts, and staleness monitoring.
   - Added `harness_runner_last_seen_timestamp_seconds` heartbeat gauge to [web/routes/metrics.py](file:///home/craig/orca/workspaces/testharness/main/web/routes/metrics.py), tested in [tests/test_web_metrics.py](file:///home/craig/orca/workspaces/testharness/main/tests/test_web_metrics.py).

2. **Multi-Environment Selector & Persistent Cookie Filtering (Issue #20 - CLOSED)**:
   - Added interactive environment selector chips (`.env-chips`, `.env-chip`) to the dashboard header in [web/templates/dashboard.html](file:///home/craig/orca/workspaces/testharness/main/web/templates/dashboard.html) and breadcrumb bar in [web/templates/detail.html](file:///home/craig/orca/workspaces/testharness/main/web/templates/detail.html).
   - Added persistent cookie resolution (`selected_environment`) in [web/routes/dashboard.py](file:///home/craig/orca/workspaces/testharness/main/web/routes/dashboard.py), persisting chosen environments across dashboard views and app details.
   - Tested in [tests/test_web_environment_filter.py](file:///home/craig/orca/workspaces/testharness/main/tests/test_web_environment_filter.py).

3. **Custom Webhook Payload Templating & Incident Webhook Dispatch (Issue #21 - CLOSED)**:
   - Added Jinja2 template rendering (`_render_webhook_payload`) in [harness/alerts.py](file:///home/craig/orca/workspaces/testharness/main/harness/alerts.py) for outbound run webhooks and incident alerts.
   - Added pre-packaged formats for PagerDuty Events API v2 (`format: "pagerduty"`) and Opsgenie Alerts API (`format: "opsgenie"`).
   - Added support for custom headers (`headers: {...}`) and HMAC-SHA256 payload signing (`X-Harness-Signature`).
   - Enabled incident alert webhooks in `dispatch_alerts()` supporting both single webhook dicts and lists of webhook endpoints.
   - Tested in [tests/test_alerts_templating.py](file:///home/craig/orca/workspaces/testharness/main/tests/test_alerts_templating.py).

4. **TLS Certificate Expiration & Security Headers Prober (Issue #17 - CLOSED)**:
   - Added [harness/tls_engine.py](file:///home/craig/orca/workspaces/testharness/main/harness/tls_engine.py) implementing `run_tls_test` and `inspect_cert_sync` using Python `ssl` and `socket`.
   - Validates TLS certificate expiration date against configurable threshold (`min_days_valid`, default 30 days).
   - Verifies Subject Alternative Names (SANs) match the target hostname and checks negotiated TLS protocol against `min_tls_version` (e.g. `TLSv1.2` or `TLSv1.3`).
   - Checks security headers (`Strict-Transport-Security`, `X-Content-Type-Options`, `Content-Security-Policy`) over HTTPS responses.
   - Tested in [tests/test_tls_engine.py](file:///home/craig/orca/workspaces/testharness/main/tests/test_tls_engine.py).

5. **Visual Regression Diff Viewer & Baseline Approval Workflow (Issue #18 - CLOSED)**:
   - Added `generate_diff_image` in [harness/screenshot_diff.py](file:///home/craig/orca/workspaces/testharness/main/harness/screenshot_diff.py) generating a high-contrast magenta (`#FF007F`) visual heatmap overlay on a dimmed grayscale background.
   - Added `POST /api/visual/approve` endpoint in [web/routes/api.py](file:///home/craig/orca/workspaces/testharness/main/web/routes/api.py) requiring `admin` or `runner` role.
   - Added "Approve as New Baseline" UI button in [web/templates/detail.html](file:///home/craig/orca/workspaces/testharness/main/web/templates/detail.html).
   - Tested in [tests/test_visual_approval.py](file:///home/craig/orca/workspaces/testharness/main/tests/test_visual_approval.py).

6. **Core Web Vitals & Frontend Performance Timing Metrics (Issue #19 - CLOSED)**:
   - Added `extract_web_vitals` in [harness/browser.py](file:///home/craig/orca/workspaces/testharness/main/harness/browser.py) querying the Navigation Timing API and PerformanceObserver (TTFB, DOMContentLoaded, Load Time, FCP, LCP, CLS).
   - Tested in [tests/test_browser_performance.py](file:///home/craig/orca/workspaces/testharness/main/tests/test_browser_performance.py).

7. **Test Suite Milestone**:
   - Reached **868 passing unit and integration tests** (100% green, 0 failures, 0 regressions).

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
2. **[Scheduled Maintenance Window Recurrence]**:
   - Add recurring maintenance window schedules (e.g. weekly Sunday 02:00-04:00 UTC) in addition to one-off datetime windows.
3. **[Optional Exploration: Windows-Level Control & Desktop Automation (Linear CRO-47)]**:
   - Evaluate hybrid browser + desktop automation (`rpaframework`, `pywinauto`) for Windows-hosted remote workers testing native desktop clients and OS dialogs.

---

## 5. Kickoff Prompt for Next Session

```text
I am resuming work on Web Testing Harness (testharness).
Please read docs/HANDOFF.md, docs/OVERVIEW.md, and docs/STANDARDS.md to understand the current architecture and state.
All 867 tests are passing on master in .venv/bin/pytest. All GitHub issues (#8-#21) are closed.
Our next priority is to review the new prioritized roadmap items in Section 4.
```
