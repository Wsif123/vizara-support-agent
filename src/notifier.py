import os
import urllib.request
import urllib.parse
from datetime import datetime, timezone


FORMSPREE_URL = "https://formspree.io/f/xykonbob"


def send_escalation_email(session_id: str, question: str, customer_email: str) -> bool:
    from dotenv import load_dotenv
    load_dotenv()

    notify_to = os.getenv("NOTIFY_EMAIL_TO", "")

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    data = urllib.parse.urlencode({
        "subject": "🤖 Bot escalation — needs human",
        "_replyto": customer_email,
        "Customer Email": customer_email,
        "Session": session_id,
        "Message": question,
        "Timestamp": timestamp,
        "Notify To": notify_to,
    }).encode()

    try:
        req = urllib.request.Request(FORMSPREE_URL, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status == 200:
                print(f"[notifier] Escalation sent via Formspree for session {session_id}")
                return True
            print(f"[notifier] Formspree returned status {resp.status}")
            return False
    except Exception as e:
        print(f"[notifier] Failed to send via Formspree: {e}")
        return False
