import pytest
import yaml
from harness.app_manager import (
    AppManagerError,
    slugify_app_name,
    app_file_path,
    write_app,
    update_app,
    read_app_raw,
    archive_app,
    restore_app,
    delete_archived_app,
    list_apps,
    list_archived,
    get_known_vars,
)

SAMPLE_APP = {
    "app": "My API",
    "url": "https://example.com",
    "tests": [{"name": "health", "type": "availability", "expect_status": 200}],
}


def test_slugify_app_name():
    assert slugify_app_name("My API") == "my-api"
    assert slugify_app_name("Customer Portal!") == "customer-portal"
    assert slugify_app_name("  spaces  ") == "spaces"


def test_app_file_path(tmp_path):
    p = app_file_path("My API", apps_dir=str(tmp_path))
    assert p == tmp_path / "my-api.yaml"


def test_write_app_creates_yaml_file(tmp_path):
    path = write_app(SAMPLE_APP, apps_dir=str(tmp_path))
    assert path.exists()
    data = yaml.safe_load(path.read_text())
    assert data["app"] == "My API"
    assert data["url"] == "https://example.com"


def test_write_app_raises_on_duplicate(tmp_path):
    write_app(SAMPLE_APP, apps_dir=str(tmp_path))
    with pytest.raises(AppManagerError, match="already exists"):
        write_app(SAMPLE_APP, apps_dir=str(tmp_path))


def test_read_app_raw_returns_string_with_unresolved_vars(tmp_path):
    app_def = {**SAMPLE_APP, "url": "$BASE_URL"}
    write_app(app_def, apps_dir=str(tmp_path))
    raw = read_app_raw("My API", apps_dir=str(tmp_path))
    assert isinstance(raw, str)
    assert "$BASE_URL" in raw


def test_read_app_raw_raises_if_not_found(tmp_path):
    with pytest.raises(AppManagerError, match="not found"):
        read_app_raw("Missing App", apps_dir=str(tmp_path))


def test_update_app_overwrites_file(tmp_path):
    write_app(SAMPLE_APP, apps_dir=str(tmp_path))
    updated = {**SAMPLE_APP, "url": "https://updated.com"}
    path = update_app("My API", updated, apps_dir=str(tmp_path))
    data = yaml.safe_load(path.read_text())
    assert data["url"] == "https://updated.com"


def test_update_app_raises_if_not_found(tmp_path):
    with pytest.raises(AppManagerError, match="not found"):
        update_app("Missing App", SAMPLE_APP, apps_dir=str(tmp_path))


def test_archive_app_moves_file_to_archived(tmp_path):
    write_app(SAMPLE_APP, apps_dir=str(tmp_path))
    archived_path = archive_app("My API", apps_dir=str(tmp_path))
    assert archived_path.exists()
    assert "archived" in str(archived_path)
    assert not app_file_path("My API", apps_dir=str(tmp_path)).exists()


def test_archive_app_raises_if_not_found(tmp_path):
    with pytest.raises(AppManagerError, match="not found"):
        archive_app("Missing App", apps_dir=str(tmp_path))


def test_restore_app_moves_file_back(tmp_path):
    write_app(SAMPLE_APP, apps_dir=str(tmp_path))
    archive_app("My API", apps_dir=str(tmp_path))
    path = restore_app("My API", apps_dir=str(tmp_path))
    assert path.exists()
    assert "archived" not in str(path)


def test_restore_app_raises_if_not_in_archived(tmp_path):
    with pytest.raises(AppManagerError, match="not found"):
        restore_app("Missing App", apps_dir=str(tmp_path))


def test_delete_archived_app_removes_file(tmp_path):
    write_app(SAMPLE_APP, apps_dir=str(tmp_path))
    archive_app("My API", apps_dir=str(tmp_path))
    delete_archived_app("My API", apps_dir=str(tmp_path))
    archived_path = tmp_path / "archived" / "my-api.yaml"
    assert not archived_path.exists()


def test_delete_archived_app_raises_if_not_found(tmp_path):
    with pytest.raises(AppManagerError, match="not found"):
        delete_archived_app("Missing App", apps_dir=str(tmp_path))


def test_list_apps_returns_active_only(tmp_path):
    write_app(SAMPLE_APP, apps_dir=str(tmp_path))
    apps = list_apps(apps_dir=str(tmp_path))
    assert len(apps) == 1
    assert apps[0]["app"] == "My API"


def test_list_apps_excludes_archived(tmp_path):
    write_app(SAMPLE_APP, apps_dir=str(tmp_path))
    archive_app("My API", apps_dir=str(tmp_path))
    apps = list_apps(apps_dir=str(tmp_path))
    assert len(apps) == 0


def test_list_archived_returns_archived_apps(tmp_path):
    write_app(SAMPLE_APP, apps_dir=str(tmp_path))
    archive_app("My API", apps_dir=str(tmp_path))
    archived = list_archived(apps_dir=str(tmp_path))
    assert len(archived) == 1
    assert archived[0]["app"] == "My API"


def test_write_app_raises_if_no_app_key(tmp_path):
    with pytest.raises(AppManagerError, match="app"):
        write_app({"url": "https://example.com"}, apps_dir=str(tmp_path))


def test_archive_app_raises_if_already_archived(tmp_path):
    write_app(SAMPLE_APP, apps_dir=str(tmp_path))
    archive_app("My API", apps_dir=str(tmp_path))
    # Restore it so we can re-archive, then manually put the archived version back
    restore_app("My API", apps_dir=str(tmp_path))
    archive_app("My API", apps_dir=str(tmp_path))
    # Now write a new copy and try to archive when archived/ already has the file
    write_app(SAMPLE_APP, apps_dir=str(tmp_path))
    with pytest.raises(AppManagerError):
        archive_app("My API", apps_dir=str(tmp_path))


def test_slugify_produces_empty_string_raises(tmp_path):
    with pytest.raises(AppManagerError):
        app_file_path("!!!", apps_dir=str(tmp_path))


def test_get_known_vars_returns_var_names(tmp_path):
    """Scans YAML files and returns sorted unique $VAR names."""
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    (apps_dir / "myapp.yaml").write_text(
        "app: myapp\nurl: https://example.com\ntests:\n"
        "  - name: login\n    type: browser\n    steps:\n"
        "      - fill:\n          field: '#user'\n          value: $MY_USERNAME\n"
        "      - fill:\n          field: '#pass'\n          value: $MY_PASSWORD\n"
    )
    result = get_known_vars(apps_dir=str(apps_dir))
    assert result == ["$MY_PASSWORD", "$MY_USERNAME"]


def test_get_known_vars_deduplicates(tmp_path):
    """Same var used in multiple files appears once."""
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    (apps_dir / "a.yaml").write_text("url: $SHARED_URL\n")
    (apps_dir / "b.yaml").write_text("url: $SHARED_URL\nother: $OTHER_VAR\n")
    result = get_known_vars(apps_dir=str(apps_dir))
    assert result == ["$OTHER_VAR", "$SHARED_URL"]


def test_get_known_vars_includes_archived(tmp_path):
    """Also scans apps/archived/ so users can reuse vars from archived apps."""
    apps_dir = tmp_path / "apps"
    (apps_dir / "archived").mkdir(parents=True)
    (apps_dir / "archived" / "old.yaml").write_text("url: $ARCHIVED_SECRET\n")
    result = get_known_vars(apps_dir=str(apps_dir))
    assert "$ARCHIVED_SECRET" in result


def test_get_known_vars_empty_dir(tmp_path):
    """Returns empty list when no YAML files exist."""
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    result = get_known_vars(apps_dir=str(apps_dir))
    assert result == []


def test_get_known_vars_missing_dir(tmp_path):
    """Returns empty list when apps_dir does not exist."""
    result = get_known_vars(apps_dir=str(tmp_path / "nonexistent"))
    assert result == []
