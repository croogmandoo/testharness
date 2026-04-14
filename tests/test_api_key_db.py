# tests/test_api_key_db.py
import pytest
from harness.db import Database

@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    return d

def test_api_keys_table_created(db):
    """init_schema creates the api_keys table without error."""
    with db._connect() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='api_keys'"
        ).fetchone()
    assert row is not None
