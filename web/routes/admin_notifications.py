"""Admin Notification Groups management routes."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

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


@router.get("/admin/notifications", response_class=HTMLResponse)
async def notification_groups_list(
    request: Request,
    current_user: dict = Depends(require_role("admin")),
):
    from web.main import get_db, get_apps
    db = get_db()
    groups = db.list_notification_groups()

    # Map groups to assigned apps
    apps = get_apps()
    for g in groups:
        assigned = []
        for a in apps:
            app_groups = a.get("notification_groups") or []
            if g["name"] in app_groups or g["id"] in app_groups:
                assigned.append(a.get("app"))
        g["assigned_apps"] = assigned

    return templates.TemplateResponse(
        request,
        "admin_notifications.html",
        _ctx(request, current_user, groups=groups, error=None),
    )


@router.post("/admin/notifications", response_class=HTMLResponse, dependencies=[Depends(verify_csrf)])
async def notification_group_create(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    emails: str = Form(...),
    current_user: dict = Depends(require_role("admin")),
):
    from web.main import get_db
    db = get_db()

    # Parse emails by newline or comma
    email_list = [e.strip() for part in emails.replace(",", "\n").splitlines() if (e := part.strip())]

    try:
        group = db.create_notification_group(name=name, emails=email_list, description=description)
        db.log_audit(
            "notification_group.create",
            user_id=current_user["id"],
            username=current_user.get("username"),
            target=group["name"],
            details=f"Created notification group with {len(group['emails'])} emails",
        )
        return RedirectResponse("/admin/notifications", status_code=303)
    except ValueError as e:
        groups = db.list_notification_groups()
        return templates.TemplateResponse(
            request,
            "admin_notifications.html",
            _ctx(request, current_user, groups=groups, error=str(e)),
            status_code=400,
        )
    except Exception as e:
        groups = db.list_notification_groups()
        err_msg = "A notification group with this name already exists" if "UNIQUE" in str(e) else str(e)
        return templates.TemplateResponse(
            request,
            "admin_notifications.html",
            _ctx(request, current_user, groups=groups, error=err_msg),
            status_code=400,
        )


@router.post("/admin/notifications/{group_id}/edit", response_class=HTMLResponse, dependencies=[Depends(verify_csrf)])
async def notification_group_edit(
    request: Request,
    group_id: str,
    name: str = Form(...),
    description: str = Form(""),
    emails: str = Form(...),
    current_user: dict = Depends(require_role("admin")),
):
    from web.main import get_db
    db = get_db()

    email_list = [e.strip() for part in emails.replace(",", "\n").splitlines() if (e := part.strip())]

    try:
        updated = db.update_notification_group(group_id, name=name, emails=email_list, description=description)
        if not updated:
            raise HTTPException(status_code=404, detail="Notification group not found")
        db.log_audit(
            "notification_group.update",
            user_id=current_user["id"],
            username=current_user.get("username"),
            target=updated["name"],
            details=f"Updated notification group ({len(updated['emails'])} recipients)",
        )
        return RedirectResponse("/admin/notifications", status_code=303)
    except ValueError as e:
        groups = db.list_notification_groups()
        return templates.TemplateResponse(
            request,
            "admin_notifications.html",
            _ctx(request, current_user, groups=groups, error=str(e)),
            status_code=400,
        )
    except Exception as e:
        groups = db.list_notification_groups()
        err_msg = "A notification group with this name already exists" if "UNIQUE" in str(e) else str(e)
        return templates.TemplateResponse(
            request,
            "admin_notifications.html",
            _ctx(request, current_user, groups=groups, error=err_msg),
            status_code=400,
        )


@router.post("/admin/notifications/{group_id}/delete", response_class=HTMLResponse, dependencies=[Depends(verify_csrf)])
async def notification_group_delete(
    request: Request,
    group_id: str,
    current_user: dict = Depends(require_role("admin")),
):
    from web.main import get_db
    db = get_db()
    existing = db.get_notification_group(group_id)
    if existing:
        db.delete_notification_group(group_id)
        db.log_audit(
            "notification_group.delete",
            user_id=current_user["id"],
            username=current_user.get("username"),
            target=existing["name"],
            details=f"Deleted notification group {existing['name']}",
        )
    return RedirectResponse("/admin/notifications", status_code=303)
