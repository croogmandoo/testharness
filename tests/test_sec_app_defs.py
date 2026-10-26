"""Security tests for app-definition safety (WP3).

#4: app YAML must not resolve arbitrary process env — only the secrets store
    and names allowlisted via ``apps_env_allowlist``.
#5: app/environment URLs must be http(s); templates render others as "#".
"""
import logging

import pytest
import yaml

import harness.config as hconfig
from harness.config import ConfigError, app_environ, load_config, resolve_env_vars
from harness.loader import load_apps


# ── #4: app YAML vs process env ─────────────────────────────────────────────

@pytest.fixture
def home_app_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", "/home/victim")
    monkeypatch.setenv("HARNESS_APP_BASE", "https://allowed.example")
    d = tmp_path / "apps"
    d.mkdir()
    (d / "leak.yaml").write_text(yaml.dump({
        "app": "leak",
        "url": "https://x.example",
        "tests": [{"name": "t", "type": "api", "endpoint": "/",
                   "headers": {"X-Home": "$HOME",
                               "X-Base": "$HARNESS_APP_BASE"}}],
    }))
    return str(d)


def _headers(apps):
    return apps[0]["tests"][0]["headers"]


def test_app_yaml_does_not_resolve_process_env_without_allowlist(home_app_dir):
    apps = load_apps(home_app_dir, environ=app_environ({}))
    assert _headers(apps) == {"X-Home": "$HOME", "X-Base": "$HARNESS_APP_BASE"}


def test_load_apps_environ_none_means_no_env(home_app_dir):
    apps = load_apps(home_app_dir)
    assert _headers(apps)["X-Home"] == "$HOME"


def test_allowlist_exact_name(home_app_dir):
    env = app_environ({"apps_env_allowlist": ["HOME"]})
    apps = load_apps(home_app_dir, environ=env)
    assert _headers(apps) == {"X-Home": "/home/victim", "X-Base": "$HARNESS_APP_BASE"}


def test_allowlist_glob(home_app_dir):
    env = app_environ({"apps_env_allowlist": ["HARNESS_APP_*"]})
    apps = load_apps(home_app_dir, environ=env)
    assert _headers(apps) == {"X-Home": "$HOME", "X-Base": "https://allowed.example"}


def test_allowlist_wildcard_restores_legacy_and_warns(home_app_dir, monkeypatch, caplog):
    monkeypatch.setattr(hconfig, "_wildcard_warned", False)
    with caplog.at_level(logging.WARNING, logger="harness.config"):
        env = app_environ({"apps_env_allowlist": ["*"]})
        app_environ({"apps_env_allowlist": ["*"]})
    apps = load_apps(home_app_dir, environ=env)
    assert _headers(apps) == {"X-Home": "/home/victim",
                              "X-Base": "https://allowed.example"}
    warnings = [r for r in caplog.records if "can resolve ANY" in r.getMessage()]
    assert len(warnings) == 1


@pytest.mark.parametrize("pattern", ["?*", "**", "*?", "*"])
def test_allowlist_wildcard_only_patterns_warn(monkeypatch, caplog, pattern):
    monkeypatch.setattr(hconfig, "_wildcard_warned", False)
    monkeypatch.setenv("HOME", "/home/victim")
    with caplog.at_level(logging.WARNING, logger="harness.config"):
        env = app_environ({"apps_env_allowlist": [pattern]})
    assert env.get("HOME") == "/home/victim"
    assert any("can resolve ANY" in r.getMessage() for r in caplog.records)


def test_allowlist_specific_glob_does_not_trigger_wildcard_warning(monkeypatch, caplog):
    monkeypatch.setattr(hconfig, "_wildcard_warned", False)
    with caplog.at_level(logging.WARNING, logger="harness.config"):
        app_environ({"apps_env_allowlist": ["MYAPP_*"]})
    assert not any("can resolve ANY" in r.getMessage() for r in caplog.records)


def test_allowlist_warns_on_credential_like_names_without_values(monkeypatch, caplog):
    monkeypatch.setattr(hconfig, "_sensitive_warned", set())
    monkeypatch.setenv("HARNESS_API_KEY", "server-key-value")
    monkeypatch.setenv("HARNESS_DB_Password", "pw-value")
    monkeypatch.setenv("HARNESS_APP_BASE", "https://x.example")
    with caplog.at_level(logging.WARNING, logger="harness.config"):
        env = app_environ({"apps_env_allowlist": ["HARNESS_*"]})
        app_environ({"apps_env_allowlist": ["HARNESS_*"]})
    assert "HARNESS_API_KEY" in env
    msgs = [r.getMessage() for r in caplog.records if "credential-like" in r.getMessage()]
    assert len(msgs) == 1
    assert "HARNESS_API_KEY" in msgs[0] and "HARNESS_DB_Password" in msgs[0]
    assert "HARNESS_APP_BASE" not in msgs[0]
    assert "server-key-value" not in msgs[0] and "pw-value" not in msgs[0]


def test_app_environ_default_is_empty(monkeypatch):
    monkeypatch.setenv("HOME", "/home/victim")
    assert app_environ({}) == {}
    assert app_environ(None) == {}
    assert app_environ({"apps_env_allowlist": None}) == {}


def test_secrets_take_precedence_and_need_no_allowlist(home_app_dir):
    apps = load_apps(home_app_dir, extra_vars={"HOME": "from-secret"})
    assert _headers(apps)["X-Home"] == "from-secret"


def test_strict_error_includes_hint():
    with pytest.raises(ConfigError, match="apps_env_allowlist"):
        resolve_env_vars("$NOPE_NOT_SET", strict=True, environ={})


def test_load_apps_warns_about_unallowlisted_env_ref(home_app_dir, monkeypatch, caplog):
    import harness.loader as loader
    monkeypatch.setattr(loader, "_warned_env_refs", set())
    with caplog.at_level(logging.WARNING, logger="harness.loader"):
        load_apps(home_app_dir)
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "$HOME" in msgs and "$HARNESS_APP_BASE" in msgs
    assert "/home/victim" not in msgs


def test_config_yaml_still_reads_process_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBHOOK_FOR_CONFIG", "https://hook.example")
    p = tmp_path / "config.yaml"
    p.write_text(yaml.dump({"alerts": {"teams": {"webhook_url": "$WEBHOOK_FOR_CONFIG"}}}))
    cfg = load_config(str(p))
    assert cfg["alerts"]["teams"]["webhook_url"] == "https://hook.example"


def test_config_rejects_non_list_allowlist(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(yaml.dump({"apps_env_allowlist": "HOME"}))
    with pytest.raises(ConfigError, match="apps_env_allowlist"):
        load_config(str(p))


def test_resolve_app_def_for_dispatch_blocks_env(monkeypatch):
    from harness.worker_dispatch import resolve_app_def_for_dispatch
    monkeypatch.setenv("HARNESS_API_KEY", "server-key")
    app_def = {"app": "a", "tests": [{"name": "t", "headers": {"K": "$HARNESS_API_KEY"}}]}
    with pytest.raises(ConfigError):
        resolve_app_def_for_dispatch(app_def)
    out = resolve_app_def_for_dispatch(
        app_def, config={"apps_env_allowlist": ["HARNESS_API_KEY"]})
    assert out["tests"][0]["headers"]["K"] == "server-key"


def test_resolve_app_def_for_dispatch_keeps_capture_placeholders():
    from harness.worker_dispatch import resolve_app_def_for_dispatch
    app_def = {"app": "a", "tests": [
        {"name": "login", "extract": {"TOKEN": "$.token"}},
        {"name": "use", "headers": {"Authorization": "Bearer $TOKEN"}},
    ]}
    out = resolve_app_def_for_dispatch(app_def)
    assert out["tests"][1]["headers"]["Authorization"] == "Bearer $TOKEN"


@pytest.mark.asyncio
async def test_run_app_does_not_leak_env_per_test(tmp_path, monkeypatch):
    """Sequential per-test re-resolution must not fall back to os.environ."""
    from harness.db import Database
    from harness import runner as hrunner
    from harness.models import TestResult

    monkeypatch.setenv("HARNESS_SECRET_ENV", "leaked")
    db = Database(str(tmp_path / "h.db"))
    db.init_schema()
    seen = []

    async def fake_execute(td, run_id, app, environment, *a, **kw):
        seen.append(td)
        return TestResult(run_id=run_id, app=app, environment=environment,
                          test_name=td["name"], status="pass")

    monkeypatch.setattr(hrunner, "_execute_test", fake_execute)
    app_def = {"app": "a", "url": "https://x.example", "tests": [
        {"name": "login", "type": "api", "endpoint": "/", "extract": {"HARNESS_SECRET_ENV": "$.t"}},
        {"name": "use", "type": "api", "endpoint": "/",
         "headers": {"X": "$HARNESS_SECRET_ENV"}},
    ]}
    await hrunner.run_app(app_def, "production", "test", db, {})
    # The second test's capture never happened → must stay unresolved / error,
    # never be filled from process env.
    assert all("leaked" not in str(td) for td in seen)
    assert [td["name"] for td in seen] == ["login"]


def test_secrets_page_env_status_uses_allowlist(tmp_path, monkeypatch):
    import web.main as wm
    from web.routes.secrets import _env_vars
    d = tmp_path / "apps"
    d.mkdir()
    (d / "a.yaml").write_text("app: a\nurl: https://x.example\nheaders: {A: $HOME, B: $HARNESS_APP_X}\n")
    monkeypatch.setenv("HOME", "/home/victim")
    monkeypatch.setenv("HARNESS_APP_X", "1")
    monkeypatch.setattr(wm, "_config", {"apps_dir": str(d),
                                        "apps_env_allowlist": ["HARNESS_APP_*"]})
    assert dict(_env_vars()) == {"HOME": False, "HARNESS_APP_X": True}


# ── #5: safe_url template filter ────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    ("https://example.com/a?b=1", "https://example.com/a?b=1"),
    ("http://localhost:8080", "http://localhost:8080"),
    ("javascript:alert(1)", "#"),
    ("  JavaScript:alert(1)", "#"),
    ("java\nscript:alert(1)", "#"),
    ("data:text/html;base64,PHNjcmlwdD4=", "#"),
    ("vbscript:msgbox", "#"),
    ("$UNRESOLVED_URL", "#"),
    ("", "#"),
    (None, "#"),
])
def test_safe_url_filter(value, expected):
    from web.templating import safe_url
    assert safe_url(value) == expected


def test_safe_url_registered_on_templates():
    from web.templating import make_templates
    t = make_templates()
    out = t.env.from_string('<a href="{{ u | safe_url }}">').render(u="javascript:x")
    assert out == '<a href="#">'


def test_load_apps_warns_but_keeps_app_with_bad_url(tmp_path, caplog):
    d = tmp_path / "apps"
    d.mkdir()
    (d / "evil.yaml").write_text(yaml.dump({"app": "evil", "url": "javascript:alert(1)"}))
    with caplog.at_level(logging.WARNING, logger="harness.loader"):
        apps = load_apps(str(d))
    assert [a["app"] for a in apps] == ["evil"]
    assert any("http(s)" in r.getMessage() for r in caplog.records)


# ── Resolved URLs are re-validated before any run / dispatch ───────────────

class _FakeSecrets:
    def __init__(self, values):
        self._values = values

    def get_all_decrypted(self):
        return dict(self._values)


def _var_url_app(**extra):
    return {"app": "fileapp", "url": "$TARGET_URL",
            "tests": [{"name": "t", "type": "availability"}], **extra}


@pytest.mark.parametrize("secret_url", [
    "file:///etc/passwd", "javascript:alert(1)", "data:text/html,x",
])
@pytest.mark.asyncio
async def test_run_app_rejects_non_http_url_from_secret(tmp_path, monkeypatch, secret_url):
    from harness.db import Database
    from harness import runner as hrunner

    db = Database(str(tmp_path / "h.db"))
    db.init_schema()
    called = []

    async def fake_execute(*a, **kw):  # pragma: no cover — must not run
        called.append(a)

    monkeypatch.setattr(hrunner, "_execute_test", fake_execute)
    run_id = await hrunner.run_app(
        _var_url_app(), "production", "test", db, {},
        secrets_store=_FakeSecrets({"TARGET_URL": secret_url}),
    )
    assert called == []
    run = db.get_run(run_id)
    assert run["status"] == "error"
    results = db.get_results_for_run(run_id)
    assert len(results) == 1 and results[0]["status"] == "error"
    assert "http(s)" in results[0]["error_msg"]
    # The secret-derived value must not be echoed into results.
    assert secret_url not in results[0]["error_msg"]


@pytest.mark.asyncio
async def test_run_app_missing_var_records_error_run_with_preinserted_id(tmp_path, monkeypatch):
    from harness.db import Database
    from harness.models import Run
    from harness import runner as hrunner

    db = Database(str(tmp_path / "h.db"))
    db.init_schema()
    run = Run(app="fileapp", environment="production", triggered_by="api")
    db.insert_run(run)
    rid = await hrunner.run_app(_var_url_app(), "production", "api", db, {},
                                run_id=run.id)
    assert rid == run.id
    assert db.get_run(run.id)["status"] == "error"
    msg = db.get_results_for_run(run.id)[0]["error_msg"]
    assert "TARGET_URL" in msg and "apps_env_allowlist" in msg


@pytest.mark.asyncio
async def test_run_app_rejects_bad_environment_base_url(tmp_path, monkeypatch):
    from harness.db import Database
    from harness import runner as hrunner

    db = Database(str(tmp_path / "h.db"))
    db.init_schema()
    app_def = {"app": "envapp", "url": "https://ok.example",
               "environments": {"staging": "$STAGING_URL"},
               "tests": [{"name": "t", "type": "availability"}]}
    run_id = await hrunner.run_app(
        app_def, "staging", "test", db, {},
        secrets_store=_FakeSecrets({"STAGING_URL": "file:///etc/shadow"}),
    )
    assert db.get_run(run_id)["status"] == "error"


def test_queue_remote_run_rejects_non_http_url_from_secret(tmp_path):
    from harness.db import Database
    from harness.worker_dispatch import queue_remote_run

    db = Database(str(tmp_path / "h.db"))
    db.init_schema()
    with pytest.raises(ConfigError, match="http") as excinfo:
        queue_remote_run(
            db, _var_url_app(run_on="w1"), "production", "api",
            secrets_store=_FakeSecrets({"TARGET_URL": "file:///etc/passwd"}),
        )
    assert "/etc/passwd" not in str(excinfo.value)
    with db._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM runner_jobs").fetchone()[0] == 0


def test_resolve_app_def_for_dispatch_rejects_non_http_url():
    from harness.worker_dispatch import resolve_app_def_for_dispatch
    with pytest.raises(ConfigError):
        resolve_app_def_for_dispatch(
            _var_url_app(),
            secrets_store=_FakeSecrets({"TARGET_URL": "file:///etc/passwd"}),
        )


def test_api_trigger_remote_run_with_bad_resolved_url_returns_422(tmp_path):
    from fastapi.testclient import TestClient
    from harness.db import Database
    from web.main import create_app
    from web.auth import get_current_user

    db = Database(str(tmp_path / "h.db"))
    db.init_schema()
    apps = tmp_path / "apps"
    apps.mkdir()
    (apps / "fileapp.yaml").write_text(yaml.dump(_var_url_app(run_on="w1")))
    app = create_app(db=db, config={"environments": {"production": {}}},
                     apps_dir=str(apps))
    app.dependency_overrides[get_current_user] = lambda: {
        "id": 1, "username": "admin", "role": "admin"}
    resp = TestClient(app).post("/api/runs", json={"app": "fileapp",
                                                    "environment": "production"})
    assert resp.status_code == 422
    with db._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM runner_jobs").fetchone()[0] == 0


# ── Worker per-test resolution never reads the worker's env ────────────────

@pytest.mark.asyncio
async def test_worker_execute_job_does_not_resolve_worker_env(monkeypatch):
    from types import SimpleNamespace
    import harness.api
    import worker.worker as w

    monkeypatch.setenv("HARNESS_API_KEY", "worker-api-key")
    seen = []

    async def fake_api_test(run_id, app, env, base_url, td, ssl_ctx=None):
        seen.append(td)  # pragma: no cover — must not be reached
        raise AssertionError("test should not run")

    monkeypatch.setattr(harness.api, "run_api_test", fake_api_test)
    job = {
        "job_id": "j1", "run_id": "r1", "environment": "production",
        "app_def": {"app": "a", "url": "https://x.example", "tests": [
            {"name": "exfil", "type": "api", "endpoint": "/",
             "headers": {"X-Key": "$HARNESS_API_KEY"}},
        ]},
    }
    with pytest.raises(ConfigError):
        await w.execute_job(job, SimpleNamespace(name="w1"))
    assert all("worker-api-key" not in str(td) for td in seen)
