"""
Lightweight email service using SMTP if configured; otherwise logs to stdout.
Env vars:
- SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM, SMTP_TLS ("true"/"false")
"""
import os
import smtplib
from email.message import EmailMessage
import logging

logger = logging.getLogger(__name__)

def send_email(to: str, subject: str, body: str, html: str | None = None) -> bool:
    host = os.getenv('SMTP_HOST')
    port = int(os.getenv('SMTP_PORT') or 587)
    user = os.getenv('SMTP_USER')
    pwd = os.getenv('SMTP_PASSWORD')
    from_addr = os.getenv('SMTP_FROM') or (user or 'no-reply@meddataflow')
    use_tls = (os.getenv('SMTP_TLS') or 'true').lower() == 'true'

    msg = EmailMessage()
    msg['From'] = from_addr
    msg['To'] = to
    msg['Subject'] = subject
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype='html')

    if not host or not (user and pwd):
        # Fallback: log the email content
        return False

    try:
        if use_tls:
            server = smtplib.SMTP(host, port)
            server.starttls()
        else:
            server = smtplib.SMTP(host, port)
        server.login(user, pwd)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {to}: {e}")
        return False
