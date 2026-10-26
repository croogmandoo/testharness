# `/admin/runners` Reference

The admin dashboard at `/admin/runners` is the single pane of glass for every
remote worker registered with the central. This page documents every column,
badge, panel, and button — what it means, where the data comes from, and what
to do when you see a non-green state.

See [`remote-workers.md`](remote-workers.md) for the deployment guide and
[`troubleshooting.md`](troubleshooting.md) for diagnostic recipes that cross-link
back here.

> Access: requires the `admin` role. All mutating actions
> (Drain / Resume / Remove / Re-queue) are CSRF-protected and audit-logged
> under `runner.drain`, `runner.resume`, `runner.delete`, `runner.requeue`.

---

## Page layout

```
┌───────────────────────────────────────────────────────────────┐
│  Last 24h artifacts            (panel — present iff workers   │
│    Screenshots: N               are uploading screenshots)    │
│    Total bytes:  M                                            │
├───────────────────────────────────────────────────────────────┤
│  Registered Workers            (one row per runner)           │
│  ┌────────────────────────────────────────────────────────┐   │
│  │ Name · Status · Tags · Caps · Key scope · Version ·    │   │
│  │  In-flight · Retries · Last seen · [Drain] [Remove]    │   │
│  └────────────────────────────────────────────────────────┘   │
├───────────────────────────────────────────────────────────────┤
│  Failed runs (reaper-exhausted)  (panel — present iff there   │
│    Manual re-queue button         are runs the reaper gave    │
│                                   up on)                      │
├───────────────────────────────────────────────────────────────┤
│  How dispatch works            (static explainer)             │
└───────────────────────────────────────────────────────────────┘
```

---

## Top panel: "Last 24h artifacts"

A summary of screenshots uploaded by remote workers in the trailing 24 hours.

| Stat | Source | Notes |
|---|---|---|
| `Screenshots` | Count of `test_results` rows whose `screenshot_path` is set and whose `started_at` is within 24h, restricted to runs that came from a worker | Includes both passing and failing tests when the test definition requested a screenshot |
| `Total bytes` | Sum of `os.path.getsize()` for each uploaded screenshot still on disk | Files removed by the retention sweeper count as 0 bytes — the count drops as the bytes shrink |

The panel is hidden entirely if no workers have uploaded anything in 24h —
useful as a quick health check that screenshot upload is wired correctly.
If you can see browser tests failing on a worker but this panel is empty or
zero, look at `HARNESS_SCREENSHOT_DIR` writability on the worker side
(see [Volume health & auto-drain](remote-workers.md#volume-health--auto-drain)).

---

## "Registered Workers" table

One row per runner in the `runners` table. Each column:

### Name

The worker's `HARNESS_WORKER_NAME`. Matched verbatim against `run_on:` in
`apps/*.yaml`. Workers without a unique name will overwrite each other on
registration — names must be globally unique.

### Status

One of three values, derived at render time (not stored as-is):

| Display | Beacon | Condition |
|---|---|---|
| **online** | green | Heartbeat within the last 90 seconds and `runners.status != 'draining'` |
| **draining** | grey | `runners.status = 'draining'` — admin clicked Drain, or the worker self-reported unhealthy |
| **offline** | red | No heartbeat in 90+ seconds and `status != 'draining'` |

A worker can transition `online → draining → online` automatically when its
self-reported `health` flips between `unhealthy` and `healthy`
(see [Volume health & auto-drain](remote-workers.md#volume-health--auto-drain)).
Admin-initiated drains stick until you click Resume.

### Tags

The worker's `HARNESS_WORKER_TAGS`, comma-separated. Apps with
`run_on: { tag: <tag> }` load-balance across any registered worker whose tag
list contains a matching entry. An em-dash (—) means no tags.

### Capabilities

The worker's `HARNESS_CAPABILITIES`. Default for the full image is
`api,availability,browser`; the slim image hard-codes `api,availability`. The
central dispatcher refuses to route a `browser` test to a worker whose
capabilities don't include `browser`.

If you queue a browser run and it sits in `queued` state with `0` in-flight
on a worker that should pick it up, check this column first.

### Key scope

The scopes attached to the API key the worker used to register. Sourced from
the `api_keys.scope` JSON column for `runners.api_key_id`. Possible values:

- `worker:pull worker:results` — the recommended minimum.
- `*` — legacy/wildcard key. Works, but the audit-blast-radius is bigger
  than necessary; rotate to a scoped key on the next opportunity.
- Em-dash (—) — the runner row has no `api_key_id`, or the key it points
  at has been deleted. A row with no key pre-dates name-to-key binding:
  poll, long-poll and heartbeat return 403 until the worker re-registers,
  which binds the name to its key. A deleted key gets 401 on every call.

A scope cell that's missing the scope your worker actually needs is a hint
the worker was provisioned wrong. See `remote-workers.md` § "Provision an
API key".

### Version

The worker's `HARNESS_WORKER_VERSION` (defaults to `1.0.0`). Compared
against `harness.__version__` on every register and heartbeat. Three badge
states can decorate this cell:

| Badge | Colour | Meaning | Dispatch effect |
|---|---|---|---|
| _(none)_ | — | Same major; minor drift within `worker.version_skew_warning_minors` (default 2) | Receives jobs normally |
| **stale** | amber | Same major; minor more than `warning_minors` behind central | Receives jobs normally — the badge is advisory |
| **incompatible** | red | MAJOR version differs from central | **Receives no jobs.** Dispatch hard-refuses; long-poll returns 204 and short-poll returns `{"jobs": []}` |

If a worker is registered but its `In-flight` count is stuck at 0 and you
expect work, this is the first column to check. See `remote-workers.md`
§ "Version compatibility" for the full skew model.

### In-flight

The current count of `runner_jobs` rows in `state='assigned'` and
`assigned_to=<worker>`. Sourced live per page render via
`db.count_inflight_jobs_for_runner(name)`. A worker at its
`HARNESS_CAPACITY` ceiling will sit here until a job finishes; one stuck
at 0 with queued jobs nearby is the symptom of dispatch refusal (scope,
capability mismatch, version skew, or `draining` state).

### Retries

Total of `runner_jobs.retry_count` summed across every job assigned to
this worker that completed in the last 24h. A non-zero value means the
reaper has had to requeue at least one of this worker's jobs — which
typically points at:

- The worker crashing mid-job (process dies, container restarts).
- Network drops that exceed `worker.job_timeout_seconds` (default 600 s).
- A particular test that wedges Playwright/Chromium.

A single retry over a 24h window is usually noise. Repeated double-digit
counts on the same worker are a signal — drain it and investigate.

### Last seen (UTC)

The most recent heartbeat timestamp from `runners.last_seen_at`, truncated
to seconds. Reads `"never"` if the row was created (via register) but the
worker has never followed up with a heartbeat — unusual outside of fresh
registration races.

### Actions

| Button | Endpoint | Effect | Audit event |
|---|---|---|---|
| **Drain** | `POST /admin/runners/{name}/drain` | Sets `runners.status='draining'`. Dispatch stops sending new jobs. In-flight jobs are allowed to finish. | `runner.drain` |
| **Resume** | `POST /admin/runners/{name}/resume` | Sets `runners.status='online'`. Dispatch resumes immediately. | `runner.resume` |
| **Remove** | `POST /admin/runners/{name}/delete` | Deletes the `runners` row. The worker process is unaffected until its next register/heartbeat, at which point it 404s and should exit. **In-flight jobs assigned to this worker become stranded** — the reaper will requeue them after `worker.job_timeout_seconds`. | `runner.delete` |

The Drain ↔ Resume buttons swap based on current state. Remove is always
present and always confirms via a JS dialog. Revoke the worker's API key
separately (`/api-keys`) if removal is in response to a credential leak.

### Rebinding a runner name

The first register binds a runner name to the API key that sent it
(`runners.api_key_id`). After that, only that key can poll, long-poll,
heartbeat or post results for the name. A register for the same name with a
different key gets `409 runner name already bound to another API key`,
unless:

- the bound key has been revoked, has expired or has been deleted, or
- the new key belongs to the same user as the bound key (key rotation for
  one service account).

In both cases the register rebinds the name and audit-logs `runner.rebind`
with the old and new key ids.

To move a name to a key owned by a **different** user while the old key is
still live, click **Remove**. The next register from the new key creates the
row again, bound to the new key. Revoke the old key too if it shouldn't be
used again.

---

## "Failed runs (reaper-exhausted)" panel

A list of up to 50 runs whose underlying `runner_job` was given up on by the
reaper — that is, retried `worker.max_retries` times (default 3) and then
marked `state='failed'`. The associated `runs.status` is `error`, so they
appear in the regular dashboard alongside genuine test failures.

This panel only appears when there's at least one such row.

| Column | Source | Notes |
|---|---|---|
| When (UTC) | `runs.completed_at` | Time the reaper gave up (not when the job was originally queued) |
| App | `runs.app` | Same value as the app's `app:` field in YAML |
| Env | `runs.environment` | |
| Last worker | `runner_jobs.assigned_to` | The worker that last had the job. May be `—` for jobs that were never claimed. |
| Retries | `runner_jobs.retry_count` | Will equal `worker.max_retries` for reaper-exhausted rows |
| Last error | `runner_jobs.error` | The reaper writes the timeout/max-retries message here |
| _action_ | **Re-queue manually** | See below |

### Re-queue manually

`POST /admin/runners/jobs/{job_id}/requeue` (CSRF-protected,
`runner.requeue` audit event). Flips `state` back to `queued` and clears
`assigned_to` + `assigned_at`. The next polling worker that matches the
job's runner name or tag picks it up.

The implementation **preserves `retry_count`** — manual requeues are
intentionally a separate concept from automatic ones, so the row's history
isn't reset. A `409 Conflict` is returned if the job is no longer in
`failed` state (someone else moved it first).

#### Decision tree: requeue manually, or investigate?

```
The reaper-exhausted row appeared. What now?
│
├── Was the worker offline at the time?
│     └─ Bring the worker back up. Then re-queue manually.
│        (The retry budget was eaten by network outages, not bad tests.)
│
├── Is it a one-off — most of this worker's other jobs succeed?
│     └─ Re-queue manually. Watch the next run.
│        If it fails again, you have a real test/app problem.
│
├── Is the same worker showing this repeatedly?
│     └─ DO NOT re-queue blindly. Drain the worker first.
│        Check its logs for OOM, Playwright crashes, or volume health alarms.
│        Re-queue only after you've fixed the root cause.
│
└── Is the test definition flaky (timing/race) and you know it?
      └─ Re-queue manually, but file an issue to add a retry: in the YAML
         so the in-test retry absorbs the flake instead of the reaper.
```

The UI confirms re-queue via a JS dialog that explicitly tells you to
investigate worker health first — heed it. A misbehaving worker that keeps
failing the same job wastes reaper retries on every requeue.

---

## "How dispatch works" panel

Static explainer at the bottom of the page. Reproduced from the deployment
narrative — if you're an admin re-reading it for the third time and the
text isn't helping, the deep dive lives in
[`remote-workers.md`](remote-workers.md) § "Route apps to workers".

---

## See also

- [`remote-workers.md`](remote-workers.md) — deployment & configuration
- [`troubleshooting.md`](troubleshooting.md) — diagnostic recipes for the
  "registered but 0 jobs" and "worker keeps draining itself" cases
- [`deployment.md`](deployment.md) — central-side `config.yaml` knobs
  including `worker.version_skew_warning_minors`,
  `worker.job_timeout_seconds`, and `worker.max_retries`
- [`worker/README.md`](../../worker/README.md) — worker-side env-var
  reference
