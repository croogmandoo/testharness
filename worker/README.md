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
3. Set the key on the worker as `HARNESS_API_KEY`.

The `runner` role is sufficient — workers don't need admin. The key is
stored as a SHA-256 hash on the central; the plaintext is shown once at
creation. Treat it like a credential.

## Configuration (environment variables only)

| Variable | Required | Default | Notes |
|---|---|---|---|
| `HARNESS_URL` | ✅ | — | Central harness URL; no trailing slash needed |
| `HARNESS_API_KEY` | ✅ | — | `hth_…` Bearer token |
| `HARNESS_WORKER_NAME` | ✅ | — | Globally unique; used by `run_on:` in app YAML |
| `HARNESS_WORKER_TAGS` | | (empty) | Comma-separated. Matched by `run_on: { tag: ... }` |
| `HARNESS_CAPABILITIES` | | `api,availability,browser` | Reported at register; informational |
| `HARNESS_CAPACITY` | | `4` | Max concurrent jobs |
| `HARNESS_POLL_INTERVAL` | | `10` | Seconds between polls (short-poll v1) |
| `HARNESS_HEARTBEAT_INTERVAL` | | `30` | Seconds between heartbeats |
| `HARNESS_VERIFY_TLS` | | `true` | Set `false` only for self-signed central in dev |
| `HARNESS_WORKER_VERSION` | | `1.0.0` | Reported at register |

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

## What workers don't do (yet)

- **Upload screenshots** to the central. Browser tests still pass/fail
  correctly, but the failure-screenshot artefact stays on the worker host.
  Coming in a follow-up: base64-embedded screenshots in the results POST.
- **Buffer offline results.** If the worker can't reach the central, the
  job's results are lost. Coming in a follow-up.
- **Long-poll.** v1 uses short-poll on `HARNESS_POLL_INTERVAL`. Long-poll
  is on the roadmap for sub-second job pickup.
- **Self-update.** Operators redeploy / `apt-get` to upgrade. No
  orchestration plane.
- **mTLS.** Bearer-only in v1; mTLS is a planned phase-5 enhancement.

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
