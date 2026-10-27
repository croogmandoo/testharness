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


def _translate_dow_digit(n: int) -> int:
    """Standard cron: 0=Sun, 1=Mon..6=Sat, 7=Sun.
    APScheduler:    0=Mon, 1=Tue..6=Sun.
    Map a single digit accordingly."""
    if n == 0 or n == 7:
        return 6
    return n - 1


def _normalize_dow_field(field: str) -> str:
    """Translate the day-of-week field of a standard cron expression to the
    convention APScheduler uses internally.  Pass-through for named days
    (mon, tue, ...), star, and step expressions."""
    if field == "*" or "/" in field:
        return field
    parts = []
    for part in field.split(","):
        if "-" in part and not part.startswith("-"):
            a, _, b = part.partition("-")
            try:
                parts.append(f"{_translate_dow_digit(int(a))}-"
                             f"{_translate_dow_digit(int(b))}")
                continue
            except ValueError:
                pass
        try:
            parts.append(str(_translate_dow_digit(int(part))))
        except ValueError:
            parts.append(part)  # named days like 'mon', 'sat'
    return ",".join(parts)


def cron_trigger(expr: str, timezone: str = "UTC") -> CronTrigger:
    """Build a CronTrigger from a standard cron expression where 0=Sunday
    in the day-of-week field — matching `cron(5)` and most users' mental
    model.  APScheduler's own from_crontab() uses 0=Monday, which is a
    classic gotcha; this wrapper isolates the translation."""
    fields = expr.strip().split()
    if len(fields) == 5:
        fields[4] = _normalize_dow_field(fields[4])
    return CronTrigger.from_crontab(" ".join(fields), timezone=timezone)


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
                trigger = cron_trigger(cron, timezone="UTC")
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
        cron_trigger(cron, timezone="UTC"),
        id="retention::prune",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    _log.info(
        "auto_scheduler: retention prune scheduled %r (retention_days=%d)",
        cron, retention_days,
    )


def add_stale_check_job(scheduler: AsyncIOScheduler,
                        stale_callback: Callable[[], None],
                        cron: str = "0 * * * *") -> None:
    """Wire an hourly stale-run detector onto an existing scheduler.

    `stale_callback` is invoked with no args; it should call
    `harness.staleness.check_and_dispatch(...)` itself.  Default cadence is
    once an hour, on the hour.
    """
    scheduler.add_job(
        stale_callback,
        cron_trigger(cron, timezone="UTC"),
        id="staleness::check",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    _log.info("auto_scheduler: stale-run check scheduled %r", cron)


def add_analytics_export_job(scheduler: AsyncIOScheduler,
                             export_callback: Callable[[], None],
                             cron: str = "0 1 * * *") -> None:
    """Wire the daily Parquet analytics export onto an existing scheduler.

    `export_callback` is invoked with no args; it should call
    `harness.analytics.export_all_tables(...)` with the configured target.
    Default cadence is 01:00 UTC daily — late enough that any in-flight
    runs from the previous day have finished, early enough that BI's
    morning batch jobs see fresh data.
    """
    scheduler.add_job(
        export_callback,
        cron_trigger(cron, timezone="UTC"),
        id="analytics::export",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    _log.info("auto_scheduler: analytics export scheduled %r", cron)


def add_cert_check_job(scheduler: AsyncIOScheduler,
                       cert_check_callback: Callable[[], None],
                       cron: str = "0 * * * *") -> None:
    """Wire the expedited TLS/SSL certificate renewal verification check.

    `cert_check_callback` is invoked periodically to find apps nearing
    certificate expiration (< 30d) or in failing state and perform an
    immediate re-check. Default is hourly.
    """
    scheduler.add_job(
        cert_check_callback,
        cron_trigger(cron, timezone="UTC"),
        id="tls::cert_check",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    _log.info("auto_scheduler: TLS cert renewal check scheduled %r", cron)


def is_enabled(config: dict) -> bool:
    return (config.get("scheduling") or {}).get("enabled", True)
