"""Unit and integration tests for visual regression diff viewer and baseline approval workflow."""

import os
import uuid
from datetime import datetime, timezone
import pytest
from PIL import Image
from fastapi.testclient import TestClient

from harness.screenshot_diff import generate_diff_image
from harness.models import TestResult
from web.main import create_app
from harness.db import Database


@pytest.fixture
def temp_images(tmp_path):
    img1_path = str(tmp_path / "img1.png")
    img2_path = str(tmp_path / "img2.png")
    diff_path = str(tmp_path / "diff.png")

    # Image 1: 100x100 white
    im1 = Image.new("RGB", (100, 100), color=(255, 255, 255))
    im1.save(img1_path)

    # Image 2: 100x100 white with 20x20 black box in center
    im2 = Image.new("RGB", (100, 100), color=(255, 255, 255))
    for x in range(40, 60):
        for y in range(40, 60):
            im2.putpixel((x, y), (0, 0, 0))
    im2.save(img2_path)

    return img1_path, img2_path, diff_path


def test_generate_diff_image_identical(tmp_path):
    path1 = str(tmp_path / "base.png")
    path2 = str(tmp_path / "same.png")
    diff_path = str(tmp_path / "diff.png")

    im = Image.new("RGB", (50, 50), color=(100, 100, 100))
    im.save(path1)
    im.save(path2)

    diff_val = generate_diff_image(path1, path2, diff_path)
    assert diff_val == 0.0
    assert os.path.exists(diff_path)


def test_generate_diff_image_with_differences(temp_images):
    img1, img2, diff_path = temp_images
    diff_val = generate_diff_image(img1, img2, diff_path, threshold=10)

    assert diff_val is not None
    # 20x20 = 400 pixels out of 10000 = 0.04
    assert 0.035 <= diff_val <= 0.045
    assert os.path.exists(diff_path)

    # Verify generated diff image has magenta pixel at (50, 50)
    out_img = Image.open(diff_path)
    pixel = out_img.getpixel((50, 50))
    # Magenta is (255, 0, 128)
    assert pixel == (255, 0, 128)


def test_generate_diff_image_mismatched_dimensions(tmp_path):
    path1 = str(tmp_path / "small.png")
    path2 = str(tmp_path / "large.png")
    diff_path = str(tmp_path / "diff.png")

    Image.new("RGB", (50, 50), color=(200, 200, 200)).save(path1)
    Image.new("RGB", (100, 100), color=(200, 200, 200)).save(path2)

    diff_val = generate_diff_image(path1, path2, diff_path)
    assert diff_val is not None
    assert os.path.exists(diff_path)


def test_generate_diff_image_missing_file():
    diff_val = generate_diff_image("/nonexistent/a.png", "/nonexistent/b.png", "/tmp/diff.png")
    assert diff_val is None


def _make_user(db, username: str, role: str) -> dict:
    user = {
        "id": str(uuid.uuid4()),
        "username": username,
        "display_name": username.title(),
        "email": None,
        "password_hash": None,
        "role": role,
        "auth_provider": "local",
        "role_override": 0,
        "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_login_at": None,
    }
    db.insert_user(user)
    return user


def test_approve_baseline_unauthenticated(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.init_schema()
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    app = create_app(db=db, config={}, apps_dir=str(apps_dir))
    client = TestClient(app)

    resp = client.post("/api/visual/approve", json={"result_id": "res-123"})
    assert resp.status_code == 401


def test_approve_baseline_viewer_forbidden(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.init_schema()
    viewer = _make_user(db, "viewer1", "viewer")
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    app = create_app(db=db, config={}, apps_dir=str(apps_dir))
    from web.auth import get_current_user
    app.dependency_overrides[get_current_user] = lambda: viewer
    client = TestClient(app)

    resp = client.post("/api/visual/approve", json={"result_id": "res-123"})
    assert resp.status_code == 403


def test_approve_baseline_not_found(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.init_schema()
    admin = _make_user(db, "admin1", "admin")
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    app = create_app(db=db, config={}, apps_dir=str(apps_dir))
    from web.auth import get_current_user
    app.dependency_overrides[get_current_user] = lambda: admin
    client = TestClient(app)

    resp = client.post("/api/visual/approve", json={"result_id": "nonexistent-result"})
    assert resp.status_code == 404
    assert "Test result not found" in resp.json()["detail"]


def test_approve_baseline_no_screenshot(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.init_schema()
    admin = _make_user(db, "admin2", "admin")
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    app = create_app(db=db, config={}, apps_dir=str(apps_dir))
    from web.auth import get_current_user
    app.dependency_overrides[get_current_user] = lambda: admin
    client = TestClient(app)

    tr = TestResult(
        run_id="run-1",
        app="webapp",
        environment="prod",
        test_name="Homepage Visual",
        status="fail",
        duration_ms=120,
        finished_at="2026-10-26T12:00:00Z",
        error_msg="Failed",
        screenshot=None,
    )
    db.insert_test_result(tr)

    resp = client.post("/api/visual/approve", json={"result_id": tr.id})
    assert resp.status_code == 400
    assert "no screenshot to approve" in resp.json()["detail"]


def test_approve_baseline_success_and_updates_last_screenshot(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.init_schema()
    runner = _make_user(db, "runner1", "runner")
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    app = create_app(db=db, config={}, apps_dir=str(apps_dir))
    from web.auth import get_current_user
    app.dependency_overrides[get_current_user] = lambda: runner
    client = TestClient(app)

    # 1. Baseline passing result from yesterday
    tr_old = TestResult(
        run_id="run-0",
        app="webapp",
        environment="prod",
        test_name="Homepage Visual",
        status="pass",
        duration_ms=100,
        finished_at="2026-10-25T10:00:00Z",
        screenshot="baseline-old.png",
    )
    db.insert_test_result(tr_old)
    assert db.get_last_screenshot("webapp", "prod", "Homepage Visual") == "baseline-old.png"

    # 2. Today's run failed with visual regression
    tr_new = TestResult(
        run_id="run-1",
        app="webapp",
        environment="prod",
        test_name="Homepage Visual",
        status="fail",
        duration_ms=150,
        finished_at="2026-10-26T10:00:00Z",
        error_msg="Visual regression: 5.2% pixels changed",
        screenshot="candidate-new.png",
    )
    db.insert_test_result(tr_new)

    # Since tr_new is fail, get_last_screenshot still returns baseline-old.png!
    assert db.get_last_screenshot("webapp", "prod", "Homepage Visual") == "baseline-old.png"

    # 3. Approve candidate-new.png
    resp = client.post("/api/visual/approve", json={"result_id": tr_new.id})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["status"] == "pass"

    # 4. Verify get_last_screenshot now returns candidate-new.png
    assert db.get_last_screenshot("webapp", "prod", "Homepage Visual") == "candidate-new.png"

    # 5. Verify audit log entry
    audit_entries = db.list_audit_log(limit=10)
    assert any(entry["action"] == "visual.baseline_approve" for entry in audit_entries)
