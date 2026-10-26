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
    if not os.path.isdir(_apps_dir):
        _apps = []
        return
    from harness.config import app_environ
    extra = _secrets_store.get_all_decrypted() if _secrets_store is not None else None
    _apps = load_apps(_apps_dir, extra_vars=extra, environ=app_environ(_config))


def reload_config_and_apps() -> str:
    """Re-read config.yaml from disk + reload apps. Returns a human-readable
    summary of what changed. Limited reload — see docs in
    web/templates/admin_config.html for what still needs a full restart."""
    global _config
    from harness.config import load_config
    config_path = "config.yaml"
    if not os.path.exists(config_path):
        return f"skipped: {config_path} not found"
    before_apps = len(_apps)
    new_config = load_config(config_path)
    _config = new_config
    reload_apps()
    return f"config re-read; apps {before_apps} -> {len(_apps)}"


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
            build_scheduler, add_retention_job, add_stale_check_job, is_enabled,
        )
        from harness.runner import run_app
        from harness.retention import prune_old_runs
        from harness.staleness import check_and_dispatch as stale_dispatch
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
        # Apps with `run_on:` are dispatched to remote workers — queue the
        # job here and a polling worker picks it up. Otherwise execute
        # in-process as before.
        try:
            from harness.worker_dispatch import (
                queue_remote_run, should_dispatch_remotely,
            )
            if should_dispatch_remotely(app_def):
                queue_remote_run(
                    _db, app_def, environment, "schedule",
                    secrets_store=_secrets_store, config=_config,
                )
                return
        except Exception:
            _log.exception("scheduler: dispatch check failed; falling back to in-process")
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

    # Worker stuck-job reaper — every ``worker.reaper_interval_seconds`` (60s
    # default) the reaper scans runner_jobs in state='assigned' that have
    # exceeded ``worker.job_timeout_seconds`` and either re-queues them or
    # marks them failed after ``worker.max_retries`` exceeded.
    try:
        from harness.worker_dispatch import add_reaper_job
        add_reaper_job(sched, _db, config=_config)
    except Exception:
        _log.exception("scheduler: failed to wire stuck-job reaper")

    # Hourly stale-run check.  Defaults are sane; opt-out via
    # `alerts.stale_check_enabled: false`.
    stale_enabled = ((_config.get("alerts") or {})
                     .get("stale_check_enabled", True))
    if stale_enabled:
        async def _stale_cb() -> None:
            if _db is None:
                return
            try:
                await stale_dispatch(_db, _apps, _config)
            except Exception:
                _log.exception("scheduler: stale-run check failed")
        add_stale_check_job(sched, _stale_cb)

    # Daily Parquet analytics export — see docs/guides/analytics.md.
    # Opt-in via `analytics.enabled: true` + a target block in config.yaml.
    analytics_cfg = _config.get("analytics") or {}
    if analytics_cfg.get("enabled"):
        try:
            from harness.analytics import build_target, export_all_tables
            from harness.auto_scheduler import add_analytics_export_job
            _export_target = build_target(analytics_cfg.get("target") or {})
            _export_tables = analytics_cfg.get("tables")  # None -> default set
            _export_cron = analytics_cfg.get("export_cron", "0 1 * * *")

            def _export_cb() -> None:
                if _db is None:
                    return
                try:
                    export_all_tables(_db, _export_target, tables=_export_tables)
                except Exception:
                    _log.exception("scheduler: analytics export failed")

            add_analytics_export_job(sched, _export_cb, cron=_export_cron)
        except Exception:
            _log.exception(
                "scheduler: failed to wire analytics export; export disabled "
                "for this process. Check analytics.target config."
            )

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

    # Wire structured logging FIRST so anything that logs during startup
    # (scheduler, secrets store, app loader) lands in the rotating file.
    # Idempotent: safe to call again from main() / test fixtures.
    from harness.logging_config import configure as _configure_logging
    _configure_logging(_config)

    # Initialise DB and SecretsStore first so decrypted secrets can be passed
    # into app YAML resolution via extra_vars (no os.environ mutation).
    if db is None:
        os.makedirs("data", exist_ok=True)
        _db = Database("data/harness.db")
        _db.init_schema()
    else:
        _db = db

    from harness.secrets_store import SecretsStore
    from web.auth import set_auth_config
    _secrets_store = SecretsStore(_db)

    from harness.config import app_environ
    _apps = (
        load_apps(apps_dir, extra_vars=_secrets_store.get_all_decrypted(),
                  environ=app_environ(_config))
        if os.path.isdir(apps_dir)
        else []
    )
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

    # ── Optional mTLS verification (workers-v2 feature #5b) ──────────────
    # Enabled via ``worker.mtls.enabled: true`` + ``client_ca_path``. The
    # ASGI server (uvicorn) is responsible for the actual TLS handshake;
    # this middleware verifies that a client certificate was presented for
    # any ``/api/runner/*`` request, and stashes the subject CN as the
    # worker identity on request.state so downstream handlers can audit it.
    mtls_cfg = (_config.get("worker") or {}).get("mtls") or {}
    if mtls_cfg.get("enabled"):
        from web.mtls import MTLSEnforcementMiddleware
        app.add_middleware(
            MTLSEnforcementMiddleware,
            require_for_prefix="/api/runner/",
            client_ca_path=mtls_cfg.get("client_ca_path"),
        )
        _log.info(
            "mTLS: enforcement enabled for /api/runner/* (ca_path=%r)",
            mtls_cfg.get("client_ca_path"),
        )

    # ── Reverse proxy & forwarded headers middleware ─────────────────────
    # Normalizes client IP, scheme, host, and subpath prefix from trusted
    # reverse proxies (Caddy, Traefik, Nginx, ALB, Cloudflare). Added last
    # so it executes FIRST in ASGI middleware stack.
    proxy_cfg = _config.get("proxy", {})
    trusted_proxies = proxy_cfg.get("trusted_proxies")
    if trusted_proxies is None:
        env_ips = os.environ.get("FORWARDED_ALLOW_IPS") or os.environ.get("TESTHARNESS_TRUSTED_PROXIES")
        if env_ips:
            trusted_proxies = env_ips
        elif proxy_cfg.get("behind_reverse_proxy"):
            from web.proxy_middleware import DEFAULT_PRIVATE_NETWORKS
            trusted_proxies = DEFAULT_PRIVATE_NETWORKS
        else:
            trusted_proxies = ["127.0.0.1", "::1"]

    url_prefix = (
        proxy_cfg.get("url_prefix")
        or os.environ.get("TESTHARNESS_ROOT_PATH")
        or os.environ.get("URL_PREFIX")
        or ""
    )

    from web.proxy_middleware import ReverseProxyMiddleware
    app.add_middleware(
        ReverseProxyMiddleware,
        trusted_proxies=trusted_proxies,
        trust_all=proxy_cfg.get("trust_all", False) or trusted_proxies in ("*", ["*"]),
        trust_proto=proxy_cfg.get("trust_proto", True),
        trust_host=proxy_cfg.get("trust_host", True),
        trust_prefix=proxy_cfg.get("trust_prefix", True),
        default_root_path=url_prefix,
    )

    # 403 exception handler — renders 403.html for HTML requests.
    from fastapi.exceptions import HTTPException as _HTTPExc
    from fastapi.responses import JSONResponse
    from web.templating import make_templates as _make_tmpl
    _tmpl = _make_tmpl()

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
        # Propagate exc.headers so redirect responses raised from dependencies
        # (notably web/auth.py:72 — HTTPException(307, headers={"Location":...}))
        # actually carry their Location header. Without this, browsers see a 307
        # with no destination and render the JSON body verbatim.
        # Propagate exc.headers so redirect responses raised from dependencies
        # (notably web/auth.py:72 — HTTPException(307, headers={"Location":...}))
        # actually carry their Location header. Without this, browsers see a 307
        # with no destination and render the JSON body verbatim.
        return JSONResponse(
            {"detail": exc.detail},
            status_code=exc.status_code,
            headers=dict(exc.headers) if exc.headers else None,
        )

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

    from web.routes.runner import router as runner_router
    app.include_router(runner_router)

    from web.routes.admin_runners import router as admin_runners_router
    app.include_router(admin_runners_router)

    from web.routes.admin_dashboard import router as admin_dashboard_router
    app.include_router(admin_dashboard_router)

    from web.routes.admin_proxy import router as admin_proxy_router
    app.include_router(admin_proxy_router)

    from web.routes.admin_notifications import router as admin_notifications_router
    app.include_router(admin_notifications_router)

    screenshots_dir = "data/screenshots"
    os.makedirs(screenshots_dir, exist_ok=True)
    app.mount("/screenshots", StaticFiles(directory=screenshots_dir), name="screenshots")

    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.isdir(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    return app


def _preflight() -> None:
    """Verify writable runtime locations before SQLite or SecretsStore try
    to use them. Produces an actionable error instead of a cryptic
    ``sqlite3.OperationalError`` when bind-mounted volumes are mis-owned.

    The Docker entrypoint (``entrypoint.sh``) normally fixes ownership
    before the application starts, so this check is a defense-in-depth
    backstop for non-Docker installs and operators who override the
    entrypoint.
    """
    import sys
    required = ("data", "data/screenshots")
    for path in required:
        try:
            os.makedirs(path, exist_ok=True)
            sentinel = os.path.join(path, ".harness-preflight")
            with open(sentinel, "w") as f:
                f.write("")
            os.remove(sentinel)
        except OSError as e:
            uid = os.getuid() if hasattr(os, "getuid") else "?"
            sys.stderr.write(
                "\n"
                f"[harness preflight] Cannot write to {os.path.abspath(path)} "
                f"(running as uid {uid}): {e}\n"
                "\n"
                "This usually means a bind-mounted host directory is not\n"
                "writable by the harness user (uid 10001). Fix with one of:\n"
                "\n"
                "  # Option A — chown the host directory to uid 10001\n"
                f"  sudo chown -R 10001:10001 ./{path}\n"
                "\n"
                "  # Option B — run the container as your own host uid\n"
                "  # in docker-compose.yml:\n"
                "  #   user: \"$(id -u):$(id -g)\"\n"
                "\n"
                "See docs/guides/deployment.md#permissions for details.\n"
                "\n"
            )
            sys.exit(1)


def main():
    import sys
    import uvicorn
    from dotenv import load_dotenv
    from harness.config import load_config

    load_dotenv()
    _preflight()

    # When run as `python -m web.main`, this module is __main__, not web.main.
    # Request handlers do `from web.main import get_db`, which imports a fresh
    # web.main module with _db=None.  Fix: register __main__ as web.main so
    # both names point to the same module object.
    if __name__ == "__main__" or sys.modules.get("web.main") is None:
        sys.modules["web.main"] = sys.modules[__name__]

    config = load_config("config.yaml")
    app = create_app(config=config)
    proxy_cfg = config.get("proxy", {})
    forwarded_ips = os.environ.get(
        "FORWARDED_ALLOW_IPS",
        "*" if proxy_cfg.get("behind_reverse_proxy") else "127.0.0.1",
    )
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=9552,
        proxy_headers=True,
        forwarded_allow_ips=forwarded_ips,
    )


if __name__ == "__main__":
    main()
