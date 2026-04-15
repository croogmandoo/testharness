# tests/test_web_admin_ca_certs.py
import uuid
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from web.main import create_app
from harness.db import Database


FAKE_PEM = (
    "-----BEGIN CERTIFICATE-----\n"
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAfake\n"
    "-----END CERTIFICATE-----"
)


def _seed_admin(db) -> dict:
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


def _seed_runner(db) -> dict:
    user = {
        "id": str(uuid.uuid4()),
        "username": "_runner",
        "display_name": "Runner",
        "email": None,
        "password_hash": None,
        "role": "runner",
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
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    _seed_admin(d)
    _seed_runner(d)
    return d


def _make_client(db, user):
    config = {
        "default_environment": "production",
        "environments": {"production": {"label": "Production"}},
    }
    app = create_app(db=db, config=config, apps_dir="apps")
    from web.auth import get_current_user
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


@pytest.fixture
def admin_client(db):
    return _make_client(db, db.get_user_by_username("_admin"))


@pytest.fixture
def runner_client(db):
    return _make_client(db, db.get_user_by_username("_runner"))


def test_list_page_renders_empty(admin_client):
    r = admin_client.get("/admin/ca-certs")
    assert r.status_code == 200
    assert "No CA certificates stored" in r.text


def test_add_cert_via_paste(admin_client, db):
    r = admin_client.post("/admin/ca-certs", data={"name": "Corp CA", "pem_content": FAKE_PEM})
    assert r.status_code in (200, 303)
    certs = db.list_ca_certs()
    assert len(certs) == 1
    assert certs[0]["name"] == "Corp CA"
    assert certs[0]["pem_content"] == FAKE_PEM


def test_add_cert_via_file_upload(admin_client, db, tmp_path):
    pem_file = tmp_path / "test.pem"
    pem_file.write_text(FAKE_PEM)
    with open(pem_file, "rb") as f:
        r = admin_client.post(
            "/admin/ca-certs",
            data={"name": "Upload CA", "pem_content": ""},
            files={"pem_file": ("test.pem", f, "application/octet-stream")},
        )
    assert r.status_code in (200, 303)
    certs = db.list_ca_certs()
    assert len(certs) == 1
    assert certs[0]["name"] == "Upload CA"


def test_add_cert_file_takes_priority_over_paste(admin_client, db, tmp_path):
    pem_file = tmp_path / "test.pem"
    pem_file.write_text(FAKE_PEM)
    with open(pem_file, "rb") as f:
        r = admin_client.post(
            "/admin/ca-certs",
            data={"name": "Priority CA", "pem_content": "should be ignored"},
            files={"pem_file": ("test.pem", f, "application/octet-stream")},
        )
    assert r.status_code in (200, 303)
    cert = db.list_ca_certs()[0]
    assert cert["pem_content"] == FAKE_PEM


def test_add_cert_invalid_pem_rejected(admin_client, db):
    r = admin_client.post("/admin/ca-certs",
                          data={"name": "Bad", "pem_content": "this is not a cert"},
                          follow_redirects=False)
    assert r.status_code == 422
    assert db.list_ca_certs() == []


def test_add_cert_missing_name_rejected(admin_client, db):
    r = admin_client.post("/admin/ca-certs",
                          data={"name": "", "pem_content": FAKE_PEM},
                          follow_redirects=False)
    assert r.status_code == 422
    assert db.list_ca_certs() == []


def test_list_page_shows_cert_after_add(admin_client, db):
    db.insert_ca_cert({
        "id": "cert-show",
        "name": "Visible CA",
        "pem_content": FAKE_PEM,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "added_by": db.get_user_by_username("_admin")["id"],
    })
    r = admin_client.get("/admin/ca-certs")
    assert r.status_code == 200
    assert "Visible CA" in r.text


def test_delete_cert(admin_client, db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db.insert_ca_cert({
        "id": "cert-del",
        "name": "Delete Me",
        "pem_content": FAKE_PEM,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "added_by": None,
    })
    r = admin_client.post("/admin/ca-certs/cert-del/delete")
    assert r.status_code in (200, 303)
    assert db.get_ca_cert("cert-del") is None


def test_non_admin_gets_403(runner_client):
    r = runner_client.get("/admin/ca-certs", follow_redirects=False)
    assert r.status_code == 403


def test_non_admin_cannot_post(runner_client):
    r = runner_client.post("/admin/ca-certs",
                           data={"name": "Corp CA", "pem_content": FAKE_PEM},
                           follow_redirects=False)
    assert r.status_code == 403
