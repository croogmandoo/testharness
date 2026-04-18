import os
import yaml
from typing import Any


class ConfigError(Exception):
    pass


def _validate_config(cfg: dict) -> None:
    if not isinstance(cfg, dict):
        raise ConfigError("config.yaml must be a mapping at the top level")
    for key, expected_type in [
        ("auth", dict), ("environments", dict), ("alerts", dict), ("browser", dict)
    ]:
        if key in cfg and not isinstance(cfg[key], expected_type):
            raise ConfigError(
                f"config.yaml: '{key}' must be a mapping, "
                f"got {type(cfg[key]).__name__}"
            )
    if "browser" in cfg and isinstance(cfg["browser"], dict):
        b = cfg["browser"]
        if "timeout_ms" in b and not isinstance(b["timeout_ms"], int):
            raise ConfigError(
                f"config.yaml: 'browser.timeout_ms' must be an integer, "
                f"got {type(b['timeout_ms']).__name__}"
            )
        if "headless" in b and not isinstance(b["headless"], bool):
            raise ConfigError(
                f"config.yaml: 'browser.headless' must be true or false, "
                f"got {type(b['headless']).__name__}"
            )
    if "auth" in cfg and isinstance(cfg["auth"], dict) and "ldap" in cfg["auth"]:
        ldap = cfg["auth"]["ldap"]
        if not isinstance(ldap, dict):
            raise ConfigError("config.yaml: 'auth.ldap' must be a mapping")
        if ldap.get("enabled"):
            for required in ("server", "base_dn", "user_search_filter"):
                if not ldap.get(required):
                    raise ConfigError(
                        f"config.yaml: 'auth.ldap.{required}' is required "
                        f"when LDAP is enabled"
                    )


def resolve_env_vars(obj: Any, strict: bool = True) -> Any:
    if isinstance(obj, dict):
        return {k: resolve_env_vars(v, strict) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_env_vars(v, strict) for v in obj]
    if isinstance(obj, str) and obj.startswith("$"):
        expr = obj[1:]
        if "#" in expr:
            # $VAR#env — try env-specific key first, fall back to base var
            base_var, _ = expr.split("#", 1)
            val = os.environ.get(expr)   # e.g. SONARR_PASSWORD#staging
            if val is not None:
                return val
            val = os.environ.get(base_var)
            if val is None:
                if strict:
                    raise ConfigError(
                        f"Environment variable '{base_var}' (or '{expr}') is not set"
                    )
                return obj
            return val
        else:
            val = os.environ.get(expr)
            if val is None:
                if strict:
                    raise ConfigError(f"Environment variable '{expr}' is not set")
                return obj
            return val
    return obj


def load_config(path: str = "config.yaml") -> dict:
    if not os.path.exists(path):
        raise ConfigError(f"Config file not found: {path}")
    with open(path) as f:
        raw = yaml.safe_load(f)
    if raw is None:
        return {}
    _validate_config(raw)
    return resolve_env_vars(raw)
