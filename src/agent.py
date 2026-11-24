import os
import re

from dotenv import load_dotenv

from src import registry
from src.retriever import query as retrieve
from src.llm_client import LLMUnavailable, generate as call_llm
from src.guardrails import build_classification_prompt, parse_classification_response
from src.memory import (
    add_message,
    get_history,
    get_email,
    set_email,
    set_pending,
    get_pending,
    clear_pending,
)
from src import logger
from src.notifier import send_escalation_email

load_dotenv()

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def _contact_name(config: dict) -> str:
    return (
        config.get("escalation", {}).get("contact_name")
        or config.get("display_name")
        or "the team"
    )


def _escalation_recipients(config: dict) -> list[str]:
    operator = (os.getenv("NOTIFY_EMAIL_TO") or "").strip()
    client_addr = (config.get("escalation", {}).get("email") or "").strip()
    if config.get("escalation", {}).get("direct_to_client") and client_addr:
        return [a for a in (client_addr, operator) if a]
    return [a for a in (operator,) if a]


def _sla(config: dict) -> str:
    return (config.get("escalation", {}).get("sla_text") or "shortly").strip()


def _deliver_escalation(
    session_id: str, question: str, customer_email: str, config: dict
) -> bool:
    recipients = _escalation_recipients(config)
    if not recipients:
        print(f"[agent] no escalation recipient for session {session_id}")
        return False

    primary, *cc = recipients
    detail = f"to {primary}"
    if cc:
        detail += f"; cc {', '.join(cc)}"
    print(f"[agent] escalation recipients for session {session_id}: {detail}")
    return send_escalation_email(session_id, question, customer_email, recipients)


def _log(session_id, client_id, query, response, escalated, reason, context):
    logger.log_exchange(
        session_id=session_id,
        client_id=client_id,
        query=query,
        response=response,
        escalated=escalated,
        escalation_reason=reason,
        context=context,
    )


def answer(session_id: str, user_query: str, client_id: str) -> dict:
    item = registry.get_client(client_id)
    if not item:
        return {
            "answer": "This assistant is not available right now.",
            "escalated": True,
            "escalation_reason": "unknown_client",
        }
    config = item["config"]
    contact = _contact_name(config)
    sla = _sla(config)

    add_message(session_id, "user", user_query)

    # Capture an email whenever it appears — not only in the message that the
    # model happens to classify as NEEDS_HUMAN. Previously a customer could
    # reply with just their address, get an OFF_TOPIC/ANSWERABLE
    # classification, and the escalation would never fire.
    email = get_email(session_id)
    if not email:
        match = _EMAIL_RE.search(user_query)
        if match:
            email = match.group().rstrip(".,;:!?)]}'\"")
            set_email(session_id, email)

    retrieved_docs = retrieve(
        client_id,
        user_query,
        top_k=config.get("retrieval", {}).get("top_k", 6),
        score_threshold=config.get("retrieval", {}).get("score_threshold", 0.5),
    )

    # Only chunks that clear the threshold belong in the prompt. The old code
    # used every retrieved chunk here, so score_threshold could never filter
    # anything out.
    relevant_docs = [d for d in retrieved_docs if d.get("passed")]
    if not relevant_docs:
        # The old reply promised a human but never captured contact details or
        # sent anything. Treat it like NEEDS_HUMAN so the escalation actually
        # reaches the operator.
        escalation_reason = "no_relevant_context"
        awaiting_email = False
        if email:
            escalation_msg = (
                f"Thanks — {contact} has been notified and will personally follow up at {email} "
                f"{sla}. You'll hear from {contact} soon!"
            )
            delivered = _deliver_escalation(session_id, user_query, email, config)
            if not delivered:
                escalation_reason = "needs_human_undelivered"
                print(
                    f"[agent] escalation email not delivered for session {session_id}; "
                    "marking needs_human_undelivered"
                )
            clear_pending(session_id)
        else:
            set_pending(session_id, user_query)
            awaiting_email = True
            escalation_msg = (
                f"I don't have that information. Let me connect you with {contact} — "
                f"could you share your email address so {contact} can follow up with you personally?"
            )

        add_message(session_id, "assistant", escalation_msg)
        _log(session_id, client_id, user_query, escalation_msg, True, escalation_reason, retrieved_docs)
        return {
            "answer": escalation_msg,
            "escalated": True,
            "escalation_reason": escalation_reason,
            "awaiting_email": awaiting_email,
        }

    context_text = "\n\n".join(
        [f"[Source: {d['metadata'].get('source', 'unknown')}]\n{d['text']}" for d in relevant_docs]
    )

    history = get_history(session_id)
    history_text = ""
    if history:
        history_lines = [f"{msg['role'].capitalize()}: {msg['content']}" for msg in history[:-1]]
        history_text = "\n".join(history_lines)

    system_prompt, user_prompt = build_classification_prompt(
        user_query,
        history_text,
        context_text,
        persona=config.get("persona"),
        prompt_rules=config.get("prompt_rules"),
    )
    llm_error: str | None = None
    try:
        raw_response = call_llm(system_prompt, user_prompt, temperature=0.0)
    except LLMUnavailable as exc:
        llm_error = str(exc)
        print(f"[agent] LLM unavailable: {llm_error}")
        classification = "NEEDS_HUMAN"
        answer_text = ""
    else:
        result = parse_classification_response(raw_response)
        classification = result.get("classification", "NEEDS_HUMAN")
        answer_text = result.get("answer", raw_response)

    pending_question = get_pending(session_id)
    escalation_reason = "llm_unavailable" if llm_error else "needs_human"
    awaiting_email = False

    # If we were waiting for an email and the customer supplied one, escalate
    # now even if this follow-up message itself looks answerable/off-topic.
    if email and pending_question:
        escalation_msg = (
            f"Thanks — {contact} has been notified and will personally follow up at {email} "
            f"{sla}. You'll hear from {contact} soon!"
        )
        delivered = _deliver_escalation(session_id, pending_question, email, config)
        if not delivered:
            escalation_reason = "needs_human_undelivered"
            print(
                f"[agent] escalation email not delivered for session {session_id}; "
                "marking needs_human_undelivered"
            )
        clear_pending(session_id)
        add_message(session_id, "assistant", escalation_msg)
        _log(session_id, client_id, user_query, escalation_msg, True, escalation_reason, relevant_docs)
        return {"answer": escalation_msg, "escalated": True, "escalation_reason": escalation_reason}

    if classification == "ANSWERABLE":
        add_message(session_id, "assistant", answer_text)
        _log(session_id, client_id, user_query, answer_text, False, None, relevant_docs)
        return {"answer": answer_text, "escalated": False}

    if classification == "OFF_TOPIC":
        add_message(session_id, "assistant", answer_text)
        _log(session_id, client_id, user_query, answer_text, False, None, relevant_docs)
        return {"answer": answer_text, "escalated": False}

    # NEEDS_HUMAN
    if email:
        escalation_msg = (
            f"Thanks — {contact} has been notified and will personally follow up at {email} "
            f"{sla}. You'll hear from {contact} soon!"
        )
        delivered = _deliver_escalation(session_id, user_query, email, config)
        if not delivered:
            escalation_reason = "needs_human_undelivered"
            print(
                f"[agent] escalation email not delivered for session {session_id}; "
                "marking needs_human_undelivered"
            )
        clear_pending(session_id)
    else:
        set_pending(session_id, user_query)
        awaiting_email = True
        if llm_error:
            escalation_msg = (
                "Our assistant is having a moment — let me get a human on this. "
                "What's the best email for you?"
            )
        else:
            escalation_msg = (
                f"I'll need to loop in {contact} on this one. Could you share your email address "
                f"so {contact} can follow up with you personally? {contact} typically responds {sla}."
            )

    add_message(session_id, "assistant", escalation_msg)
    _log(session_id, client_id, user_query, escalation_msg, True, escalation_reason, relevant_docs)
    return {
        "answer": escalation_msg,
        "escalated": True,
        "escalation_reason": escalation_reason,
        "awaiting_email": awaiting_email,
    }
