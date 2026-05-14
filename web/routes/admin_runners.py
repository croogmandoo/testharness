"""Admin UI for managing remote workers — list, drain, remove."""
import os
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from web.auth import require_role
from web.csrf import csrf_token_for, verify_csrf

router = APIRouter()
templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(__file__), "..", "templates")
)


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


@router.get("/admin/runners", response_class=HTMLResponse)
async def runners_list(request: Request,
                       current_user: dict = Depends(require_role("admin"))):
    from web.main import get_db, get_config
    from harness.version_skew import central_version, classify_skew
    db = get_db()
    runners = db.list_runners()
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
        is_on = _is_online(r.get("last_seen_at"))
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
        })
    return templates.TemplateResponse(
        request, "admin_runners.html",
        _ctx(request, current_user, runners=enriched),
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
    db.delete_runner(name)
    db.log_audit(
        "runner.delete",
        user_id=current_user["id"],
        username=current_user.get("username"),
        target=name,
    )
    return RedirectResponse("/admin/runners", status_code=303)
