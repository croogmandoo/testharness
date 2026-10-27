"""Admin Maintenance Windows management routes."""

import json
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from harness.maintenance import is_in_maintenance
from web.auth import require_role
from web.csrf import csrf_token_for, verify_csrf
from web.templating import make_templates

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


@router.get("/admin/maintenance", response_class=HTMLResponse)
async def maintenance_windows_list(
    request: Request,
    current_user: dict = Depends(require_role("admin", "operator")),
):
    from web.main import get_db, get_config, get_apps
    db = get_db()
    config = get_config()
    apps = get_apps()

    windows = db.list_maintenance_windows()
    active_now = is_in_maintenance(config=config, db=db)

    # Annotate windows with whether they are currently active
    now = datetime.now(timezone.utc)
    for w in windows:
        w["is_active"] = bool(
            w.get("enabled") and is_in_maintenance(config={}, now=now, app=w.get("app"), environment=w.get("environment"), db=db)
        )

    return templates.TemplateResponse(
        request,
        "admin_maintenance.html",
        _ctx(
            request,
            current_user,
            windows=windows,
            active_now=active_now,
            apps=apps,
            error=None,
        ),
    )


@router.post("/admin/maintenance", response_class=HTMLResponse, dependencies=[Depends(verify_csrf)])
async def maintenance_window_create(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    window_type: str = Form(...),
    app: str = Form(""),
    environment: str = Form(""),
    cron: str = Form(""),
    duration_minutes: str = Form(""),
    start_time: str = Form(""),
    end_time: str = Form(""),
    timezone_str: str = Form("UTC"),
    days_of_week: list[str] = Form(None),
    current_user: dict = Depends(require_role("admin", "operator")),
):
    from web.main import get_db, get_config, get_apps
    db = get_db()
    config = get_config()
    apps = get_apps()

    dur = int(duration_minutes) if duration_minutes.strip().isdigit() else None

    try:
        win = db.create_maintenance_window(
            name=name,
            window_type=window_type,
            description=description,
            app=app or None,
            environment=environment or None,
            cron=cron or None,
            duration_minutes=dur,
            start_time=start_time or None,
            end_time=end_time or None,
            days_of_week=days_of_week or None,
            timezone_str=timezone_str or "UTC",
            enabled=True,
            created_by=current_user.get("id"),
        )
        db.log_audit(
            "maintenance_window.create",
            user_id=current_user["id"],
            username=current_user.get("username"),
            target=win["name"],
            details=f"Created {window_type} maintenance window (app={app or 'all'}, env={environment or 'all'})",
        )
        return RedirectResponse("/admin/maintenance", status_code=303)
    except Exception as e:
        windows = db.list_maintenance_windows()
        active_now = is_in_maintenance(config=config, db=db)
        return templates.TemplateResponse(
            request,
            "admin_maintenance.html",
            _ctx(
                request,
                current_user,
                windows=windows,
                active_now=active_now,
                apps=apps,
                error=str(e),
            ),
            status_code=400,
        )


@router.post("/admin/maintenance/{window_id}/toggle", response_class=HTMLResponse, dependencies=[Depends(verify_csrf)])
async def maintenance_window_toggle(
    window_id: str,
    current_user: dict = Depends(require_role("admin", "operator")),
):
    from web.main import get_db
    db = get_db()
    win = db.get_maintenance_window(window_id)
    if not win:
        raise HTTPException(status_code=404, detail="Maintenance window not found")

    new_state = not win.get("enabled", True)
    db.update_maintenance_window(window_id, enabled=new_state)
    db.log_audit(
        "maintenance_window.toggle",
        user_id=current_user["id"],
        username=current_user.get("username"),
        target=win["name"],
        details=f"Toggled maintenance window to {'enabled' if new_state else 'disabled'}",
    )
    return RedirectResponse("/admin/maintenance", status_code=303)


@router.post("/admin/maintenance/{window_id}/delete", response_class=HTMLResponse, dependencies=[Depends(verify_csrf)])
async def maintenance_window_delete(
    window_id: str,
    current_user: dict = Depends(require_role("admin", "operator")),
):
    from web.main import get_db
    db = get_db()
    win = db.get_maintenance_window(window_id)
    if not win:
        raise HTTPException(status_code=404, detail="Maintenance window not found")

    db.delete_maintenance_window(window_id)
    db.log_audit(
        "maintenance_window.delete",
        user_id=current_user["id"],
        username=current_user.get("username"),
        target=win["name"],
        details=f"Deleted maintenance window {win['name']}",
    )
    return RedirectResponse("/admin/maintenance", status_code=303)
