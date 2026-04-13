import base64
import json
import os
import pytest
from harness.export import export_pdf, export_docx

# Minimal valid 1x1 PNG (for screenshot embedding tests)
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVQI12NgAAIABQ"
    "AABjkB6QAAAABJRU5ErkJggg=="
)

SAMPLE_RUN = {
    "id": "abcd1234-0000-0000-0000-000000000000",
    "app": "myapp",
    "environment": "prod",
    "status": "complete",
    "started_at": "2026-01-01T12:00:00",
    "finished_at": "2026-01-01T12:01:00",
    "triggered_by": "test",
}

SAMPLE_RESULTS_NO_SCREENSHOTS = [
    {
        "test_name": "health",
        "status": "pass",
        "duration_ms": 120,
        "error_msg": None,
        "screenshot": None,
        "step_log": json.dumps([
            {"step": "navigate /", "status": "pass", "duration_ms": 80,
             "error": None, "screenshot": None},
        ]),
    }
]

SAMPLE_RESULTS_WITH_ERROR = [
    {
        "test_name": "login",
        "status": "fail",
        "duration_ms": 3000,
        "error_msg": "Element not found: #submit",
        "screenshot": None,
        "step_log": json.dumps([
            {"step": "navigate /login", "status": "pass", "duration_ms": 200,
             "error": None, "screenshot": None},
            {"step": "click #submit", "status": "fail", "duration_ms": 2800,
             "error": "Element not found: #submit", "screenshot": None},
        ]),
    }
]


def test_export_pdf_returns_pdf_bytes():
    result = export_pdf(SAMPLE_RUN, SAMPLE_RESULTS_NO_SCREENSHOTS)
    assert len(result) > 0
    assert result[:4] == b"%PDF"


def test_export_docx_returns_docx_bytes():
    result = export_docx(SAMPLE_RUN, SAMPLE_RESULTS_NO_SCREENSHOTS)
    assert len(result) > 0
    assert result[:2] == b"PK"


def test_export_pdf_with_error_result():
    result = export_pdf(SAMPLE_RUN, SAMPLE_RESULTS_WITH_ERROR)
    assert result[:4] == b"%PDF"


def test_export_docx_with_error_result():
    result = export_docx(SAMPLE_RUN, SAMPLE_RESULTS_WITH_ERROR)
    assert result[:2] == b"PK"


def test_export_pdf_skips_missing_failure_screenshot(tmp_path):
    results = [{**SAMPLE_RESULTS_NO_SCREENSHOTS[0],
                "screenshot": "myapp/prod/run1/test.png"}]
    result = export_pdf(SAMPLE_RUN, results, screenshots_dir=str(tmp_path))
    assert result[:4] == b"%PDF"


def test_export_docx_skips_missing_failure_screenshot(tmp_path):
    results = [{**SAMPLE_RESULTS_NO_SCREENSHOTS[0],
                "screenshot": "myapp/prod/run1/test.png"}]
    result = export_docx(SAMPLE_RUN, results, screenshots_dir=str(tmp_path))
    assert result[:2] == b"PK"


def test_export_pdf_embeds_existing_failure_screenshot(tmp_path):
    shot_path = tmp_path / "myapp" / "prod" / "run1"
    shot_path.mkdir(parents=True)
    (shot_path / "test.png").write_bytes(PNG_1X1)
    results = [{**SAMPLE_RESULTS_NO_SCREENSHOTS[0],
                "screenshot": "myapp/prod/run1/test.png"}]
    result = export_pdf(SAMPLE_RUN, results, screenshots_dir=str(tmp_path))
    assert result[:4] == b"%PDF"


def test_export_docx_embeds_existing_failure_screenshot(tmp_path):
    shot_path = tmp_path / "myapp" / "prod" / "run1"
    shot_path.mkdir(parents=True)
    (shot_path / "test.png").write_bytes(PNG_1X1)
    results = [{**SAMPLE_RESULTS_NO_SCREENSHOTS[0],
                "screenshot": "myapp/prod/run1/test.png"}]
    result = export_docx(SAMPLE_RUN, results, screenshots_dir=str(tmp_path))
    assert result[:2] == b"PK"


def test_export_pdf_embeds_step_screenshot(tmp_path):
    shot_path = tmp_path / "myapp" / "prod" / "run1"
    shot_path.mkdir(parents=True)
    (shot_path / "test-step-0.png").write_bytes(PNG_1X1)
    results = [{
        **SAMPLE_RESULTS_NO_SCREENSHOTS[0],
        "step_log": json.dumps([
            {"step": "screenshot", "status": "pass", "duration_ms": 50,
             "error": None, "screenshot": "myapp/prod/run1/test-step-0.png"},
        ]),
    }]
    result = export_pdf(SAMPLE_RUN, results, screenshots_dir=str(tmp_path))
    assert result[:4] == b"%PDF"


def test_export_pdf_skips_missing_step_screenshot(tmp_path):
    results = [{
        **SAMPLE_RESULTS_NO_SCREENSHOTS[0],
        "step_log": json.dumps([
            {"step": "screenshot", "status": "pass", "duration_ms": 50,
             "error": None, "screenshot": "myapp/prod/run1/missing-step-0.png"},
        ]),
    }]
    result = export_pdf(SAMPLE_RUN, results, screenshots_dir=str(tmp_path))
    assert result[:4] == b"%PDF"


# ---- Route tests ----

from fastapi.testclient import TestClient
from web.main import create_app
from harness.db import Database
from harness.models import Run, TestResult


@pytest.fixture
def export_db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    run = Run(app="myapp", environment="production", triggered_by="test", status="complete")
    d.insert_run(run)
    tr = TestResult(
        run_id=run.id,
        app="myapp",
        environment="production",
        test_name="health",
        status="pass",
        duration_ms=120,
    )
    d.insert_test_result(tr)
    return d, run.id


@pytest.fixture
def export_client(export_db, tmp_path):
    db, run_id = export_db
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    config = {}
    app = create_app(db=db, config=config, apps_dir=str(apps_dir))
    return TestClient(app)


@pytest.fixture
def export_run_id(export_db):
    db, run_id = export_db
    return run_id


def test_export_route_pdf(export_client, export_run_id):
    resp = export_client.get(f"/api/runs/{export_run_id}/export?format=pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"


def test_export_route_docx(export_client, export_run_id):
    resp = export_client.get(f"/api/runs/{export_run_id}/export?format=docx")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


def test_export_route_unknown_run(export_client):
    resp = export_client.get("/api/runs/nonexistent/export?format=pdf")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Run not found"


def test_export_route_bad_format(export_client, export_run_id):
    resp = export_client.get(f"/api/runs/{export_run_id}/export?format=xml")
    assert resp.status_code == 422
    assert resp.json()["detail"] == "format must be pdf or docx"
