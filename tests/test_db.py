import pytest
from harness.db import Database
from harness.models import Run, TestResult, AppState

@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    return d

def test_insert_and_get_run(db):
    run = Run(app="myapp", environment="production", triggered_by="ui")
    db.insert_run(run)
    fetched = db.get_run(run.id)
    assert fetched["app"] == "myapp"
    assert fetched["status"] == "pending"

def test_update_run_status(db):
    run = Run(app="myapp", environment="production", triggered_by="api")
    db.insert_run(run)
    db.update_run_status(run.id, "running", started_at="2026-04-11T10:00:00")
    fetched = db.get_run(run.id)
    assert fetched["status"] == "running"
    assert fetched["started_at"] == "2026-04-11T10:00:00"

def test_insert_test_result(db):
    run = Run(app="myapp", environment="production", triggered_by="api")
    db.insert_run(run)
    tr = TestResult(run_id=run.id, app="myapp", environment="production",
                    test_name="login", status="fail", error_msg="Timeout")
    db.insert_test_result(tr)
    results = db.get_results_for_run(run.id)
    assert len(results) == 1
    assert results[0]["test_name"] == "login"

def test_upsert_and_get_app_state(db):
    state = AppState(app="myapp", environment="production", test_name="login",
                     state="failing", since="2026-04-11T10:00:00")
    db.upsert_app_state(state)
    fetched = db.get_app_state("myapp", "production", "login")
    assert fetched["state"] == "failing"
    state.state = "passing"
    state.since = "2026-04-11T11:00:00"
    db.upsert_app_state(state)
    fetched = db.get_app_state("myapp", "production", "login")
    assert fetched["state"] == "passing"

def test_is_run_active(db):
    run = Run(app="myapp", environment="production", triggered_by="api")
    db.insert_run(run)
    assert not db.is_run_active("myapp", "production")
    db.update_run_status(run.id, "running")
    assert db.is_run_active("myapp", "production")
    db.update_run_status(run.id, "complete")
    assert not db.is_run_active("myapp", "production")

def test_get_app_summary_returns_all_states(db):
    for test_name, state in [("login", "passing"), ("health", "failing")]:
        db.upsert_app_state(AppState(
            app="myapp", environment="production",
            test_name=test_name, state=state, since="2026-04-11T10:00:00"
        ))
    summary = db.get_app_summary("production")
    myapp = next(s for s in summary if s["app"] == "myapp")
    assert myapp["total"] == 2
    assert myapp["passing"] == 1
