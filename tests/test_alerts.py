import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from harness.alerts import dispatch_alerts, format_alert_message
from harness.runner import AlertType

def test_format_alert_message_fail():
    msg = format_alert_message(AlertType.FAIL, "Customer Portal", "production",
                               "Login works", "Timeout waiting for selector")
    assert "Customer Portal" in msg
    assert "Login works" in msg
    assert "production" in msg

def test_format_alert_message_resolve():
    msg = format_alert_message(AlertType.RESOLVE, "Customer Portal", "production",
                               "Login works", None)
    assert "Customer Portal" in msg

@pytest.mark.asyncio
async def test_dispatch_skips_missing_config():
    alerts = [(AlertType.FAIL, "myapp", "production", "login", "Timeout")]
    await dispatch_alerts(alerts, {})  # should not raise

@pytest.mark.asyncio
async def test_dispatch_sends_teams_webhook(httpx_mock):
    alerts = [(AlertType.FAIL, "myapp", "production", "login", "Timeout")]
    config = {"teams": {"webhook_url": "https://teams.example.com/webhook"}}
    httpx_mock.add_response(url="https://teams.example.com/webhook", status_code=200)
    await dispatch_alerts(alerts, config)

@pytest.mark.asyncio
async def test_slack_alert_sends_on_fail():
    from harness.alerts import dispatch_alerts
    from harness.types import AlertType
    posted = []

    async def fake_post(url, json=None, **kwargs):
        posted.append({"url": url, "json": json})
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    alerts = [(AlertType.FAIL, "Sonarr", "production", "login", "timeout")]
    cfg = {"slack": {"webhook_url": "https://hooks.slack.com/test"}}

    with patch("httpx.AsyncClient") as mock_cls:
        mock_instance = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        mock_instance.post = AsyncMock(side_effect=fake_post)
        mock_cls.return_value = mock_instance
        await dispatch_alerts(alerts, cfg)

    assert len(posted) == 1
    assert posted[0]["url"] == "https://hooks.slack.com/test"
    assert "FAIL" in posted[0]["json"]["text"] or "fail" in posted[0]["json"]["text"].lower()

@pytest.mark.asyncio
async def test_discord_alert_sends_on_fail():
    from harness.alerts import dispatch_alerts
    from harness.types import AlertType
    posted = []

    async def fake_post(url, json=None, **kwargs):
        posted.append({"url": url, "json": json})
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    alerts = [(AlertType.FAIL, "Sonarr", "production", "login", "timeout")]
    cfg = {"discord": {"webhook_url": "https://discord.com/api/webhooks/test"}}

    with patch("httpx.AsyncClient") as mock_cls:
        mock_instance = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        mock_instance.post = AsyncMock(side_effect=fake_post)
        mock_cls.return_value = mock_instance
        await dispatch_alerts(alerts, cfg)

    assert len(posted) == 1
    assert posted[0]["url"] == "https://discord.com/api/webhooks/test"
    assert "content" in posted[0]["json"]
    assert "FAIL" in posted[0]["json"]["content"] or "fail" in posted[0]["json"]["content"].lower()


@pytest.mark.asyncio
async def test_dispatch_run_webhook_posts_payload():
    from harness.alerts import dispatch_run_webhook
    posted = []

    async def fake_post(url, json=None, headers=None, **kwargs):
        posted.append({"url": url, "json": json, "headers": headers or {}})
        mock_resp = MagicMock()
        return mock_resp

    webhook_cfg = {"url": "https://ci.example.com/hook"}
    results = [{"test_name": "login", "status": "pass", "duration_ms": 3000}]
    with patch("httpx.AsyncClient") as mock_cls:
        mock_instance = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        mock_instance.post = AsyncMock(side_effect=fake_post)
        mock_cls.return_value = mock_instance
        await dispatch_run_webhook("run1", "Sonarr", "production", "complete",
                                   "api", "2026-04-17T12:00:00Z", results, webhook_cfg)

    assert len(posted) == 1
    body = posted[0]["json"]
    assert body["event"] == "run_complete"
    assert body["run_id"] == "run1"
    assert body["app"] == "Sonarr"
    assert body["tests"][0]["name"] == "login"


@pytest.mark.asyncio
async def test_dispatch_run_webhook_adds_signature_when_secret_set():
    from harness.alerts import dispatch_run_webhook
    posted = []

    async def fake_post(url, json=None, headers=None, **kwargs):
        posted.append({"headers": headers or {}})
        return MagicMock()

    webhook_cfg = {"url": "https://ci.example.com/hook", "secret": "mysecret"}
    with patch("httpx.AsyncClient") as mock_cls:
        mock_instance = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        mock_instance.post = AsyncMock(side_effect=fake_post)
        mock_cls.return_value = mock_instance
        await dispatch_run_webhook("r1", "App", "prod", "complete",
                                   "api", "2026-04-17T00:00:00Z", [], webhook_cfg)

    assert "X-Harness-Signature" in posted[0]["headers"]
    assert posted[0]["headers"]["X-Harness-Signature"].startswith("sha256=")


@pytest.mark.asyncio
async def test_dispatch_run_webhook_no_op_when_no_url():
    from harness.alerts import dispatch_run_webhook
    # Should not raise
    await dispatch_run_webhook("r1", "App", "prod", "complete",
                               "api", "2026-04-17T00:00:00Z", [], {})
