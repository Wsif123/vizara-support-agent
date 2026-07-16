import os
import httpx
import itertools

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
CEREBRAS_API_URL = "https://api.cerebras.ai/v1/chat/completions"
GROQ_MODEL = "llama-3.1-8b-instant"
CEREBRAS_MODEL = "gemma-4-31b"


def _load_env():
    from dotenv import load_dotenv
    load_dotenv()


def _get_groq_keys() -> list[str]:
    keys_str = os.getenv("GROQ_API_KEYS")
    if not keys_str:
        _load_env()
        keys_str = os.getenv("GROQ_API_KEYS")
    if not keys_str:
        fallback = os.getenv("SUPPORT_AGENT_GROQ_API_KEY")
        if not fallback:
            _load_env()
            fallback = os.getenv("SUPPORT_AGENT_GROQ_API_KEY")
        if fallback:
            return [fallback]
        raise RuntimeError("No Groq API keys found — set GROQ_API_KEYS or SUPPORT_AGENT_GROQ_API_KEY")
    return [k.strip() for k in keys_str.split(",") if k.strip()]


def _get_cerebras_key() -> str | None:
    key = os.getenv("CEREBRAS_API_KEY")
    if not key:
        _load_env()
        key = os.getenv("CEREBRAS_API_KEY")
    return key if key else None


_keys = _get_groq_keys()
_round_robin = itertools.cycle(range(len(_keys))) if _keys else iter([])


def _next_groq_key() -> tuple[int, str]:
    idx = next(_round_robin)
    return idx, _keys[idx]


def _call_api(url: str, api_key: str, model: str, system_prompt: str, user_message: str, temperature: float) -> str:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": temperature,
    }
    with httpx.Client(timeout=30) as client:
        resp = client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


def generate(system_prompt: str, user_message: str, temperature: float = 0.3) -> str:
    attempts = 0
    last_error = None

    # Try each Groq key in round-robin order
    for _ in range(len(_keys)):
        idx, key = _next_groq_key()
        attempts += 1
        print(f"[llm] attempt {attempts}: Groq key {idx}")
        try:
            result = _call_api(GROQ_API_URL, key, GROQ_MODEL, system_prompt, user_message, temperature)
            print(f"[llm] attempt {attempts}: success on Groq key {idx}")
            return result
        except httpx.HTTPStatusError as e:
            last_error = e
            if e.response.status_code == 429:
                print(f"[llm] attempt {attempts}: Groq key {idx} returned 429 — trying next key")
                continue
            print(f"[llm] attempt {attempts}: Groq key {idx} error {e.response.status_code} — trying next key")
            continue
        except Exception as e:
            last_error = e
            print(f"[llm] attempt {attempts}: Groq key {idx} exception {e} — trying next key")
            continue

    # All Groq keys exhausted — try Cerebras fallback
    cerebras_key = _get_cerebras_key()
    if cerebras_key:
        print(f"[llm] all Groq keys exhausted — falling back to Cerebras")
        try:
            result = _call_api(CEREBRAS_API_URL, cerebras_key, CEREBRAS_MODEL, system_prompt, user_message, temperature)
            print(f"[llm] Cerebras fallback succeeded")
            return result
        except Exception as e:
            print(f"[llm] Cerebras fallback also failed: {e}")
            last_error = e

    return f"[LLM Error] All {len(_keys)} Groq keys exhausted" + (f", Cerebras fallback also failed: {last_error}" if cerebras_key else f", no Cerebras key configured: {last_error}")
