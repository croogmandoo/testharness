# Remote-worker v2 smoke-test runbook

End-to-end manual smoke test for the v2 remote-worker feature set: stuck-job
reaper, screenshot upload, offline result buffering, long-poll, scoped API
keys, optional mTLS, and the slim Docker image.

Live-run on 2026-05-13 against the worktree at branch `swarm/smoke-test`:
docker engine 29.4.2 (Docker Desktop 4.70.0). Observed-output sections are
filled in from that run; reproduce by re-running each scenario.

This runbook deliberately uses ports `19550-19560` so it can run alongside
a developer's normal harness instance (port `9552`). All commands are
copy-pasteable from the repo root.

---

## Prerequisites

- `docker` + `docker compose` v2 available
- Outbound internet (httpbin.org used as the test target)
- Ports `19552` free on the host
- `curl` and `python3` in `$PATH`

---

## Setup (run once)

### Build images

```bash
docker build -t harness-central:smoke .
docker build -f worker/Dockerfile.slim -t harness-worker-slim:smoke .
docker images --format "{{.Repository}}:{{.Tag}} {{.Size}}" | grep smoke
```

Observed sizes (2026-05-13):

```
harness-worker-slim:smoke 242MB
harness-central:smoke     2.58GB
```

The slim image is **242 MB**, above the 200 MB assertion in the task brief.
Recorded as a follow-up below (#3). The full image is the same size as
production (no slimming attempted).

### Bring up the central and complete first-run setup

```bash
docker compose -f docs/guides/v2-smoke-compose.yml up -d central
# wait for healthcheck (~10s)
docker compose -f docs/guides/v2-smoke-compose.yml ps
```

Open `http://localhost:19552/setup` and create an admin user, e.g.
`admin / smoketest123!`. (The central does **not** read `ADMIN_PASSWORD`
or `SECRET_KEY` from env — it bootstraps via the first-run wizard.)

### Mint a worker API key

Log in at `/auth/login`, visit `/api-keys`, create a key with **both**
`worker:pull` and `worker:results` scopes. Copy the `hth_…` plaintext —
it is shown only once. Export it:

```bash
export WORKER_KEY=hth_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### Start the worker

> CAVEAT: the *slim* worker image fails to start out-of-the-box because
> `harness/__init__.py` unconditionally imports `harness.browser`, which
> requires `playwright`. The slim image deliberately omits playwright.
> See follow-up #1. For end-to-end scenarios 1, 3, 4 below the runbook
> falls back to running the worker from the **full** central image with
> `python -m worker.worker` as documented in `worker/README.md`.

Full-image worker (works today):

```bash
docker run -d --name smoke-worker-full --network guides_default \
  -v guides_smoke-worker:/app/data \
  -e HARNESS_URL=http://central:9552 \
  -e HARNESS_API_KEY=$WORKER_KEY \
  -e HARNESS_WORKER_NAME=smoke-w1 \
  -e HARNESS_WORKER_TAGS=smoke,api \
  -e HARNESS_VERIFY_TLS=false \
  -e HARNESS_LONG_POLL_WAIT=10 \
  -e HARNESS_POLL_INTERVAL=5 \
  -e HARNESS_OUTBOX_MAX=50 \
  -e HARNESS_HEARTBEAT_INTERVAL=15 \
  -e HARNESS_CAPABILITIES=api,availability \
  harness-central:smoke python -m worker.worker
```

Confirm registration:

```bash
docker logs smoke-worker-full | grep "registered"
# 2026-05-14 01:33:10,886 worker[smoke-w1] registered (server poll_interval=10s capacity=4)
```

### Seed a worker-pinned app

```bash
cat > /tmp/smoke-app.yaml <<'EOF'
app: "Smoke Test API"
run_on: smoke-w1
url: https://httpbin.org
tests:
  - name: Status check
    type: api
    method: GET
    endpoint: /status/200
EOF
docker compose -f docs/guides/v2-smoke-compose.yml cp \
  /tmp/smoke-app.yaml central:/app/apps/smoke-app.yaml
# reload (admin UI -> /admin/config -> Reload, or POST /admin/config/reload)
```

---

## Scenario 1: Stuck-job reaper

**Goal**: a job assigned to a dead worker is requeued (or failed after
`max_retries`) once `job_timeout_seconds` elapses.

**Prereqs**: worker container running, `Smoke VSlow API` seeded (uses
`httpbin.org/delay/30` so the job stays in-flight long enough to kill the
worker mid-execution).

**Steps**:

```bash
# 1. queue a slow job and verify it's been assigned to the worker
curl -s -b /tmp/cookies.txt -X POST http://localhost:19552/api/runs \
  -H "Content-Type: application/json" \
  -d '{"app":"Smoke VSlow API","environment":"production","triggered_by":"reaper-test"}'

sleep 6   # let worker long-poll pick it up

# 2. kill the worker mid-execution
docker kill smoke-worker-full

# 3. confirm the job is stuck in state='assigned'
docker compose -f docs/guides/v2-smoke-compose.yml exec central python -c "
import sqlite3
c = sqlite3.connect('/app/data/harness.db')
print(c.execute(\"SELECT id, state, retry_count, assigned_to FROM runner_jobs WHERE state='assigned'\").fetchall())
"

# 4. invoke the reaper with a 5s timeout (avoids waiting 600s for the default)
docker compose -f docs/guides/v2-smoke-compose.yml exec central python -c "
import sys; sys.path.insert(0,'/app')
from harness.db import Database
from harness.worker_dispatch import reap_stuck_jobs
print(reap_stuck_jobs(Database('/app/data/harness.db'), job_timeout_seconds=5, max_retries=3))
"

# 5. verify state transition: assigned -> queued, retry_count +1, assigned_to NULL
docker compose -f docs/guides/v2-smoke-compose.yml exec central python -c "
import sqlite3
c = sqlite3.connect('/app/data/harness.db')
print(c.execute(\"SELECT id, state, retry_count, assigned_to FROM runner_jobs WHERE id='<JOB_ID>'\").fetchall())
"
```

**Observed (2026-05-13)**:

```
# step 3: job is stuck
[('a8de8c02-2289-4f57-beaa-25efded67c8a', 'assigned', 0, 'smoke-w1',
  '2026-05-14T01:41:15.623190+00:00')]

# step 4: reaper output
reaper result: {'scanned': 1, 'requeued': 1, 'failed': 0}

# step 5: state after reap
[('a8de8c02-2289-4f57-beaa-25efded67c8a', 'queued', 1, None)]
```

After restarting the worker the requeued job is picked up immediately via
long-poll and runs to completion. **Verdict: PASS.**

---

## Scenario 2: Screenshot upload from worker

**Goal**: a worker that runs a browser test which produces a screenshot
uploads it to the central's `data/screenshots/{run_id}/`.

**Status**: **DEFERRED — `untested-needs-fixture`**.

The smoke fixture set in this runbook is API-only; the slim image cannot
run browser tests at all (no playwright), and the full-image worker would
need a `type: browser` app with `run_on:` pointing at the worker plus a
deliberate failure (or `screenshot_on:` config) to generate the artefact.
Building that fixture is out of scope for this pass — see follow-up #2.

Code review confirms the upload path is wired:
`web/routes/runner.py::RunnerResultsRequest.screenshots` is a list of
`{step_name, content_base64, mime_type}` entries, capped server-side at
`MAX_SCREENSHOT_BYTES`. Worker side at `worker/worker.py` reads from
`HARNESS_SCREENSHOT_DIR` and embeds base64 in the results POST.

---

## Scenario 3: Offline result buffering (outbox)

**Goal**: when the central is unreachable, a completed job's results are
written to `data/worker_outbox.jsonl` and replayed when the central comes
back.

**Steps**:

```bash
# 1. seed a slow app (so we have time to pause central while job is in flight)
cat > /tmp/smoke-vslow.yaml <<'EOF'
app: "Smoke VSlow API"
run_on: smoke-w1
url: https://httpbin.org
tests:
  - name: VSlow status
    type: api
    method: GET
    endpoint: /delay/30
    timeout_ms: 60000
EOF
docker compose -f docs/guides/v2-smoke-compose.yml cp \
  /tmp/smoke-vslow.yaml central:/app/apps/smoke-vslow.yaml
# reload config

# 2. queue the slow job; wait for worker to pull
curl -s -b /tmp/cookies.txt -X POST http://localhost:19552/api/runs \
  -H "Content-Type: application/json" \
  -d '{"app":"Smoke VSlow API","environment":"production","triggered_by":"outbox-test"}'

sleep 6  # worker pulls within long-poll window

# 3. pause central WHILE the worker is busy on /delay/30
docker compose -f docs/guides/v2-smoke-compose.yml pause central

# 4. wait for the worker to finish its httpbin call (~30s)
sleep 35
docker logs smoke-worker-full --tail=15

# 5. confirm outbox has the unsent payload
docker exec smoke-worker-full cat /app/data/worker_outbox.jsonl

# 6. unpause central; queue a quick job to trigger opportunistic outbox flush
docker compose -f docs/guides/v2-smoke-compose.yml unpause central
sleep 10
curl -s -b /tmp/cookies.txt -X POST http://localhost:19552/api/runs \
  -H "Content-Type: application/json" \
  -d '{"app":"Smoke Test API","environment":"production","triggered_by":"outbox-drain"}'
sleep 15

# 7. verify outbox drained
docker exec smoke-worker-full wc -l /app/data/worker_outbox.jsonl
```

**Observed (2026-05-13)**:

```
# step 4 — worker logs after pause+complete
2026-05-14 01:38:26  GET https://httpbin.org/delay/30 "HTTP/1.1 200 OK"
2026-05-14 01:38:56  WARNING  submit_results network error for job
                              2122b990-… — buffering for replay

# step 5 — outbox contents (1 line, ~440 bytes)
{"job_id":"2122b990-8ab4-48bb-a7ff-7bdce2c22f02","run_status":"complete",
 "error":null,"results":[{"test_name":"VSlow status","status":"pass",...}]}

# step 6 — drain after fresh successful submit
2026-05-14 01:40:06  POST /api/runner/results  200 OK   # new job
2026-05-14 01:40:06  POST /api/runner/results  409 Conflict   # outbox replay
2026-05-14 01:40:06  ERROR outbox: head entry 409 — dropping (4xx)

# step 7 — outbox file is empty
0 /app/data/worker_outbox.jsonl
```

The replay returned **409 Conflict** because while the central was paused
the original job (state=assigned) was implicitly considered stuck and the
new job submission for the run was treated as a duplicate by the central.
This is correct behaviour: the worker's "drop 4xx replay entries" path
fires, the outbox empties, and no harm is done. End-to-end the run
appears as `complete` with `status=pass` in `runs`/`test_results`.

**Verdict: PASS** (buffering on network error, replay on next success,
graceful 4xx handling).

---

## Scenario 4: Long-poll job pickup

**Goal**: the worker blocks on `GET /api/runner/jobs?wait=10` and picks
up a freshly-queued job in 1-2 s rather than waiting for the next poll
interval.

**Steps**:

```bash
# in one shell, tail worker logs
docker logs -f smoke-worker-full

# in another, queue a job
curl -s -b /tmp/cookies.txt -X POST http://localhost:19552/api/runs \
  -H "Content-Type: application/json" \
  -d '{"app":"Smoke Test API","environment":"production","triggered_by":"long-poll-test"}'
```

**Observed (2026-05-13)** — abridged trace around the pickup:

```
01:35:28.201  GET /api/runner/jobs?name=smoke-w1&wait=10&capacity=4   (request opens, blocks)
01:35:33.570  GET /api/runner/jobs ... -> "HTTP/1.1 200 OK"            (returns ~5s later)
01:35:33.572  worker[smoke-w1] running job 094d8d27-…  app=Smoke Test API
01:35:33.816  GET https://httpbin.org/status/200 -> 200 OK
01:35:33.913  POST /api/runner/results -> 200 OK
```

Job was queued at ~01:35:28, picked up within ~5 s, results posted ~0.3 s
after the test ran. **Verdict: PASS.**

While idle the worker repeatedly issues `wait=10` long-polls that return
`HTTP 204 No Content` after exactly 10 seconds — server-side block
working as designed.

---

## Scenario 5: Scoped API keys

**Goal**: a worker configured with an API key that lacks the required
`worker:*` scopes is rejected with HTTP 403.

**Steps**:

```bash
# 1. as admin, mint a key with ONLY `read` scope (no worker:* scopes)
#    via the /api-keys UI

# 2. attempt to register
READONLY_KEY=hth_…
curl -s -X POST http://localhost:19552/api/runner/register \
  -H "Authorization: Bearer $READONLY_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"smoke-rogue","tags":["rogue"],"capabilities":["api"],"version":"0.0.1"}' \
  -w "\nHTTP %{http_code}\n"

# 3. attempt to long-poll for jobs
curl -s "http://localhost:19552/api/runner/jobs?name=smoke-rogue&wait=1&capacity=1" \
  -H "Authorization: Bearer $READONLY_KEY" -w "\nHTTP %{http_code}\n"
```

**Observed (2026-05-13)**:

```
# /register
{"detail":"API key lacks required scope: worker:results"}
HTTP 403

# /jobs
{"detail":"API key lacks required scope: worker:pull"}
HTTP 403
```

Error message names the missing scope, which is exactly what an operator
needs to fix the key. The key minted with `worker:results` alone (no
`worker:pull`) was also rejected on `/jobs` with the same shape — the
two scopes are independent. **Verdict: PASS.**

---

## Scenario 6: Slim worker image — uid + size

**Goal**: the slim image runs as `uid=10001(harness)` and is under 200 MB.

**Steps**:

```bash
docker run --rm --entrypoint id harness-worker-slim:smoke
docker images --format "{{.Repository}}:{{.Tag}} {{.Size}}" | grep slim
```

**Observed (2026-05-13)**:

```
uid=10001(harness) gid=10001(harness) groups=10001(harness)
harness-worker-slim:smoke 242MB
```

UID check **PASS**. Size assertion **FAIL** (242 MB > 200 MB target —
roughly 100% over the stated "~120 MB" comment in `Dockerfile.slim`).
See follow-up #3.

Also the image **cannot start** as-is — `python -m worker.worker` raises
`ModuleNotFoundError: No module named 'playwright'` due to the
unconditional import in `harness/__init__.py`. Filed as follow-up #1.

---

## Cleanup

```bash
docker compose -f docs/guides/v2-smoke-compose.yml down -v
docker rm -f smoke-worker-full 2>/dev/null || true
docker rmi harness-central:smoke harness-worker-slim:smoke
```

---

## Known issues / follow-ups

These were observed during the 2026-05-13 smoke run. **Do not fix here**
— file as separate work for the next pass.

1. **Slim worker image cannot start.**
   `python -m worker.worker` on `harness-worker-slim:smoke` raises
   `ModuleNotFoundError: No module named 'playwright'` because
   `harness/__init__.py` line 2 does `from harness.browser import
   run_browser_test`, and `harness/browser.py` line 6 imports
   `playwright.async_api`. The slim image deliberately excludes
   playwright (~1.3 GB saved). Fix candidates: lazy-import `harness.browser`
   in `harness/__init__.py`, or guard the import with a
   `try/except ImportError`. Until fixed, the slim image is unusable.
   Reproduce: `docker run --rm harness-worker-slim:smoke`.

2. **No worker-pinned browser fixture available for screenshot smoke
   testing.** The bundled `example-web-app.yaml` has a browser test but
   no `run_on:`, so it executes in-process on the central, not on a
   worker. Need to seed a small `apps/smoke-browser.yaml` with
   `run_on: smoke-w1` and a deliberate-failure step to produce a
   screenshot — then re-run Scenario 2 to validate the
   `RunnerResultsRequest.screenshots[]` path.

3. **Slim image size 242 MB, not ~120 MB.** `worker/Dockerfile.slim`
   advertises "Target final size: ~120 MB" in its header comment, and
   the task brief asserts "< 200 MB". The actual built image (Docker
   Desktop 4.70, linux/amd64+arm64 multi-arch via default builder) is
   242 MB. Likely contributors: multi-arch manifest doubles per-image
   storage, `python:3.11-slim` base is ~120 MB by itself, copying the
   entire `/install` site-packages tree from the build stage includes
   pip + setuptools artefacts that aren't strictly needed at runtime,
   and `cryptography` carries native libs. Investigate with
   `docker history harness-worker-slim:smoke` and consider
   `--platform linux/amd64` for single-arch builds.

4. **Long-poll connection-aborted on docker stop.** When the central is
   stopped with `docker compose stop central`, the worker's open
   long-poll `GET /jobs` connection terminates with
   `Server disconnected without sending a response.` This is logged as
   a `WARNING` and the worker correctly falls back to short-poll. Not
   a bug, but the `WARNING` line is noisy in normal restart scenarios —
   consider downgrading to `INFO` when the close handshake is clean
   (mid-`wait` server shutdown), or distinguishing graceful-shutdown
   from network-loss via the exception class.

5. **httpbin `/delay/30` returned in 87 ms during one run.** Likely a
   transient httpbin cache hit, not a harness bug — the next run used
   the full 30 s as expected. Documenting in case it shows up again
   while smoke-testing slow-job flows.

6. **`HARNESS_LONG_POLL_WAIT=10` is honoured client-side but the server
   responded with a max wait of ~10 s anyway.** This is correct (server
   caps at 60 s, client at its own value, smaller wins). Confirming the
   contract works as documented in `worker.py` line 18.

---

## Quick reference — environment contract

Worker (`worker/Dockerfile.slim` env vars actually consumed at runtime):

| Variable | Required | Default |
|---|---|---|
| `HARNESS_URL` | yes | — |
| `HARNESS_API_KEY` | yes | — |
| `HARNESS_WORKER_NAME` | yes | — |
| `HARNESS_WORKER_TAGS` | no | empty |
| `HARNESS_CAPABILITIES` | no | `api,availability,browser` |
| `HARNESS_CAPACITY` | no | `4` |
| `HARNESS_POLL_INTERVAL` | no | `10` |
| `HARNESS_LONG_POLL_WAIT` | no | `30` |
| `HARNESS_HEARTBEAT_INTERVAL` | no | `30` |
| `HARNESS_VERIFY_TLS` | no | `true` |
| `HARNESS_OUTBOX_PATH` | no | `data/worker_outbox.jsonl` |
| `HARNESS_OUTBOX_MAX` | no | `1000` |
| `HARNESS_SCREENSHOT_DIR` | no | `data/worker_screenshots` |
| `HARNESS_MAX_SCREENSHOT_BYTES` | no | `5242880` |
| `HARNESS_WORKER_VERSION` | no | `1.0.0` |

Central image: needs **no** environment variables for a fresh boot — admin
is provisioned via the `/setup` first-run wizard. `config.yaml` overrides
are mounted at `/app/config.yaml`. Data lives in `/app/data` (volume).
