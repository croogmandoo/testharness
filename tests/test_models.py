from harness.models import Run, TestResult, AppState, StepResult
import uuid

def test_run_has_uuid_by_default():
    run = Run(app="myapp", environment="production", triggered_by="ui")
    assert len(run.id) == 36
    assert run.status == "pending"

def test_test_result_defaults():
    tr = TestResult(run_id="abc", app="myapp", environment="production", test_name="login")
    assert tr.status == ""
    assert tr.screenshot is None
    assert tr.step_log == []

def test_app_state_fields():
    state = AppState(app="myapp", environment="staging", test_name="login",
                     state="failing", since="2026-04-11T10:00:00")
    assert state.state == "failing"

def test_step_result():
    step = StepResult(step="navigate /login", status="pass", duration_ms=120)
    assert step.error is None
