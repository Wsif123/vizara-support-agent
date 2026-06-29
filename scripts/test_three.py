import httpx, json, sys

BASE = "http://localhost:8000"

tests = [
    ("Do you do logo design?", "OFF_TOPIC — no email"),
    ("Can you do a custom 60-second ad for a 3-product bundle?", "NEEDS_HUMAN — escalated + email"),
    ("Do you edit personal photos?", "ANSWERABLE — from new FAQ"),
]

for i, (q, expected) in enumerate(tests, 1):
    sid = f"test-llm-{i}"
    print(f"=== Test {i}: {q} ===")
    print(f"  Expected: {expected}")
    try:
        resp = httpx.post(f"{BASE}/chat", json={"session_id": sid, "query": q}, timeout=30)
        data = resp.json()
        print(json.dumps(data, indent=2))
    except Exception as e:
        print(f"  Error: {e}")
    print()
