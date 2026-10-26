import os
import re
import glob
import logging
import yaml
from typing import Mapping
from harness.config import resolve_env_vars, ConfigError, _INLINE_REF

_log = logging.getLogger(__name__)

# Names we've already warned about (once per process) for the
# apps_env_allowlist migration aid.
_warned_env_refs: set = set()

def slugify_test_name(name: str) -> str:
    name = name.strip().lower()
    name = re.sub(r"\s+", "-", name)
    name = re.sub(r"[^a-z0-9-]", "", name)
    return name

def resolve_base_url(app: dict, environment: str) -> str:
    envs = app.get("environments", {})
    if environment in envs:
        return envs[environment]
    if "url" in app:
        return app["url"]
    raise ConfigError(
        f"No base URL found for app '{app.get('app', '?')}' in environment '{environment}'"
    )

def _iter_strings(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_strings(v)
    elif isinstance(obj, str):
        yield obj


def _warn_unallowlisted_env_refs(raw, path: str, extra: Mapping,
                                 environ: Mapping) -> None:
    """Migration aid: warn (once per name) about ``$VAR`` references that
    used to resolve from process env but no longer do because the name is
    neither a secret nor in ``apps_env_allowlist``."""
    for value in _iter_strings(raw):
        if "$" not in value:
            continue
        for match in _INLINE_REF.finditer(value):
            ref = match.group(1)
            for name in (ref, ref.split("#", 1)[0]):
                if name in _warned_env_refs:
                    continue
                if name in extra or name in environ or name not in os.environ:
                    continue
                _warned_env_refs.add(name)
                _log.warning(
                    "app %s references $%s, which is set in the process "
                    "environment but is no longer exposed to app YAML. Add it "
                    "as a secret, or list it in apps_env_allowlist in "
                    "config.yaml.", path, name,
                )


def load_apps(apps_dir: str = "apps", extra_vars: dict | None = None,
              environ: Mapping | None = None) -> list:
    """Load app YAML files from ``apps_dir`` and resolve ``$VAR`` references.

    Pass ``extra_vars`` (e.g. decrypted secrets from ``SecretsStore``) to make
    them available during resolution without mutating ``os.environ``.

    ``environ`` is the process-env subset app YAML may read — normally
    ``harness.config.app_environ(config)``. Unlike ``resolve_env_vars``,
    ``None`` here means ``{}`` (no process env), never ``os.environ``.
    """
    from harness.app_manager import AppValidationError, validate_app_urls

    _extra = extra_vars or {}
    _environ = {} if environ is None else environ
    paths = sorted(
        glob.glob(os.path.join(apps_dir, "*.yaml")) +
        glob.glob(os.path.join(apps_dir, "*.yml"))
    )
    apps = []
    for path in paths:
        with open(path) as f:
            raw = yaml.safe_load(f)
        _warn_unallowlisted_env_refs(raw, path, _extra, _environ)
        resolved = resolve_env_vars(raw, strict=False, extra_vars=extra_vars,
                                    environ=_environ)
        try:
            validate_app_urls(resolved, redact=True)
        except AppValidationError as exc:
            # Don't hide the app — the UI renders unsafe URLs as "#".
            _log.warning("app %s: %s", path, exc)
        resolved["_source"] = path
        resolved["_type"] = "yaml"
        apps.append(resolved)
    return apps
