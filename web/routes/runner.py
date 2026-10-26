"""Worker (edge runner) protocol — registration, job polling, result submission,
heartbeat. Workers authenticate with an existing API key (Bearer hth_…) so we
reuse the user/role/audit infrastructure without inventing a new credential
type. CSRF doesn't apply (Bearer-only, /api/* exempt).

Every endpoint goes through ``require_worker``: the caller must present an API
key (session cookies are rejected) owned by a user with role admin or runner.
Job descriptors carry resolved secrets, so a scope check alone isn't enough —
any user can mint a key.

See ``docs/superpowers/plans/2026-05-14-remote-worker.md`` for the design.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from typing import Optional

from web.auth import require_scope

router = APIRouter(prefix="/api/runner")
_log = logging.getLogger("harness.runner_api")

# Hard cap on how long the server will hold a long-poll connection open.
# Longer than this and intermediate proxies / cloud LBs start killing the
# connection silently.
_MAX_LONG_POLL_WAIT_SECONDS = 60


# Roles allowed to act as a worker. The runner role can already edit app YAML
# (and so read any secret through an app), so this grants nothing new.
_WORKER_ROLES = ("admin", "runner")


def require_worker(scope: str):
    """Dependency for /api/runner/*: ``require_scope(scope)`` plus API-key
    auth and a worker-capable role. Returns ``(user, api_key_row)``.

    * 401 when the request authenticated with a session cookie — workers
      always use a key, and the key id is what a runner name is bound to.
    * 403 when the key's owner isn't admin or runner. Applies to legacy
      wildcard keys too.
    """
    scope_dep = require_scope(scope)

    async def _dep(request: Request, user: dict = Depends(scope_dep)):
        api_key = getattr(request.state, "api_key", None)
        if api_key is None:
            raise HTTPException(
                status_code=401,
                detail="runner endpoints require API-key (Bearer) authentication",
            )
        if user.get("role") not in _WORKER_ROLES:
            _log.info(
                "require_worker: user %s (role=%r) denied on %s",
                user.get("username"), user.get("role"), request.url.path,
            )
            raise HTTPException(
                status_code=403,
                detail="runner endpoints require a user with role 'admin' or 'runner'",
            )
        return user, api_key
    return _dep


def _require_bound(db, name: str, api_key: dict) -> None:
    """403 unless runner ``name`` is bound to the caller's API key. Used by
    /poll, /jobs and /heartbeat after the 404 check. An unbound row (legacy,
    or registered before name↔key binding existed) is refused too — the
    worker re-registers to claim it."""
    if db.check_runner_binding(name, api_key["id"]) != "ok":
        raise HTTPException(
            status_code=403,
            detail=(
                f"runner {name!r} is not bound to this API key; "
                "re-register with this key"
            ),
        )


def _bound_key_releasable(bound_key: Optional[dict], user: dict) -> bool:
    """True when a runner bound to ``bound_key`` may be rebound by ``user``:
    the old key is gone, revoked or expired, or belongs to the same user
    (key rotation for one service account)."""
    if bound_key is None or not bound_key.get("is_active"):
        return True
    if bound_key.get("expires_at"):
        expires = datetime.fromisoformat(bound_key["expires_at"])
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < datetime.now(timezone.utc):
            return True
    return bound_key.get("user_id") == user["id"]


# ── Schemas ─────────────────────────────────────────────────────────────────

class RunnerRegisterRequest(BaseModel):
    name: str
    tags: list[str] = []
    capabilities: list[str] = []   # e.g. ["api", "availability", "browser"]
    version: Optional[str] = None


class RunnerRegisterResponse(BaseModel):
    name: str
    poll_interval_seconds: int
    capacity: int
    server_time: str
    config_version: str
    # workers-v2-1: optional version-skew notice. Populated when the worker's
    # MAJOR version differs from central — the dispatch path will refuse to
    # send it jobs until upgraded. Worker should log this on each receive.
    notice: Optional[str] = None
    # workers-v2-1: central's reported version, echoed so the worker can log
    # an alignment line on startup ("worker 1.0.0 vs central 1.0.0").
    central_version: Optional[str] = None


class RunnerPollRequest(BaseModel):
    name: str
    capacity: int = 1


class RunnerStepLog(BaseModel):
    step: str
    status: str
    duration_ms: int = 0
    error: Optional[str] = None
    screenshot: Optional[str] = None


class RunnerTestResult(BaseModel):
    test_name: str
    status: str                    # "pass" | "fail" | "error"
    error_msg: Optional[str] = None
    duration_ms: Optional[int] = None
    finished_at: Optional[str] = None
    step_log: list[RunnerStepLog] = []
    screenshot: Optional[str] = None


class RunnerScreenshot(BaseModel):
    step_name: str
    content_base64: str
    mime_type: str = "image/png"


class RunnerResultsRequest(BaseModel):
    job_id: str
    run_status: str = "complete"   # "complete" | "error"
    error: Optional[str] = None
    results: list[RunnerTestResult]
    # Optional artifact uploads — base64-encoded image bytes keyed by step
    # name. Each entry is capped at MAX_SCREENSHOT_BYTES on the server.
    screenshots: list[RunnerScreenshot] = []


class RunnerHeartbeatRequest(BaseModel):
    name: str
    # workers-v2-1: worker self-reported volume / outbox health. None preserved
    # for older worker builds that don't send this. 'healthy' | 'unhealthy'.
    health: Optional[str] = None
    # workers-v2-1: worker reports its version on every heartbeat too, not
    # just register — central re-classifies skew per ping so an upgrade
    # mid-run takes effect immediately.
    version: Optional[str] = None


class RunnerHeartbeatResponse(BaseModel):
    ok: bool = True
    server_time: str
    # workers-v2-1: major-version incompatibility warning. Workers log this
    # on receive; it repeats every heartbeat until the worker is upgraded.
    notice: Optional[str] = None


# ── Endpoints ───────────────────────────────────────────────────────────────

@router.post("/register", response_model=RunnerRegisterResponse)
async def runner_register(
    req: RunnerRegisterRequest,
    worker: tuple = Depends(require_worker("worker:results")),
):
    """Worker announces itself on startup. Idempotent: re-registering with
    the same name updates tags/capabilities/version and resets last_seen_at.

    The first register binds the runner name to the caller's API key; later
    poll/jobs/heartbeat/results calls must use that key. Registering a name
    bound to another key is refused (409) unless that key is revoked,
    expired or deleted, or belongs to the same user — otherwise an admin
    must delete the runner first.

    On register we also classify the worker's version against central:
    same major + minor within N → ok; same major + minor far behind → stale
    (badge on /admin/runners); different major → incompatible (the dispatch
    path refuses jobs and the response carries a ``notice`` field the worker
    logs and re-receives on every heartbeat).
    """
    from web.main import get_db, get_config
    from harness.version_skew import (
        central_version,
        classify_skew,
        incompatibility_notice,
        is_version_stale,
    )
    current_user, api_key = worker
    db = get_db()
    if not req.name.strip():
        raise HTTPException(status_code=422, detail="name must be non-empty")

    api_key_id = api_key["id"]
    name = req.name.strip()

    # Bind check happens here, not in upsert_runner: the upsert overwrites
    # api_key_id unconditionally, which is exactly what a rebind needs.
    existing = db.get_runner_by_name(name)
    binding = db.check_runner_binding(name, api_key_id)
    if binding == "conflict":
        if not _bound_key_releasable(db.get_runner_bound_key(name), current_user):
            _log.warning(
                "runner_register: %s refused for user %s — bound to another key",
                name, current_user.get("username"),
            )
            raise HTTPException(
                status_code=409,
                detail=(
                    "runner name already bound to another API key; "
                    "an admin must delete the runner to rebind"
                ),
            )

    cv = central_version()
    warning_minors = _version_skew_warning_minors(get_config())
    stale = is_version_stale(req.version, cv, warning_minors=warning_minors)
    notice = incompatibility_notice(req.version, cv)
    classification = classify_skew(req.version, cv, warning_minors=warning_minors)

    runner = db.upsert_runner(
        name=name,
        tags=req.tags,
        capabilities=req.capabilities,
        version=req.version,
        api_key_id=api_key_id,
        version_stale=1 if stale else 0,
    )
    db.log_audit(
        "runner.register",
        user_id=current_user["id"],
        username=current_user.get("username"),
        target=runner["name"],
        details=(
            f"tags={runner['tags']} caps={runner['capabilities']} "
            f"v={runner.get('version') or '-'} skew={classification}"
        ),
    )
    if existing is not None and binding != "ok":
        db.log_audit(
            "runner.rebind",
            user_id=current_user["id"],
            username=current_user.get("username"),
            target=runner["name"],
            details=f"api_key {existing.get('api_key_id') or '-'} -> {api_key_id}",
        )
    if notice:
        _log.warning(
            "runner_register: %s reported version=%r incompatible with central=%r",
            runner["name"], req.version, cv,
        )
    return RunnerRegisterResponse(
        name=runner["name"],
        poll_interval_seconds=10,
        capacity=4,
        server_time=datetime.now(timezone.utc).isoformat(),
        config_version="1",
        notice=notice,
        central_version=cv,
    )


def _version_skew_warning_minors(config: Optional[dict]) -> int:
    """Resolve ``worker.version_skew_warning_minors`` from config, falling
    back to the module default (2). Tolerates missing or malformed values
    silently — the feature should never crash a heartbeat."""
    try:
        from harness.version_skew import DEFAULT_WARNING_MINORS
        val = ((config or {}).get("worker") or {}).get(
            "version_skew_warning_minors", DEFAULT_WARNING_MINORS,
        )
        return int(val)
    except (TypeError, ValueError):
        from harness.version_skew import DEFAULT_WARNING_MINORS
        return DEFAULT_WARNING_MINORS


@router.post("/poll")
async def runner_poll(
    req: RunnerPollRequest,
    worker: tuple = Depends(require_worker("worker:pull")),
):
    """Worker asks for next jobs. Returns 0..capacity job descriptors,
    immediately (short-poll v1). Updates the worker's last_seen_at.

    workers-v2-1: refuses to dispatch any jobs to a worker whose MAJOR
    version differs from central — the worker is still allowed to poll
    (so it can keep heartbeating and surface its incompatibility in the
    admin UI), but receives an empty job list. The hard refusal happens
    here rather than at register so a major-version mismatch detected
    mid-deploy doesn't drop the worker from the registry.
    """
    from web.main import get_db
    from harness.version_skew import is_major_incompatible, central_version
    _, api_key = worker
    db = get_db()
    runner = db.get_runner_by_name(req.name)
    if runner is None:
        raise HTTPException(status_code=404, detail=f"runner {req.name!r} not registered")
    _require_bound(db, req.name, api_key)
    if runner.get("status") == "draining":
        # Draining workers receive no new jobs, but we still touch heartbeat
        db.touch_runner_heartbeat(req.name, datetime.now(timezone.utc).isoformat())
        return {"jobs": []}
    if is_major_incompatible(runner.get("version"), central_version()):
        # Hard refusal: incompatible major version. Heartbeat still ticks
        # so the worker's row stays "online" in the UI with its red badge.
        db.touch_runner_heartbeat(req.name, datetime.now(timezone.utc).isoformat())
        _log.warning(
            "runner_poll: refusing dispatch to %s — version %r incompatible with central %r",
            req.name, runner.get("version"), central_version(),
        )
        return {"jobs": []}

    db.touch_runner_heartbeat(req.name, datetime.now(timezone.utc).isoformat())
    claimed = db.claim_runner_jobs(
        worker_name=req.name,
        worker_tags=runner.get("tags") or [],
        capacity=max(1, min(int(req.capacity), 16)),
    )
    jobs = []
    for j in claimed:
        try:
            app_def = json.loads(j["app_def_json"])
        except Exception:
            app_def = {}
        jobs.append({
            "job_id": j["id"],
            "run_id": j["run_id"],
            "environment": j["environment"],
            "app_def": app_def,
        })
    return {"jobs": jobs}


@router.post("/results")
async def runner_results(
    req: RunnerResultsRequest,
    worker: tuple = Depends(require_worker("worker:results")),
):
    """Worker submits completed test results for a job it owns — i.e. the
    job is assigned to a runner bound to the caller's API key.

    Optional ``screenshots`` entries are base64-decoded and written under
    ``data/screenshots/{run_id}/{step_name}.{ext}``. Any individual screenshot
    larger than ``MAX_SCREENSHOT_BYTES`` (5 MiB) is rejected with 413.
    """
    from web.main import get_db, get_config
    from harness.worker_dispatch import ingest_runner_results
    _, api_key = worker
    db = get_db()
    job = db.get_runner_job(req.job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job: {req.job_id}")
    if job["state"] != "assigned":
        raise HTTPException(
            status_code=409,
            detail=f"job {req.job_id} is in state {job['state']!r}, expected 'assigned'",
        )
    # Results carry no runner name, so ownership goes job → assigned runner
    # → that runner's bound key.
    owner = db.get_runner_by_name(job.get("assigned_to") or "")
    if owner is None or owner.get("api_key_id") != api_key["id"]:
        raise HTTPException(
            status_code=403,
            detail=f"job {req.job_id} is not assigned to a runner bound to this API key",
        )

    # Pre-flight: size-check every base64 screenshot before doing any work.
    # We compute the *decoded* size from the base64 length so a malicious
    # worker can't OOM us by sending a huge encoded blob.
    for s in req.screenshots:
        decoded_size = (len(s.content_base64) * 3) // 4
        if decoded_size > MAX_SCREENSHOT_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"screenshot for step {s.step_name!r} is {decoded_size} bytes; "
                    f"max is {MAX_SCREENSHOT_BYTES}"
                ),
            )

    # Save screenshots to disk, keyed by run_id / step_name. Update
    # ``test_results.screenshot_path`` so the dashboard's image links work.
    saved_paths = _save_screenshots(job["run_id"], req.screenshots)

    try:
        summary = await ingest_runner_results(
            db,
            get_config() or {},
            job_id=req.job_id,
            run_status=req.run_status,
            test_results=[r.model_dump() for r in req.results],
            error=req.error,
            screenshot_paths=saved_paths,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "screenshots_saved": len(saved_paths), **summary}


MAX_SCREENSHOT_BYTES = 5 * 1024 * 1024  # 5 MiB, mirrors worker-side limit

# Map MIME types to safe file extensions. Anything not on this list is
# stored as ``.bin`` rather than letting the worker dictate the file
# suffix — defence in depth against arbitrary-extension uploads.
_MIME_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}


def _save_screenshots(run_id: str, screenshots: list) -> dict:
    """Decode and persist screenshots under ``data/screenshots/<run_id>/``.
    Returns ``{step_name: relative_path}`` for every successfully saved
    file. Anything that fails to decode is logged and skipped — bad data
    from one screenshot shouldn't drop an entire results payload.
    """
    import base64
    import os
    import re

    if not screenshots:
        return {}

    # Guard against directory-traversal in run_id. UUIDs are safe but never
    # trust input — sanitise hard.
    safe_run = re.sub(r"[^A-Za-z0-9._-]", "_", run_id)[:100]
    out_dir = os.path.join("data", "screenshots", safe_run)
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as e:
        _log.error("screenshots: cannot create %s: %s", out_dir, e)
        return {}

    saved: dict = {}
    for s in screenshots:
        ext = _MIME_EXT.get(s.mime_type, "bin")
        safe_step = re.sub(r"[^A-Za-z0-9._-]", "_", s.step_name)[:120] or "step"
        path = os.path.join(out_dir, f"{safe_step}.{ext}")
        try:
            data = base64.b64decode(s.content_base64, validate=True)
        except Exception as e:  # pragma: no cover — malformed b64 is rare
            _log.warning("screenshots: decode failed for %s: %s", s.step_name, e)
            continue
        if len(data) > MAX_SCREENSHOT_BYTES:
            _log.warning(
                "screenshots: decoded size %d exceeds cap; skipping %s",
                len(data), s.step_name,
            )
            continue
        try:
            with open(path, "wb") as f:
                f.write(data)
        except OSError as e:
            _log.error("screenshots: write %s failed: %s", path, e)
            continue
        # Store the URL path the dashboard's <img src> uses.
        saved[s.step_name] = f"/screenshots/{safe_run}/{safe_step}.{ext}"
    return saved


@router.post("/heartbeat", response_model=RunnerHeartbeatResponse)
async def runner_heartbeat(
    req: RunnerHeartbeatRequest,
    worker: tuple = Depends(require_worker("worker:results")),
):
    """Liveness ping. Workers call this periodically (~30s) between polls
    so the /admin/runners status display stays accurate even if the worker
    is idle (no queued jobs).

    workers-v2-1 additions:
    * ``health`` field — when "unhealthy", auto-drain the runner so no new
      jobs are dispatched. When the worker recovers and reports "healthy"
      we restore status='online'. Status='draining' set by an admin is
      preserved across health flips (we only flip back to 'online' if the
      previous reason was a worker-reported unhealthy state).
    * ``version`` field — re-classified per ping so an upgrade or rollback
      mid-run takes effect on the very next heartbeat without re-register.
    * ``notice`` in response — major-version-mismatch warning repeated on
      every heartbeat; the worker logs it for ops visibility.
    """
    from web.main import get_db, get_config
    from harness.version_skew import (
        central_version,
        incompatibility_notice,
        is_version_stale,
    )
    _, api_key = worker
    db = get_db()
    runner = db.get_runner_by_name(req.name)
    if runner is None:
        raise HTTPException(status_code=404, detail=f"runner {req.name!r} not registered")
    _require_bound(db, req.name, api_key)

    db.touch_runner_heartbeat(req.name, datetime.now(timezone.utc).isoformat())

    # Re-classify version skew on every heartbeat. The worker MAY have been
    # upgraded since registration; we want the badge / dispatch decision to
    # reflect reality without forcing a re-register.
    cv = central_version()
    warning_minors = _version_skew_warning_minors(get_config())
    notice = None
    if req.version is not None:
        stale = is_version_stale(req.version, cv, warning_minors=warning_minors)
        db.set_runner_version_stale(req.name, stale)
        notice = incompatibility_notice(req.version, cv)
        if notice:
            _log.warning(
                "runner_heartbeat: %s version=%r incompatible with central=%r",
                req.name, req.version, cv,
            )

    # Worker self-reported health. Persist and auto-toggle status:
    # * 'unhealthy' → status='draining' (no new jobs)
    # * 'healthy' AND current row was unhealthy → status='online'
    # We're careful NOT to flip 'draining' workers back to 'online' if the
    # admin drained them manually (their last health was healthy/None).
    if req.health in ("healthy", "unhealthy"):
        previous_health = runner.get("health")
        db.set_runner_health(req.name, req.health)
        if req.health == "unhealthy":
            # Always drain — admin can still re-drain explicitly if they
            # care, and the next 'healthy' ping will restore.
            if runner.get("status") != "draining":
                db.set_runner_status(req.name, "draining")
                _log.warning(
                    "runner_heartbeat: %s reported unhealthy — auto-draining",
                    req.name,
                )
        elif req.health == "healthy" and previous_health == "unhealthy":
            # Recovered. Restore to online so dispatch resumes.
            db.set_runner_status(req.name, "online")
            _log.info(
                "runner_heartbeat: %s recovered (healthy) — auto-restoring online",
                req.name,
            )

    return RunnerHeartbeatResponse(
        ok=True,
        server_time=datetime.now(timezone.utc).isoformat(),
        notice=notice,
    )


# ── Long-poll job retrieval (feature #4) ────────────────────────────────────
#
# GET /api/runner/jobs?wait=30&name=eu-worker-1&capacity=4
#
# Blocks up to ``wait`` seconds waiting for a queued job that matches this
# worker. The block ends as soon as harness.worker_dispatch.notify_job_queued()
# is called by the dispatcher (when ``queue_remote_run`` lands a new job, or
# when the reaper requeues a stuck one).
#
# If no job is delivered within the timeout, returns 204 No Content with a
# ``Retry-After`` header so the worker can fall back to fixed-interval polling.

@router.get("/jobs")
async def runner_jobs_long_poll(
    request: Request,
    response: Response,
    name: str,
    wait: int = 0,
    capacity: int = 1,
    worker: tuple = Depends(require_worker("worker:pull")),
):
    """Long-poll alternative to POST /poll. Matches the same dispatch semantics
    but blocks until a job is available (or ``wait`` seconds elapse)."""
    from web.main import get_db
    from harness.worker_dispatch import get_job_queued_event

    _, api_key = worker
    db = get_db()
    runner = db.get_runner_by_name(name)
    if runner is None:
        raise HTTPException(status_code=404, detail=f"runner {name!r} not registered")
    _require_bound(db, name, api_key)

    # Clamp wait into [0, 60] — beyond that, intermediate proxies kill it.
    wait = max(0, min(int(wait), _MAX_LONG_POLL_WAIT_SECONDS))
    cap = max(1, min(int(capacity), 16))

    db.touch_runner_heartbeat(name, datetime.now(timezone.utc).isoformat())

    if runner.get("status") == "draining":
        response.headers["Retry-After"] = "30"
        response.status_code = 204
        return Response(status_code=204, headers={"Retry-After": "30"})

    # workers-v2-1: hard refusal for major-version mismatch. Same shape as
    # the draining case — 204 with a generous Retry-After so an incompatible
    # worker doesn't hammer the central with long-poll attempts.
    from harness.version_skew import is_major_incompatible, central_version
    if is_major_incompatible(runner.get("version"), central_version()):
        _log.warning(
            "runner_jobs: refusing dispatch to %s — version %r incompatible with central %r",
            name, runner.get("version"), central_version(),
        )
        return Response(status_code=204, headers={"Retry-After": "60"})

    deadline = asyncio.get_event_loop().time() + wait
    event = get_job_queued_event()

    while True:
        claimed = db.claim_runner_jobs(
            worker_name=name,
            worker_tags=runner.get("tags") or [],
            capacity=cap,
        )
        if claimed:
            jobs = []
            for j in claimed:
                try:
                    app_def = json.loads(j["app_def_json"])
                except Exception:
                    app_def = {}
                jobs.append({
                    "job_id": j["id"],
                    "run_id": j["run_id"],
                    "environment": j["environment"],
                    "app_def": app_def,
                })
            return {"jobs": jobs}

        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            return Response(
                status_code=204,
                headers={"Retry-After": str(max(1, wait // 2 if wait else 30))},
            )
        # Wait on the queued-job event; on wake, re-attempt the claim. Spurious
        # wake-ups are fine — the next claim attempt simply returns nothing
        # and we loop back here.
        try:
            await asyncio.wait_for(event.wait(), timeout=remaining)
        except asyncio.TimeoutError:
            return Response(
                status_code=204,
                headers={"Retry-After": str(max(1, wait // 2 if wait else 30))},
            )
        # Defensive: clear so the next iteration blocks again.
        event.clear()
