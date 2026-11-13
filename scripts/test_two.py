import httpx
import json

BASE = "http://localhost:8000"

queries = [
    "How much does a video ad cost?",
    "Can you build me a full e-commerce website?",
]

session_id = "test-crud-1"

for i, q in enumerate(queries, 1):
    print(f"=== Test {i}: {q} ===")
    try:
        resp = httpx.post(
            f"{BASE}/chat",
            json={"session_id": session_id, "query": q, "client_id": "vizara"},
            timeout=30,
        )
        if resp.status_code != 200:
            print(f"HTTP {resp.status_code}: {resp.text}")
            print()
            continue
        data = resp.json()
        print(json.dumps(data, indent=2))
    except Exception as e:
        print(f"Error: {e}")
    print()
