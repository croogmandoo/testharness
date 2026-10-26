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
    warnings = [r for r in caplog.records if "apps_env_allowlist" in r.getMessage()]
    assert len(warnings) == 1


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
