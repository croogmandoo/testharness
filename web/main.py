import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from harness.db import Database
from harness.loader import load_apps

_log = logging.getLogger(__name__)

_db: Database = None
_config: dict = {}
_apps: list = []
_apps_dir: str = "apps"
_secrets_store = None
_scheduler = None


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


def get_scheduler():
    return _scheduler


def reload_apps() -> None:
    global _apps
    _apps = load_apps(_apps_dir) if os.path.isdir(_apps_dir) else []


def _start_scheduler() -> None:
    """Build + start the in-process cron scheduler from current config/apps.

    No-op if scheduling is disabled or no jobs would be created.  Errors here
    are logged, not raised, so a misconfigured cron can't take the web app down.
    """
    global _scheduler
    if _scheduler is not None:
        return
    try:
        from harness.auto_scheduler import (
            build_scheduler, add_retention_job, is_enabled,
        )
        from harness.runner import run_app
        from harness.retention import prune_old_runs
    except Exception as e:  # pragma: no cover — import-time misconfig
        _log.warning("scheduler: import failed, scheduling disabled: %s", e)
        return

    if not is_enabled(_config):
        _log.info("scheduler: disabled via config (scheduling.enabled=false)")
        return

    async def _run_cb(app_def: dict, environment: str) -> None:
        if _db is None:
            return
        if _db.is_run_active(app_def["app"], environment):
            _log.info("scheduler: %s/%s already active, skipping",
                      app_def["app"], environment)
            return
        try:
            await run_app(app_def, environment, "schedule",
                          _db, _config, secrets_store=_secrets_store)
        except Exception:  # pragma: no cover — runner has its own error capture
            _log.exception("scheduler: run_app failed for %s/%s",
                           app_def["app"], environment)

    sched = build_scheduler(_config, _apps, _run_cb)

    retention_days = _config.get("retention_days")
    if retention_days:
        def _prune_cb() -> None:
            try:
                prune_old_runs(_db, retention_days)
            except Exception:
                _log.exception("scheduler: retention prune failed")
        add_retention_job(sched, retention_days, _prune_cb)

    if not sched.get_jobs():
        _log.info("scheduler: no jobs configured, not starting")
        return

    try:
        sched.start()
    except RuntimeError as e:
        # AsyncIOScheduler.start() requires a running loop; under pure-sync
        # TestClient creation there isn't one — that's fine, tests don't
        # need scheduling.
        _log.info("scheduler: not started (%s)", e)
        return
    _scheduler = sched


def _stop_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    try:
        _scheduler.shutdown(wait=False)
    except Exception:  # pragma: no cover
        _log.exception("scheduler: shutdown failed")
    _scheduler = None


def create_app(db: Database = None, config: dict = None, apps_dir: str = "apps") -> FastAPI:
    global _db, _config, _apps, _apps_dir, _secrets_store
    _config = config or {}
    _apps_dir = apps_dir

    # Initialise DB and SecretsStore first so secrets are in os.environ
    # before app YAML files are loaded and env vars are resolved.
    if db is None:
        os.makedirs("data", exist_ok=True)
        _db = Database("data/harness.db")
        _db.init_schema()
    else:
        _db = db

    from harness.secrets_store import SecretsStore
    from web.auth import set_auth_config
    _secrets_store = SecretsStore(_db)
    _secrets_store.inject_to_env()

    _apps = load_apps(apps_dir) if os.path.isdir(apps_dir) else []
    auth_cfg = _config.get("auth", {})
    set_auth_config(
        signing_key=_secrets_store.session_signing_key,
        session_hours=auth_cfg.get("session_hours", 8),
        secure_cookie=auth_cfg.get("secure_cookie", False),
    )

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        _start_scheduler()
        try:
            yield
        finally:
            _stop_scheduler()

    app = FastAPI(title="Web Testing Harness", lifespan=_lifespan)

    # First-run middleware: redirect every HTML route to /setup when no users exist.
    from starlette.middleware.base import BaseHTTPMiddleware
    from fastapi.responses import RedirectResponse as _Redirect

    class _FirstRunMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            skip = {"/setup", "/auth/login", "/auth/logout",
                    "/auth/oauth/github/login", "/auth/oauth/github/callback",
                    "/metrics", "/status",
                    "/docs", "/redoc", "/openapi.json"}
            skip_prefix = ("/static", "/screenshots", "/api", "/health")
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
                    "environments": _config.get("environments", {}),
                    "environment": None,
                    "current_user": None,
                }, status_code=403)
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    @app.get("/health")
    async def health_check():
        return {"status": "ok"}

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

    from web.routes.admin_ca_certs import router as admin_ca_certs_router
    app.include_router(admin_ca_certs_router)
    from web.routes.api_keys import router as api_keys_router
    app.include_router(api_keys_router)

    from web.routes.metrics import router as metrics_router
    app.include_router(metrics_router)

    from web.routes.status import router as status_router
    app.include_router(status_router)

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
