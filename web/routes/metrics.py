"""Prometheus exposition endpoint.

Hand-written exposition format (text/plain; version=0.0.4) — keeps the
dependency surface minimal.  See:
https://prometheus.io/docs/instrumenting/exposition_formats/

By default `/metrics` is unauthenticated (Prometheus scrapers can't easily
present cookies/keys).  Set `metrics.bearer_token` in config to require
`Authorization: Bearer <token>` instead.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import PlainTextResponse

router = APIRouter()

PROM_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def _escape_label(value: str) -> str:
    """Per the exposition spec: escape backslash, quote, and newline."""
    return (
        value.replace("\\", "\\\\")
             .replace('"', '\\"')
             .replace("\n", "\\n")
    )


def _format_labels(labels: dict) -> str:
    if not labels:
        return ""
    parts = ",".join(f'{k}="{_escape_label(str(v))}"' for k, v in labels.items())
    return "{" + parts + "}"


def _emit(name: str, help_text: str, mtype: str,
          samples: list[tuple[dict, float]]) -> list[str]:
    lines = [f"# HELP {name} {help_text}", f"# TYPE {name} {mtype}"]
    for labels, value in samples:
        lines.append(f"{name}{_format_labels(labels)} {value}")
    return lines


def _iso_to_unix(iso: Optional[str]) -> Optional[float]:
    if not iso:
        return None
    try:
        # Python <3.11 doesn't accept trailing Z; the codebase already uses
        # `.isoformat()` which produces +00:00, so this should always parse.
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return None


def _build_metrics(db) -> str:
    lines: list[str] = []

    # ── Per-test current state ─────────────────────────────────────────────
    with db._connect() as conn:
        state_rows = conn.execute(
            "SELECT app, environment, test_name, state, since FROM app_state"
        ).fetchall()
        run_rows = conn.execute(
            "SELECT app, environment, status, COUNT(*) AS cnt FROM runs "
            "GROUP BY app, environment, status"
        ).fetchall()
        last_finished = conn.execute(
            "SELECT app, environment, MAX(finished_at) AS last_run FROM runs "
            "WHERE status='complete' GROUP BY app, environment"
        ).fetchall()
        last_durations = conn.execute(
            "SELECT app, environment, test_name, duration_ms, finished_at "
            "FROM test_results tr "
            "WHERE finished_at = (SELECT MAX(finished_at) FROM test_results "
            "                     WHERE app=tr.app AND environment=tr.environment "
            "                     AND test_name=tr.test_name)"
        ).fetchall()

    # harness_test_status: 1=passing, 0=failing, -1=unknown
    status_samples = []
    state_to_value = {"passing": 1, "failing": 0, "unknown": -1}
    for r in state_rows:
        labels = {
            "app": r["app"],
            "environment": r["environment"],
            "test": r["test_name"],
        }
        status_samples.append((labels, state_to_value.get(r["state"], -1)))
    lines += _emit(
        "harness_test_status",
        "Latest known test state (1=passing, 0=failing, -1=unknown)",
        "gauge", status_samples,
    )

    # harness_test_last_duration_seconds
    duration_samples = []
    for r in last_durations:
        if r["duration_ms"] is None:
            continue
        labels = {
            "app": r["app"],
            "environment": r["environment"],
            "test": r["test_name"],
        }
        duration_samples.append((labels, r["duration_ms"] / 1000.0))
    lines += _emit(
        "harness_test_last_duration_seconds",
        "Duration of the most recent run of each test, in seconds",
        "gauge", duration_samples,
    )

    # harness_app_last_run_timestamp_seconds
    last_run_samples = []
    for r in last_finished:
        ts = _iso_to_unix(r["last_run"])
        if ts is None:
            continue
        last_run_samples.append((
            {"app": r["app"], "environment": r["environment"]},
            ts,
        ))
    lines += _emit(
        "harness_app_last_run_timestamp_seconds",
        "Unix timestamp of each app's most recent completed run",
        "gauge", last_run_samples,
    )

    # harness_runs_total — counter by status
    run_samples = []
    for r in run_rows:
        labels = {
            "app": r["app"],
            "environment": r["environment"],
            "status": r["status"],
        }
        run_samples.append((labels, r["cnt"]))
    lines += _emit(
        "harness_runs_total",
        "Total number of runs grouped by app, environment, and status",
        "counter", run_samples,
    )

    try:
        runner_rows = conn.execute("SELECT name, last_seen_at FROM runners").fetchall()
        runner_samples = []
        for r in runner_rows:
            ts = _iso_to_unix(r["last_seen_at"])
            if ts is not None:
                runner_samples.append(({"runner": r["name"]}, ts))
        if runner_samples:
            lines += _emit(
                "harness_runner_last_seen_timestamp_seconds",
                "Unix timestamp of each runner's last heartbeat",
                "gauge", runner_samples,
            )
    except Exception:
        pass

    return "\n".join(lines) + "\n"


@router.get("/metrics", response_class=PlainTextResponse,
            include_in_schema=False)
async def metrics(authorization: Optional[str] = Header(default=None)):
    from web.main import get_db, get_config
    config = get_config() or {}
    cfg = config.get("metrics") or {}
    if cfg.get("enabled", True) is False:
        raise HTTPException(status_code=404)

    expected = cfg.get("bearer_token")
    if expected:
        token = ""
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        if token != expected:
            raise HTTPException(status_code=401, detail="invalid metrics token")

    body = _build_metrics(get_db())
    return PlainTextResponse(body, media_type=PROM_CONTENT_TYPE)
