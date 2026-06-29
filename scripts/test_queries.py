import httpx
import json

BASE = "http://localhost:8000"

queries = [
    "How much does a video ad cost?",
    "How much does the product photography Starter plan cost?",
    "How many revisions does the Premium video ad include?",
    "What platforms are the visuals optimized for?",
    "Can you build me a full e-commerce website?",
]

session_id = "test-session-1"

for i, q in enumerate(queries, 1):
    print(f"=== Test {i}: {q} ===")
    try:
        resp = httpx.post(
            f"{BASE}/chat",
            json={"session_id": session_id, "query": q},
            timeout=30,
        )
        print(resp.text)
    except Exception as e:
        print(f"Error: {e}")
    print()
