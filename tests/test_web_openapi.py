"""Tier-3: OpenAPI/Swagger UI accessible even pre-setup (no users)."""
import pytest
from fastapi.testclient import TestClient

from harness.db import Database
from web.main import create_app


@pytest.fixture
def empty_db(tmp_path):
    d = Database(str(tmp_path / "empty.db"))
    d.init_schema()
    return d


def _client(db, apps_dir):
    app = create_app(
        db=db,
        config={"environments": {"production": {"label": "Production"}}},
        apps_dir=str(apps_dir),
    )
    return TestClient(app)


def test_openapi_json_reachable_pre_setup(empty_db, tmp_path):
    apps_dir = tmp_path / "apps"; apps_dir.mkdir()
    with _client(empty_db, apps_dir) as c:
        r = c.get("/openapi.json")
    assert r.status_code == 200
    body = r.json()
    assert body["openapi"].startswith("3.")
    assert "/api/runs" in body["paths"]


def test_swagger_ui_reachable_pre_setup(empty_db, tmp_path):
    apps_dir = tmp_path / "apps"; apps_dir.mkdir()
    with _client(empty_db, apps_dir) as c:
        r = c.get("/docs")
    assert r.status_code == 200
    assert b"swagger" in r.content.lower() or b"Swagger" in r.content


def test_redoc_reachable_pre_setup(empty_db, tmp_path):
    apps_dir = tmp_path / "apps"; apps_dir.mkdir()
    with _client(empty_db, apps_dir) as c:
        r = c.get("/redoc")
    assert r.status_code == 200


def test_openapi_schema_completeness_and_references(empty_db, tmp_path):
    apps_dir = tmp_path / "apps"; apps_dir.mkdir()
    with _client(empty_db, apps_dir) as c:
        r = c.get("/openapi.json")
    assert r.status_code == 200
    spec = r.json()

    # Basic metadata
    assert spec["openapi"].startswith("3.")
    assert spec["info"]["title"] == "Web Testing Harness"

    paths = spec["paths"]
    # Run endpoints
    assert "/api/runs" in paths
    assert "post" in paths["/api/runs"]
    assert "/api/runs/{run_id}" in paths
    assert "/api/runs/{run_id}/export" in paths
    assert "/api/results/{app}/{environment}" in paths
    assert "/api/vars" in paths

    # App management endpoints
    assert "/api/apps" in paths
    assert "get" in paths["/api/apps"]
    assert "post" in paths["/api/apps"]
    assert "/api/apps/{app_name}" in paths
    assert "put" in paths["/api/apps/{app_name}"]
    assert "delete" in paths["/api/apps/{app_name}"]
    assert "/api/apps/{app_name}/restore" in paths
    assert "/api/apps/{app_name}/permanent" in paths

    # Remote runner endpoints
    assert "/api/runner/register" in paths
    assert "/api/runner/poll" in paths
    assert "/api/runner/jobs" in paths
    assert "/api/runner/heartbeat" in paths
    assert "/api/runner/results" in paths

    # Schemas in components
    schemas = spec.get("components", {}).get("schemas", {})
    required_schemas = [
        "RunRequest",
        "RunResponse",
        "AppDefRequest",
        "AppMutationResponse",
        "RunnerRegisterRequest",
        "RunnerRegisterResponse",
        "RunnerPollRequest",
        "RunnerPollResponse",
        "RunnerResultsRequest",
        "RunnerResultsResponse",
        "RunnerHeartbeatRequest",
        "RunnerHeartbeatResponse",
    ]
    for s in required_schemas:
        assert s in schemas, f"Schema {s} missing from components.schemas"

    # All $ref references must resolve
    def find_refs(node):
        refs = []
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "$ref":
                    refs.append(v)
                else:
                    refs.extend(find_refs(v))
        elif isinstance(node, list):
            for item in node:
                refs.extend(find_refs(item))
        return refs

    all_refs = find_refs(spec)
    assert len(all_refs) > 0
    prefix = "#/components/schemas/"
    for ref in all_refs:
        assert ref.startswith(prefix), f"Unexpected ref target: {ref}"
        schema_name = ref[len(prefix):]
        assert schema_name in schemas, f"Dangling reference: {ref}"
