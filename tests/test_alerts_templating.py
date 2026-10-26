"""Unit tests for webhook payload templating, formats (PagerDuty, Opsgenie), and incident alerts."""

import json
import pytest
from unittest.mock import AsyncMock, patch

from harness.alerts import (
    _render_webhook_payload,
    _send_custom_webhook,
    dispatch_alerts,
    dispatch_run_webhook,
)
from harness.types import AlertType


def test_render_webhook_payload_default():
    data = {"event": "run_complete", "app": "storefront", "status": "pass"}
    rendered = _render_webhook_payload(data, {})
    assert rendered == data


def test_render_webhook_payload_pagerduty_trigger():
    data = {
        "event": "run_complete",
        "app": "storefront",
        "environment": "prod",
        "status": "fail",
        "text": "Checkout test failed",
    }
    cfg = {"format": "pagerduty", "routing_key": "pd-routing-key-123"}
    rendered = _render_webhook_payload(data, cfg)

    assert isinstance(rendered, dict)
    assert rendered["routing_key"] == "pd-routing-key-123"
    assert rendered["event_action"] == "trigger"
    assert rendered["payload"]["severity"] == "critical"
    assert rendered["payload"]["summary"] == "Checkout test failed"
    assert rendered["dedup_key"] == "storefront-prod-run"


def test_render_webhook_payload_pagerduty_resolve():
    data = {
        "event": "alert",
        "alert_type": "resolved",
        "app": "storefront",
        "environment": "prod",
        "test_name": "checkout",
        "status": "pass",
    }
    cfg = {"format": "pagerduty", "routing_key": "pd-key"}
    rendered = _render_webhook_payload(data, cfg)

    assert rendered["event_action"] == "resolve"
    assert rendered["payload"]["severity"] == "info"
    assert rendered["dedup_key"] == "storefront-prod-checkout"


def test_render_webhook_payload_opsgenie():
    data = {
        "event": "run_complete",
        "app": "auth-service",
        "environment": "staging",
        "status": "fail",
    }
    cfg = {"format": "opsgenie"}
    rendered = _render_webhook_payload(data, cfg)

    assert isinstance(rendered, dict)
    assert "auth-service (staging) - fail" in rendered["message"]
    assert rendered["alias"] == "auth-service-staging-run"
    assert rendered["priority"] == "P2"


def test_render_webhook_payload_custom_jinja_template():
    data = {
        "event": "run_complete",
        "app": "payment-gateway",
        "environment": "production",
        "status": "pass",
        "run_id": "run-456",
    }
    template = """{
      "text": "Run completed for {{ app }} in {{ environment }} with status {{ status }}",
      "meta": {
        "id": "{{ run_id }}"
      }
    }"""
    cfg = {"template": template}
    rendered = _render_webhook_payload(data, cfg)

    assert isinstance(rendered, dict)
    assert rendered["text"] == "Run completed for payment-gateway in production with status pass"
    assert rendered["meta"]["id"] == "run-456"


@pytest.mark.asyncio
async def test_send_custom_webhook_headers_and_hmac():
    cfg = {
        "headers": {
            "Authorization": "Bearer token-abc",
            "X-Environment": "prod",
        },
        "secret": "supersecretkey",
    }
    data = {"event": "test", "app": "myapp"}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value.status_code = 200
        await _send_custom_webhook("https://webhook.site/test", data, cfg)

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "https://webhook.site/test"
        assert kwargs["json"] == data
        headers = kwargs["headers"]
        assert headers["Authorization"] == "Bearer token-abc"
        assert headers["X-Environment"] == "prod"
        assert "X-Harness-Signature" in headers
        assert headers["X-Harness-Signature"].startswith("sha256=")


@pytest.mark.asyncio
async def test_dispatch_alerts_incident_webhook():
    alerts = [
        (AlertType.FAIL, "payment-api", "production", "charge_card", "Timeout 504"),
    ]
    alerts_config = {
        "webhook": {
            "url": "https://incident.company.internal/hooks",
            "format": "pagerduty",
            "routing_key": "pd-routing-123",
            "headers": {"X-Sender": "testharness"},
        }
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value.status_code = 200
        await dispatch_alerts(alerts, alerts_config)

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "https://incident.company.internal/hooks"
        payload = kwargs["json"]
        assert payload["routing_key"] == "pd-routing-123"
        assert payload["event_action"] == "trigger"
        assert payload["payload"]["severity"] == "critical"


@pytest.mark.asyncio
async def test_dispatch_alerts_multiple_webhooks_list():
    alerts = [
        (AlertType.RESOLVE, "search-api", "prod", "query", None),
    ]
    alerts_config = {
        "webhook": [
            {"url": "https://hook1.example.com/alerts"},
            {"url": "https://hook2.example.com/alerts"},
        ]
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value.status_code = 200
        await dispatch_alerts(alerts, alerts_config)

        assert mock_post.call_count == 2
        calls = [c[0][0] for c in mock_post.call_args_list]
        assert "https://hook1.example.com/alerts" in calls
        assert "https://hook2.example.com/alerts" in calls


@pytest.mark.asyncio
async def test_dispatch_run_webhook_with_template():
    results = [
        {"test_name": "health", "status": "pass", "duration_ms": 42},
    ]
    cfg = {
        "url": "https://custom.webhook/events",
        "template": '{"msg": "Run {{ run_id }} on {{ app }} passed!"}',
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value.status_code = 200
        await dispatch_run_webhook(
            run_id="run-999",
            app="api-gateway",
            environment="staging",
            status="pass",
            triggered_by="cron",
            finished_at="2026-10-26T18:00:00Z",
            results=results,
            webhook_config=cfg,
        )

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "https://custom.webhook/events"
        assert kwargs["json"] == {"msg": "Run run-999 on api-gateway passed!"}
