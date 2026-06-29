import os
import smtplib
from email.message import EmailMessage


def _get_env(name: str) -> str:
    val = os.getenv(name)
    if not val:
        from dotenv import load_dotenv
        load_dotenv()
        val = os.getenv(name)
    return val or ""


def send_escalation_email(session_id: str, question: str, customer_email: str) -> bool:
    smtp_user = _get_env("NOTIFY_SMTP_USER")
    smtp_pass = _get_env("NOTIFY_SMTP_APP_PASSWORD")
    notify_to = _get_env("NOTIFY_EMAIL_TO")

    if not smtp_user or not smtp_pass or not notify_to:
        print("[notifier] SMTP not configured — skipping email")
        return False

    msg = EmailMessage()
    msg["Subject"] = f"[Vizara Support Agent] Escalation — {session_id}"
    msg["From"] = smtp_user
    msg["To"] = notify_to

    body = (
        f"A customer query has been escalated to a human.\n\n"
        f"Session ID: {session_id}\n"
        f"Customer email: {customer_email}\n"
        f"Question: {question}\n"
    )
    msg.set_content(body)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        print(f"[notifier] Email sent to {notify_to} for session {session_id}")
        return True
    except Exception as e:
        print(f"[notifier] Failed to send email: {e}")
        return False
