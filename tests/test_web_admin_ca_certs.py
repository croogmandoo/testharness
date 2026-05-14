# tests/test_web_admin_ca_certs.py
import uuid
import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from web.main import create_app
from harness.db import Database


def _build_real_pem() -> str:
    """Generate a real self-signed CA cert so the route's PEM validator
    (cryptography.x509.load_pem_x509_certificates) actually accepts it.
    The old hard-coded ``FAKE_PEM`` only passed the now-removed substring
    check; with real validation it would be rejected at upload time."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test CA")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode("utf-8")


REAL_PEM = _build_real_pem()
# Back-compat alias. The route strips trailing whitespace on insert, so the
# stored copy is also stripped — equality assertions in existing tests
# expect the storage-shape value here.
FAKE_PEM = REAL_PEM.strip()


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
    from web.csrf import verify_csrf
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[verify_csrf] = lambda: None
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


def test_add_cert_empty_file_falls_back_to_paste(admin_client, db):
    """When file input is empty (no file selected), use the pem_content paste field."""
    r = admin_client.post(
        "/admin/ca-certs",
        data={"name": "Paste CA", "pem_content": FAKE_PEM},
        files={"pem_file": ("", b"", "application/octet-stream")},
    )
    assert r.status_code in (200, 303)
    certs = db.list_ca_certs()
    assert len(certs) == 1
    assert certs[0]["pem_content"] == FAKE_PEM


def test_non_admin_cannot_delete(runner_client, db):
    db.insert_ca_cert({
        "id": "cert-prot",
        "name": "Protected",
        "pem_content": FAKE_PEM,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "added_by": None,
    })
    r = runner_client.post("/admin/ca-certs/cert-prot/delete", follow_redirects=False)
    assert r.status_code == 403
    assert db.get_ca_cert("cert-prot") is not None  # cert was NOT deleted


# ── Hardening regression tests ──────────────────────────────────────────


def test_add_cert_malformed_pem_rejected(admin_client, db):
    """Cert content that has BEGIN/END markers but invalid base64 between
    them must fail the real x509 parser, not just the substring check."""
    bogus = (
        "-----BEGIN CERTIFICATE-----\n"
        "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAfake\n"
        "-----END CERTIFICATE-----"
    )
    r = admin_client.post("/admin/ca-certs",
                          data={"name": "Bogus", "pem_content": bogus},
                          follow_redirects=False)
    assert r.status_code == 422
    assert "Invalid certificate" in r.text or "could not" in r.text.lower()
    assert db.list_ca_certs() == []


def test_add_cert_binary_file_rejected_with_helpful_message(admin_client, db):
    """Uploading a DER/binary file gives an actionable error instead of
    silently U+FFFD-mangling the bytes and then failing with a generic
    'must contain BEGIN CERTIFICATE' message."""
    binary_payload = bytes(range(256)) * 4  # 1 KB of random binary
    r = admin_client.post(
        "/admin/ca-certs",
        data={"name": "DER File", "pem_content": ""},
        files={"pem_file": ("cert.der", binary_payload, "application/octet-stream")},
        follow_redirects=False,
    )
    assert r.status_code == 422
    # The error should mention DER or PEM conversion, not a generic substring miss.
    assert ("DER" in r.text) or ("convert to PEM" in r.text)
    assert db.list_ca_certs() == []


def test_delete_missing_cert_404(admin_client, db):
    """Deleting a non-existent cert_id used to silently succeed (no-op
    DELETE) and write a misleading audit-log entry. It should 404."""
    r = admin_client.post("/admin/ca-certs/does-not-exist/delete",
                          follow_redirects=False)
    assert r.status_code == 404


def test_add_cert_duplicate_name_rejected(admin_client, db):
    """The ca_certs.name column is now UNIQUE; a second insert with the
    same name must surface a friendly error from the route."""
    r1 = admin_client.post("/admin/ca-certs",
                           data={"name": "Corp Root", "pem_content": REAL_PEM},
                           follow_redirects=False)
    assert r1.status_code in (200, 303)
    assert len(db.list_ca_certs()) == 1
    r2 = admin_client.post("/admin/ca-certs",
                           data={"name": "Corp Root", "pem_content": REAL_PEM},
                           follow_redirects=False)
    assert r2.status_code == 422
    assert "already exists" in r2.text
    assert len(db.list_ca_certs()) == 1  # second insert was rejected
