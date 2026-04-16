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


import ssl
import os


def test_get_ssl_context_no_certs(db):
    from harness.ssl_context import get_ssl_context
    ctx = get_ssl_context(db)
    assert isinstance(ctx, ssl.SSLContext)


def test_write_ca_bundle_creates_file(db, tmp_path):
    from harness.ssl_context import write_ca_bundle
    db.insert_ca_cert({
        "id": "cert-bundle",
        "name": "Bundle CA",
        "pem_content": "-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "added_by": None,
    })
    bundle_path = str(tmp_path / "ca-bundle.pem")
    write_ca_bundle(db, path=bundle_path)
    assert os.path.exists(bundle_path)
    content = open(bundle_path).read()
    assert "BEGIN CERTIFICATE" in content


def test_write_ca_bundle_removes_file_when_empty(db, tmp_path):
    from harness.ssl_context import write_ca_bundle
    bundle_path = str(tmp_path / "ca-bundle.pem")
    # Create a stale file
    open(bundle_path, "w").write("old content")
    write_ca_bundle(db, path=bundle_path)
    assert not os.path.exists(bundle_path)


def test_write_ca_bundle_multiple_certs(db, tmp_path):
    from harness.ssl_context import write_ca_bundle
    for i in range(3):
        db.insert_ca_cert({
            "id": f"cert-m{i}",
            "name": f"CA {i}",
            "pem_content": f"-----BEGIN CERTIFICATE-----\nfake{i}\n-----END CERTIFICATE-----",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "added_by": None,
        })
    bundle_path = str(tmp_path / "ca-bundle.pem")
    write_ca_bundle(db, path=bundle_path)
    content = open(bundle_path).read()
    assert content.count("BEGIN CERTIFICATE") == 3


def test_get_ssl_context_with_valid_cert(db):
    """get_ssl_context loads a real CA cert without raising."""
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        import datetime as dt
    except ImportError:
        pytest.skip("cryptography package not available")

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-ca")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(dt.datetime.now(dt.timezone.utc))
        .not_valid_after(dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()

    db.insert_ca_cert({
        "id": "cert-real",
        "name": "Real Test CA",
        "pem_content": pem,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "added_by": None,
    })

    from harness.ssl_context import get_ssl_context
    ctx = get_ssl_context(db)
    assert isinstance(ctx, ssl.SSLContext)
