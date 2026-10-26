import fnmatch
import logging
import os
import re
import yaml
from typing import Any, Mapping

_log = logging.getLogger(__name__)


class ConfigError(Exception):
    pass


# Strings starting with `$` are env-var references only when they look like an
# identifier (optionally with a `#env` qualifier).  Anything else — `"$.token"`
# JSONPath, `"$1.50"` price, etc. — passes through untouched.
_ENV_REF = re.compile(r"^\$[A-Za-z_][A-Za-z0-9_]*(#[A-Za-z0-9_-]+)?$")

# Inline form: substring matches of `$IDENTIFIER` inside larger strings, e.g.
# `"Bearer $AUTH_TOKEN"`.  Matches identifier-only — does not span past
# whitespace, punctuation, or non-identifier chars.
_INLINE_REF = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*(?:#[A-Za-z0-9_-]+)?)")


_ENV_HINT = (
    " (for app YAML: add it as a secret, or list it in `apps_env_allowlist` "
    "in config.yaml)"
)


def _resolve_var(name: str, strict: bool, extra: dict, environ: Mapping):
    """Resolve a single $VAR or $VAR#env name. Returns None if missing
    (when strict=False); raises ConfigError when strict=True.

    Lookup order is ``extra`` then ``environ`` (``os.environ`` for
    config.yaml; the allowlisted subset from :func:`app_environ` for app
    YAML)."""
    hint = "" if environ is os.environ else _ENV_HINT
    if "#" in name:
        base_var, _ = name.split("#", 1)
        val = extra.get(name) or environ.get(name)
        if val is not None:
            return val
        val = extra.get(base_var) or environ.get(base_var)
        if val is None:
            if strict:
                raise ConfigError(
                    f"Environment variable '{base_var}' (or '{name}') is not set"
                    f"{hint}"
                )
            return None
        return val
    val = extra.get(name) or environ.get(name)
    if val is None:
        if strict:
            raise ConfigError(f"Environment variable '{name}' is not set{hint}")
        return None
    return val


_wildcard_warned = False


def app_environ(config: dict | None) -> dict:
    """Return the subset of ``os.environ`` that app YAML may reference.

    App definitions are editable by non-admin roles, so letting them resolve
    arbitrary process env (``$HARNESS_API_KEY``, ``$AWS_SECRET_ACCESS_KEY``,
    ...) would leak server secrets. Only names matching the top-level
    ``apps_env_allowlist`` (exact names or ``fnmatch`` globs such as
    ``HARNESS_APP_*``) are exposed. The default (absent/empty) is ``{}`` —
    app YAML then resolves from the secrets store only. ``["*"]`` restores
    the legacy behaviour (every env var) and logs a warning once.
    """
    global _wildcard_warned
    patterns = (config or {}).get("apps_env_allowlist") or []
    if isinstance(patterns, str):
        patterns = [patterns]
    patterns = [p for p in patterns if isinstance(p, str) and p]
    if not patterns:
        return {}
    if "*" in patterns:
        if not _wildcard_warned:
            _log.warning(
                "apps_env_allowlist contains '*': app YAML can resolve ANY "
                "process environment variable (including server secrets). "
                "Prefer the secrets store or an explicit allowlist."
            )
            _wildcard_warned = True
        return dict(os.environ)
    return {
        k: v for k, v in os.environ.items()
        if any(fnmatch.fnmatchcase(k, p) for p in patterns)
    }


def _validate_config(cfg: dict) -> None:
    if not isinstance(cfg, dict):
        raise ConfigError("config.yaml must be a mapping at the top level")
    for key, expected_type in [
        ("auth", dict), ("environments", dict), ("alerts", dict), ("browser", dict),
        ("analytics", dict),
    ]:
        if key in cfg and cfg[key] is not None and not isinstance(cfg[key], expected_type):
            raise ConfigError(
                f"config.yaml: '{key}' must be a mapping, "
                f"got {type(cfg[key]).__name__}"
            )
        if key in cfg and cfg[key] is None:
            cfg[key] = {}
    if "apps_env_allowlist" in cfg:
        allow = cfg["apps_env_allowlist"]
        if allow is None:
            cfg["apps_env_allowlist"] = []
        elif not isinstance(allow, list) or not all(isinstance(a, str) for a in allow):
            raise ConfigError(
                "config.yaml: 'apps_env_allowlist' must be a list of variable "
                "names or glob patterns"
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
    if "analytics" in cfg and isinstance(cfg["analytics"], dict):
        analytics = cfg["analytics"]
        if analytics.get("enabled"):
            target = analytics.get("target")
            if not isinstance(target, dict):
                raise ConfigError(
                    "config.yaml: 'analytics.target' must be a mapping when "
                    "analytics is enabled"
                )
            ttype = (target.get("type") or "local").lower()
            if ttype not in ("local", "azure_blob"):
                raise ConfigError(
                    f"config.yaml: 'analytics.target.type' must be 'local' or "
                    f"'azure_blob', got {target.get('type')!r}"
                )
            if ttype == "azure_blob" and not target.get("container"):
                raise ConfigError(
                    "config.yaml: 'analytics.target.container' is required "
                    "when target.type is 'azure_blob'"
                )


def resolve_env_vars(obj: Any, strict: bool = True, extra_vars: dict = None,
                     environ: Mapping | None = None) -> Any:
    """Substitute ``$VAR`` references in ``obj`` (recursively).

    Values come from ``extra_vars`` first, then ``environ``. ``environ=None``
    means ``os.environ`` (used for config.yaml). App YAML callers must pass
    ``environ=app_environ(config)`` (or ``{}``) so app definitions cannot
    read arbitrary process env.
    """
    _extra = extra_vars or {}
    _environ = os.environ if environ is None else environ
    if isinstance(obj, dict):
        return {k: resolve_env_vars(v, strict, extra_vars, _environ)
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_env_vars(v, strict, extra_vars, _environ) for v in obj]
    if not isinstance(obj, str) or "$" not in obj:
        return obj
    # `$X.token` vs `$.token`: only strings starting with `$` followed by an
    # IDENTIFIER (letters/underscore/digits, optional `#env` suffix) are treated
    # as env-var references. JSONPath expressions like `$.token` or
    # `$.user.id` start with `$.` (a non-identifier char) and so do NOT match
    # _ENV_REF / _INLINE_REF — they fall through and are returned unchanged.
    # This lets `extract:` paths (`$.token`) coexist with `$VAR` substitution.
    # Whole-string `$VAR` ref — preserve value type (could be a non-string).
    if _ENV_REF.match(obj):
        val = _resolve_var(obj[1:], strict, _extra, _environ)
        return val if val is not None else obj
    # Inline `... $VAR ...` substitution — coerce values to str.
    def _sub(match: "re.Match[str]") -> str:
        val = _resolve_var(match.group(1), strict, _extra, _environ)
        # Non-strict + missing -> leave the literal `$NAME` in place.
        return str(val) if val is not None else match.group(0)
    return _INLINE_REF.sub(_sub, obj)


def load_config(path: str = "config.yaml") -> dict:
    if not os.path.exists(path):
        raise ConfigError(f"Config file not found: {path}")
    with open(path) as f:
        raw = yaml.safe_load(f)
    if raw is None:
        return {}
    _validate_config(raw)
    return resolve_env_vars(raw)
