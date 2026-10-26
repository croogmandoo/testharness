#!/usr/bin/env python3
"""Seed synthetic monitor runs, test results, and app state for local
demonstration, testing, and UI verification.

Usage:
    python scripts/seed_demo_data.py [--db PATH] [--env ENV] [--runs-per-app N]
"""
import argparse
import datetime
import os
import random
import uuid

from harness.db import Database
from harness.models import AppState, Run, StepResult, TestResult


def seed_synthetic_data(db_path: str = "data/harness.db",
                        environment: str = "production",
                        runs_per_app: int = 10,
                        apps: list[str] | None = None) -> dict:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    db = Database(db_path)
    db.init_schema()

    if not apps:
        apps = ["Harness API", "Example Web App", "Example REST API", "Radarr", "Sonarr"]

    now = datetime.datetime.now(datetime.timezone.utc)
    seeded_runs = 0
    seeded_results = 0

    for app in apps:
        # Give some apps 100% uptime, others occasional transient hiccups
        fail_chance = 0.15 if app in ("Example REST API", "Sonarr") else 0.0

        for i in range(runs_per_app, 0, -1):
            run_time = now - datetime.timedelta(minutes=i * 15)
            finished_time = run_time + datetime.timedelta(seconds=random.randint(1, 4))
            run_time_iso = run_time.isoformat()
            finished_time_iso = finished_time.isoformat()

            run_id = f"run_seed_{uuid.uuid4().hex[:12]}"
            run = Run(
                id=run_id,
                app=app,
                environment=environment,
                triggered_by="scheduler",
                status="complete",
                started_at=run_time_iso,
                finished_at=finished_time_iso,
            )
            db.insert_run(run)
            db.update_run_status(run_id, "complete", started_at=run_time_iso, finished_at=finished_time_iso)
            seeded_runs += 1

            # Test results for this run
            test_names = ["healthcheck", "api_ping"] if "API" in app else ["homepage", "navigation"]
            is_run_failing = (random.random() < fail_chance) and (i > 1)  # latest run usually healthy

            for test_name in test_names:
                tr_id = f"tr_seed_{uuid.uuid4().hex[:12]}"
                tr_status = "fail" if is_run_failing and test_name == test_names[0] else "pass"
                err_msg = "Synthetic timeout: 504 Gateway Timeout" if tr_status == "fail" else None
                duration_ms = random.randint(30, 240) if tr_status == "pass" else 5000

                steps = [
                    StepResult(step="http_get", status=tr_status, duration_ms=duration_ms, error=err_msg),
                ]
                tr = TestResult(
                    id=tr_id,
                    run_id=run_id,
                    app=app,
                    environment=environment,
                    test_name=test_name,
                    status=tr_status,
                    error_msg=err_msg,
                    step_log=steps,
                    duration_ms=duration_ms,
                    finished_at=finished_time_iso,
                )
                db.insert_test_result(tr)
                seeded_results += 1

                # Update latest app state
                if i == 1:
                    db.upsert_app_state(AppState(
                        app=app,
                        environment=environment,
                        test_name=test_name,
                        state="passing" if tr_status == "pass" else "failing",
                        since=finished_time_iso,
                    ))

    return {
        "apps": apps,
        "runs": seeded_runs,
        "results": seeded_results,
        "environment": environment,
    }


def main():
    parser = argparse.ArgumentParser(description="Seed synthetic monitor runs for testharness.")
    parser.add_argument("--db", default="data/harness.db", help="Path to harness SQLite DB")
    parser.add_argument("--env", default="production", help="Environment key (default: production)")
    parser.add_argument("--runs-per-app", type=int, default=10, help="Number of runs per app (default: 10)")
    args = parser.parse_args()

    result = seed_synthetic_data(
        db_path=args.db,
        environment=args.env,
        runs_per_app=args.runs_per_app,
    )
    print(f"Seeded {result['runs']} runs and {result['results']} test results across {len(result['apps'])} apps in '{result['environment']}'.")


if __name__ == "__main__":
    main()
