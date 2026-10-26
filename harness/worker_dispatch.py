"""Worker dispatch — decides whether a run should execute in-process on
the central harness or be queued for a remote worker, and handles the
queue-row lifecycle.

App YAML enables remote dispatch via a top-level ``run_on:`` field:

  run_on: eu-worker-1               # pin to a specific worker by name
  run_on: { tag: media }            # any worker with the tag 'media'

When `run_on` is absent the run executes locally exactly as before
(today's behaviour). The runner protocol is documented in
docs/superpowers/plans/2026-05-14-remote-worker.md.

Workers see the **resolved** app definition including any $VAR
substitutions. Workers are a trust boundary — do not deploy them in
less-trusted segments than the secrets they need.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from harness.config import ConfigError, app_environ, resolve_env_vars
from harness.models import Run

_log = logging.getLogger("harness.worker_dispatch")

# Process-local Event set every time a new runner_job is queued. Long-poll
# handlers wait on it so newly-queued work is delivered to idle workers
# without burning CPU on tight polling loops. The Event is reset shortly
# after being set so the next wait blocks again.
_job_queued_event: Optional[asyncio.Event] = None


def get_job_queued_event() -> asyncio.Event:
    """Lazy-init an asyncio.Event bound to the running loop. The event is
    set by ``notify_job_queued`` and awaited by ``/api/runner/jobs`` long-poll
    handlers. The reaper also fires it after requeuing a stuck job."""
    global _job_queued_event
    if _job_queued_event is None:
        _job_queued_event = asyncio.Event()
    return _job_queued_event


def notify_job_queued() -> None:
    """Wake up every coroutine currently blocked on
    ``get_job_queued_event().wait()``. Safe to call from non-async contexts —
    if no loop is running the call is a no-op."""
    try:
        ev = get_job_queued_event()
        ev.set()
        # Schedule an immediate clear so the next waiter blocks again.
        try:
            loop = asyncio.get_running_loop()
            loop.call_soon(ev.clear)
        except RuntimeError:
            # No running loop — caller is sync code. The event stays set;
            # any future async waiter will see it once and clear via the
            # async helper below.
            pass
    except Exception:
        _log.debug("notify_job_queued: no event loop available; skipping wake-up")


def parse_run_on(app_def: dict) -> tuple[Optional[str], Optional[str]]:
    """Return (runner_name, target_tag) from the app's ``run_on:`` field.

    Both None  → execute locally (no remote dispatch)
    name, None → pin to that worker
    None, tag  → tag-based dispatch (any matching worker claims it)
    """
    spec = app_def.get("run_on")
    if not spec:
        return (None, None)
    if isinstance(spec, str):
        return (spec, None)
    if isinstance(spec, dict):
        tag = spec.get("tag")
        if tag and isinstance(tag, str):
            return (None, tag)
        name = spec.get("name")
        if name and isinstance(name, str):
            return (name, None)
    _log.warning(
        "worker_dispatch: app %r has malformed run_on=%r — running locally",
        app_def.get("app"), spec,
    )
    return (None, None)


def should_dispatch_remotely(app_def: dict) -> bool:
    name, tag = parse_run_on(app_def)
    return name is not None or tag is not None


def resolve_app_def_for_dispatch(
    app_def: dict,
    *,
    secrets_store=None,
    config: Optional[dict] = None,
) -> dict:
    """Resolve ``$VAR`` references in ``app_def`` for a remote worker.

    Values come from the secrets store, then the ``apps_env_allowlist``
    subset of process env (never arbitrary env). ``extract:`` capture names
    are seeded with their own ``$NAME`` placeholder so they survive for the
    worker's per-test resolution. Raises ``ConfigError`` on a missing var or
    when a resolved ``url``/``environments.*`` value is not http(s).
    """
    from harness.app_manager import AppValidationError, validate_app_urls

    extra_vars = (
        secrets_store.get_all_decrypted() if secrets_store is not None else {}
    )
    capture_seeds = {}
    for td in app_def.get("tests", []):
        for capture_name in (td.get("extract") or {}).keys():
            capture_seeds[capture_name] = "$" + capture_name
    resolved = resolve_env_vars(
        app_def, strict=True, extra_vars={**extra_vars, **capture_seeds},
        environ=app_environ(config),
    )
    try:
        validate_app_urls(resolved, allow_refs=False, redact=True)
    except AppValidationError as exc:
        raise ConfigError(f"app {resolved.get('app', '?')!r}: {exc}") from None
    return resolved


def queue_remote_run(
    db,
    app_def: dict,
    environment: str,
    triggered_by: str,
    secrets_store=None,
    run_id: Optional[str] = None,
    config: Optional[dict] = None,
) -> str:
    """Create a Run row in state='queued' and a runner_jobs row carrying the
    fully-resolved app definition. Returns the run_id.

    The app_def is resolved here (not on the worker) because the central
    harness owns the secrets store. The worker sees the resolved version
    with plaintext secrets in headers / fill values / etc. ``config`` supplies
    ``apps_env_allowlist``; without it no process env is exposed.
    """
    # Resolve secrets the same way harness.runner.run_app does.
    resolved = resolve_app_def_for_dispatch(
        app_def, secrets_store=secrets_store, config=config,
    )
    from harness.app_manager import resolve_safe_base_url
    # The worker navigates to this; fail before queueing, not on the worker.
    resolve_safe_base_url(resolved, environment)

    runner_name, target_tag = parse_run_on(resolved)

    # Create the Run record so the UI / API can poll for it.
    if run_id:
        run = Run(id=run_id, app=resolved["app"], environment=environment,
                  triggered_by=triggered_by, status="queued")
    else:
        run = Run(app=resolved["app"], environment=environment,
                  triggered_by=triggered_by, status="queued")
        db.insert_run(run)
        # insert_run hard-codes status='pending'; fix it
        db.update_run_status(run.id, "queued")

    db.queue_runner_job(
        run_id=run.id,
        app_def_json=json.dumps(resolved),
        environment=environment,
        runner_name=runner_name,
        target_tag=target_tag,
    )
    notify_job_queued()
    _log.info(
        "worker_dispatch: queued run %s for app=%s env=%s (runner_name=%r tag=%r)",
        run.id, resolved["app"], environment, runner_name, target_tag,
    )
    return run.id


# ── Result ingestion (called by /api/runner/results) ────────────────────────

async def ingest_runner_results(
    db,
    config: dict,
    job_id: str,
    run_status: str,
    test_results: list,
    error: Optional[str] = None,
    screenshot_paths: Optional[dict] = None,
) -> dict:
    """Persist worker-submitted results, run alert state-transition logic,
    fire the run webhook if configured. Mirrors the post-execution half of
    harness.runner.run_app so behavior is identical regardless of where
    the tests actually ran.

    `test_results` is a list of dicts shaped like TestResult JSON:
        {test_name, status, error_msg, duration_ms, finished_at, step_log}
    `run_status` is "complete" or "error".
    ``screenshot_paths`` maps step_name → URL path (set by
    web/routes/runner.py when the worker uploaded artifacts).
    """
    screenshot_paths = screenshot_paths or {}
    from harness.alerts import dispatch_alerts, dispatch_run_webhook
    from harness.models import AppState, StepResult, TestResult
    from harness.maintenance import is_in_maintenance
    from harness.runner import determine_alert

    job = db.get_runner_job(job_id)
    if job is None:
        raise ValueError(f"unknown runner job: {job_id}")
    run = db.get_run(job["run_id"])
    if run is None:
        raise ValueError(f"runner job {job_id} references missing run {job['run_id']}")
    app = run["app"]
    environment = job["environment"]

    db.update_run_status(
        run["id"], "running",
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    alerts_to_send = []
    for r in test_results:
        # Re-hydrate step_log into StepResult records
        step_log = [
            StepResult(
                step=s.get("step", ""),
                status=s.get("status", ""),
                duration_ms=int(s.get("duration_ms") or 0),
                error=s.get("error"),
                screenshot=s.get("screenshot"),
            )
            for s in (r.get("step_log") or [])
        ]
        # If the worker uploaded a screenshot for this test, prefer the local
        # storage path over whatever the worker recorded in ``r["screenshot"]``
        # (which may be a path on the worker's own filesystem). The match
        # logic looks for either the test name or a step name as the dict key.
        resolved_screenshot = r.get("screenshot")
        for candidate_key in (r["test_name"], *(s.get("step", "") for s in (r.get("step_log") or []))):
            if candidate_key and candidate_key in screenshot_paths:
                resolved_screenshot = screenshot_paths[candidate_key]
                break

        tr = TestResult(
            run_id=run["id"],
            app=app,
            environment=environment,
            test_name=r["test_name"],
            status=r.get("status", "error"),
            error_msg=r.get("error_msg"),
            step_log=step_log,
            screenshot=resolved_screenshot,
            duration_ms=r.get("duration_ms"),
            finished_at=r.get("finished_at"),
        )
        db.insert_test_result(tr)
        if resolved_screenshot and resolved_screenshot.startswith("/screenshots/"):
            # Record on test_results.screenshot_path too — the dashboard
            # falls back to the canonical column when the embedded blob
            # isn't usable.
            db.set_test_result_screenshot_path(tr.id, resolved_screenshot)

        prev = db.get_app_state(app, environment, tr.test_name)
        prev_state = prev["state"] if prev else "unknown"
        new_state_str = "passing" if tr.status == "pass" else "failing"
        db.upsert_app_state(AppState(
            app=app, environment=environment,
            test_name=tr.test_name, state=new_state_str,
            since=tr.finished_at or datetime.now(timezone.utc).isoformat(),
        ))
        alert_type = determine_alert(prev_state, tr.status)
        if alert_type:
            alerts_to_send.append(
                (alert_type, app, environment, tr.test_name, tr.error_msg)
            )

    finished_at = datetime.now(timezone.utc).isoformat()
    final_status = "complete" if error is None else "error"
    db.update_run_status(run["id"], final_status, finished_at=finished_at)
    db.clear_stale_alert(app, environment)

    db.complete_runner_job(job_id, error=error)

    maint = is_in_maintenance(config)
    if maint and alerts_to_send:
        _log.info(
            "alerts: suppressed %d alert(s) for %s — maintenance window %r active",
            len(alerts_to_send), app, maint,
        )
        alerts_to_send = []
    if alerts_to_send:
        await dispatch_alerts(alerts_to_send, (config.get("alerts") or {}))

    webhook_cfg = (config.get("alerts") or {}).get("webhook") or {}
    if webhook_cfg.get("url") and not maint:
        run_results = db.get_results_for_run(run["id"])
        await dispatch_run_webhook(
            run["id"], app, environment, final_status,
            run["triggered_by"], finished_at, run_results, webhook_cfg,
        )

    return {"run_id": run["id"], "status": final_status,
            "tests_recorded": len(test_results)}


# ── Stuck-job reaper (feature #1) ───────────────────────────────────────────
#
# When a worker crashes or loses network mid-job, its ``runner_jobs`` row is
# stuck in state='assigned' with no completion. The reaper periodically scans
# for such rows older than ``job_timeout_seconds`` and either:
#   - re-queues them (state='queued', retry_count += 1), or
#   - if retry_count >= ``max_retries``, marks them 'failed' so the Run row
#     surfaces in the dashboard as an error.

DEFAULT_JOB_TIMEOUT_SECONDS = 600
DEFAULT_MAX_RETRIES = 3
DEFAULT_REAPER_INTERVAL_SECONDS = 60


def reap_stuck_jobs(
    db,
    *,
    job_timeout_seconds: int = DEFAULT_JOB_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    now: Optional[datetime] = None,
) -> dict:
    """Scan for stuck 'assigned' jobs and either requeue or fail them.

    Returns a summary dict::

        {"requeued": int, "failed": int, "scanned": int}

    Pure function on the DB — safe to call from anywhere (scheduler, tests).
    Logs every state transition for forensic analysis.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(seconds=job_timeout_seconds)).isoformat()
    stuck = db.find_stuck_runner_jobs(older_than=cutoff)
    requeued = 0
    failed = 0
    for job in stuck:
        current_retries = int(job.get("retry_count") or 0)
        job_id = job["id"]
        if current_retries >= max_retries:
            msg = (
                f"stuck job {job_id} exceeded max_retries={max_retries} "
                f"(was assigned to {job.get('assigned_to')!r} at "
                f"{job.get('assigned_at')!r}); marking failed"
            )
            db.fail_runner_job(job_id, error=msg)
            failed += 1
            _log.warning("reaper: %s", msg)
        else:
            db.requeue_runner_job(job_id)
            requeued += 1
            _log.info(
                "reaper: requeued stuck job %s (was assigned to %r at %r; "
                "retry_count %d → %d)",
                job_id, job.get("assigned_to"), job.get("assigned_at"),
                current_retries, current_retries + 1,
            )
    if requeued:
        # Wake any long-poll workers — they may pick up the requeued jobs.
        notify_job_queued()
    if requeued or failed:
        _log.info(
            "reaper: scanned=%d requeued=%d failed=%d (timeout=%ds max_retries=%d)",
            len(stuck), requeued, failed, job_timeout_seconds, max_retries,
        )
    return {"scanned": len(stuck), "requeued": requeued, "failed": failed}


def add_reaper_job(
    scheduler,
    db,
    *,
    config: Optional[dict] = None,
    interval_seconds: int = DEFAULT_REAPER_INTERVAL_SECONDS,
) -> None:
    """Wire the reaper onto an APScheduler. Reads tunables from ``config``
    under the ``worker`` key with sensible defaults.

    Config shape::

        worker:
          job_timeout_seconds: 600    # how long before 'assigned' is stuck
          max_retries: 3
          reaper_interval_seconds: 60
    """
    from apscheduler.triggers.interval import IntervalTrigger

    worker_cfg = (config or {}).get("worker") or {}
    timeout = int(worker_cfg.get("job_timeout_seconds", DEFAULT_JOB_TIMEOUT_SECONDS))
    max_retries = int(worker_cfg.get("max_retries", DEFAULT_MAX_RETRIES))
    interval = int(worker_cfg.get("reaper_interval_seconds", interval_seconds))

    def _cb() -> None:
        try:
            reap_stuck_jobs(db, job_timeout_seconds=timeout, max_retries=max_retries)
        except Exception:
            _log.exception("reaper: scan failed")

    scheduler.add_job(
        _cb,
        IntervalTrigger(seconds=interval),
        id="worker_dispatch::reaper",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    _log.info(
        "reaper: scheduled every %ds (timeout=%ds, max_retries=%d)",
        interval, timeout, max_retries,
    )
