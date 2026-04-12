import json
from harness.models import StepResult, TestResult


def test_step_result_has_screenshot_field():
    s = StepResult(step="screenshot", status="pass", duration_ms=42, screenshot="app/prod/run1/test-step-0.png")
    assert s.screenshot == "app/prod/run1/test-step-0.png"


def test_step_result_screenshot_defaults_none():
    s = StepResult(step="navigate /", status="pass", duration_ms=10)
    assert s.screenshot is None


def test_db_serialises_screenshot_in_step_log(tmp_path):
    """screenshot field survives round-trip through insert_test_result → get_results_for_run."""
    import os
    os.environ.setdefault("HARNESS_DB", str(tmp_path / "h.db"))
    from harness.db import Database
    from harness.models import Run, TestResult, StepResult

    db = Database(str(tmp_path / "h.db"))
    db.init_schema()

    run = Run(app="myapp", environment="prod", triggered_by="test")
    db.insert_run(run)

    step_with_shot = StepResult(step="screenshot", status="pass", duration_ms=5,
                                screenshot="myapp/prod/run1/test-step-0.png")
    step_no_shot = StepResult(step="navigate /", status="pass", duration_ms=3)
    tr = TestResult(run_id=run.id, app="myapp", environment="prod",
                    test_name="login", status="pass",
                    step_log=[step_with_shot, step_no_shot])
    db.insert_test_result(tr)

    results = db.get_results_for_run(run.id)
    assert len(results) == 1
    steps = json.loads(results[0]["step_log"])
    assert steps[0]["screenshot"] == "myapp/prod/run1/test-step-0.png"
    assert steps[1]["screenshot"] is None


def test_get_app_summary_has_active_run_id_when_run_is_running(tmp_path):
    from harness.db import Database
    from harness.models import Run, AppState
    db = Database(str(tmp_path / "h.db"))
    db.init_schema()
    run = Run(app="myapp", environment="prod", triggered_by="test")
    db.insert_run(run)
    db.update_run_status(run.id, "running", started_at="2026-01-01T00:00:00")

    # Seed app_state so myapp appears in summary
    db.upsert_app_state(AppState(app="myapp", environment="prod",
                                 test_name="t", state="passing",
                                 since="2026-01-01T00:00:00"))
    summary = db.get_app_summary("prod")
    row = next(r for r in summary if r["app"] == "myapp")
    assert row["active_run_id"] == run.id


def test_get_app_summary_active_run_id_is_none_when_complete(tmp_path):
    from harness.db import Database
    from harness.models import Run, AppState
    db = Database(str(tmp_path / "h.db"))
    db.init_schema()
    run = Run(app="myapp", environment="prod", triggered_by="test")
    db.insert_run(run)
    db.update_run_status(run.id, "complete",
                         started_at="2026-01-01T00:00:00",
                         finished_at="2026-01-01T00:01:00")
    db.upsert_app_state(AppState(app="myapp", environment="prod",
                                 test_name="t", state="passing",
                                 since="2026-01-01T00:00:00"))
    summary = db.get_app_summary("prod")
    row = next(r for r in summary if r["app"] == "myapp")
    assert row["active_run_id"] is None
