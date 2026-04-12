import httpx
import smtplib
from email.message import EmailMessage
from harness.types import AlertType

def format_alert_message(alert_type: AlertType, app: str, environment: str,
                          test_name: str, error_msg) -> str:
    if alert_type == AlertType.FAIL:
        msg = f"[FAIL] {app} ({environment}) — {test_name} failed"
        if error_msg:
            msg += f"\n\nError: {error_msg}"
        return msg
    return f"[RESOLVED] {app} ({environment}) — {test_name} is now passing"

async def _send_teams(webhook_url: str, text: str) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(webhook_url, json={"text": text})

def _send_email(smtp_config: dict, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp_config["from"]
    msg["To"] = ", ".join(smtp_config["to"])
    msg.set_content(body)
    with smtplib.SMTP(smtp_config["smtp_host"], smtp_config.get("smtp_port", 587)) as s:
        s.starttls()
        s.login(smtp_config["username"], smtp_config["password"])
        s.send_message(msg)

async def dispatch_alerts(alerts: list, alerts_config: dict) -> None:
    teams_cfg = alerts_config.get("teams")
    email_cfg = alerts_config.get("email")
    for alert_type, app, environment, test_name, error_msg in alerts:
        text = format_alert_message(alert_type, app, environment, test_name, error_msg)
        if teams_cfg and teams_cfg.get("webhook_url"):
            try:
                await _send_teams(teams_cfg["webhook_url"], text)
            except Exception as e:
                print(f"[alerts] Teams webhook failed: {e}")
        if email_cfg:
            subject = f"[Harness] {app} — {test_name} {'FAILED' if alert_type == AlertType.FAIL else 'RESOLVED'}"
            try:
                _send_email(email_cfg, subject, text)
            except Exception as e:
                print(f"[alerts] Email failed: {e}")
