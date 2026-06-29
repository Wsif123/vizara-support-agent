import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import load_client_config
from src.retriever import query as retrieve

queries = [
    "How much does a video ad cost?",
    "What platforms are the visuals optimized for?",
    "How many revisions does the Premium video ad include?",
]

for q in queries:
    print(f"=== Query: {q} ===")
    docs = retrieve("vizara-studio", q)
    print(f"  Retrieved: {len(docs)} docs")
    for d in docs:
        print(f"  Score: {d['score']:.4f} | Passed: {d['passed']} | Source: {d['metadata'].get('source')}")
        print(f"  Text preview: {d['text'][:100]}...")
    print()
