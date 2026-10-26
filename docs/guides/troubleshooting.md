# Troubleshooting: App Stuck on "Running"

When an app gets stuck in "running" status, it typically means:
- The test process crashed or was terminated unexpectedly
- Required secrets/environment variables were added after the run started
- Database state wasn't updated when the run ended

This prevents new runs from being triggered because the harness detects an active run already in progress.

---

## Solution: Reset the Stuck Run

> **As of v2 (2026-05-13), the stuck-job reaper handles this automatically.**
> Claimed jobs older than `worker.job_timeout_seconds` (default **600** seconds)
> are requeued every `worker.reaper_interval_seconds` (default **60** seconds),
> up to `worker.max_retries` (default **3**) before being marked `failed`. The
> manual procedure below is a last-resort fallback for catastrophic cases —
> e.g. the reaper itself isn't running (look for
> `scheduler: failed to wire stuck-job reaper` in the log), the run was
> dispatched in-process and the central crashed mid-run, or you need to clear
> a stuck row right now and don't want to wait out the timeout.

### Prerequisites
- SQLite3 command-line tool (or any SQLite client)
- The app name and environment that's stuck

  
---

## Steps

### 1. Open a Terminal
Navigate to the workspace root (wherever you cloned / deployed the harness):
```bash
cd /path/to/testharness
```

### 2. Open SQLite and Connect to the Database
```bash
sqlite3 data/harness.db
```

You should see the SQLite prompt:
```
SQLite version 3.x.x ...
sqlite>
```

### 3. Verify the Stuck Run
Before making changes, view the stuck run:

```sql
SELECT id, app, environment, status, started_at, finished_at FROM runs 
WHERE app='YourAppName' AND environment='YourEnvironment' ORDER BY started_at DESC LIMIT 5;
```

Replace `YourAppName` and `YourEnvironment` with the actual values. Look for a row with `status='running'`.

Example output:
```
id                                  app      environment  status   started_at           finished_at
────────────────────────────────────────────────────────────────────────────────────────────────────
550e8400-e29b-41d4-a716-446655440000  My App   production   running  2026-04-16 10:15:32  (null)
```

### 4. Reset the Status

Once confirmed, update the stuck run to mark it complete:

```sql
UPDATE runs 
SET status='complete', finished_at=datetime('now') 
WHERE app='YourAppName' AND environment='YourEnvironment' AND status='running';
```

You should see:
```
sqlite> ...
sqlite>
```

### 5. Verify the Change
Run the SELECT query again to confirm:

```sql
SELECT id, app, environment, status, started_at, finished_at FROM runs 
WHERE app='YourAppName' AND environment='YourEnvironment' ORDER BY started_at DESC LIMIT 5;
```

The stuck run should now show `status='complete'` and have a `finished_at` timestamp.

### 6. Exit SQLite
```sql
.quit
```

Or press `Ctrl+D`.

---

## Verify in the Web Dashboard

1. Open the dashboard: http://localhost:9552 (or your configured URL)
2. Navigate to the app and environment
3. You should now be able to trigger a new run
4. The stuck run should appear in the history with status "complete" (or "fail" if you prefer)

---

## Prevent This in the Future

1. **Create all secrets before running tests:**  
   Go to `/admin/secrets` in the web UI and create all required `$VAR_NAME` secrets before triggering app runs.

2. **Check test YAML for required variables:**  
   Review your app YAML and identify all `$VAR_NAME` references, then ensure those secrets exist.

3. **Use environment variables for passwords:**  
   Instead of hardcoding, use `$SECRET_NAME` so the harness can substitute actual values at runtime.

---

## Example Walkthrough

Given this app definition with a missing secret:

```yaml
# apps/my-system.yaml
app: "My System"
url: "https://system.example.com"
tests:
  - name: "Login flow"
    type: browser
    steps:
      - navigate: /login
      - fill:
          field: "#username"
          value: "admin"
      - fill:
          field: "#password"
          value: "$ADMIN_PASSWORD"  # ← This secret doesn't exist yet!
      - click: "button[type=submit]"
```

**What happens:**
1. You run the test without creating the `ADMIN_PASSWORD` secret first
2. The test hangs or fails when trying to substitute `$ADMIN_PASSWORD`
3. The run gets stuck in status='running'

**How to fix:**
1. Create the secret at `/admin/secrets` (name: `ADMIN_PASSWORD`, value: your actual password)
2. Reset the stuck run using the SQL steps above
3. Trigger a new run — it should pass now

---

## If Multiple Runs Are Stuck

To reset all stuck runs for an app (if there are more than one):

```sql
UPDATE runs 
SET status='complete', finished_at=datetime('now') 
WHERE app='YourAppName' AND status='running';
```

Or reset all stuck runs across all apps and environments:

```sql
UPDATE runs 
SET status='complete', finished_at=datetime('now') 
WHERE status='running';
```

**Use with caution** — only if you're sure you want to reset ALL pending/running tasks.

---

# Remote Worker Troubleshooting

For deployment of remote workers see
[`remote-workers.md`](remote-workers.md); for the admin dashboard see
[`admin-runners.md`](admin-runners.md). The two entries below cover the
diagnostic cases most worth a runbook.

## Worker registered but processing 0 jobs

A worker appears at `/admin/runners` with status `online`, but its
**In-flight** column sits at `0` while jobs queue elsewhere. Walk this
checklist in order — the first one that matches is almost always the
cause.

### 1. Check the version badge

In the worker's **Version** column, look for a red **incompatible**
badge. That means MAJOR versions differ between the worker and central
and dispatch is hard-refusing this worker.

Worker log will show, on every heartbeat:

```text
worker[eu-1] central notice: worker version '1.0.0' is incompatible
  with central version '2.0.0': MAJOR versions differ. This worker
  will receive no jobs until upgraded.
```

**Fix**: upgrade the worker (`docker pull` + recreate, or `apt-get` on
systemd installs) to a release in the same major as the central.

A **stale** (amber) badge does NOT block dispatch — it's a heads-up
about minor-version drift, advisory only.

### 2. Check the Status column

`draining` workers receive no new jobs. Two paths get there:

- An admin clicked **Drain**. Click **Resume**.
- The worker self-reported `health="unhealthy"` on heartbeat and the
  central auto-drained it. See
  [Worker keeps draining itself](#worker-keeps-draining-itself) below.

### 3. Check the Capabilities column

The slim worker image hard-codes `api,availability` — it cannot run
`type: browser` tests. If the queued job is a browser test and the
only matching worker is slim, dispatch will route past it.

**Fix**: route browser tests to a full-image worker
(`run_on: <name-of-full-worker>` or a tag those workers share), or
deploy a full-image worker in the same network zone.

### 4. Check `run_on:` matches a worker

```yaml
run_on: eu-worker-1            # must equal a worker's HARNESS_WORKER_NAME exactly
run_on: { tag: media }         # 'media' must appear in some worker's HARNESS_WORKER_TAGS
```

Typos here strand the job in `queued` state indefinitely. The central
doesn't validate `run_on:` values against registered workers at config
load — only at dispatch time, silently.

### 5. Check the API key scope

In `/admin/runners`, the **Key scope** column should show
`worker:pull worker:results`. If it's missing one of those, the
worker's poll or results POST will 403 — visible in the central log:

```text
require_scope: api_key abc12... denied — needs 'worker:pull'
```

**Fix**: regenerate the API key at `/api-keys` with the correct
scopes and update `HARNESS_API_KEY` on the worker.

## Worker keeps draining itself

A worker oscillates between `online` and `draining` on
`/admin/runners` without anyone touching the buttons. This is the
**volume health auto-drain** kicking in: the worker has self-reported
its outbox or screenshot directory as unwritable, and the central is
draining it on the corresponding heartbeat.

Worker log will show, repeatedly:

```text
worker[eu-1] volume health degraded: outbox parent 'data' not writable: ...
worker[eu-1] volume health still degraded after recreate: ...
```

### Fix

The volume health probe writes a real temp file (`os.access(W_OK)` lies
on NFS/overlayfs, so we don't trust it). Two directories are checked:

- **Outbox parent**: parent of `HARNESS_OUTBOX_PATH` (default `data/`)
- **Screenshot dir**: `HARNESS_SCREENSHOT_DIR` (default `data/worker_screenshots`)

Fix the one named in the log:

1. **Confirm the path on the host.** For Docker:
   ```bash
   docker inspect harness-worker --format \
     '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}'
   ```
2. **Check ownership / permissions.** Both the full and slim images
   run as uid `10001` (`harness`). The mount target must be writable by
   that uid:
   ```bash
   ls -ld /host/path/to/worker-data
   # Want: drwxr-xr-x harness ... OR mode 777 on a shared NFS share
   ```
3. **Fix and watch.** `chown` / `chmod` as needed:
   ```bash
   sudo chown -R 10001:10001 /host/path/to/worker-data
   ```
   The next heartbeat (within `HARNESS_HEARTBEAT_INTERVAL`, default 30 s)
   re-probes; on success the worker reports `health="healthy"` and the
   central restores `runners.status='online'` automatically.

### Why this is the right design

A worker that can't write its outbox can't preserve results across a
central outage. Letting it keep claiming jobs in that state would lose
work the next time the network blips. Auto-draining stops new claims
until the underlying problem is fixed; the worker recovers on its own
when it is.

### Persistent flapping

If the worker drains/restores every minute or two, the substrate is
intermittently failing — IOPS exhaustion, full disk (`df -h` on the
host), NFS soft-mount timeouts, or container overlay corruption. The
worker config itself is fine; fix the storage.


