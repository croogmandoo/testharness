import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from harness.db import Database
from harness.loader import load_apps

_db: Database = None
_config: dict = {}
_apps: list = []
_apps_dir: str = "apps"


def get_db() -> Database:
    return _db


def get_config() -> dict:
    return _config


def get_apps() -> list:
    return _apps


def get_apps_dir() -> str:
    return _apps_dir


def reload_apps() -> None:
    global _apps
    _apps = load_apps(_apps_dir) if os.path.isdir(_apps_dir) else []


def create_app(db: Database = None, config: dict = None, apps_dir: str = "apps") -> FastAPI:
    global _db, _config, _apps, _apps_dir
    _config = config or {}
    _apps_dir = apps_dir
    _apps = load_apps(apps_dir) if os.path.isdir(apps_dir) else []

    if db is None:
        os.makedirs("data", exist_ok=True)
        _db = Database("data/harness.db")
        _db.init_schema()
    else:
        _db = db

    app = FastAPI(title="Web Testing Harness")

    from web.routes.api import router as api_router
    app.include_router(api_router)

    from web.routes.dashboard import router as dashboard_router
    app.include_router(dashboard_router)

    from web.routes.apps import router as apps_router
    app.include_router(apps_router)

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

    if __name__ == "__main__" or sys.modules.get("web.main") is None:
        sys.modules["web.main"] = sys.modules[__name__]

    config = load_config("config.yaml")
    app = create_app(config=config)
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
