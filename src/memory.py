from collections import OrderedDict

_MAX_HISTORY = 10
_MAX_SESSIONS = 5000

_sessions: "OrderedDict[str, list[dict]]" = OrderedDict()
_emails: dict[str, str] = {}
_pending: dict[str, str] = {}


def _touch(session_id: str):
    """Create/refresh a session and evict the oldest one when over the cap.

    The old dict grew forever: every browser that loaded the widget created a
    session that was never removed.
    """
    if session_id in _sessions:
        _sessions.move_to_end(session_id)
        return
    while len(_sessions) >= _MAX_SESSIONS:
        evicted, _ = _sessions.popitem(last=False)
        _emails.pop(evicted, None)
        _pending.pop(evicted, None)
    _sessions[session_id] = []


def add_message(session_id: str, role: str, content: str):
    _touch(session_id)
    _sessions[session_id].append({"role": role, "content": content})
    if len(_sessions[session_id]) > _MAX_HISTORY:
        _sessions[session_id] = _sessions[session_id][-_MAX_HISTORY:]


def get_history(session_id: str) -> list[dict]:
    return _sessions.get(session_id, [])


def clear(session_id: str):
    _sessions.pop(session_id, None)
    _emails.pop(session_id, None)
    _pending.pop(session_id, None)


def get_email(session_id: str) -> str | None:
    return _emails.get(session_id)


def set_email(session_id: str, email: str):
    _touch(session_id)
    _emails[session_id] = email


def set_pending(session_id: str, question: str):
    """Remember a NEEDS_HUMAN question while we wait for the customer's email."""
    _touch(session_id)
    _pending[session_id] = question


def get_pending(session_id: str) -> str | None:
    return _pending.get(session_id)


def clear_pending(session_id: str):
    _pending.pop(session_id, None)
