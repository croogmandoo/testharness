# Web Testing Harness System Overview & Architectural Blueprint

**Web Testing Harness (`testharness`)** is an enterprise-grade, self-hosted synthetic monitoring and automated testing engine. It combines lightweight HTTP endpoint polling, complex multi-step REST/JSON API contract tests with variable chaining, and end-to-end browser journeys via Playwright.

---

## 1. System Architecture

```mermaid
graph TD
    Client["Browser / Dashboard / Status Page"] --> Gateway["FastAPI Web Layer (web/main.py :9552)"]

    subgraph Web & Admin Tier
        Gateway --> AuthMiddleware["Auth & Session Guard (web/auth.py)"]
        Gateway --> APIRoutes["REST API Routes (web/routes/api.py)"]
        Gateway --> DashboardRoutes["Dashboard & Status UI (web/routes/dashboard.py)"]
        Gateway --> AdminRoutes["Admin, Users & CA Certs (web/routes/admin_*.py)"]
    end

    subgraph Test Execution Engine
        Scheduler["APScheduler Engine (harness/scheduler.py)"] --> Runner["Test Runner Orchestrator (harness/runner.py)"]
        Gateway -.->|On-Demand Run| Runner
        
        Runner --> HTTPEngine["HTTP Availability Engine (harness/http_engine.py)"]
        Runner --> APIEngine["API & JSON Assertions (harness/api_engine.py)"]
        Runner --> BrowserEngine["Playwright Headless Browser (harness/browser.py)"]
    end

    subgraph Reliability & Alerts
        Runner --> AlertManager["Incident Alerts (harness/alerts.py)"]
        AlertManager --> Channels["Teams / Slack / Discord / Email / Webhooks"]
        Runner --> Metrics["Prometheus Metrics (/metrics)"]
    end

    subgraph Storage & Security Layer
        Runner --> SQLite[("SQLite WAL: data/harness.db")]
        AuthMiddleware --> AuthDB[("SQLite Auth: data/auth.db")]
        Runner --> SecretsStore["Encrypted Secrets (data/secret.key)"]
        BrowserEngine --> Screenshots["Visual Diff & Artifacts (data/screenshots/)"]
        Runner --> CACerts["Custom CA Store (data/ca-certs/)"]
    end
```

---

## 2. Component Directory Map

```text
testharness/
├── harness/                      # Core test execution engine
│   ├── runner.py                 # Central orchestrator: loads tests, executes engines, persists results
│   ├── http_engine.py            # Fast async HTTP/HTTPS ping & availability checks
│   ├── api_engine.py             # Multi-step REST API tests, JSON schema extraction & step chaining
│   ├── browser.py                # Playwright automation, screenshot captures & visual diffing
│   ├── scheduler.py              # In-process cron scheduling (APScheduler)
│   ├── alerts.py                 # Webhook, Slack, Teams, Discord & email dispatch
│   ├── db.py                     # SQLite interface for runs, results, and test history (WAL mode)
│   ├── auth_db.py                # SQLite store for users, hashed passwords, roles & session tokens
│   ├── secrets.py                # Symmetric Fernet encryption for credentials and auth headers
│   ├── ca_certs.py               # Custom CA certificates and Chromium SPKI pin injection
│   └── models.py                 # Pydantic data schemas for tests, environments, and results
├── web/                          # FastAPI Web UI and REST API
│   ├── main.py                   # App factory, lifespan lifecycle, middleware & static mounts
│   ├── routes/                   # Route handlers (dashboard, API, auth, apps, runner, admin)
│   ├── static/                   # Dark-themed CSS (Inter / JetBrains Mono) & client scripts
│   └── templates/                # Jinja2 HTML templates for dashboard, run logs, and status pages
├── tests/                        # Pytest suite (790+ passing unit & integration tests)
├── apps/                         # App definition YAMLs (target applications and synthetic steps)
├── data/                         # Local database files, secrets key & screenshots (gitignored)
├── config.yaml                   # Global scheduling, alerting channels & environment definitions
├── docker-compose.yml            # Production container configuration
├── Dockerfile                    # Multi-stage container build (UID 10001 unprivileged)
└── GEMINI.md                     # Root AI developer directives
```

---

## 3. Test Execution Lifecycles

### 3.1 HTTP Availability Check
1. Runner schedules execution via cron or receives on-demand trigger.
2. `http_engine.py` opens `httpx.AsyncClient` with custom timeout and optional TLS verification settings.
3. Evaluates status code, response time, TLS certificate expiration, and substring assertions.
4. Records duration, status (`pass` / `fail`), and error trace to SQLite.
5. If consecutive failures exceed threshold, `alerts.py` dispatches notifications to configured channels.

### 3.2 Multi-Step API Chaining Check
1. Executes sequential HTTP requests (e.g. `POST /login` -> extract `access_token`).
2. Regex / JSONPath extracts variables into ephemeral execution context.
3. Injects variables into downstream headers (`Authorization: Bearer {{access_token}}`) or request JSON bodies.
4. Performs assertions against JSON schemas, response headers, and latency SLOs.

### 3.3 Playwright Browser Journey
1. Spawns isolated Chromium browser context with configured viewport, user agent, and optional custom CA bundle (`--ignore-certificate-errors-spki-list`).
2. Traverses test steps: click, type, wait for selector, evaluate JavaScript.
3. Captures full-page screenshots and compares with baseline images via Pillow diff.
4. Records network logs, console errors, and step execution timings.
