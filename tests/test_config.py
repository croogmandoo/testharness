import os
import pytest
import yaml
from harness.config import load_config, resolve_env_vars, ConfigError

def test_resolve_env_vars_substitutes_dollar_vars(monkeypatch):
    monkeypatch.setenv("MY_SECRET", "hunter2")
    result = resolve_env_vars({"key": "$MY_SECRET", "other": "plain"})
    assert result["key"] == "hunter2"
    assert result["other"] == "plain"

def test_resolve_env_vars_raises_on_missing(monkeypatch):
    monkeypatch.delenv("MISSING_VAR", raising=False)
    with pytest.raises(ConfigError, match="MISSING_VAR"):
        resolve_env_vars({"key": "$MISSING_VAR"})

def test_resolve_env_vars_nested(monkeypatch):
    monkeypatch.setenv("WEBHOOK", "https://example.com")
    result = resolve_env_vars({"alerts": {"teams": {"webhook_url": "$WEBHOOK"}}})
    assert result["alerts"]["teams"]["webhook_url"] == "https://example.com"

def test_load_config_reads_yaml(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.dump({
        "default_environment": "production",
        "environments": {"production": {"label": "Production"}},
        "browser": {"headless": True, "timeout_ms": 30000},
    }))
    config = load_config(str(cfg_file))
    assert config["default_environment"] == "production"
    assert config["browser"]["timeout_ms"] == 30000

def test_load_config_missing_file():
    with pytest.raises(ConfigError, match="not found"):
        load_config("/nonexistent/config.yaml")


def test_invalid_auth_type_raises(tmp_path):
    (tmp_path / "config.yaml").write_text("auth: not-a-mapping\n")
    with pytest.raises(ConfigError, match="'auth' must be a mapping"):
        load_config(str(tmp_path / "config.yaml"))


def test_browser_timeout_must_be_int(tmp_path):
    (tmp_path / "config.yaml").write_text("browser:\n  timeout_ms: '30000'\n")
    with pytest.raises(ConfigError, match="browser.timeout_ms.*integer"):
        load_config(str(tmp_path / "config.yaml"))


def test_browser_headless_must_be_bool(tmp_path):
    (tmp_path / "config.yaml").write_text("browser:\n  headless: yes-please\n")
    with pytest.raises(ConfigError, match="browser.headless.*true or false"):
        load_config(str(tmp_path / "config.yaml"))


def test_ldap_enabled_requires_base_dn(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "auth:\n  ldap:\n    enabled: true\n    server: ldap://dc\n"
    )
    with pytest.raises(ConfigError, match="base_dn.*required"):
        load_config(str(tmp_path / "config.yaml"))


def test_valid_config_loads_without_error(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "auth:\n  session_hours: 8\n"
        "environments:\n  production:\n    label: Production\n"
        "browser:\n  headless: true\n  timeout_ms: 30000\n"
    )
    result = load_config(str(tmp_path / "config.yaml"))
    assert result["browser"]["timeout_ms"] == 30000


def test_empty_config_returns_empty_dict(tmp_path):
    (tmp_path / "config.yaml").write_text("")
    assert load_config(str(tmp_path / "config.yaml")) == {}
