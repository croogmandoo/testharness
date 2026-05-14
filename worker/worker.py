"""Remote worker — registers with a central harness, polls for queued jobs,
runs them locally, submits results back.

Reuses every test-execution module from harness/ — only the orchestration
loop is worker-specific. The harness's runner module's `_execute_test`
function handles api / availability / browser tests identically whether
called from the in-process scheduler or from here.

Configuration is environment-variable only (twelve-factor):

    HARNESS_URL              — central harness, e.g. https://harness.example.com
    HARNESS_API_KEY          — Bearer token, hth_...
    HARNESS_WORKER_NAME      — unique name for this worker (required)
    HARNESS_WORKER_TAGS      — comma-separated tag list (default: empty)
    HARNESS_WORKER_VERSION   — reported back at registration (default: harness version)
    HARNESS_CAPABILITIES     — comma-separated (default: api,availability,browser)
    HARNESS_POLL_INTERVAL    — seconds between polls (default: 10)
    HARNESS_HEARTBEAT_INTERVAL — seconds between heartbeats (default: 30)
    HARNESS_CAPACITY         — max concurrent jobs (default: 4)
    HARNESS_VERIFY_TLS       — "true"/"false" (default: true)

Run:
    python -m worker.worker
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from harness.models import StepResult, TestResult

_log = logging.getLogger("harness.worker")


# ── Config ──────────────────────────────────────────────────────────────────

class WorkerConfig:
    def __init__(self):
        self.harness_url = _required_env("HARNESS_URL").rstrip("/")
        self.api_key = _required_env("HARNESS_API_KEY")
        self.name = _required_env("HARNESS_WORKER_NAME")
        self.tags = _csv_env("HARNESS_WORKER_TAGS", default=[])
        self.capabilities = _csv_env(
            "HARNESS_CAPABILITIES",
            default=["api", "availability", "browser"],
        )
        self.version = os.environ.get("HARNESS_WORKER_VERSION", "1.0.0")
        self.poll_interval = int(os.environ.get("HARNESS_POLL_INTERVAL", "10"))
        self.heartbeat_interval = int(os.environ.get("HARNESS_HEARTBEAT_INTERVAL", "30"))
        self.capacity = int(os.environ.get("HARNESS_CAPACITY", "4"))
        verify_str = os.environ.get("HARNESS_VERIFY_TLS", "true").lower()
        self.verify_tls = verify_str not in ("false", "0", "no", "off")


def _required_env(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if not val:
        sys.stderr.write(
            f"\n[worker] required env var {key} is not set\n"
            "See worker/README.md for the full env-var contract.\n\n"
        )
        sys.exit(2)
    return val


def _csv_env(key: str, default: list[str]) -> list[str]:
    raw = os.environ.get(key, "")
    if not raw.strip():
        return default
    return [x.strip() for x in raw.split(",") if x.strip()]


# ── Central API helpers ─────────────────────────────────────────────────────

def _headers(cfg: WorkerConfig) -> dict:
    return {"Authorization": f"Bearer {cfg.api_key}", "Accept": "application/json"}


async def register(client: httpx.AsyncClient, cfg: WorkerConfig) -> dict:
    resp = await client.post(
        f"{cfg.harness_url}/api/runner/register",
        json={
            "name": cfg.name,
            "tags": cfg.tags,
            "capabilities": cfg.capabilities,
            "version": cfg.version,
        },
        headers=_headers(cfg),
    )
    resp.raise_for_status()
    return resp.json()


async def heartbeat(client: httpx.AsyncClient, cfg: WorkerConfig) -> None:
    try:
        resp = await client.post(
            f"{cfg.harness_url}/api/runner/heartbeat",
            json={"name": cfg.name},
            headers=_headers(cfg),
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        _log.warning("heartbeat failed: %s", e)


async def poll_for_jobs(client: httpx.AsyncClient, cfg: WorkerConfig,
                         capacity: int) -> list[dict]:
    try:
        resp = await client.post(
            f"{cfg.harness_url}/api/runner/poll",
            json={"name": cfg.name, "capacity": capacity},
            headers=_headers(cfg),
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json().get("jobs", [])
    except httpx.HTTPError as e:
        _log.warning("poll failed: %s", e)
        return []


async def submit_results(client: httpx.AsyncClient, cfg: WorkerConfig,
                          job_id: str, run_status: str,
                          results: list[dict],
                          error: Optional[str] = None) -> None:
    payload = {
        "job_id": job_id,
        "run_status": run_status,
        "error": error,
        "results": results,
    }
    try:
        resp = await client.post(
            f"{cfg.harness_url}/api/runner/results",
            json=payload,
            headers=_headers(cfg),
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        # Log + raise — caller may want to retry / queue locally
        _log.error("submit_results failed for job %s: %s", job_id, e)
        raise


# ── Job execution ───────────────────────────────────────────────────────────

def _test_result_to_dict(tr: TestResult) -> dict:
    """Serialize a TestResult into the wire format expected by /api/runner/results."""
    return {
        "test_name": tr.test_name,
        "status": tr.status,
        "error_msg": tr.error_msg,
        "duration_ms": tr.duration_ms,
        "finished_at": tr.finished_at,
        "step_log": [
            {
                "step": s.step,
                "status": s.status,
                "duration_ms": s.duration_ms,
                "error": s.error,
                "screenshot": None,  # screenshots aren't uploaded in v1
            }
            for s in (tr.step_log or [])
        ],
        "screenshot": None,
    }


async def execute_job(job: dict, cfg: WorkerConfig) -> tuple[str, list[dict], Optional[str]]:
    """Run all tests in `job["app_def"]` against `job["environment"]`.
    Returns (run_status, results, error).
    """
    from harness.api import run_api_test
    from harness.browser import run_availability_test, run_browser_test
    from harness.loader import resolve_base_url
    from harness.runner import _apply_slo_threshold

    app_def = job["app_def"]
    environment = job["environment"]
    job_id = job["job_id"]
    run_id = job["run_id"]

    _log.info("worker[%s] running job %s app=%s env=%s",
              cfg.name, job_id, app_def.get("app"), environment)

    try:
        base_url = resolve_base_url(app_def, environment)
    except Exception as e:
        return ("error", [], f"resolve_base_url: {e}")

    browser_cfg = {"headless": True, "timeout_ms": 30000}
    results_out: list[dict] = []

    # Sequential mode is forced when any test has `extract:` — but since the
    # central already resolved `$VAR` references, capture chaining is the only
    # remaining reason. We always run sequentially in v1 to keep the worker
    # simple; parallelism is a future optimization.
    captures: dict = {}
    for test_def in app_def.get("tests", []):
        # Worker-side capture chaining mirrors harness.runner._execute_test
        from harness.config import resolve_env_vars
        resolved_td = resolve_env_vars(test_def, strict=True, extra_vars=captures)
        ttype = resolved_td.get("type", "availability")
        max_retries = int(resolved_td.get("retry", 0))
        timeout_ms = browser_cfg.get("timeout_ms", 30000)

        result: Optional[TestResult] = None
        for _attempt in range(max_retries + 1):
            try:
                if ttype == "api":
                    result = await run_api_test(
                        run_id, app_def["app"], environment, base_url,
                        resolved_td, ssl_ctx=None,
                    )
                elif ttype == "browser":
                    test_timeout_ms = resolved_td.get("timeout_ms", timeout_ms)
                    result = await run_browser_test(
                        run_id, app_def["app"], environment, base_url,
                        resolved_td,
                        headless=browser_cfg.get("headless", True),
                        timeout_ms=test_timeout_ms,
                    )
                else:
                    result = await run_availability_test(
                        run_id, app_def["app"], environment, base_url,
                        resolved_td, ssl_ctx=None,
                    )
                _apply_slo_threshold(resolved_td, result)
                if result.status not in ("fail", "error"):
                    break
            except Exception as e:
                result = TestResult(
                    run_id=run_id, app=app_def["app"], environment=environment,
                    test_name=resolved_td.get("name", "unknown"),
                    status="error",
                    error_msg=f"Worker exception: {type(e).__name__}: {e}",
                    finished_at=datetime.now(timezone.utc).isoformat(),
                )
                _log.exception("worker[%s] test %r raised", cfg.name,
                               resolved_td.get("name"))

        if result is not None and result.captures:
            captures.update(result.captures)
        if result is not None:
            results_out.append(_test_result_to_dict(result))

    return ("complete", results_out, None)


# ── Main loop ───────────────────────────────────────────────────────────────

_shutdown_event: Optional[asyncio.Event] = None


def _install_signal_handlers(loop: asyncio.AbstractEventLoop) -> None:
    global _shutdown_event
    _shutdown_event = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _shutdown_event.set)
        except NotImplementedError:  # Windows
            pass


async def heartbeat_loop(client: httpx.AsyncClient, cfg: WorkerConfig) -> None:
    while _shutdown_event is None or not _shutdown_event.is_set():
        await heartbeat(client, cfg)
        try:
            await asyncio.wait_for(_shutdown_event.wait(), timeout=cfg.heartbeat_interval)
            return  # shutdown requested
        except asyncio.TimeoutError:
            continue


async def poll_loop(client: httpx.AsyncClient, cfg: WorkerConfig) -> None:
    semaphore = asyncio.Semaphore(cfg.capacity)
    inflight: set[asyncio.Task] = set()

    async def _run_one(job: dict) -> None:
        async with semaphore:
            try:
                status, results, error = await execute_job(job, cfg)
                await submit_results(client, cfg, job["job_id"], status, results, error)
            except Exception:
                _log.exception("execute_job failed for %s", job.get("job_id"))

    while _shutdown_event is None or not _shutdown_event.is_set():
        # Available slots = capacity - currently in flight
        # (semaphore approximates this; for poll we ask for what we can take on)
        remaining = max(1, cfg.capacity - len([t for t in inflight if not t.done()]))
        jobs = await poll_for_jobs(client, cfg, remaining)
        for job in jobs:
            task = asyncio.create_task(_run_one(job))
            inflight.add(task)
            task.add_done_callback(inflight.discard)
        try:
            await asyncio.wait_for(_shutdown_event.wait(), timeout=cfg.poll_interval)
            break  # shutdown requested
        except asyncio.TimeoutError:
            continue

    # On shutdown, wait briefly for in-flight jobs to complete (best effort)
    if inflight:
        _log.info("worker[%s] waiting for %d in-flight jobs to finish",
                  cfg.name, len(inflight))
        try:
            await asyncio.wait_for(asyncio.gather(*inflight, return_exceptions=True),
                                   timeout=60.0)
        except asyncio.TimeoutError:
            _log.warning("worker[%s] some jobs didn't finish in time", cfg.name)


async def amain() -> int:
    # Honor config.yaml's `logging:` block when present; falls back to sensible
    # defaults (rotating data/logs/harness.log + console) if config is missing
    # or unreadable, so the worker can still start in minimal environments.
    try:
        from harness.config import load_config
        from harness.logging_config import configure as _configure_logging
        _cfg = load_config("config.yaml") if os.path.exists("config.yaml") else {}
        _configure_logging(_cfg)
    except Exception:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )
    cfg = WorkerConfig()
    _install_signal_handlers(asyncio.get_event_loop())

    _log.info(
        "worker[%s] starting — central=%s tags=%s capacity=%d",
        cfg.name, cfg.harness_url, cfg.tags, cfg.capacity,
    )

    async with httpx.AsyncClient(verify=cfg.verify_tls, timeout=30.0) as client:
        try:
            reg = await register(client, cfg)
        except httpx.HTTPError as e:
            _log.error("worker[%s] registration failed: %s", cfg.name, e)
            return 3
        _log.info("worker[%s] registered (server poll_interval=%ds capacity=%d)",
                  cfg.name, reg.get("poll_interval_seconds"), reg.get("capacity"))

        await asyncio.gather(
            heartbeat_loop(client, cfg),
            poll_loop(client, cfg),
        )

    _log.info("worker[%s] exiting cleanly", cfg.name)
    return 0


def main() -> int:
    return asyncio.run(amain())


if __name__ == "__main__":
    sys.exit(main())
