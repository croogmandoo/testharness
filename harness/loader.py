import os
import re
import glob
import yaml
from harness.config import resolve_env_vars, ConfigError

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

def load_apps(apps_dir: str = "apps") -> list:
    paths = sorted(
        glob.glob(os.path.join(apps_dir, "*.yaml")) +
        glob.glob(os.path.join(apps_dir, "*.yml"))
    )
    apps = []
    for path in paths:
        with open(path) as f:
            raw = yaml.safe_load(f)
        resolved = resolve_env_vars(raw, strict=False)
        resolved["_source"] = path
        resolved["_type"] = "yaml"
        apps.append(resolved)
    return apps
