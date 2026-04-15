# tests/test_ca_cert_db.py
import pytest
from harness.db import Database


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    return d


def test_ca_certs_table_exists(db):
    """Schema init creates ca_certs table without errors."""
    import sqlite3
    conn = sqlite3.connect(db.path)
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='ca_certs'"
    ).fetchone()
    conn.close()
    assert row is not None


from datetime import datetime, timezone


def test_insert_and_list_ca_cert(db):
    db.insert_ca_cert({
        "id": "cert-1",
        "name": "Corp Root CA",
        "pem_content": "-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "added_by": None,
    })
    certs = db.list_ca_certs()
    assert len(certs) == 1
    assert certs[0]["name"] == "Corp Root CA"
    assert certs[0]["id"] == "cert-1"


def test_get_ca_cert(db):
    db.insert_ca_cert({
        "id": "cert-2",
        "name": "Dev CA",
        "pem_content": "-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "added_by": None,
    })
    cert = db.get_ca_cert("cert-2")
    assert cert is not None
    assert cert["name"] == "Dev CA"


def test_get_ca_cert_not_found(db):
    assert db.get_ca_cert("nonexistent") is None


def test_delete_ca_cert(db):
    db.insert_ca_cert({
        "id": "cert-3",
        "name": "Old CA",
        "pem_content": "-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "added_by": None,
    })
    db.delete_ca_cert("cert-3")
    assert db.get_ca_cert("cert-3") is None
    assert db.list_ca_certs() == []


def test_list_ca_certs_ordered_desc(db):
    db.insert_ca_cert({
        "id": "cert-a",
        "name": "Older CA",
        "pem_content": "-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----",
        "created_at": "2026-01-01T00:00:00+00:00",
        "added_by": None,
    })
    db.insert_ca_cert({
        "id": "cert-b",
        "name": "Newer CA",
        "pem_content": "-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----",
        "created_at": "2026-06-01T00:00:00+00:00",
        "added_by": None,
    })
    certs = db.list_ca_certs()
    assert certs[0]["id"] == "cert-b"
    assert certs[1]["id"] == "cert-a"
