import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from harness.db import Database
from harness.loader import load_apps

_db: Database = None
_config: dict = {}
_apps: list = []
_apps_dir: str = "apps"
_secrets_store = None


def get_db() -> Database:
    return _db


def get_config() -> dict:
    return _config


def get_apps() -> list:
    return _apps


def get_apps_dir() -> str:
    return _apps_dir


def get_secrets_store():
    return _secrets_store


def reload_apps() -> None:
    global _apps
    _apps = load_apps(_apps_dir) if os.path.isdir(_apps_dir) else []


def create_app(db: Database = None, config: dict = None, apps_dir: str = "apps") -> FastAPI:
    global _db, _config, _apps, _apps_dir, _secrets_store
    _config = config or {}
    _apps_dir = apps_dir
    _apps = load_apps(apps_dir) if os.path.isdir(apps_dir) else []

    if db is None:
        os.makedirs("data", exist_ok=True)
        _db = Database("data/harness.db")
        _db.init_schema()
    else:
        _db = db

    # Initialise SecretsStore and derive session signing key.
    from harness.secrets_store import SecretsStore
    from web.auth import set_auth_config
    _secrets_store = SecretsStore(_db)
    _secrets_store.inject_to_env()
    auth_cfg = _config.get("auth", {})
    set_auth_config(
        signing_key=_secrets_store.session_signing_key,
        session_hours=auth_cfg.get("session_hours", 8),
        secure_cookie=auth_cfg.get("secure_cookie", False),
    )

    app = FastAPI(title="Web Testing Harness")

    # First-run middleware: redirect every HTML route to /setup when no users exist.
    from starlette.middleware.base import BaseHTTPMiddleware
    from fastapi.responses import RedirectResponse as _Redirect

    class _FirstRunMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            skip = {"/setup", "/auth/login", "/auth/logout"}
            skip_prefix = ("/static", "/screenshots", "/api")
            if (request.url.path not in skip and
                    not request.url.path.startswith(skip_prefix)):
                if _db is not None and _db.count_users() == 0:
                    return _Redirect("/setup", status_code=302)
            return await call_next(request)

    app.add_middleware(_FirstRunMiddleware)

    # 403 exception handler — renders 403.html for HTML requests.
    from fastapi.exceptions import HTTPException as _HTTPExc
    from fastapi.responses import JSONResponse
    from fastapi.templating import Jinja2Templates as _Tmpl
    _tmpl = _Tmpl(directory=os.path.join(os.path.dirname(__file__), "templates"))

    @app.exception_handler(_HTTPExc)
    async def _http_exc_handler(request, exc: _HTTPExc):
        if exc.status_code == 403 and not request.url.path.startswith("/api"):
            accept = request.headers.get("accept", "")
            if "text/html" in accept or "/" in accept:
                return _tmpl.TemplateResponse(request, "403.html", {
                    "request": request,
                    "environments": _config.get("environments", {}),
                    "environment": None,
                    "current_user": None,
                }, status_code=403)
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    from web.routes.api import router as api_router
    app.include_router(api_router)

    from web.routes.dashboard import router as dashboard_router
    app.include_router(dashboard_router)

    from web.routes.apps import router as apps_router
    app.include_router(apps_router)

    from web.routes.export import router as export_router
    app.include_router(export_router)

    from web.routes.auth import router as auth_router
    app.include_router(auth_router)

    from web.routes.secrets import router as secrets_router
    app.include_router(secrets_router)

    from web.routes.users import router as users_router
    app.include_router(users_router)

    from web.routes.admin import router as admin_router
    app.include_router(admin_router)

    screenshots_dir = "data/screenshots"
    os.makedirs(screenshots_dir, exist_ok=True)
    app.mount("/screenshots", StaticFiles(directory=screenshots_dir), name="screenshots")

    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.isdir(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    return app


def main():
    import sys
    import uvicorn
    from dotenv import load_dotenv
    from harness.config import load_config

    load_dotenv()

    # When run as `python -m web.main`, this module is __main__, not web.main.
    # Request handlers do `from web.main import get_db`, which imports a fresh
    # web.main module with _db=None.  Fix: register __main__ as web.main so
    # both names point to the same module object.
    if __name__ == "__main__" or sys.modules.get("web.main") is None:
        sys.modules["web.main"] = sys.modules[__name__]

    config = load_config("config.yaml")
    app = create_app(config=config)
    uvicorn.run(app, host="0.0.0.0", port=9552)


if __name__ == "__main__":
    main()
