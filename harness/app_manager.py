import re
import shutil
import yaml
from pathlib import Path
from harness.loader import load_apps


class AppManagerError(Exception):
    pass


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
    Path(apps_dir).mkdir(parents=True, exist_ok=True)
    path = app_file_path(app_def["app"], apps_dir=apps_dir)
    if path.exists():
        raise AppManagerError(f"App '{app_def['app']}' already exists at {path}")
    path.write_text(yaml.dump(app_def, default_flow_style=False, allow_unicode=True))
    return path


def update_app(app_name: str, app_def: dict, apps_dir: str = "apps") -> Path:
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
