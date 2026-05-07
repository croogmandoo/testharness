"""In-process cron scheduler — replaces dependence on Windows Task Scheduler.

Schedules are sourced from `config.yaml` (per-environment default) with optional
per-app overrides in `apps/*.yaml`:

    # config.yaml
    environments:
      production:
        label: "Production"
        schedule: "*/15 * * * *"      # all apps in this env, every 15 min

    # apps/<app>.yaml
    schedule: "*/5 * * * *"           # this app overrides the env default

Set `scheduling.enabled: false` (or omit all schedules) to disable.
"""
import logging
from typing import Callable, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

_log = logging.getLogger(__name__)


def _resolve_cron(app_def: dict, env_name: str, env_cfg: dict) -> Optional[str]:
    """Per-app override wins over env default."""
    return app_def.get("schedule") or (env_cfg or {}).get("schedule")


def build_scheduler(config: dict, apps: list,
                    run_callback: Callable[[dict, str], None]) -> AsyncIOScheduler:
    """Build (but don't start) an AsyncIOScheduler.

    `run_callback(app_def, environment)` is invoked when each cron fires.
    Invalid cron expressions are logged and skipped — never raised.
    """
    sched = AsyncIOScheduler(timezone="UTC")
    envs = config.get("environments", {}) or {}

    for env_name, env_cfg in envs.items():
        for app_def in apps:
            # Skip apps that don't define this environment
            if env_name not in (app_def.get("environments") or {}):
                continue
            cron = _resolve_cron(app_def, env_name, env_cfg)
            if not cron:
                continue
            try:
                trigger = CronTrigger.from_crontab(cron, timezone="UTC")
            except (ValueError, KeyError) as e:
                _log.warning(
                    "auto_scheduler: invalid cron %r for %s/%s: %s",
                    cron, app_def["app"], env_name, e,
                )
                continue
            sched.add_job(
                run_callback,
                trigger,
                args=[app_def, env_name],
                id=f"run::{app_def['app']}::{env_name}",
                replace_existing=True,
                coalesce=True,           # collapse missed fires into one
                max_instances=1,         # never overlap with self
            )
            _log.info(
                "auto_scheduler: scheduled %s/%s with cron %r",
                app_def["app"], env_name, cron,
            )
    return sched


def add_retention_job(scheduler: AsyncIOScheduler, retention_days: int,
                      prune_callback: Callable[[], None],
                      cron: str = "0 3 * * *") -> None:
    """Wire a daily prune job onto an existing scheduler."""
    if not retention_days or retention_days <= 0:
        return
    scheduler.add_job(
        prune_callback,
        CronTrigger.from_crontab(cron, timezone="UTC"),
        id="retention::prune",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    _log.info(
        "auto_scheduler: retention prune scheduled %r (retention_days=%d)",
        cron, retention_days,
    )


def is_enabled(config: dict) -> bool:
    return (config.get("scheduling") or {}).get("enabled", True)
