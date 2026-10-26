"""Secrets management routes."""
import os
from fastapi import APIRouter, Depends, Form, Request
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


def _env_vars() -> list:
    """Return [(name, is_set)] for $VAR references found in app YAML files.

    ``is_set`` is True only when the name is visible to app YAML through
    ``apps_env_allowlist`` — other process env is not resolvable."""
    import re
    from harness.config import app_environ
    from web.main import get_config
    config = get_config()
    apps_dir = config.get("apps_dir", "apps")
    if not os.path.isdir(apps_dir):
        return []
    refs: set = set()
    for fname in os.listdir(apps_dir):
        if fname.endswith((".yaml", ".yml")):
            try:
                with open(os.path.join(apps_dir, fname)) as f:
                    refs.update(re.findall(r'\$([A-Z_][A-Z0-9_]*)', f.read()))
            except OSError:
                pass
    visible = app_environ(config)
    return sorted((name, name in visible) for name in refs)


@router.get("/secrets", response_class=HTMLResponse)
async def secrets_list(request: Request,
                       current_user: dict = Depends(require_role("admin", "runner"))):
    from web.main import get_secrets_store
    store = get_secrets_store()
    secrets = store.list() if store else []
    return templates.TemplateResponse(request, "secrets.html",
                                      _ctx(request, current_user,
                                           secrets=secrets,
                                           env_vars=_env_vars(),
                                           error=None))


@router.post("/secrets", response_class=HTMLResponse)
async def secrets_create(
    request: Request,
    name: str = Form(...),
    value: str = Form(...),
    description: str = Form(""),
    current_user: dict = Depends(require_role("admin", "runner")),
    _csrf=Depends(verify_csrf),
):
    from web.main import get_secrets_store
    store = get_secrets_store()
    name = name.strip().upper()
    if not name:
        from web.main import get_secrets_store
        secrets = store.list() if store else []
        return templates.TemplateResponse(request, "secrets.html",
                                          _ctx(request, current_user,
                                               secrets=secrets,
                                               env_vars=_env_vars(),
                                               error="Secret name is required."),
                                          status_code=422)
    if store:
        store.set(name, value, description=description.strip() or None,
                  user_id=current_user["id"])
        from web.main import get_db, reload_apps
        get_db().log_audit("secret.upsert", user_id=current_user["id"],
                           username=current_user.get("username"), target=name)
        # Refresh the in-memory _apps snapshot so the new secret is visible
        # to subsequent $VAR resolution without touching os.environ.
        reload_apps()
    return RedirectResponse("/secrets", status_code=303)


@router.post("/secrets/{name}/delete")
async def secrets_delete(
    request: Request,
    name: str,
    current_user: dict = Depends(require_role("admin")),
    _csrf=Depends(verify_csrf),
):
    from web.main import get_secrets_store, get_db, reload_apps
    store = get_secrets_store()
    if store:
        store.delete(name)
        get_db().log_audit("secret.delete", user_id=current_user["id"],
                           username=current_user.get("username"), target=name)
        # Refresh the in-memory _apps snapshot so the removed secret falls
        # out of the resolved $VAR table.
        reload_apps()
    return RedirectResponse("/secrets", status_code=303)
