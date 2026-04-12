import pytest
import yaml
from harness.loader import load_apps, resolve_base_url, slugify_test_name
from harness.config import ConfigError

@pytest.fixture
def apps_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("EXAMPLE_USER", "admin")
    monkeypatch.setenv("EXAMPLE_PASS", "secret")
    (tmp_path / "portal.yaml").write_text(yaml.dump({
        "app": "Customer Portal",
        "url": "https://portal.example.com",
        "environments": {
            "staging": "https://staging.portal.example.com",
            "production": "https://portal.example.com",
        },
        "tests": [
            {"name": "Page is reachable", "type": "availability", "expect_status": 200},
            {"name": "Login works", "type": "browser", "steps": [
                {"navigate": "/login"},
                {"fill": {"field": "#user", "value": "$EXAMPLE_USER"}},
                {"click": "button[type=submit]"},
                {"assert_url_contains": "/dashboard"},
            ]},
        ]
    }))
    return str(tmp_path)

def test_load_apps_returns_list(apps_dir):
    apps = load_apps(apps_dir)
    assert len(apps) == 1
    assert apps[0]["app"] == "Customer Portal"

def test_load_apps_resolves_env_vars(apps_dir):
    apps = load_apps(apps_dir)
    login_test = next(t for t in apps[0]["tests"] if t["name"] == "Login works")
    fill_step = login_test["steps"][1]
    assert fill_step["fill"]["value"] == "admin"

def test_resolve_base_url_uses_environment_key():
    app = {"url": "https://default.com", "environments": {"staging": "https://staging.com"}}
    assert resolve_base_url(app, "staging") == "https://staging.com"

def test_resolve_base_url_falls_back_to_url():
    app = {"url": "https://default.com", "environments": {"production": "https://prod.com"}}
    assert resolve_base_url(app, "staging") == "https://default.com"

def test_resolve_base_url_raises_when_no_url():
    app = {"environments": {}}
    with pytest.raises(ConfigError, match="No base URL"):
        resolve_base_url(app, "staging")

def test_slugify_test_name():
    assert slugify_test_name("Login works!") == "login-works"
    assert slugify_test_name("API health check") == "api-health-check"
    assert slugify_test_name("  spaces  ") == "spaces"
