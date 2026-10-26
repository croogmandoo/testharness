import asyncio
import json
import logging
import httpx
import smtplib
from email.message import EmailMessage
from typing import Union, Optional
from harness.types import AlertType

logger = logging.getLogger("harness.alerts")

def format_alert_message(alert_type: AlertType, app: str, environment: str,
                          test_name: str, error_msg) -> str:
    if alert_type == AlertType.FAIL:
        msg = f"[FAIL] {app} ({environment}) — {test_name} failed"
        if error_msg:
            msg += f"\n\nError: {error_msg}"
        return msg
    if alert_type == AlertType.STALE:
        # For STALE alerts, `test_name` carries the freshness budget summary
        # (e.g. "no run in 28h, threshold 24h").
        msg = f"[STALE] {app} ({environment}) — {test_name}"
        if error_msg:
            msg += f"\n\nDetails: {error_msg}"
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

def _send_email(smtp_config: dict, subject: str, body: str,
                recipients: Optional[list] = None) -> None:
    to_list = recipients if recipients else smtp_config.get("to", [])
    if isinstance(to_list, str):
        to_list = [to_list]
    if not to_list:
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp_config["from"]
    msg["To"] = ", ".join(to_list)
    msg.set_content(body)
    with smtplib.SMTP(smtp_config["smtp_host"], smtp_config.get("smtp_port", 587)) as s:
        s.starttls()
        s.login(smtp_config["username"], smtp_config["password"])
        s.send_message(msg)


def _render_webhook_payload(data: dict, webhook_config: dict) -> Union[dict, list, str]:
    """Format webhook payload using template, format preset, or standard default."""
    fmt = (webhook_config.get("format") or "").lower()
    custom_template = webhook_config.get("template")

    if custom_template:
        from jinja2 import Template
        rendered = Template(custom_template).render(**data)
        try:
            return json.loads(rendered)
        except Exception:
            return rendered

    if fmt == "pagerduty":
        routing_key = webhook_config.get("routing_key") or webhook_config.get("integration_key") or ""
        is_fail = (
            data.get("status") in ("fail", "error")
            or data.get("alert_type") in ("fail", "fail_alert")
            or (hasattr(AlertType, "FAIL") and data.get("alert_type") == AlertType.FAIL.value)
        )
        return {
            "routing_key": routing_key,
            "event_action": "trigger" if is_fail else "resolve",
            "dedup_key": f"{data.get('app')}-{data.get('environment')}-{data.get('test_name', 'run')}",
            "payload": {
                "summary": data.get("text") or f"[{data.get('status', 'ALERT')}] {data.get('app')} ({data.get('environment')})",
                "source": "testharness",
                "severity": "critical" if is_fail else "info",
                "custom_details": data,
            },
        }

    if fmt == "opsgenie":
        is_fail = (
            data.get("status") in ("fail", "error")
            or data.get("alert_type") in ("fail", "fail_alert")
            or (hasattr(AlertType, "FAIL") and data.get("alert_type") == AlertType.FAIL.value)
        )
        return {
            "message": data.get("text") or f"{data.get('app')} ({data.get('environment')}) - {data.get('status')}",
            "alias": f"{data.get('app')}-{data.get('environment')}-{data.get('test_name', 'run')}",
            "description": json.dumps(data, indent=2),
            "priority": "P2" if is_fail else "P4",
            "details": {
                "app": str(data.get("app")),
                "environment": str(data.get("environment")),
                "event": str(data.get("event")),
            },
        }

    return data


async def _send_custom_webhook(url: str, payload_data: dict, webhook_config: dict) -> None:
    payload = _render_webhook_payload(payload_data, webhook_config)
    headers = dict(webhook_config.get("headers") or {})
    secret = webhook_config.get("secret")
    if secret:
        import hmac, hashlib
        if isinstance(payload, (dict, list)):
            body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        else:
            body = str(payload).encode()
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        headers["X-Harness-Signature"] = f"sha256={sig}"

    timeout = float(webhook_config.get("timeout", 10))
    async with httpx.AsyncClient(timeout=timeout) as client:
        if isinstance(payload, (dict, list)):
            await client.post(url, json=payload, headers=headers)
        else:
            headers.setdefault("Content-Type", "application/json")
            await client.post(url, content=payload, headers=headers)


async def dispatch_alerts(alerts: list, alerts_config: dict,
                           db=None, app_defs: Optional[list] = None) -> None:
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
                logger.warning("Teams webhook failed: %s", e)
        slack_cfg = alerts_config.get("slack", {})
        if slack_cfg.get("webhook_url"):
            try:
                await _send_slack(slack_cfg["webhook_url"], text)
            except Exception as e:
                logger.warning("Slack error: %s", e)
        discord_cfg = alerts_config.get("discord", {})
        if discord_cfg.get("webhook_url"):
            try:
                await _send_discord(discord_cfg["webhook_url"], text)
            except Exception as e:
                logger.warning("Discord error: %s", e)
        if email_cfg:
            subject = f"[Harness] {app} — {test_name} {'FAILED' if alert_type == AlertType.FAIL else 'RESOLVED'}"
            recipients = None
            active_db = db
            active_apps = app_defs
            if active_db is None:
                try:
                    from web.main import get_db
                    active_db = get_db()
                except Exception:
                    pass
            if active_apps is None:
                try:
                    from web.main import get_apps
                    active_apps = get_apps()
                except Exception:
                    pass
            if active_db is not None:
                groups = None
                if active_apps:
                    matched = next((a for a in active_apps if a.get("app") == app), None)
                    if matched:
                        groups = matched.get("notification_groups")
                if groups:
                    resolved = active_db.resolve_emails_for_groups(groups)
                    if resolved:
                        recipients = resolved

            try:
                # Run blocking smtplib off the event loop via asyncio.to_thread
                # (the older executor-fetch pattern was removed in Python 3.14).
                if recipients is not None:
                    await asyncio.to_thread(_send_email, email_cfg, subject, text, recipients=recipients)
                else:
                    await asyncio.to_thread(_send_email, email_cfg, subject, text)
            except Exception as e:
                logger.error("Email failed: %s", e)

        webhook_cfgs = alerts_config.get("webhook") or []
        if isinstance(webhook_cfgs, dict):
            webhook_cfgs = [webhook_cfgs]
        for wh in webhook_cfgs:
            wh_url = wh.get("url") or wh.get("webhook_url")
            if wh_url:
                try:
                    alert_data = {
                        "event": "alert",
                        "alert_type": alert_type.value if hasattr(alert_type, "value") else str(alert_type),
                        "app": app,
                        "environment": environment,
                        "test_name": test_name,
                        "error_msg": error_msg,
                        "text": text,
                    }
                    await _send_custom_webhook(wh_url, alert_data, wh)
                except Exception as e:
                    logger.error("Incident webhook error: %s", e)


async def dispatch_run_webhook(
    run_id: str, app: str, environment: str, status: str,
    triggered_by: str, finished_at: str, results: list,
    webhook_config: dict,
) -> None:
    url = webhook_config.get("url") or webhook_config.get("webhook_url")
    if not url:
        return
    data = {
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
    try:
        await _send_custom_webhook(url, data, webhook_config)
    except Exception as e:
        logger.error("Outbound webhook error: %s", e)
