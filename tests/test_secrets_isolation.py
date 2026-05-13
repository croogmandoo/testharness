"""Tier 2 contract: secrets reach app YAML via extra_vars, not os.environ.

The web entry point (``create_app``) must NOT mutate the global
``os.environ`` with decrypted secrets. Instead, secrets travel as a
keyword argument through ``load_apps(..., extra_vars=...)`` into
``resolve_env_vars`` for $VAR resolution.
"""
import os
import pytest

from harness.db import Database
from harness.secrets_store import SecretsStore


SECRET_NAME = "MY_TEST_SECRET"
SECRET_VALUE = "isolated-value-9d4ce"


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    return d


@pytest.fixture
def apps_dir(tmp_path):
    apps = tmp_path / "apps"
    apps.mkdir()
    # Minimal app YAML referencing $MY_TEST_SECRET in two places so we can
    # observe resolution end-to-end.
    (apps / "myapp.yaml").write_text(
        "app: myapp\n"
        "url: $MY_TEST_SECRET\n"
        "environments:\n"
        "  production: $MY_TEST_SECRET\n"
        "tests: []\n"
    )
    return apps


def test_create_app_does_not_inject_secret_into_os_environ(
    db, apps_dir, tmp_path, monkeypatch
):
    # chdir into tmp_path so create_app's default "data/secret.key" lands
    # under tmp_path — and seed the SecretsStore at the *same* path so the
    # store created inside create_app can decrypt what we wrote.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)

    # Seed an encrypted secret in the DB using the same key path create_app
    # will use ("data/secret.key" relative to cwd).
    store = SecretsStore(db, key_path=str(tmp_path / "data" / "secret.key"))
    store.set(SECRET_NAME, SECRET_VALUE)

    # Make sure os.environ is clean before create_app runs.
    monkeypatch.delenv(SECRET_NAME, raising=False)
    assert SECRET_NAME not in os.environ

    from web.main import create_app, get_apps
    create_app(db=db, config={}, apps_dir=str(apps_dir))

    # Contract: secrets must NOT have been written to os.environ.
    assert SECRET_NAME not in os.environ, (
        f"create_app() leaked {SECRET_NAME} into os.environ — secrets "
        "must travel through extra_vars only."
    )

    # Contract: the in-memory _apps snapshot must still see the resolved value.
    apps = get_apps()
    assert len(apps) == 1, f"expected exactly 1 app loaded, got {len(apps)}"
    myapp = apps[0]
    assert myapp["url"] == SECRET_VALUE, (
        f"expected $MY_TEST_SECRET to resolve to {SECRET_VALUE!r}, "
        f"got {myapp['url']!r}"
    )
    assert myapp["environments"]["production"] == SECRET_VALUE
