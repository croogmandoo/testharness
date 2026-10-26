import logging
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Any, Optional
from harness.config import ConfigError
from harness.models import Run
from harness.runner import run_app
from harness.app_manager import get_known_vars
from web.auth import get_current_user, require_role

router = APIRouter(prefix="/api")


class RunRequest(BaseModel):
    app: Optional[str] = None
    tag: Optional[str] = None       # Tier-3: bulk run by app tag
    environment: str
    triggered_by: str = "api"


class RunResponse(BaseModel):
    run_id: Optional[str] = None
    run_ids: list[str] = []
    apps: list[str] = []


class AppMutationResponse(BaseModel):
    app: str
    file: str


class AppArchiveResponse(BaseModel):
    archived: str


class VarsResponse(BaseModel):
    vars: list[str] = []


class ApproveBaselineRequest(BaseModel):
    result_id: str


class ApproveBaselineResponse(BaseModel):
    ok: bool = True
    result_id: str
    status: str
    message: str


@router.post("/runs", status_code=202, response_model=RunResponse)
async def trigger_run(req: RunRequest, background_tasks: BackgroundTasks,
                      current_user: dict = Depends(require_role("admin", "runner"))):
    from web.main import get_db, get_config, get_apps, get_secrets_store
    db = get_db()
    config = get_config()
    all_apps = get_apps()

    if req.app and req.tag:
        raise HTTPException(
            status_code=400,
            detail="Specify either 'app' or 'tag', not both",
        )

    if req.tag:
        target_apps = [a for a in all_apps if req.tag in (a.get("tags") or [])]
        if not target_apps:
            raise HTTPException(
                status_code=404,
                detail=f"No apps tagged '{req.tag}'",
            )
    else:
        target_apps = [a for a in all_apps if req.app is None or a["app"] == req.app]
    if req.app and not target_apps:
        # Check for an active run before returning 404 — the app may not be in the config
        # but could still be running (e.g. config reloaded after a run was queued).
        if db.is_run_active(req.app, req.environment):
            raise HTTPException(
                status_code=409,
                detail=f"A run for {req.app} ({req.environment}) is already in progress"
            )
        raise HTTPException(status_code=404, detail=f"App '{req.app}' not found")

    # Pre-insert Run records so callers get real IDs back immediately.
    # The background task updates status from 'pending' → 'running' → 'complete'.
    # Apps with `run_on:` are queued for remote workers instead of running
    # in-process — see harness/worker_dispatch.py.
    from harness.worker_dispatch import (
        queue_remote_run, should_dispatch_remotely,
    )
    queued = []
    for app_def in target_apps:
        if db.is_run_active(app_def["app"], req.environment):
            if req.app:
                raise HTTPException(
                    status_code=409,
                    detail=f"A run for {app_def['app']} ({req.environment}) is already in progress"
                )
            continue  # skip already-running apps in "run all" mode
        if should_dispatch_remotely(app_def):
            try:
                run_id = queue_remote_run(
                    db, app_def, req.environment, req.triggered_by,
                    secrets_store=get_secrets_store(), config=config,
                )
            except ConfigError as exc:
                # Unresolvable $VAR or unsafe resolved URL — nothing queued.
                if req.app:
                    raise HTTPException(status_code=422, detail=str(exc))
                logging.getLogger(__name__).error(
                    "run-all: not dispatching %s: %s", app_def["app"], exc)
                continue
            queued.append(run_id)
            continue
        run = Run(app=app_def["app"], environment=req.environment, triggered_by=req.triggered_by)
        db.insert_run(run)
        background_tasks.add_task(run_app, app_def, req.environment, req.triggered_by, db, config,
                                  run_id=run.id, secrets_store=get_secrets_store())
        queued.append(run.id)

    return {"run_id": queued[0] if queued else None, "run_ids": queued, "apps": [a["app"] for a in target_apps]}


@router.get("/runs/{run_id}")
async def get_run(run_id: str, current_user: dict = Depends(get_current_user)):
    from web.main import get_db
    db = get_db()
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    results = db.get_results_for_run(run_id)
    return {**run, "results": results}


@router.get("/apps")
async def list_apps(environment: str = "production",
                    current_user: dict = Depends(get_current_user)):
    from web.main import get_db
    db = get_db()
    return db.get_app_summary(environment)


@router.get("/vars", response_model=VarsResponse)
async def list_vars(current_user: dict = Depends(get_current_user)):
    """Return all $VAR names referenced in app YAML files. Never returns values."""
    from web.main import get_apps_dir
    return {"vars": get_known_vars(apps_dir=get_apps_dir())}


@router.get("/results/{app}/{environment}")
async def get_results(app: str, environment: str, limit: int = 20, offset: int = 0,
                      current_user: dict = Depends(get_current_user)):
    from web.main import get_db
    db = get_db()
    return db.get_results_for_app(app, environment, limit, offset)


# ---- App management mutation endpoints ----


class AppDefRequest(BaseModel):
    app_def: dict[str, Any]


@router.post("/apps", status_code=201, response_model=AppMutationResponse)
async def create_app_def(req: AppDefRequest,
                         current_user: dict = Depends(require_role("admin", "runner"))):
    from web.main import get_apps_dir, reload_apps
    import harness.app_manager as mgr
    try:
        path = mgr.write_app(req.app_def, apps_dir=get_apps_dir())
    except mgr.AppValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except mgr.AppManagerError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    try:
        reload_apps()
    except Exception:
        pass  # in-memory app list may be briefly stale; harmless for single-operator use
    return {"app": req.app_def.get("app", ""), "file": str(path)}


@router.put("/apps/{app_name}", status_code=200, response_model=AppMutationResponse)
async def update_app_def(app_name: str, req: AppDefRequest,
                         current_user: dict = Depends(require_role("admin", "runner"))):
    from web.main import get_apps_dir, reload_apps
    import harness.app_manager as mgr
    if req.app_def.get("app") and req.app_def["app"] != app_name:
        raise HTTPException(
            status_code=422,
            detail=f"app_def.app must match the URL app name '{app_name}'"
        )
    try:
        path = mgr.update_app(app_name, req.app_def, apps_dir=get_apps_dir())
    except mgr.AppValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except mgr.AppManagerError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    try:
        reload_apps()
    except Exception:
        pass  # in-memory app list may be briefly stale; harmless for single-operator use
    return {"app": app_name, "file": str(path)}


@router.delete("/apps/{app_name}", status_code=200, response_model=AppArchiveResponse)
async def archive_app_def(app_name: str,
                          current_user: dict = Depends(require_role("admin", "runner"))):
    from web.main import get_apps_dir, reload_apps
    import harness.app_manager as mgr
    try:
        path = mgr.archive_app(app_name, apps_dir=get_apps_dir())
    except mgr.AppManagerError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    try:
        reload_apps()
    except Exception:
        pass  # in-memory app list may be briefly stale; harmless for single-operator use
    return {"archived": str(path)}


@router.post("/apps/{app_name}/restore", status_code=200, response_model=AppMutationResponse)
async def restore_app_def(app_name: str,
                          current_user: dict = Depends(require_role("admin", "runner"))):
    from web.main import get_apps_dir, reload_apps
    import harness.app_manager as mgr
    try:
        path = mgr.restore_app(app_name, apps_dir=get_apps_dir())
    except mgr.AppManagerError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    try:
        reload_apps()
    except Exception:
        pass  # in-memory app list may be briefly stale; harmless for single-operator use
    return {"app": app_name, "file": str(path)}


@router.delete("/apps/{app_name}/permanent", status_code=204)
async def delete_app_permanently(app_name: str,
                                 current_user: dict = Depends(require_role("admin", "runner"))) -> None:
    from web.main import get_apps_dir
    import harness.app_manager as mgr
    try:
        mgr.delete_archived_app(app_name, apps_dir=get_apps_dir())
    except mgr.AppManagerError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/visual/approve", response_model=ApproveBaselineResponse)
async def approve_visual_baseline(
    req: ApproveBaselineRequest,
    current_user: dict = Depends(require_role("admin", "runner")),
):
    """Approve a visual regression failure screenshot as the new baseline."""
    from web.main import get_db
    from datetime import datetime, timezone
    from harness.models import AppState

    db = get_db()
    with db._connect() as conn:
        row = conn.execute(
            "SELECT * FROM test_results WHERE id=?",
            (req.result_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Test result not found")
        result = dict(row)
        if not result.get("screenshot"):
            raise HTTPException(status_code=400, detail="Test result has no screenshot to approve")

        conn.execute(
            "UPDATE test_results SET status='pass', error_msg=? WHERE id=?",
            (f"Approved baseline by {current_user.get('username')}", req.result_id),
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    db.upsert_app_state(AppState(
        app=result["app"],
        environment=result["environment"],
        test_name=result["test_name"],
        state="passing",
        since=now_iso,
    ))

    db.log_audit(
        "visual.baseline_approve",
        user_id=current_user["id"],
        username=current_user.get("username"),
        details=f"Approved screenshot {result['screenshot']} for {result['app']}/{result['environment']}/{result['test_name']}",
    )

    return {
        "ok": True,
        "result_id": req.result_id,
        "status": "pass",
        "message": f"Successfully approved {result['screenshot']} as new baseline",
    }


class NotificationGroupCreateRequest(BaseModel):
    name: str
    description: Optional[str] = ""
    emails: list[str]


class NotificationGroupUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    emails: Optional[list[str]] = None


@router.get("/notification-groups")
async def list_notification_groups(current_user: dict = Depends(require_role("admin", "runner", "viewer"))):
    from web.main import get_db
    return get_db().list_notification_groups()


@router.post("/notification-groups", status_code=201)
async def create_notification_group(
    req: NotificationGroupCreateRequest,
    current_user: dict = Depends(require_role("admin", "runner")),
):
    from web.main import get_db
    db = get_db()
    try:
        group = db.create_notification_group(name=req.name, emails=req.emails, description=req.description or "")
    except ValueError as e:
        if "already exists" in str(e):
            raise HTTPException(status_code=409, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        if "UNIQUE constraint failed" in str(e):
            raise HTTPException(status_code=409, detail=f"Notification group '{req.name}' already exists")
        raise HTTPException(status_code=500, detail=str(e))

    db.log_audit(
        "notification_group.create",
        user_id=current_user["id"],
        username=current_user.get("username"),
        target=group["name"],
        details=f"Created notification group with {len(group['emails'])} recipients",
    )
    return group


@router.put("/notification-groups/{group_id}")
async def update_notification_group(
    group_id: str,
    req: NotificationGroupUpdateRequest,
    current_user: dict = Depends(require_role("admin", "runner")),
):
    from web.main import get_db
    db = get_db()
    existing = db.get_notification_group(group_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Notification group not found")

    try:
        updated = db.update_notification_group(
            group_id,
            name=req.name,
            emails=req.emails,
            description=req.description,
        )
    except ValueError as e:
        if "already exists" in str(e):
            raise HTTPException(status_code=409, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        if "UNIQUE constraint failed" in str(e):
            raise HTTPException(status_code=409, detail="A notification group with this name already exists")
        raise HTTPException(status_code=500, detail=str(e))

    db.log_audit(
        "notification_group.update",
        user_id=current_user["id"],
        username=current_user.get("username"),
        target=updated["name"],
        details=f"Updated notification group ({len(updated['emails'])} recipients)",
    )
    return updated


@router.delete("/notification-groups/{group_id}")
async def delete_notification_group(
    group_id: str,
    current_user: dict = Depends(require_role("admin", "runner")),
):
    from web.main import get_db
    db = get_db()
    existing = db.get_notification_group(group_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Notification group not found")

    db.delete_notification_group(group_id)
    db.log_audit(
        "notification_group.delete",
        user_id=current_user["id"],
        username=current_user.get("username"),
        target=existing["name"],
        details=f"Deleted notification group {existing['name']}",
    )
    return {"ok": True, "deleted": group_id}
