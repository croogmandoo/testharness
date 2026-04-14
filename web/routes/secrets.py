"""Secrets management routes."""
import os
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from web.auth import require_role

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
        **extra,
    }


def _env_vars() -> list:
    """Return [(name, is_set)] for $VAR references found in app YAML files."""
    import re
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
    return sorted((name, name in os.environ) for name in refs)


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
    return RedirectResponse("/secrets", status_code=303)


@router.post("/secrets/{name}/delete")
async def secrets_delete(
    request: Request,
    name: str,
    current_user: dict = Depends(require_role("admin")),
):
    from web.main import get_secrets_store
    store = get_secrets_store()
    if store:
        store.delete(name)
    return RedirectResponse("/secrets", status_code=303)
