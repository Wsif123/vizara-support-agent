import time

import httpx

from src import key_store

GROQ_API_URL = key_store.GROQ_API_URL


def _error_type(resp: httpx.Response) -> str:
    """A short, secret-free error label from Groq's response."""
    try:
        payload = resp.json()
        return str(payload.get("error", {}).get("type") or f"HTTP {resp.status_code}")[:80]
    except Exception:
        return f"HTTP {resp.status_code}"


def _call_api(
    api_key: str,
    model: str,
    system_prompt: str,
    user_message: str,
    temperature: float,
) -> tuple[str, dict]:
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
    with httpx.Client(timeout=45) as client:
        resp = client.post(GROQ_API_URL, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"], data.get("usage") or {}


def generate(system_prompt: str, user_message: str, temperature: float = 0.3) -> str:
    """Try each Groq key once, in weighted round-robin order.

    Groq is the only provider. A 429 cools that key down and moves on; a
    401/403 marks it invalid; the dashboard shows both states. No key material
    is ever written to stdout, logs, or the returned error string.
    """
    keys = key_store.select_keys()
    if not keys:
        return "[LLM Error] No Groq API key is configured. Add one in the operator console."

    model = key_store.get_groq_model()
    last_status: int | None = None
    last_reason = "unknown error"
    attempts = len(keys)

    for attempt, key in enumerate(keys, start=1):
        started = time.perf_counter()
        label = key.get("label") or "Groq key"
        print(f"[llm] attempt {attempt}/{attempts}: {label}")
        try:
            content, usage = _call_api(
                key["secret"], model, system_prompt, user_message, temperature
            )
            latency = int((time.perf_counter() - started) * 1000)
            key_store.record_result(
                key,
                "success",
                http_status=200,
                usage=usage,
                latency_ms=latency,
                model=model,
            )
            print(f"[llm] attempt {attempt}: success on {label}")
            return content

        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            last_status = status
            last_reason = _error_type(exc.response)
            latency = int((time.perf_counter() - started) * 1000)
            if status == 429:
                wait = key_store.retry_after_seconds(exc.response, 60)
                key_store.record_result(
                    key,
                    "429",
                    http_status=status,
                    latency_ms=latency,
                    error_code="rate_limit",
                    model=model,
                )
                key_store.cool_down(key, wait)
                print(f"[llm] attempt {attempt}: {label} rate-limited; cooling {wait}s")
            elif status in (401, 403):
                key_store.record_result(
                    key,
                    "invalid",
                    http_status=status,
                    latency_ms=latency,
                    error_code="auth_failed",
                    model=model,
                )
                key_store.invalidate(key)
                print(f"[llm] attempt {attempt}: {label} rejected — marked invalid")
            else:
                key_store.record_result(
                    key,
                    "error",
                    http_status=status,
                    latency_ms=latency,
                    error_code=_error_type(exc.response),
                    model=model,
                )
                key_store.cool_down(key, 15)
                print(f"[llm] attempt {attempt}: {label} returned {status}")
            continue

        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_reason = type(exc).__name__
            latency = int((time.perf_counter() - started) * 1000)
            key_store.record_result(
                key,
                "error",
                latency_ms=latency,
                error_code=last_reason,
                model=model,
            )
            key_store.cool_down(key, 15)
            print(f"[llm] attempt {attempt}: {label} network error ({last_reason})")
            continue

        except Exception as exc:
            last_reason = type(exc).__name__
            latency = int((time.perf_counter() - started) * 1000)
            key_store.record_result(
                key,
                "error",
                latency_ms=latency,
                error_code=last_reason,
                model=model,
            )
            print(f"[llm] attempt {attempt}: {label} unexpected error ({last_reason})")
            continue

    status_text = f" (last HTTP {last_status})" if last_status else ""
    return (
        f"[LLM Error] All {attempts} Groq key(s) failed{status_text}: {last_reason}. "
        "Check the API keys screen in the operator console."
    )
