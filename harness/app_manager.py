import re
import shutil
import yaml
from pathlib import Path
from urllib.parse import urlsplit
from harness.config import _ENV_REF
from harness.loader import load_apps


class AppManagerError(Exception):
    pass


class AppValidationError(AppManagerError):
    """The app definition is well-formed YAML but has an unsafe/invalid value."""
    pass


# Whitespace and C0/C1 control characters. Browsers strip these when parsing
# an href (e.g. "java\tscript:" or " javascript:"), so strip them before
# checking the scheme.
_URL_STRIP = re.compile(r"[\x00-\x20\x7f-\x9f]")


def is_http_url(value) -> bool:
    """True when ``value`` is a string whose scheme is http/https and which
    has a non-empty host, after stripping whitespace/control characters."""
    if not isinstance(value, str):
        return False
    try:
        parts = urlsplit(_URL_STRIP.sub("", value))
    except ValueError:
        return False
    return parts.scheme.lower() in ("http", "https") and bool(parts.netloc)


def _check_url(field: str, value) -> None:
    # A whole-value `$VAR` reference is resolved later (secrets store /
    # allowlisted env); the rendered link goes through the `safe_url` filter.
    if isinstance(value, str) and _ENV_REF.match(value.strip()):
        return
    if not is_http_url(value):
        raise AppValidationError(
            f"{field} must be an http(s) URL with a host, got {value!r}"
        )


def validate_app_urls(app_def: dict) -> None:
    """Validate ``url`` and every ``environments.*`` value as http(s) URLs.

    Raises :class:`AppValidationError` on the first invalid value.
    """
    if not isinstance(app_def, dict):
        raise AppValidationError("app definition must be a mapping")
    if "url" in app_def:
        _check_url("url", app_def["url"])
    envs = app_def.get("environments")
    if envs is None:
        return
    if not isinstance(envs, dict):
        raise AppValidationError("environments must be a mapping of name -> URL")
    for name, value in envs.items():
        _check_url(f"environments.{name}", value)


def slugify_app_name(name: str) -> str:
    name = name.strip().lower()
    name = re.sub(r"\s+", "-", name)
    name = re.sub(r"[^a-z0-9-]", "", name)
    return name


def app_file_path(name: str, apps_dir: str = "apps") -> Path:
    slug = slugify_app_name(name)
    if not slug:
        raise AppManagerError(f"App name '{name}' produces an empty slug")
    return Path(apps_dir) / f"{slug}.yaml"


def write_app(app_def: dict, apps_dir: str = "apps") -> Path:
    if "app" not in app_def:
        raise AppManagerError("app_def must have an 'app' key")
    validate_app_urls(app_def)
    Path(apps_dir).mkdir(parents=True, exist_ok=True)
    path = app_file_path(app_def["app"], apps_dir=apps_dir)
    if path.exists():
        raise AppManagerError(f"App '{app_def['app']}' already exists at {path}")
    path.write_text(yaml.dump(app_def, default_flow_style=False, allow_unicode=True))
    return path


def update_app(app_name: str, app_def: dict, apps_dir: str = "apps") -> Path:
    validate_app_urls(app_def)
    path = app_file_path(app_name, apps_dir=apps_dir)
    if not path.exists():
        raise AppManagerError(f"App '{app_name}' not found at {path}")
    path.write_text(yaml.dump(app_def, default_flow_style=False, allow_unicode=True))
    return path


def read_app_raw(app_name: str, apps_dir: str = "apps") -> str:
    path = app_file_path(app_name, apps_dir=apps_dir)
    if not path.exists():
        raise AppManagerError(f"App '{app_name}' not found at {path}")
    return path.read_text()


def archive_app(app_name: str, apps_dir: str = "apps") -> Path:
    src = app_file_path(app_name, apps_dir=apps_dir)
    if not src.exists():
        raise AppManagerError(f"App '{app_name}' not found at {src}")
    archived_dir = Path(apps_dir) / "archived"
    archived_dir.mkdir(parents=True, exist_ok=True)
    dest = archived_dir / src.name
    if dest.exists():
        raise AppManagerError(f"Archived file already exists at {dest}")
    shutil.move(str(src), str(dest))
    return dest


def restore_app(app_name: str, apps_dir: str = "apps") -> Path:
    archived_dir = Path(apps_dir) / "archived"
    src = archived_dir / f"{slugify_app_name(app_name)}.yaml"
    if not src.exists():
        raise AppManagerError(f"App '{app_name}' not found in archived at {src}")
    dest = Path(apps_dir) / src.name
    if dest.exists():
        raise AppManagerError(f"App file already exists at {dest}")
    shutil.move(str(src), str(dest))
    return dest


def delete_archived_app(app_name: str, apps_dir: str = "apps") -> None:
    archived_dir = Path(apps_dir) / "archived"
    path = archived_dir / f"{slugify_app_name(app_name)}.yaml"
    if not path.exists():
        raise AppManagerError(f"App '{app_name}' not found in archived at {path}")
    path.unlink()


def list_apps(apps_dir: str = "apps") -> list:
    if not Path(apps_dir).is_dir():
        return []
    return load_apps(apps_dir)


def list_archived(apps_dir: str = "apps") -> list:
    archived_dir = str(Path(apps_dir) / "archived")
    if not Path(archived_dir).is_dir():
        return []
    return load_apps(archived_dir)


def get_known_vars(apps_dir: str = "apps") -> list:
    """Scan all YAML files (including archived/) for $VAR references.

    Returns a sorted list of unique variable names like ['$MY_PASSWORD', '$MY_TOKEN'].
    Never reads actual env var values — only discovers names used in YAML files.
    """
    apps_path = Path(apps_dir)
    if not apps_path.is_dir():
        return []
    pattern = re.compile(r'\$([A-Z_][A-Z0-9_]*)')
    found = set()
    for ext in ("*.yaml", "*.yml"):
        for yaml_file in apps_path.rglob(ext):
            content = yaml_file.read_text(encoding="utf-8")
            for match in pattern.finditer(content):
                found.add(f"${match.group(1)}")
    return sorted(found)
