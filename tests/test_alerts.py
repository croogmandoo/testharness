import httpx
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


@pytest.mark.asyncio
async def test_email_dispatch_uses_to_thread_not_get_event_loop():
    """Regression test for Python 3.14 compatibility.

    asyncio.get_event_loop() was removed in 3.14 — the email path must use
    asyncio.to_thread() so smtplib (a blocking call) runs off the event loop
    without reaching for a deprecated API.
    """
    from harness.alerts import dispatch_alerts
    from harness.types import AlertType
    sent = []

    def fake_send_email(cfg, subject, body):
        sent.append({"cfg": cfg, "subject": subject, "body": body})

    alerts = [(AlertType.FAIL, "myapp", "production", "login", "Timeout")]
    cfg = {
        "email": {
            "from": "harness@example.com",
            "to": ["ops@example.com"],
            "smtp_host": "smtp.example.com",
            "username": "u",
            "password": "p",
        }
    }

    with patch("harness.alerts._send_email", side_effect=fake_send_email):
        await dispatch_alerts(alerts, cfg)

    assert len(sent) == 1, "email path should fire exactly once"
    assert sent[0]["subject"].startswith("[Harness] myapp")
    assert "FAILED" in sent[0]["subject"]
    assert "Timeout" in sent[0]["body"]


def test_alerts_module_has_no_get_event_loop_call():
    """Guard against re-introducing asyncio.get_event_loop() (Python 3.14 break).

    Uses AST so explanatory comments mentioning the name don't false-positive.
    """
    import ast
    import inspect
    from harness import alerts

    tree = ast.parse(inspect.getsource(alerts))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            # Match both `get_event_loop(...)` and `asyncio.get_event_loop(...)`
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            assert name != "get_event_loop", (
                "asyncio.get_event_loop() was removed in Python 3.14; "
                "use asyncio.to_thread() or asyncio.get_running_loop() instead."
            )


@pytest.mark.asyncio
async def test_teams_webhook_failure_does_not_crash(caplog):
    """Teams webhook network/HTTP failure is caught and logged."""
    import logging
    from harness.alerts import dispatch_alerts
    from harness.types import AlertType

    alerts = [(AlertType.FAIL, "myapp", "production", "login", "Timeout")]
    cfg = {"teams": {"webhook_url": "https://teams.example.com/fail"}}

    with patch("harness.alerts._send_teams", side_effect=httpx.ConnectError("Connection refused")):
        with caplog.at_level(logging.WARNING):
            await dispatch_alerts(alerts, cfg)

    assert "Teams webhook failed" in caplog.text


@pytest.mark.asyncio
async def test_slack_webhook_failure_does_not_crash(caplog):
    """Slack webhook timeout is caught and logged."""
    import logging
    from harness.alerts import dispatch_alerts
    from harness.types import AlertType

    alerts = [(AlertType.FAIL, "myapp", "production", "login", "Timeout")]
    cfg = {"slack": {"webhook_url": "https://hooks.slack.com/fail"}}

    with patch("harness.alerts._send_slack", side_effect=httpx.TimeoutException("Read timed out")):
        with caplog.at_level(logging.WARNING):
            await dispatch_alerts(alerts, cfg)

    assert "Slack error" in caplog.text


@pytest.mark.asyncio
async def test_discord_webhook_failure_does_not_crash(caplog):
    """Discord webhook error is caught and logged."""
    import logging
    from harness.alerts import dispatch_alerts
    from harness.types import AlertType

    alerts = [(AlertType.FAIL, "myapp", "production", "login", "Timeout")]
    cfg = {"discord": {"webhook_url": "https://discord.com/api/fail"}}

    with patch("harness.alerts._send_discord", side_effect=RuntimeError("Discord rate limited")):
        with caplog.at_level(logging.WARNING):
            await dispatch_alerts(alerts, cfg)

    assert "Discord error" in caplog.text


@pytest.mark.asyncio
async def test_email_dispatch_failure_does_not_crash(caplog):
    """Email smtplib failure is caught and logged."""
    import logging
    import smtplib
    from harness.alerts import dispatch_alerts
    from harness.types import AlertType

    alerts = [(AlertType.FAIL, "myapp", "production", "login", "Timeout")]
    cfg = {
        "email": {
            "from": "harness@example.com",
            "to": ["ops@example.com"],
            "smtp_host": "smtp.example.com",
            "username": "u",
            "password": "p",
        }
    }

    with patch("harness.alerts._send_email", side_effect=smtplib.SMTPAuthenticationError(535, b"Auth failed")):
        with caplog.at_level(logging.ERROR):
            await dispatch_alerts(alerts, cfg)

    assert "Email failed" in caplog.text


@pytest.mark.asyncio
async def test_dispatch_run_webhook_failure_does_not_crash(caplog):
    """Outbound run webhook network failure is caught and logged."""
    import logging
    from harness.alerts import dispatch_run_webhook

    with patch("httpx.AsyncClient") as mock_cls:
        mock_instance = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        mock_instance.post = AsyncMock(side_effect=httpx.ConnectTimeout("Connect timeout"))
        mock_cls.return_value = mock_instance

        with caplog.at_level(logging.ERROR):
            await dispatch_run_webhook(
                "run1", "App", "prod", "complete", "api", "2026-04-17T00:00:00Z",
                [], {"url": "https://ci.example.com/fail"}
            )

    assert "Outbound webhook error" in caplog.text


@pytest.mark.asyncio
async def test_multi_channel_partial_failure_continues_delivery():
    """Failure in one channel (e.g. Teams/Slack) does not prevent delivery to other channels."""
    from harness.alerts import dispatch_alerts
    from harness.types import AlertType

    alerts = [(AlertType.FAIL, "myapp", "production", "login", "Timeout")]
    cfg = {
        "teams": {"webhook_url": "https://teams.example.com/fail"},
        "slack": {"webhook_url": "https://hooks.slack.com/fail"},
        "discord": {"webhook_url": "https://discord.com/api/ok"},
        "email": {
            "from": "harness@example.com",
            "to": ["ops@example.com"],
            "smtp_host": "smtp.example.com",
            "username": "u",
            "password": "p",
        }
    }

    discord_sent = []
    email_sent = []

    async def mock_discord(url, text):
        discord_sent.append((url, text))

    def mock_email(cfg, subject, body):
        email_sent.append((subject, body))

    with patch("harness.alerts._send_teams", side_effect=httpx.ConnectError("down")), \
         patch("harness.alerts._send_slack", side_effect=httpx.TimeoutException("timeout")), \
         patch("harness.alerts._send_discord", side_effect=mock_discord), \
         patch("harness.alerts._send_email", side_effect=mock_email):
        await dispatch_alerts(alerts, cfg)

    assert len(discord_sent) == 1
    assert discord_sent[0][0] == "https://discord.com/api/ok"
    assert len(email_sent) == 1
    assert "FAILED" in email_sent[0][0]

