"""
notify.py

Hjælpefunktioner til at sende notifikationer, når monitor_sik.py finder nye
autorisationer. Brug den notifikationsform der passer dig bedst.

SLACK / DISCORD (nemmest):
1. Opret en "Incoming Webhook" i Slack (eller en webhook i en Discord-kanal).
2. Sæt WEBHOOK_URL i monitor_sik.py til din webhook-URL.
3. Færdig - send_webhook() bruges automatisk.

EMAIL:
Hvis du hellere vil have email, kald send_email() fra monitor_sik.py's
notify()-funktion i stedet for/i tillæg til send_webhook(). Kræver at du
udfylder SMTP-oplysninger nedenfor (fx dit firmas mailserver, eller Gmail
med en "app password").
"""

import smtplib
from email.mime.text import MIMEText

import requests


def send_webhook(webhook_url: str, message: str) -> None:
    """Sender en simpel tekstbesked til en Slack- eller Discord-webhook."""
    payload = {"text": message}  # Slack-format
    if "discord.com" in webhook_url:
        payload = {"content": message}  # Discord bruger "content" i stedet
    resp = requests.post(webhook_url, json=payload, timeout=10)
    resp.raise_for_status()


def send_email(
    smtp_host: str,
    smtp_port: int,
    username: str,
    password: str,
    from_addr: str,
    to_addr: str,
    subject: str,
    body: str,
) -> None:
    """Sender en email via SMTP. Eksempel på brug fra monitor_sik.py:

        from notify import send_email
        send_email(
            smtp_host="smtp.gmail.com",
            smtp_port=587,
            username="dig@gmail.com",
            password="dit-app-password",
            from_addr="dig@gmail.com",
            to_addr="dig@gmail.com",
            subject="Nye autorisationer fundet",
            body=message,
        )
    """
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(username, password)
        server.sendmail(from_addr, [to_addr], msg.as_string())
