import httpx, json

BASE = "http://localhost:8000"

tests = [
    # Flow 1: "what tiers for images" → then "can I see samples" → should return photography link
    ([
        ("what tiers for images", False),
        ("can I see samples", False),
    ], "Flow 1: ask tiers then samples -> photography link"),

    # Flow 2: "can I see samples" with no prior context -> both links labeled
    ([
        ("can I see samples", False),
    ], "Flow 2: samples alone -> both links"),

    # Flow 3: "show me video ad samples" -> only video link
    ([
        ("show me video ad samples", False),
    ], "Flow 3: video samples -> only video link"),
]

for messages, label in tests:
    print(f"\n=== {label} ===")
    sid = messages[0][0].replace(" ", "_")[:20] + "_" + str(hash(label))[-6:]
    for q, _ in messages:
        resp = httpx.post(f"{BASE}/chat", json={"session_id": sid, "query": q}, timeout=30)
        data = resp.json()
        print(f"  Q: {q}")
        print(f"  A: {data['answer']}")
        print(f"  Escalated: {data['escalated']}")
        print()
