"""Tier-3: POST /api/runs accepts a `tag` filter for bulk runs."""
import uuid
from datetime import datetime, timezone

import pytest
import yaml
from fastapi.testclient import TestClient

from harness.db import Database
from web.main import create_app
from web.auth import get_current_user


def _seed_admin(db):
    user = {
        "id": str(uuid.uuid4()),
        "username": "_admin",
        "display_name": "Admin",
        "email": None,
        "password_hash": None,
        "role": "admin",
        "auth_provider": "local",
        "role_override": 0,
        "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_login_at": None,
    }
    db.insert_user(user)
    return user


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "tag.db"))
    d.init_schema()
    return d


@pytest.fixture
def apps_dir(tmp_path):
    d = tmp_path / "apps"
    d.mkdir()
    (d / "a.yaml").write_text(yaml.dump({
        "app": "Alpha", "tags": ["critical", "media"],
        "environments": {"production": "https://a"}, "tests": [],
    }))
    (d / "b.yaml").write_text(yaml.dump({
        "app": "Beta", "tags": ["critical"],
        "environments": {"production": "https://b"}, "tests": [],
    }))
    (d / "c.yaml").write_text(yaml.dump({
        "app": "Gamma", "tags": ["nightly"],
        "environments": {"production": "https://c"}, "tests": [],
    }))
    return d


@pytest.fixture
def client(db, apps_dir):
    admin = _seed_admin(db)
    app = create_app(
        db=db,
        config={"environments": {"production": {"label": "Production"}}},
        apps_dir=str(apps_dir),
    )
    app.dependency_overrides[get_current_user] = lambda: admin
    return TestClient(app)


def test_run_by_tag_queues_only_matching_apps(client, db):
    with client:
        r = client.post("/api/runs", json={
            "tag": "critical", "environment": "production",
        })
    assert r.status_code == 202, r.text
    body = r.json()
    assert sorted(body["apps"]) == ["Alpha", "Beta"]


def test_run_by_unknown_tag_returns_404(client):
    with client:
        r = client.post("/api/runs", json={
            "tag": "no-such-tag", "environment": "production",
        })
    assert r.status_code == 404
    assert "no-such-tag" in r.json()["detail"]


def test_app_and_tag_are_mutually_exclusive(client):
    with client:
        r = client.post("/api/runs", json={
            "app": "Alpha", "tag": "critical", "environment": "production",
        })
    assert r.status_code == 400
    assert "either" in r.json()["detail"].lower()


def test_run_all_still_works_without_tag(client, db):
    """Sanity: existing 'run all' behaviour is unchanged."""
    with client:
        r = client.post("/api/runs", json={"environment": "production"})
    assert r.status_code == 202
    assert sorted(r.json()["apps"]) == ["Alpha", "Beta", "Gamma"]
