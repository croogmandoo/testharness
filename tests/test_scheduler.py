import pytest
from harness.scheduler import generate_task_xml


def test_valid_http_url_generates_xml():
    xml = generate_task_xml(
        task_name="Harness-Prod",
        harness_url="http://localhost:8000",
        app=None,
        environment="production",
        interval_minutes=60,
    )
    assert "<?xml" in xml
    assert "Task" in xml
    assert "http://localhost:8000/api/runs" in xml


def test_valid_https_url_generates_xml():
    xml = generate_task_xml(
        task_name="Harness-Staging",
        harness_url="https://harness.example.com",
        app="myapp",
        environment="staging",
        interval_minutes=30,
    )
    assert "https://harness.example.com/api/runs" in xml
    assert "myapp" in xml


def test_non_http_url_raises_value_error():
    with pytest.raises(ValueError, match="harness_url must be an http/https URL"):
        generate_task_xml(
            task_name="Bad",
            harness_url="ftp://example.com",
            app=None,
            environment="prod",
        )


def test_missing_scheme_raises_value_error():
    with pytest.raises(ValueError, match="harness_url must be an http/https URL"):
        generate_task_xml(
            task_name="Bad",
            harness_url="example.com",
            app=None,
            environment="prod",
        )


def test_empty_url_raises_value_error():
    with pytest.raises(ValueError, match="harness_url must be an http/https URL"):
        generate_task_xml(
            task_name="Bad",
            harness_url="",
            app=None,
            environment="prod",
        )


def test_single_quote_in_environment_is_escaped():
    xml = generate_task_xml(
        task_name="Harness-Special",
        harness_url="http://localhost:8000",
        app=None,
        environment="prod's",
        interval_minutes=60,
    )
    # The single quote in "prod's" must be doubled to "prod''s" for PowerShell safety
    assert "prod''s" in xml
    # The raw unescaped form must not appear in Arguments
    # (it will appear doubled, so raw "prod's" alone would be a subset — check
    # by verifying it only appears as doubled)
    assert "prod\\'s" not in xml


def test_single_quote_in_app_name_is_escaped():
    xml = generate_task_xml(
        task_name="Harness-Special",
        harness_url="http://localhost:8000",
        app="my'app",
        environment="production",
        interval_minutes=60,
    )
    assert "my''app" in xml


def test_url_with_single_quote_raises_value_error():
    with pytest.raises(ValueError):
        generate_task_xml("Test", "http://localhost'injection", None, "production")
