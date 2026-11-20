import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone


_PROVIDERS = {"formspree", "resend"}


def _provider() -> str:
    provider = os.getenv("NOTIFY_PROVIDER", "formspree").strip().lower()
    return provider if provider in _PROVIDERS else "formspree"


def _provider_env(provider: str) -> str:
    return "RESEND_API_KEY" if provider == "resend" else "NOTIFY_FORMSPREE_URL"


def _configured(provider: str) -> bool:
    return bool(os.getenv(_provider_env(provider), "").strip())


def notifier_configured() -> bool:
    from dotenv import load_dotenv

    load_dotenv()
    return _configured(_provider())


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _recipient_summary(to: list[str]) -> str:
    summary = f"to {to[0]}"
    if len(to) > 1:
        summary += f"; cc {', '.join(to[1:])}"
    return summary


def _send_formspree(
    session_id: str, question: str, customer_email: str, to: list[str]
) -> bool:
    formspree_url = os.getenv("NOTIFY_FORMSPREE_URL", "").strip()
    if not formspree_url:
        print(
            f"[notifier] formspree: NOTIFY_FORMSPREE_URL is not set; "
            f"escalation not emailed (session {session_id})"
        )
        return False

    timestamp = _utc_timestamp()
    fields = {
        "subject": "Bot escalation — needs human",
        "_replyto": customer_email,
        "Customer Email": customer_email,
        "Session": session_id,
        "Message": question,
        "Timestamp": timestamp,
        "Notify To": to[0],
    }
    if len(to) > 1:
        fields["_cc"] = ",".join(to[1:])
    data = urllib.parse.urlencode(fields).encode()
    recipients = _recipient_summary(to)

    try:
        req = urllib.request.Request(formspree_url, data=data, method="POST")
        req.add_header("User-Agent", "Vizara-Support-Agent/1.0")
        req.add_header("Accept", "application/json, text/plain, */*")
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            if resp.status == 200:
                print(
                    f"[notifier] formspree: escalation sent for session "
                    f"{session_id} ({recipients})"
                )
                return True
            print(
                f"[notifier] formspree: escalation failed for session "
                f"{session_id} ({recipients}): status {resp.status}: "
                f"{body[:2000]}"
            )
            return False
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(
            f"[notifier] formspree: escalation failed for session "
            f"{session_id} ({recipients}): HTTP {e.code}: {body[:2000]}"
        )
        return False
    except Exception as e:
        print(
            f"[notifier] formspree: escalation failed for session "
            f"{session_id} ({recipients}): {e}"
        )
        return False


def _send_resend(
    session_id: str, question: str, customer_email: str, to: list[str]
) -> bool:
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    if not api_key:
        print(
            f"[notifier] resend: RESEND_API_KEY is not set; "
            f"escalation not emailed (session {session_id})"
        )
        return False

    timestamp = _utc_timestamp()
    payload = {
        "from": os.getenv("NOTIFY_FROM", "").strip() or "onboarding@resend.dev",
        "to": [to[0]],
        "reply_to": customer_email,
        "subject": "Bot escalation — needs human",
        "text": (
            f"{question}\n\n"
            f"Session ID: {session_id}\n"
            f"Timestamp: {timestamp}"
        ),
    }
    if len(to) > 1:
        payload["cc"] = to[1:]
    data = json.dumps(payload).encode("utf-8")
    recipients = _recipient_summary(to)

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            if 200 <= resp.status < 300:
                print(
                    f"[notifier] resend: escalation sent for session "
                    f"{session_id} ({recipients})"
                )
                return True
            print(
                f"[notifier] resend: escalation failed for session "
                f"{session_id} ({recipients}): status {resp.status}: "
                f"{body[:2000]}"
            )
            return False
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(
            f"[notifier] resend: escalation failed for session "
            f"{session_id} ({recipients}): HTTP {e.code}: {body[:2000]}"
        )
        return False
    except Exception as e:
        print(
            f"[notifier] resend: escalation failed for session "
            f"{session_id} ({recipients}): {e}"
        )
        return False


def send_escalation_email(
    session_id: str, question: str, customer_email: str, to: list[str]
) -> bool:
    from dotenv import load_dotenv

    load_dotenv()

    provider = _provider()
    recipients = [str(addr).strip() for addr in (to or []) if str(addr).strip()]
    if not recipients:
        print(
            f"[notifier] {provider}: no escalation recipient; "
            f"escalation not emailed (session {session_id})"
        )
        return False

    if not _configured(provider):
        env_name = _provider_env(provider)
        print(
            f"[notifier] {provider}: not configured — {env_name} is not set; "
            f"escalation not emailed (session {session_id})"
        )
        return False

    if provider == "resend":
        return _send_resend(session_id, question, customer_email, recipients)
    return _send_formspree(session_id, question, customer_email, recipients)
