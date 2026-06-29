from collections import defaultdict

_sessions: dict[str, list[dict]] = defaultdict(list)
_emails: dict[str, str] = {}
_MAX_HISTORY = 10


def add_message(session_id: str, role: str, content: str):
    _sessions[session_id].append({"role": role, "content": content})
    if len(_sessions[session_id]) > _MAX_HISTORY:
        _sessions[session_id] = _sessions[session_id][-_MAX_HISTORY:]


def get_history(session_id: str) -> list[dict]:
    return _sessions.get(session_id, [])


def clear(session_id: str):
    _sessions.pop(session_id, None)
    _emails.pop(session_id, None)


def get_email(session_id: str) -> str | None:
    return _emails.get(session_id)


def set_email(session_id: str, email: str):
    _emails[session_id] = email
