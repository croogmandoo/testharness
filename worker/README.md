# Harness Remote Worker

A small companion process that runs harness checks from a different network
zone than the central. Useful when:

- The apps under test live behind firewalls / private endpoints the
  central harness can't reach
- You want geographic vantage points (latency from EU, US-East, etc.)
- Customer-VPC / multi-tenant deployments need per-tenant probes
- You're testing OT or air-gapped segments and only the worker is
  permitted to reach the target

Workers reuse the entire `harness/` test-execution stack — same Playwright,
same API client, same selectors, same step types. They differ only in
orchestration: instead of running in-process on the central, they poll
the central for queued jobs, run them locally, and submit results back.

## Deployment

Two supported targets:

### Docker (recommended — reuses the central image)

The published `ghcr.io/croogmandoo/testharness:latest` image already
contains the `worker` module — just override the `CMD`:

```bash
docker run -d \
  --name harness-worker \
  --restart unless-stopped \
  -e HARNESS_URL=https://harness.example.com \
  -e HARNESS_API_KEY=hth_xxx \
  -e HARNESS_WORKER_NAME=eu-worker-1 \
  -e HARNESS_WORKER_TAGS=eu,media \
  ghcr.io/croogmandoo/testharness:latest \
  python -m worker.worker
```

Same base, same Playwright/Chromium layer, same entrypoint security
model — only the runtime command differs.

If you want a slimmer dedicated worker image (no FastAPI/Jinja layer),
build from `worker/Dockerfile`:

```bash
docker build -f worker/Dockerfile -t my-harness-worker:local .
```

#### Ultra-slim image (api / availability only)

Workers whose apps never include browser tests can use the slim variant —
a multi-stage build that drops Playwright + Chromium entirely. The final
image lands around 120 MB instead of ~1.5 GB:

```bash
docker build -f worker/Dockerfile.slim -t my-harness-worker-slim:local .
```

The slim image hard-codes `HARNESS_CAPABILITIES=api,availability` so the
central won't dispatch browser-typed jobs to it. Override with `-e` if
you know better.

### Native Linux (Ubuntu 22.04 / 24.04, systemd)

For hosts that can't run Docker. The installer creates a system user,
clones the repo into `/opt/harness-worker`, builds a Python venv with
Playwright, writes a `harness-worker.service` unit, and starts it:

```bash
curl -fsSL \
  https://raw.githubusercontent.com/croogmandoo/testharness/master/scripts/install-worker-on-ubuntu.sh \
  | sudo HARNESS_URL=https://harness.example.com \
         HARNESS_API_KEY=hth_xxx \
         HARNESS_WORKER_NAME=eu-worker-1 \
         HARNESS_WORKER_TAGS=eu,media \
         bash
```

After install:

```bash
systemctl status harness-worker
journalctl -u harness-worker -f
```

## Provisioning credentials

The worker authenticates to the central via an **API key**. Recommended setup
on the central:

1. Create a dedicated user account, e.g. `worker-bot`, with role `runner`.
2. Sign in as that user; visit `/api-keys`; generate one key per worker
   (so you can revoke individually).
3. **Set the scope to `worker:pull,worker:results`** in the "Scopes" field
   of the key-create form. This gives the key access to exactly the four
   `/api/runner/*` endpoints the worker needs (`register`, `poll`/`jobs`,
   `results`, `heartbeat`) — and nothing else. Do **not** use `*` or `admin`;
   a leaked worker key with full scope is a much bigger blast radius than one
   that can only push run results.
4. Set the key on the worker as `HARNESS_API_KEY`.

The `runner` role is sufficient — workers don't need admin. The key is
stored as a SHA-256 hash on the central; the plaintext is shown once at
creation. Treat it like a credential.

> Keys created before scopes shipped have an implicit `*` (wildcard) scope
> and continue to work. New worker keys should always set the explicit
> minimum: `worker:pull,worker:results`.

## Configuration (environment variables only)

| Variable | Required | Default | Notes |
|---|---|---|---|
| `HARNESS_URL` | ✅ | — | Central harness URL; no trailing slash needed |
| `HARNESS_API_KEY` | ✅ | — | `hth_…` Bearer token (scope `worker:pull,worker:results`) |
| `HARNESS_WORKER_NAME` | ✅ | — | Globally unique; used by `run_on:` in app YAML |
| `HARNESS_WORKER_TAGS` | | (empty) | Comma-separated. Matched by `run_on: { tag: ... }` |
| `HARNESS_CAPABILITIES` | | `api,availability,browser` | Reported at register; informational |
| `HARNESS_CAPACITY` | | `4` | Max concurrent jobs |
| `HARNESS_POLL_INTERVAL` | | `10` | Fallback fixed-interval poll (used after long-poll 204) |
| `HARNESS_LONG_POLL_WAIT` | | `30` | Long-poll: how long the server holds the connection waiting for a job (max 60) |
| `HARNESS_HEARTBEAT_INTERVAL` | | `30` | Seconds between heartbeats |
| `HARNESS_VERIFY_TLS` | | `true` | Set `false` only for self-signed central in dev |
| `HARNESS_WORKER_VERSION` | | `1.0.0` | Reported at register |
| `HARNESS_OUTBOX_PATH` | | `data/worker_outbox.jsonl` | On-disk buffer for results when the central is unreachable |
| `HARNESS_OUTBOX_MAX` | | `1000` | Max queued results in the outbox; oldest evicted on overflow |
| `HARNESS_SCREENSHOT_DIR` | | `data/worker_screenshots` | Where `harness.runner` writes failure screenshots before upload |
| `HARNESS_MAX_SCREENSHOT_BYTES` | | `5242880` (5 MiB) | Hard cap per screenshot; worker drops anything larger before posting |

## v2 features

These shipped on 2026-05-13. Each one is wired by default — no flag needed —
but most have a tunable env var.

| Feature | Env var(s) | Central-side requirement | Notes |
|---|---|---|---|
| **Stuck-job reaper** | (server-side; no worker config) | `worker.job_timeout_seconds` (600) and `worker.reaper_interval_seconds` (60) in `config.yaml` | Server scans `runner_jobs` in `assigned` state older than the timeout every interval; requeues them (up to `worker.max_retries=3`, then marks `failed`). You no longer need to manually clear stuck rows when a worker crashes. |
| **Screenshot upload** | `HARNESS_SCREENSHOT_DIR`, `HARNESS_MAX_SCREENSHOT_BYTES` | None — the existing `/api/runner/results` endpoint accepts base64-encoded screenshots in the same POST. Server-side cap is 5 MiB per image (rejects 413 above that). | Failure screenshots for browser tests land under `data/screenshots/{run_id}/{step_name}.png` on the central, exactly like in-process runs. Worker reads from `HARNESS_SCREENSHOT_DIR` and embeds in the results payload. |
| **Offline result buffering** | `HARNESS_OUTBOX_PATH`, `HARNESS_OUTBOX_MAX` | None — the worker handles retries and replay internally. | If the central is unreachable when results are ready, the worker appends them to a local JSONL outbox and replays on reconnect. Survives worker restarts (file is persistent). Oldest entries are dropped on overflow with a `WARNING`-level log. |
| **Long-poll** | `HARNESS_LONG_POLL_WAIT` | Central exposes `GET /api/runner/jobs?name=…&wait=…&capacity=…`. Returns 200+jobs as soon as one is queued, or 204 + `Retry-After` on timeout. Max wait clamped to 60 s server-side. | Sub-second pickup of newly queued runs (vs. up to 10 s with short-poll). On 204 the worker falls back to `HARNESS_POLL_INTERVAL` before the next long-poll. |
| **Scoped API keys** | (uses `HARNESS_API_KEY`) | Create the key with scope `worker:pull,worker:results` instead of `*`. See "Provisioning credentials" above. | A scoped key that leaks can only push job results — it cannot read other apps' state, manage users, or change config. |
| **Optional mTLS** | `HARNESS_VERIFY_TLS=true` (default); worker presents a cert via the underlying httpx config — see `docs/guides/remote-workers.md` § "mTLS recipe" | `worker.mtls.enabled: true` and `worker.mtls.client_ca_path: /path/to/ca.pem` in `config.yaml`, **and** uvicorn started with `--ssl-cert-reqs 2 --ssl-ca-certs <ca>`. | Bearer-only is still the default and recommended path; mTLS is for hostile-network deployments where you need a second factor beyond the API key. The middleware reads the cert from `request.scope["client_cert"]` — that means **uvicorn must terminate TLS** (a nginx-in-front deployment requires an extra header-forwarding shim, not yet wired). |
| **Slim Docker image** | (image choice; no env var) | None | `worker/Dockerfile.slim` is a multi-stage build with no Playwright/Chromium — ~120 MB. Hard-codes `HARNESS_CAPABILITIES=api,availability` so the dispatcher won't route browser jobs to it. |

## Routing apps to workers

In any `apps/*.yaml` on the central, add a top-level `run_on:` field:

```yaml
app: "Customer Portal — EU"
run_on: eu-worker-1                    # pin to a specific worker
url: https://internal.eu.example.com
tests:
  - name: Healthcheck
    type: api
    endpoint: /health
```

Or use a tag for load-balanced dispatch across multiple workers:

```yaml
app: "Media stack"
run_on:
  tag: media                           # any worker with the 'media' tag
```

Apps **without** a `run_on:` field continue to run in-process on the
central exactly as before — zero behaviour change for existing apps.

## What workers see

Workers receive the **fully-resolved** app definition, including any
`$VAR` substitutions. The central harness resolves secrets via
`SecretsStore.get_all_decrypted()` before queuing the job, so the worker
sees plaintext values in headers, fill steps, etc.

This means **workers are a trust boundary.** Don't deploy a worker in a
segment less trusted than the secrets the apps it runs require. Future
versions will support tag-scoped secret release; v1 is all-or-nothing.

## What workers still don't do

- **Self-update.** Operators redeploy / `apt-get` to upgrade. No
  orchestration plane is planned — workers are intentionally dumb.
- **Worker-to-worker federation.** Each worker only knows about the
  central. There is no mesh or peer discovery.
- **Per-tag secret scoping.** A worker that runs any app sees every
  `$VAR` substitution in that app's resolved definition. Workers remain a
  trust boundary; deploy them only in segments at least as trusted as the
  secrets the apps they run depend on.

## Monitoring the worker

- `/admin/runners` on the central shows registered workers, their tags,
  in-flight job count, last heartbeat, and online/draining/offline state.
- Admin actions: **Drain** (stop accepting new jobs), **Resume**, **Remove**.
- All admin actions go to the audit log under `runner.drain`,
  `runner.resume`, `runner.delete`.

## Troubleshooting

**Worker never appears in `/admin/runners`** — check the worker logs
(`docker logs harness-worker` or `journalctl -u harness-worker -f`).
401 means the API key is invalid; 404 means the worker hasn't called
`/api/runner/register` yet (network unreachable or wrong `HARNESS_URL`).

**Worker shows "offline" while running** — its heartbeat hasn't reached
the central in 90s. Check the network path and TLS cert (`HARNESS_VERIFY_TLS`).

**Jobs queue but no worker picks them up** — the `run_on:` field on
the app must match a registered worker's name or tag. Check the spelling
and the worker's `HARNESS_WORKER_TAGS`.

**Browser tests fail with "Could not find Chromium"** — only an issue on
native installs that missed the playwright install step. Run:
```bash
sudo -u harness-worker /opt/harness-worker/.venv/bin/python -m playwright install chromium
```
