import os
import yaml
from typing import Any

class ConfigError(Exception):
    pass

def resolve_env_vars(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: resolve_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_env_vars(v) for v in obj]
    if isinstance(obj, str) and obj.startswith("$"):
        var = obj[1:]
        val = os.environ.get(var)
        if val is None:
            raise ConfigError(f"Environment variable '{var}' is not set")
        return val
    return obj

def load_config(path: str = "config.yaml") -> dict:
    if not os.path.exists(path):
        raise ConfigError(f"Config file not found: {path}")
    with open(path) as f:
        raw = yaml.safe_load(f)
    return resolve_env_vars(raw)
