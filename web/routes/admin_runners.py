"""Admin UI for managing remote workers — list, drain, remove."""
import logging
import os
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from web.auth import get_api_key_scopes, require_role
from web.csrf import csrf_token_for, verify_csrf
from web.templating import make_templates

_log = logging.getLogger("web.admin_runners")

router = APIRouter()
templates = make_templates()


def _ctx(request: Request, current_user: dict, **extra) -> dict:
    from web.main import get_config
    config = get_config()
    return {
        "request": request,
        "environments": config.get("environments", {}),
        "environment": None,
        "current_user": current_user,
        "csrf_token": csrf_token_for(request),
        **extra,
    }


def _is_online(last_seen_at: str | None, now: datetime | None = None) -> bool:
    """A runner is 'online' if its heartbeat is within the last 90 seconds."""
    if not last_seen_at:
        return False
    if now is None:
        now = datetime.now(timezone.utc)
    try:
        ts = datetime.fromisoformat(last_seen_at.rstrip("Z"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return (now - ts) < timedelta(seconds=90)


def _resolve_screenshot_filesize(url_path: str, _cache: dict) -> int:
    """Best-effort ``os.path.getsize`` for a /screenshots/<...> URL path,
    cached per request so a panel listing many test results doesn't stat
    the same file repeatedly.

    Returns 0 if the file is missing or unreadable — we still want to
    report the count of uploads even if disk lookups fail.
    """
    if not url_path:
        return 0
    if url_path in _cache:
        return _cache[url_path]
    # /screenshots/foo/bar.png  →  data/screenshots/foo/bar.png
    if url_path.startswith("/screenshots/"):
        rel = url_path[len("/"):]
        disk_path = os.path.join("data", rel)
    else:
        disk_path = url_path
    try:
        size = os.path.getsize(disk_path)
    except OSError:
        size = 0
    _cache[url_path] = size
    return size


def _build_screenshot_stats(db, since_iso: str) -> dict:
    """Aggregate uploaded screenshots over the last 24h. Returns
    ``{"count": int, "total_bytes": int}``. File sizes are pulled from
    disk and memoised so we don't stat the same path twice."""
    rows = db.list_recent_screenshots_for_workers(since_iso)
    size_cache: dict = {}
    total_bytes = 0
    for row in rows:
        total_bytes += _resolve_screenshot_filesize(
            row.get("screenshot_path") or "", size_cache,
        )
    return {"count": len(rows), "total_bytes": total_bytes}


def _key_scopes_for_runner(db, runner: dict) -> list[str] | None:
    """Look up the scope list of the API key the runner was registered with.
    Returns None when the runner row has no ``api_key_id`` (legacy
    registration or the key was rotated) — the template renders that as
    an em-dash.
    """
    key_id = runner.get("api_key_id")
    if not key_id:
        return None
    api_key = db.get_api_key_by_id(key_id)
    if api_key is None:
        return None
    return sorted(get_api_key_scopes(api_key))


@router.get("/admin/runners", response_class=HTMLResponse)
async def runners_list(request: Request,
                       current_user: dict = Depends(require_role("admin"))):
    from web.main import get_db, get_config
    from harness.version_skew import central_version, classify_skew
    db = get_db()
    runners = db.list_runners()
    now = datetime.now(timezone.utc)
    since_24h = (now - timedelta(hours=24)).isoformat()
    # workers-v2-1: classify each runner's version against central so the
    # template can render the stale / incompatible badges. We compute this
    # at render time rather than reading the DB column alone because the
    # column only tracks "stale" (minor drift) — the "incompatible" decision
    # depends on the LIVE central version which can shift between heartbeats.
    cv = central_version()
    cfg = get_config() or {}
    warning_minors = int(
        ((cfg.get("worker") or {}).get("version_skew_warning_minors", 2))
    )
    enriched = []
    for r in runners:
        is_on = _is_online(r.get("last_seen_at"), now=now)
        if r.get("status") == "draining":
            display_status = "draining"
        elif is_on:
            display_status = "online"
        else:
            display_status = "offline"
        skew = classify_skew(
            r.get("version"), cv, warning_minors=warning_minors,
        )
        enriched.append({
            **r,
            "is_online": is_on,
            "display_status": display_status,
            "inflight_jobs": db.count_inflight_jobs_for_runner(r["name"]),
            "version_skew": skew,
            # workers-v2 surfaces (Task #8B)
            "retries_24h": db.sum_retry_count_for_runner(
                r["name"], since_iso=since_24h,
            ),
            "key_scopes": _key_scopes_for_runner(db, r),
        })
    screenshot_stats = _build_screenshot_stats(db, since_24h)
    failed_runs = db.list_reaper_failed_runs(limit=50)
    return templates.TemplateResponse(
        request, "admin_runners.html",
        _ctx(
            request, current_user,
            runners=enriched,
            screenshot_stats=screenshot_stats,
            failed_runs=failed_runs,
        ),
    )


@router.post("/admin/runners/{name}/drain")
async def runners_drain(
    request: Request,
    name: str,
    current_user: dict = Depends(require_role("admin")),
    _csrf=Depends(verify_csrf),
):
    """Mark a runner as draining — server stops assigning new jobs to it but
    its in-flight jobs finish normally."""
    from web.main import get_db
    db = get_db()
    if db.get_runner_by_name(name) is None:
        raise HTTPException(status_code=404, detail=f"runner {name!r} not found")
    db.set_runner_status(name, "draining")
    db.log_audit(
        "runner.drain",
        user_id=current_user["id"],
        username=current_user.get("username"),
        target=name,
    )
    return RedirectResponse("/admin/runners", status_code=303)


@router.post("/admin/runners/{name}/resume")
async def runners_resume(
    request: Request,
    name: str,
    current_user: dict = Depends(require_role("admin")),
    _csrf=Depends(verify_csrf),
):
    """Reverse a drain — runner returns to normal dispatch."""
    from web.main import get_db
    db = get_db()
    if db.get_runner_by_name(name) is None:
        raise HTTPException(status_code=404, detail=f"runner {name!r} not found")
    # touch heartbeat too so the row reflects online if the worker is alive
    db.set_runner_status(name, "online")
    db.log_audit(
        "runner.resume",
        user_id=current_user["id"],
        username=current_user.get("username"),
        target=name,
    )
    return RedirectResponse("/admin/runners", status_code=303)


@router.post("/admin/runners/jobs/{job_id}/requeue")
async def runners_requeue_failed_job(
    request: Request,
    job_id: str,
    current_user: dict = Depends(require_role("admin")),
    _csrf=Depends(verify_csrf),
):
    """Manually re-queue a runner_job the reaper had permanently failed.
    Admin-only, audit-logged under ``runner.requeue``.

    The job's retry_count is preserved so the history of automatic retries
    isn't lost — manual requeues are a separate concept from automatic
    reaper requeues.
    """
    from harness.worker_dispatch import notify_job_queued
    from web.main import get_db
    db = get_db()
    job = db.get_runner_job(job_id)
    if job is None:
        raise HTTPException(status_code=404,
                            detail=f"runner job {job_id!r} not found")
    if job.get("state") != "failed":
        raise HTTPException(
            status_code=409,
            detail=f"runner job {job_id!r} is in state {job.get('state')!r}, "
                   f"not 'failed' — refusing to requeue",
        )
    updated = db.manually_requeue_failed_job(job_id)
    if updated is None:
        # Race condition: somebody else moved it out of 'failed' first.
        raise HTTPException(status_code=409,
                            detail="job state changed during requeue")
    db.log_audit(
        "runner.requeue",
        user_id=current_user["id"],
        username=current_user.get("username"),
        target=job_id,
        details=f"run_id={updated.get('run_id')}",
    )
    notify_job_queued()
    return RedirectResponse("/admin/runners", status_code=303)


@router.post("/admin/runners/{name}/delete")
async def runners_delete(
    request: Request,
    name: str,
    current_user: dict = Depends(require_role("admin")),
    _csrf=Depends(verify_csrf),
):
    """Remove a runner from the registry. The worker process is unaffected
    until it tries to poll/heartbeat next, at which point it'll receive a
    404 and should exit. Revoke the worker's API key separately if needed."""
    from web.main import get_db
    db = get_db()
    if db.get_runner_by_name(name) is None:
        raise HTTPException(status_code=404, detail=f"runner {name!r} not found")
    requeued = db.delete_runner(name)
    if requeued:
        from harness.worker_dispatch import notify_job_queued
        notify_job_queued()
    db.log_audit(
        "runner.delete",
        user_id=current_user["id"],
        username=current_user.get("username"),
        target=name,
        details=f"requeued={requeued}" if requeued else None,
    )
    return RedirectResponse("/admin/runners", status_code=303)
