from src.config import load_client_config
from src.retriever import query as retrieve
from src.llm_client import generate as call_llm
from src.guardrails import build_classification_prompt, parse_classification_response
from src.memory import add_message, get_history, get_email, set_email
from src import logger
from src.notifier import send_escalation_email

OFF_TOPIC_REDIRECT = "That's not something Vizara Studio offers — we specialize in cinematic video ads and product photography for products. Do you have any other questions about our video ads or product photography?"


def answer(session_id: str, user_query: str, client_id: str) -> dict:
    config = load_client_config(client_id)

    add_message(session_id, "user", user_query)

    retrieved_docs = retrieve(client_id, user_query, top_k=config.get("retrieval", {}).get("top_k", 6))

    # If no relevant context found, skip LLM and escalate directly
    relevant_docs = [d for d in retrieved_docs if d.get("passed")]
    if not relevant_docs:
        add_message(session_id, "assistant", "I don't have that information — let me connect you with a human.")
        logger.log_exchange(
            session_id=session_id,
            client_id=client_id,
            query=user_query,
            response="I don't have that information — let me connect you with a human.",
            escalated=True,
            escalation_reason="no_relevant_context",
            context=retrieved_docs,
        )
        return {"answer": "I don't have that information — let me connect you with a human.", "escalated": True, "escalation_reason": "no_relevant_context"}

    context_text = "\n\n".join(
        [f"[Source: {d['metadata'].get('source', 'unknown')}][Section: {d['metadata'].get('heading', '')}]\n{d['text']}" for d in retrieved_docs]
    )

    history = get_history(session_id)
    history_text = ""
    if history:
        history_lines = [f"{msg['role'].capitalize()}: {msg['content']}" for msg in history[:-1]]
        history_text = "\n".join(history_lines)

    system_prompt, user_prompt = build_classification_prompt(user_query, history_text, context_text, persona=config.get("persona"))
    raw_response = call_llm(system_prompt, user_prompt, temperature=0.0)

    result = parse_classification_response(raw_response)
    classification = result.get("classification", "NEEDS_HUMAN")
    answer_text = result.get("answer", raw_response)

    if classification == "ANSWERABLE":
        add_message(session_id, "assistant", answer_text)
        logger.log_exchange(
            session_id=session_id,
            client_id=client_id,
            query=user_query,
            response=answer_text,
            escalated=False,
            context=retrieved_docs,
        )
        return {"answer": answer_text, "escalated": False}

    if classification == "OFF_TOPIC":
        add_message(session_id, "assistant", answer_text)
        logger.log_exchange(
            session_id=session_id,
            client_id=client_id,
            query=user_query,
            response=answer_text,
            escalated=False,
            context=retrieved_docs,
        )
        return {"answer": answer_text, "escalated": False}

    # NEEDS_HUMAN
    email = get_email(session_id)
    if not email:
        import re
        email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.]+', user_query)
        if email_match:
            email = email_match.group()
            set_email(session_id, email)

    if email:
        escalation_msg = (
            f"Thanks — Wasif has been notified and will personally follow up at {email} "
            f"within ~1 hour. You'll hear from him soon!"
        )
        send_escalation_email(session_id, user_query, email)
    else:
        escalation_msg = (
            "I'll need to loop in Wasif on this one. Could you share your email address "
            "so he can follow up with you personally? He typically responds within ~1 hour."
        )

    add_message(session_id, "assistant", escalation_msg)
    logger.log_exchange(
        session_id=session_id,
        client_id=client_id,
        query=user_query,
        response=escalation_msg,
        escalated=True,
        escalation_reason="needs_human",
        context=retrieved_docs,
    )
    return {"answer": escalation_msg, "escalated": True, "escalation_reason": "needs_human"}
