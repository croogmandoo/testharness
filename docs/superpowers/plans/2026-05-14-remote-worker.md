# Remote Worker (Edge Runner) — Design Plan

**Date:** 2026-05-14
**Status:** ✅ Phase 1 deployed (2026-05-13). v1 supports both Docker and
native-Linux/systemd worker targets; admin UI live at `/admin/runners`.
Phases 2-5 remain as roadmap.

## Problem

Today, every check runs from the host where the harness is deployed.
That works fine if every monitored app is reachable from one network
location, but real environments have:

- **Network ACLs** — production apps reachable only from specific
  jump hosts / management subnets
- **Air-gapped segments** — DMZ, OT networks, customer VPCs
- **Geographic latency** — "is the app slow from EU?" needs an EU vantage
- **Tenant isolation** — checks for customer A's private endpoints must
  run from customer A's network, not the central harness

The fix is a **remote-worker model**: keep the harness's UI, results
storage, alerting, and dashboard as a central control plane; ship a
small worker binary/container that runs in each restricted zone, pulls
the apps assigned to it, runs the checks locally, and posts results back.

## Non-goals

- Replacing the central harness's own runner (it stays the default runner
  for apps with no zone assignment)
- Worker-to-worker mesh / federation
- Full multi-tenancy / per-customer dashboards
- Worker auto-update / orchestration plane

## Architecture

```
┌───────────────────────────────────────────────────────────────────┐
│ Central harness (this codebase)                                   │
│ - SQLite DB (runs, test_results, app_state, audit_log, ... )      │
│ - Web UI + REST API + alerting + analytics export                 │
│ - Default in-process runner (apps with no zone tag)               │
│                                                                   │
│ NEW: /api/runner/* endpoints                                      │
│ NEW: harness/runner_dispatch.py                                   │
│ NEW: dashboard panel /admin/runners                               │
└──────▲────────────────────────────────────────────────────────────┘
       │ HTTPS  /  Bearer hth_…  (worker API key)
       │
       │ poll for jobs   ─────►  /api/runner/poll
       │ stream results  ─────►  /api/runner/results
       │ heartbeat       ─────►  /api/runner/heartbeat
       │
   ┌───┴────────┐    ┌────────────┐    ┌────────────┐
   │  worker A  │    │  worker B  │    │  worker C  │
   │  DMZ       │    │  EU region │    │  customer  │
   └────────────┘    └────────────┘    └────────────┘

Each worker:
   - thin Docker container (or systemd-managed binary)
   - reuses harness.{runner,api,browser,ssl_context} verbatim — same
     Python codebase, just driven by the worker loop instead of the
     in-process scheduler
   - stateless across restarts (only state = the API key + worker name)
```

## App YAML — zone assignment

Apps gain an optional `run_on:` field at the top level:

```yaml
app: "Customer Portal — eu"
run_on: eu-worker            # name OR tag of a worker
environments:
  production: https://...
tests:
  - ...
```

`run_on` can be:

- **Worker name** — `run_on: eu-worker-1` — pins this app to that worker
- **Worker tag** — `run_on: { tag: media }` — any worker tagged `media`
  can pick it up (load-balanced)
- Absent — runs on the central in-process runner (today's behavior)

The central scheduler reads `run_on` and skips the in-process run for
remote-assigned apps; the worker dispatch protocol delivers them instead.

## Worker registration & auth

- Each worker has a long-lived **API key** (existing `hth_…` mechanism,
  no new auth code). Admins generate one per worker via `/api-keys`.
- Worker `POST /api/runner/register` once on startup with:
  ```json
  {
    "name": "eu-worker-1",
    "tags": ["eu", "media"],
    "version": "1.0.0",
    "capabilities": ["api", "availability", "browser"]
  }
  ```
- Server inserts/upserts a `runners` row, returns a worker config
  including current heartbeat interval, poll URL, etc.
- Subsequent calls authenticate with the same `hth_…` Bearer.

## Polling protocol (simple, robust)

We use **long-poll, not websocket** — survives transparent proxies, plays
well with cloud-load-balancer idle timeouts, requires no special infra:

1. Worker hits `POST /api/runner/poll`:
   ```json
   {
     "name": "eu-worker-1",
     "max_wait_seconds": 25,
     "capacity": 4
   }
   ```
2. Server holds the connection up to `max_wait_seconds`:
   - returns 200 + 0..N job descriptors as soon as any are queued
   - returns 200 + empty list on timeout
3. Worker receives `[{run_id, app_def, environment, triggered_by}, ...]`
4. Worker runs each job locally using `harness.runner.run_app` with a
   stub DB that captures results instead of persisting
5. Worker `POST /api/runner/results` with the structured result
6. Server inserts `runs` + `test_results` + `app_state`, dispatches
   alerts, fires the run webhook if configured — exactly the same code
   path as an in-process completion

When the central harness's scheduler fires for a remote-assigned app:

- Instead of `run_app(...)` inline, it `INSERT INTO runs ... status='queued'`
- The next poll from a matching worker picks it up

A small `harness/runner_dispatch.py` module owns the queue-row state
machine (`queued` → `assigned` → `running` → `complete` / `timeout`).

## Endpoints (new under /api/runner/)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/runner/register` | Bearer | Upsert runner row; return config |
| POST | `/api/runner/poll` | Bearer | Long-poll for next jobs (up to 30s) |
| POST | `/api/runner/results` | Bearer | Submit completed run results |
| POST | `/api/runner/heartbeat` | Bearer | Liveness ping (every 30s) |
| GET | `/api/runner/manifest` | Bearer | Pull current set of apps assigned to me (for warm-start / failover) |

All POSTs require `_csrf=Depends(verify_csrf)`-equivalent skipped (Bearer auth
already CSRF-immune; existing /api/ exemption applies).

## New DB tables

```sql
CREATE TABLE runners (
    id            TEXT PRIMARY KEY,
    name          TEXT UNIQUE NOT NULL,
    tags          TEXT NOT NULL,            -- JSON array
    capabilities  TEXT NOT NULL,            -- JSON array
    version       TEXT,
    api_key_id    TEXT NOT NULL REFERENCES api_keys(id),
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT,
    status        TEXT NOT NULL DEFAULT 'unknown'  -- online/offline/draining
);

CREATE TABLE runner_jobs (
    id            TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL REFERENCES runs(id),
    runner_name   TEXT,                     -- assigned worker (null = unassigned)
    app_def_json  TEXT NOT NULL,            -- frozen at queue time
    environment   TEXT NOT NULL,
    state         TEXT NOT NULL,            -- queued|assigned|running|complete|timeout|cancelled
    queued_at     TEXT NOT NULL,
    assigned_at   TEXT,
    completed_at  TEXT,
    error         TEXT
);
CREATE INDEX idx_runner_jobs_dispatch ON runner_jobs(state, runner_name);
```

The `app_def_json` snapshot means the worker runs exactly what was queued
even if the YAML changes between queue and execution.

## Worker codebase

```
worker/
├── Dockerfile           # FROM the harness image (reuses Playwright); switches CMD
├── worker.py            # main loop: register → poll → run → submit
├── README.md            # operator deployment guide
└── systemd/
    └── harness-worker.service   # for non-Docker deployments
```

`worker.py` is small (~200 LOC) because it reuses every existing piece:

```python
async def main():
    cfg = load_worker_config()                          # env vars
    async with httpx.AsyncClient() as client:
        await register(client, cfg)
        asyncio.create_task(heartbeat_loop(client, cfg))
        while True:
            jobs = await poll(client, cfg)
            for job in jobs:
                result = await run_job_locally(job)     # harness.runner.run_app w/ stub DB
                await submit_results(client, cfg, result)
```

Worker config is env-var only — no config file to manage:

```
HARNESS_URL=https://harness.example.com
HARNESS_API_KEY=hth_...
HARNESS_WORKER_NAME=eu-worker-1
HARNESS_WORKER_TAGS=eu,media           # comma-separated
HARNESS_POLL_INTERVAL=30               # seconds; max long-poll wait
HARNESS_CAPACITY=4                     # max concurrent jobs
HARNESS_VERIFY_TLS=true                # for self-signed central
```

Worker image is roughly the same size as the harness image (~1.5 GB)
because Playwright/Chromium is the bulk of it. Workers that only run
`api` / `availability` checks can use a slim variant — separate Dockerfile
without the Playwright layer (~200 MB).

## /admin/runners — new admin panel

A new admin-only page showing:

- All registered runners with `name`, `tags`, `version`, `last_seen_at`,
  `status`, jobs in flight
- A "drain" button per runner (set `status='draining'`; dispatch stops
  assigning new jobs; existing jobs finish)
- A "revoke" button (revoke the underlying API key)

Backed by `GET /admin/runners` (HTML) + `POST /admin/runners/{name}/drain`
+ `POST /admin/runners/{name}/revoke`. Audit-logged.

## Failure / timeout handling

- **Worker crashes mid-job:** the job row stays in `assigned` state. A
  central reaper runs every minute; any `runner_jobs` row in `assigned`
  state older than `app.timeout_ms * 2 + grace` flips to `timeout` and
  re-queues (if `retry > 0`) or marks the run as `error`.
- **Worker disconnects (poll never returns):** heartbeat absence ≥ 90s
  flips runner `status` to `offline`, drains its `assigned` jobs back
  to `queued` for redistribution.
- **Worker returns malformed results:** central rejects with 422; worker
  surfaces in its own logs; the job is marked `error` and the runner
  loses one strike. Three strikes within an hour → automatic drain.

## Security

- Bearer API key authentication — leverages existing key infrastructure
- Workers can be **scoped** to specific tags / app patterns at API-key
  generation time (future enhancement; v1 uses one key per worker with
  full scope)
- Workers see the **resolved app definition** including any `$VAR`
  substitutions — that means **secrets reach the worker**. Document
  that workers are trust boundaries: don't deploy them in less-trusted
  segments than the secrets they need. Future enhancement: per-tag
  secret scoping so workers only receive secrets relevant to their apps.
- All `/api/runner/*` traffic must be over HTTPS in production. Document
  the reverse-proxy setup in `docs/guides/deployment.md`.
- mTLS optional — operator pins the worker's TLS cert at registration
  and the central rejects polls with mismatched cert thumbprints. v2.

## Implementation phases (rough effort estimates)

| Phase | Scope | Effort |
|---|---|---|
| **1** | DB tables + `/api/runner/register|poll|results|heartbeat` + minimal `worker.py` + Dockerfile | 1 week |
| **2** | App YAML `run_on:` field + dispatcher that queues remote-assigned runs | 3 days |
| **3** | `/admin/runners` UI + drain/revoke + reaper for stuck jobs | 3 days |
| **4** | Failover, retries, timeout handling, observability metrics | 1 week |
| **5** | Slim worker image (no Playwright), mTLS option, scoped API keys | 1 week |

Total ~3-4 weeks for production-quality v1. Phase 1 alone gives you a
working remote worker for the most common case (manual `run_on:` tags,
no failover sophistication).

## Open design questions

1. **Should the worker keep an offline buffer?** If the worker can't
   reach the central harness, do completed results queue locally and
   replay on reconnect? Or do we just count the loss?
   *Proposal: yes, in-memory buffer of last 100 results with replay on
   reconnect; drop oldest on overflow.*

2. **Should app YAML changes propagate to in-flight workers?** A worker
   polled at 10:00 and got 5 jobs; the user edits one of the apps at
   10:05; the worker submits results at 10:10. The result reflects the
   old app def.
   *Proposal: yes — that's the point of `app_def_json` snapshot. The
   result corresponds to what was queued, not what's current.*

3. **Cross-worker dedup for tagged apps:** if 3 workers all carry tag
   `media` and an app is `run_on: { tag: media }`, do all 3 run it or
   just one?
   *Proposal: one. The first poll wins; the central marks the job
   `assigned` to that worker. If exactly-once-per-region is wanted,
   pin to a specific worker name instead of a tag.*

## Operator install (target UX)

```bash
# On a host inside the restricted network zone:
docker run -d \
  --name harness-worker \
  --restart unless-stopped \
  -e HARNESS_URL=https://harness.example.com \
  -e HARNESS_API_KEY=hth_xxx \
  -e HARNESS_WORKER_NAME=eu-worker-1 \
  -e HARNESS_WORKER_TAGS=eu,media \
  ghcr.io/croogmandoo/testharness-worker:latest
```

…and the central harness's `/admin/runners` page shows it online within 30s.

Air-gapped sites can pull the worker image via the same `docker save`
flow as the appliance.

## Comparison to commercial offerings

| | This design | Datadog Synthetics Private Locations | Pingdom Private Probes | Catchpoint Nodes |
|---|---|---|---|---|
| Self-hosted central | ✅ | ❌ (SaaS only) | ❌ | ❌ |
| Worker open-source | ✅ | ❌ (binary blob) | ❌ | ❌ |
| Browser checks | ✅ (Playwright) | ✅ | partial | ✅ |
| Auth model | API key (extensible to mTLS) | API key | Allowlisted IPs | Cert-based |
| Air-gapped | ✅ | ❌ | ❌ | ❌ |

The closest open-source comparable is **Uptime Kuma** (no remote workers)
and **Cabot** (deprecated). This puts the harness in a niche that's
currently underserved.

---

## Ready to implement?

The plan is concrete enough that Phase 1 is mechanical work:

1. Migrations for `runners` + `runner_jobs` tables
2. `harness/runner_dispatch.py` (queue state machine)
3. `web/routes/runner.py` (5 endpoints)
4. `worker/worker.py` + Dockerfile
5. Update `harness/loader.py` to recognize `run_on:` and forward to dispatcher

Ping me when you're ready and I'll scope it as a follow-up PR.
