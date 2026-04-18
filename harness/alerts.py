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

async def _send_slack(webhook_url: str, text: str) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(webhook_url, json={"text": text})

async def _send_discord(webhook_url: str, text: str) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(webhook_url, json={"content": text})

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
    if not alerts_config:
        return
    teams_cfg = alerts_config.get("teams")
    email_cfg = alerts_config.get("email")
    for alert_type, app, environment, test_name, error_msg in alerts:
        text = format_alert_message(alert_type, app, environment, test_name, error_msg)
        if teams_cfg and teams_cfg.get("webhook_url"):
            try:
                await _send_teams(teams_cfg["webhook_url"], text)
            except Exception as e:
                print(f"[alerts] Teams webhook failed: {e}")
        slack_cfg = alerts_config.get("slack", {})
        if slack_cfg.get("webhook_url"):
            try:
                await _send_slack(slack_cfg["webhook_url"], text)
            except Exception as e:
                print(f"[alerts] Slack error: {e}")
        discord_cfg = alerts_config.get("discord", {})
        if discord_cfg.get("webhook_url"):
            try:
                await _send_discord(discord_cfg["webhook_url"], text)
            except Exception as e:
                print(f"[alerts] Discord error: {e}")
        if email_cfg:
            subject = f"[Harness] {app} — {test_name} {'FAILED' if alert_type == AlertType.FAIL else 'RESOLVED'}"
            try:
                _send_email(email_cfg, subject, text)
            except Exception as e:
                print(f"[alerts] Email failed: {e}")


async def dispatch_run_webhook(
    run_id: str, app: str, environment: str, status: str,
    triggered_by: str, finished_at: str, results: list,
    webhook_config: dict,
) -> None:
    url = webhook_config.get("url")
    if not url:
        return
    payload = {
        "event": "run_complete",
        "run_id": run_id,
        "app": app,
        "environment": environment,
        "status": status,
        "triggered_by": triggered_by,
        "finished_at": finished_at,
        "tests": [
            {
                "name": r.get("test_name", r.get("name", "")),
                "status": r.get("status", ""),
                "duration_ms": r.get("duration_ms"),
            }
            for r in results
        ],
    }
    headers = {}
    secret = webhook_config.get("secret")
    if secret:
        import hmac, hashlib, json as _json
        body = _json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        headers["X-Harness-Signature"] = f"sha256={sig}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json=payload, headers=headers)
    except Exception as e:
        print(f"[alerts] Outbound webhook error: {e}")
