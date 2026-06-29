import os
import httpx

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "llama-3.1-8b-instant"


def _get_api_key() -> str:
    key = os.getenv("SUPPORT_AGENT_GROQ_API_KEY")
    if not key:
        from dotenv import load_dotenv
        load_dotenv()
        key = os.getenv("SUPPORT_AGENT_GROQ_API_KEY")
    if not key:
        raise RuntimeError("SUPPORT_AGENT_GROQ_API_KEY not set")
    return key


def generate(system_prompt: str, user_message: str, temperature: float = 0.3) -> str:
    api_key = _get_api_key()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": temperature,
    }

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(GROQ_API_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        return f"[LLM Error] {e}"
