"""
Lightweight email service using SMTP if configured; otherwise logs to stdout.
Sources configuration from environment first, then platform_config.json set by super admins.
Env vars:
- SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM, SMTP_TLS ("true"/"false")
"""
import os
import smtplib
from email.message import EmailMessage
import logging
import json
from pathlib import Path

logger = logging.getLogger(__name__)

def _load_platform_email_config() -> dict:
    try:
        cfg_path = Path(__file__).resolve().parent.parent / "config" / "platform_config.json"
        if cfg_path.exists():
            data = json.loads(cfg_path.read_text())
            return data.get("email") or {}
    except Exception:
        return {}
    return {}

def _resolve_email_settings() -> dict:
    # Load platform config then override with any env vars provided
    cfg = _load_platform_email_config()
    env_cfg = {
        "smtp_host": os.getenv("SMTP_HOST"),
        "smtp_port": os.getenv("SMTP_PORT"),
        "smtp_user": os.getenv("SMTP_USER"),
        "smtp_password": os.getenv("SMTP_PASSWORD"),
        "smtp_from": os.getenv("SMTP_FROM"),
        "smtp_tls": os.getenv("SMTP_TLS"),
    }
    for key, val in env_cfg.items():
        if val not in (None, ""):
            cfg[key] = val
    return cfg

def send_email(to: str, subject: str, body: str, html: str | None = None, attachments: list[dict] | None = None) -> bool:
    cfg = _resolve_email_settings()
    host = cfg.get('smtp_host')
    port_val = cfg.get('smtp_port')
    try:
        port = int(port_val) if port_val is not None else 587
    except Exception:
        port = 587
    user = cfg.get('smtp_user')
    pwd = cfg.get('smtp_password')
    from_addr = cfg.get('smtp_from') or (user or 'no-reply@meddataflow.com')
    tls_raw = cfg.get('smtp_tls')
    use_tls = True if tls_raw is None else str(tls_raw).lower() == 'true'

    logger.info(f"Sending email to {to} via {host}:{port} as {from_addr} (tls={use_tls})")

    msg = EmailMessage()
    msg['From'] = from_addr
    msg['To'] = to
    msg['Subject'] = subject
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype='html')
    if attachments:
        for att in attachments:
            try:
                fname = att.get("filename") or "attachment"
                ctype = att.get("mime_type") or "application/octet-stream"
                data = att.get("content") or b""
                msg.add_attachment(data, maintype=ctype.split("/")[0], subtype=ctype.split("/")[1] if "/" in ctype else "", filename=fname)
            except Exception:
                logger.warning("Failed to attach file; skipping one attachment")

    if not host or not (user and pwd):
        logger.warning("SMTP not configured; skipping email send (missing host/user/pwd)")
        return False

    try:
        server = smtplib.SMTP(host, port)
        if use_tls:
            server.starttls()
        server.login(user, pwd)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {to}: {e}")
        return False
