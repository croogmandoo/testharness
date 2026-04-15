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
