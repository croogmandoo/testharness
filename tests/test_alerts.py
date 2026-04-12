import pytest
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
