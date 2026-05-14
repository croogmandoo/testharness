# Remote Workers

Self-hosted "private locations" for the harness. Deploy a worker inside any
network zone the central harness can't reach directly — DMZ, customer VPC,
EU vantage point, air-gapped segment — and assign apps to it from
`apps/*.yaml`. The central keeps the UI, results storage, alerting,
analytics export, and dashboard; the worker is a thin process that pulls
queued jobs, runs them locally, and posts results back.

This is the canonical user-facing guide. The companion
[`worker/README.md`](../../worker/README.md) is the operator quickref
sitting next to the worker code; this file is the deeper integration
reference.

---

## Overview

```text
┌─────────────────────────────────────────────────────────────┐
│ Central harness                                             │
│   Web UI + REST API + alerting + analytics + dashboards     │
│   Default in-process runner (apps with no run_on:)          │
│                                                             │
│   /api/runner/register       /api/runner/results            │
│   /api/runner/poll           /api/runner/heartbeat          │
│   /api/runner/jobs (long-poll)                              │
└──────▲──────────────────────────────────────────────────────┘
       │ HTTPS  ·  Bearer hth_…  ·  optional mTLS
       │
   ┌───┴──────┐    ┌──────────┐    ┌──────────┐
   │ worker A │    │ worker B │    │ worker C │
   │  DMZ     │    │  EU      │    │ customer │
   └──────────┘    └──────────┘    └──────────┘
```

- **One central, N workers.** Workers are stateless; only state is the
  API key and the worker name.
- **Apps opt in.** An app without `run_on:` continues to run in-process
  on the central exactly as before.
- **Same code path.** Workers reuse `harness.runner` verbatim — same
  Playwright, same selectors, same step types — so a test that passes
  in-process passes on a worker.

---

## Architecture: Phase 1 vs. v2

Workers shipped in two waves. v1 (Phase 1, 2026-05-08) was a working
short-poll prototype; v2 (2026-05-13) closed the gaps that came up in
real deployments.

| Capability | Phase 1 (v1) | v2 (today) |
|---|---|---|
| Job dispatch | Short-poll (`POST /api/runner/poll`) every `HARNESS_POLL_INTERVAL` seconds | **Long-poll** (`GET /api/runner/jobs?wait=…`) — sub-second pickup; short-poll still works as a fallback |
| Stuck jobs (worker crashed mid-run) | Stayed in `assigned` state forever; manual SQL fix | **Reaper** scans every `reaper_interval_seconds` and requeues / fails after `max_retries` |
| Failure screenshots | Saved on the worker host; lost to the central | **Uploaded** as base64 in the results POST; land under `data/screenshots/{run_id}/` on the central |
| Worker offline / central unreachable | Results dropped on the floor | **Outbox** persists results to `data/worker_outbox.jsonl`; replays on reconnect; survives worker restarts |
| API key scope | One key = full access (`*`) | **Scoped keys**: `worker:pull,worker:results` is sufficient |
| Transport | Bearer over HTTPS | Bearer over HTTPS, **optional mTLS** for hostile-network deployments |
| Worker image size | ~1.5 GB (Playwright bundled) | **Slim variant** (~120 MB) for api/availability-only workers |

Everything in the "v2" column ships by default — you don't need to flip a
flag to get the reaper, screenshots, outbox, or long-poll. mTLS, scoped
keys, and the slim image are opt-in.

---

## Prerequisites

On the **central**:

- Harness master at commit `42b821f` (2026-05-13) or later.
- Reachable from each worker over HTTPS. (Plain HTTP works in dev, but
  the API key travels as Bearer — don't.)
- A user account with role `runner` (or any role; admin not required).
- For mTLS: a client CA bundle (PEM) trusted by uvicorn.

On each **worker host**:

- Docker 24+ **or** Ubuntu 22.04/24.04 with systemd.
- Outbound HTTPS to the central. No inbound ports.
- ~1.5 GB free disk if running browser tests (Playwright bundle);
  ~150 MB for slim/api-only.
- Writable `data/` directory for the outbox and (browser workers)
  screenshots.

---

## Provision an API key

Every worker authenticates as itself, with its own API key. **Do not
share one key across workers** — you'll be unable to revoke a single
compromised host.

1. As the `runner` user (or any user; admin not required), visit
   `/api-keys`.
2. **Name**: descriptive, e.g. `eu-worker-1`.
3. **Expires**: `Never` is fine for long-lived infra; pick a date if
   policy requires rotation.
4. **Scopes**: enter exactly `worker:pull,worker:results`.
5. Click *Create Key*. The plaintext `hth_…` token is shown **once**.
   Copy it somewhere safe (or straight into the worker env).

### Scope cheatsheet

| Scope | Grants | Use for |
|---|---|---|
| `worker:pull` | `POST /api/runner/poll`, `GET /api/runner/jobs` (long-poll) | Workers |
| `worker:results` | `POST /api/runner/register`, `/heartbeat`, `/results` | Workers |
| `read` | All `GET /api/...` endpoints | Dashboards / monitoring scripts |
| `admin` | Admin endpoints | Operators / CI |
| `*` | Everything | Back-compat for keys made before scopes shipped — don't grant this to new workers |

A leaked `worker:pull,worker:results` key can claim jobs and push results.
It cannot read run history, list apps, manage users, change config, or
even read its own apps' YAML. That asymmetry is the whole point.

---

## Choose an image

Three options, in decreasing order of "fits everyone":

### 1. Reuse the central image (recommended)

The published `ghcr.io/croogmandoo/testharness:latest` already ships the
worker module. Same image, different entrypoint — just override the
`CMD`:

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

Pros: same image you already trust; one image to keep current. Cons:
~1.5 GB on disk because Playwright + Chromium is bundled.

### 2. Dedicated worker image

```bash
docker build -f worker/Dockerfile -t my-harness-worker:local .
```

Same Playwright base; drops the FastAPI/Jinja layer. Sits around ~1.4 GB.
Worth it only if you don't want the central FastAPI surface in your
worker zone.

### 3. Slim image (api / availability only)

```bash
docker build -f worker/Dockerfile.slim -t my-harness-worker-slim:local .
```

Multi-stage build with no Playwright/Chromium. ~120 MB. Hard-codes
`HARNESS_CAPABILITIES=api,availability` so the central won't dispatch
browser-typed jobs to it. Override with `-e HARNESS_CAPABILITIES=…`
only if you know better (e.g. you've installed Playwright in a layer
on top).

Slim is the right pick for workers that only do REST/availability
probes — small footprint, easier to ship to air-gapped sites via
`docker save | docker load`.

---

## Run the worker

### docker-compose

The most reproducible setup. Drop this in `docker-compose.worker.yml`
next to your existing compose:

```yaml
services:
  harness-worker:
    image: ghcr.io/croogmandoo/testharness:latest
    container_name: harness-worker
    restart: unless-stopped
    command: ["python", "-m", "worker.worker"]
    environment:
      HARNESS_URL: https://harness.example.com
      HARNESS_API_KEY: ${HARNESS_API_KEY}
      HARNESS_WORKER_NAME: eu-worker-1
      HARNESS_WORKER_TAGS: eu,media
      HARNESS_CAPACITY: "4"
      HARNESS_LONG_POLL_WAIT: "30"
      # v2 outbox / screenshot config — defaults are fine for most setups
      # HARNESS_OUTBOX_PATH: data/worker_outbox.jsonl
      # HARNESS_OUTBOX_MAX: "1000"
      # HARNESS_SCREENSHOT_DIR: data/worker_screenshots
      # HARNESS_MAX_SCREENSHOT_BYTES: "5242880"
    volumes:
      - ./worker-data:/app/data
```

Put the API key in a `.env` file beside the compose so it isn't in
shell history:

```bash
# .env
HARNESS_API_KEY=hth_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

Bring it up:

```bash
docker compose -f docker-compose.worker.yml up -d
docker compose -f docker-compose.worker.yml logs -f
```

Expected log within ~5 s:

```text
[worker] registering as eu-worker-1 at https://harness.example.com
[worker] registered ok; capacity=4 capabilities=api,availability,browser
[worker] heartbeat loop started (interval=30s)
[worker] long-poll loop started (wait=30s)
```

### Standalone Docker

If you don't want compose:

```bash
docker run -d \
  --name harness-worker \
  --restart unless-stopped \
  --health-cmd "test -f /app/data/worker_outbox.jsonl.lock || true" \
  -v $(pwd)/worker-data:/app/data \
  -e HARNESS_URL=https://harness.example.com \
  -e HARNESS_API_KEY=hth_xxx \
  -e HARNESS_WORKER_NAME=eu-worker-1 \
  -e HARNESS_WORKER_TAGS=eu,media \
  -e HARNESS_LONG_POLL_WAIT=30 \
  ghcr.io/croogmandoo/testharness:latest \
  python -m worker.worker
```

### systemd (native Linux, no Docker)

For hosts that can't run Docker. The published installer creates a
system user, clones the repo into `/opt/harness-worker`, builds a
Python venv with Playwright, writes a `harness-worker.service` unit,
and starts it:

```bash
curl -fsSL \
  https://raw.githubusercontent.com/croogmandoo/testharness/master/scripts/install-worker-on-ubuntu.sh \
  | sudo HARNESS_URL=https://harness.example.com \
         HARNESS_API_KEY=hth_xxx \
         HARNESS_WORKER_NAME=eu-worker-1 \
         HARNESS_WORKER_TAGS=eu,media \
         HARNESS_LONG_POLL_WAIT=30 \
         bash
```

Manage with the usual systemd verbs:

```bash
systemctl status harness-worker
journalctl -u harness-worker -f
systemctl restart harness-worker
```

The unit file lives at `/etc/systemd/system/harness-worker.service`;
env vars are written into `/etc/harness-worker.env`. To change config,
edit that file and `systemctl restart`.

---

## Route apps to workers

A top-level `run_on:` field in any `apps/*.yaml` opts that app into
remote execution. Three forms:

### Pin to a named worker

```yaml
app: "Customer Portal — EU"
run_on: eu-worker-1
url: https://internal.eu.example.com
tests:
  - name: Healthcheck
    type: api
    endpoint: /health
```

Job is dispatched only to a worker whose `HARNESS_WORKER_NAME` matches.

### Load-balance across a tag

```yaml
app: "Media stack"
run_on:
  tag: media
tests:
  - name: Stream check
    type: api
    endpoint: /media/health
```

Any registered worker carrying tag `media` in `HARNESS_WORKER_TAGS`
can claim it. First poll wins (exactly-once-per-job).

### In-process (default)

```yaml
app: "Internal admin"
# no run_on: — runs on the central, exactly as before
```

Apps with no `run_on:` skip the queue and execute in-process on the
central harness — zero behaviour change for existing apps.

---

## Monitoring & ops

### `/admin/runners`

The admin page lists every registered worker with:

- Name, tags, capabilities, version
- Status: **online** (heartbeat within 90 s), **draining**, **offline**
- In-flight job count
- Last-seen timestamp (UTC)
- Actions: **Drain** (stop accepting new jobs), **Resume**, **Remove**

All admin actions audit-log under `runner.drain`, `runner.resume`,
`runner.delete`.

### Worker logs

```bash
docker logs harness-worker          # docker
journalctl -u harness-worker -f     # systemd
```

Useful log lines:

- `[worker] registered ok` — first contact with the central succeeded
- `[worker] picked up 2 jobs` — long-poll returned work
- `[worker] uploaded 1 screenshot(s) for run …` — screenshot embed worked
- `[worker] outbox: buffered 1 result (depth=3)` — central unreachable;
  worker is queueing
- `[worker] outbox: replayed 3 result(s) on reconnect` — back online
- `[worker] outbox overflow at 1000; dropping oldest` — you've been
  offline too long; some results were lost

### Central log lines

In the central harness log (`data/logs/harness.log` or stderr):

- `reaper: requeued stuck job …` — a worker crashed mid-job; the reaper
  recovered it
- `reaper: stuck job … exceeded max_retries=3; marking failed` — the
  job has been requeued three times and is now a hard failure
- `require_scope: api_key … denied — needs 'worker:pull'` — a key was
  used at a worker endpoint without the right scope (often: someone
  used an old `read`-scope key by mistake)
- `mtls: rejected POST /api/runner/poll — no client cert presented` —
  mTLS is enabled but the worker isn't presenting a certificate

---

## Troubleshooting

### Worker never appears in `/admin/runners`

Check the worker log first.

- **`401 Unauthorized`** — the API key is wrong, was revoked, or you
  copied a key prefix instead of the full token.
- **`403 client certificate required`** — mTLS is on but the worker
  isn't presenting a cert. See § mTLS recipe.
- **`403 require_scope: needs 'worker:results'`** — the API key has
  the wrong scopes. Create a new one with `worker:pull,worker:results`
  and update `HARNESS_API_KEY`.
- **`Connection refused` / `Name or service not known`** — the worker
  can't reach `HARNESS_URL`. Check DNS and firewall.
- **`certificate verify failed`** — the central's TLS cert is signed by
  a CA the worker doesn't trust. Either install the cert in the worker
  host's trust store, or set `HARNESS_VERIFY_TLS=false` in dev.

### Worker shows "offline" while it's actually running

The worker's heartbeat hasn't reached the central in 90 s. Either:

- Network path is dropping connections (long-poll holds the connection
  for up to 60 s; some load balancers idle-close at less than that —
  lower `HARNESS_LONG_POLL_WAIT` to e.g. `20`).
- TLS handshake is failing intermittently (check
  `HARNESS_VERIFY_TLS=true` is working).
- Worker is wedged (kill and restart; the outbox preserves any
  buffered results).

### Jobs queue but no worker picks them up

- `run_on:` value must match a worker's `HARNESS_WORKER_NAME` exactly,
  or `run_on: { tag: x }` must match an entry in `HARNESS_WORKER_TAGS`.
- The worker must be `online`, not `draining`. Check `/admin/runners`.
- The worker's `HARNESS_CAPABILITIES` must include the test type. The
  slim image hard-codes `api,availability` — if your app has a
  `browser` test, route it to a non-slim worker.

### Browser tests fail with "Could not find Chromium"

Only an issue on native installs that missed the playwright install
step. Run:

```bash
sudo -u harness-worker /opt/harness-worker/.venv/bin/python -m playwright install chromium
```

Or on Docker: confirm the image is one of `testharness:latest`,
`harness-worker:local`, **not** `harness-worker-slim:local`.

### "outbox overflow at 1000; dropping oldest" in the log

The worker has been disconnected from the central for long enough to
fill the outbox. The oldest results are lost.

- Investigate the network outage; the worker is fine, the central path is the problem.
- Raise `HARNESS_OUTBOX_MAX` if 1000 is genuinely too small for your
  expected disconnect duration. (1000 results at typical run sizes
  is ~5-30 MB on disk.)

### "scheduler: failed to wire stuck-job reaper" on central startup

The reaper didn't start. Stuck jobs will accumulate. Check the central
log for the underlying exception; usually it's a config-parse error.
The fallback is the manual procedure in
[`troubleshooting.md`](troubleshooting.md#solution-reset-the-stuck-run).

### Screenshots not appearing in the central UI

- Confirm the test type is `browser` (api/availability tests don't
  generate screenshots).
- Confirm the worker has `HARNESS_SCREENSHOT_DIR` writable.
- Look in the central log for
  `screenshots: decode failed for <step>` (worker sent malformed
  base64) or `screenshots: decoded size … exceeds cap` (image bigger
  than `HARNESS_MAX_SCREENSHOT_BYTES`).

---

## mTLS recipe

Bearer-only is the default and secure-enough for most deployments. mTLS
adds a second factor: the worker must present a client certificate signed
by a CA you control. A leaked API key alone won't get past it.

### What ships today

`web/mtls.py` registers a Starlette middleware on `/api/runner/*` that
reads `request.scope["client_cert"]`. **Uvicorn populates that field
when started with `--ssl-cert-reqs 2 --ssl-ca-certs <ca>`** — i.e. when
uvicorn itself terminates TLS. The middleware does not validate the
cert chain (uvicorn already did); it only asserts a verified cert is
present and extracts the subject CN onto `request.state.worker_cn` for
audit logging.

> **Implementation gotcha (read this).** The middleware **only**
> consults `request.scope["client_cert"]`. It does not read an
> `X-Client-Cert` header or any other forwarded header. If you run
> nginx in front of uvicorn doing TLS termination, the middleware will
> reject every request with `403 client certificate required` because
> nothing populates that scope key. Today, **only the uvicorn-direct
> recipe below works out of the box**. A header-forwarding adapter for
> the nginx path is a follow-up.

### Recipe A: uvicorn terminates TLS (supported)

This matches what `web/mtls.py` reads. Enable on the central:

```yaml
# config.yaml
worker:
  mtls:
    enabled: true
    client_ca_path: /etc/harness/client-ca.pem
```

Start uvicorn (or override the container CMD) with the TLS-termination
flags:

```bash
uvicorn web.main:app \
  --host 0.0.0.0 \
  --port 9552 \
  --ssl-certfile /etc/harness/server.pem \
  --ssl-keyfile /etc/harness/server.key \
  --ssl-ca-certs /etc/harness/client-ca.pem \
  --ssl-cert-reqs 2
```

`--ssl-cert-reqs 2` is `ssl.CERT_REQUIRED` — uvicorn rejects the TLS
handshake itself for any client that doesn't present a cert signed by
the CA bundle. The middleware is then a defence-in-depth assertion
that the verified cert is on the scope.

On the worker, point httpx at the matching client cert. The worker
respects standard `SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE` and httpx
`cert=…` configuration via env vars. The simplest path is to bind-mount
the cert+key into the container and set:

```yaml
# docker-compose.worker.yml
services:
  harness-worker:
    # ... as before ...
    environment:
      HARNESS_URL: https://harness.example.com
      HARNESS_API_KEY: hth_xxx
      HARNESS_CLIENT_CERT: /etc/harness/worker.pem    # if your worker build supports it
      HARNESS_CLIENT_KEY:  /etc/harness/worker.key
    volumes:
      - ./tls:/etc/harness:ro
      - ./worker-data:/app/data
```

> Note: at time of writing, `worker.worker` reads `HARNESS_VERIFY_TLS`
> for httpx's `verify=` flag but does not yet wire a `cert=…` tuple
> from `HARNESS_CLIENT_CERT`/`HARNESS_CLIENT_KEY`. If you need mTLS
> today, wrap the worker in a small starter script that sets the
> client cert on the httpx defaults, or run the worker against a
> sidecar (e.g. `stunnel` / `step-ca`) that terminates client TLS and
> forwards plain HTTP to the worker. Wiring `cert=` directly into
> `httpx.AsyncClient` is a small follow-up.

Expected central log on a healthy mTLS request:

```text
mtls: enforcement enabled for /api/runner/* (ca_path='/etc/harness/client-ca.pem')
```

And on a rejected one:

```text
mtls: rejected POST /api/runner/poll — no client cert presented
```

### Recipe B: nginx in front of uvicorn (not currently supported)

This is the recipe operators usually want — nginx already handles TLS
for the rest of the site, and adding mTLS just on `/api/runner/*` is a
small `location` block:

```nginx
server {
    listen 443 ssl;
    server_name harness.example.com;

    ssl_certificate     /etc/nginx/server.pem;
    ssl_certificate_key /etc/nginx/server.key;

    location /api/runner/ {
        # Require a client cert signed by our worker CA
        ssl_client_certificate /etc/nginx/worker-ca.pem;
        ssl_verify_client      on;
        ssl_verify_depth       2;

        # Forward the cert to the app so it can audit which worker
        # made the request
        proxy_set_header  X-Client-Cert     $ssl_client_escaped_cert;
        proxy_set_header  X-Client-Cert-CN  $ssl_client_s_dn;
        proxy_set_header  X-Client-Verify   $ssl_client_verify;

        proxy_pass         http://localhost:9552;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-Proto https;
    }

    location / {
        proxy_pass         http://localhost:9552;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-Proto https;
    }
}
```

> **This will not work out of the box today.** `web/mtls.py` does not
> read `X-Client-Cert` or `X-Client-Verify`; it only reads
> `request.scope["client_cert"]`, which the nginx-front path doesn't
> populate. The middleware will reject every request with
> `403 client certificate required`.
>
> Tracked as a follow-up: extend `MTLSEnforcementMiddleware` with an
> opt-in `trust_forwarded_header: X-Client-Cert` mode that synthesises
> a scope entry from the nginx header. Until that ships, use recipe A
> (uvicorn direct), or run nginx as a pure pass-through (no
> `ssl_verify_client`) and let uvicorn do the verification.

---

## Limitations

Things workers still don't do, and aren't planned for the immediate
roadmap:

- **Self-update.** Operators redeploy / `apt-get` to upgrade. There is
  no orchestration plane and there isn't going to be one.
- **Worker-to-worker federation.** Each worker only knows about the
  central. No peer discovery, no mesh.
- **Per-tag secret scoping.** A worker that runs any app sees every
  `$VAR` substitution in that app's resolved definition. Workers are a
  **trust boundary** — deploy them only in segments at least as trusted
  as the secrets the apps they run depend on. Per-tag secret release
  is a future enhancement.
- **Cross-worker dedup for tagged apps beyond first-poll-wins.** If you
  need exactly-once-per-region semantics (e.g. one run per geo per
  tick), pin to a specific worker name instead of a tag.
- **nginx-forwarded mTLS.** Recipe B above is documented for reference
  but not yet wired. Use recipe A or a stunnel sidecar.
- **Worker `cert=` config for httpx.** Today the worker handles
  `HARNESS_VERIFY_TLS` for server-cert verification but does not yet
  read client-cert env vars. For mTLS use a sidecar that terminates
  client TLS, or wait for the small follow-up that adds
  `HARNESS_CLIENT_CERT`/`HARNESS_CLIENT_KEY` directly.

---

## See also

- [`worker/README.md`](../../worker/README.md) — operator quickref next
  to the worker code
- [`troubleshooting.md`](troubleshooting.md) — the manual stuck-run
  fallback when the reaper isn't an option
- [`deployment.md`](deployment.md) — `config.yaml` reference, including
  the `logging:` and `worker:` v2 sections
- `docs/superpowers/plans/2026-05-14-remote-worker.md` — original
  design plan (phases 1-5)
