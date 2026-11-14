import sys
sys.path.insert(0, ".")
from pathlib import Path
from src import retriever
from src.ingest import ingest
from src.vectorstore import get_client

FAQ = Path("data/acme/raw/faq.md")
MARKER = "\n## Refund window\nAcme refunds are accepted for 33 days after delivery.\n"

print("STEP 1 — warm the cache")
before = retriever.query("acme", "what is the refund window", score_threshold=0.3)
print("  chunks:", len(before))

print("STEP 2 — append marker content")
text = FAQ.read_text(encoding="utf-8")
if "33 days" not in text:
    FAQ.write_text(text + MARKER, encoding="utf-8")
print("  appended")

print("STEP 3 — reindex in the same process")
ingest("acme")

print("STEP 4 — query again, no restart")
after = retriever.query("acme", "what is the refund window", score_threshold=0.3)
hit = [d["text"] for d in after if "33 days" in d["text"]]
print("  chunks:", len(after))
print("  RESULT:", "PASS - new content visible" if hit else "FAIL - stale cache")

print("STEP 5 — delete the collection, query again")
get_client().delete_collection("client_acme")
res = retriever.query("acme", "what is the refund window")
print("  chunks:", len(res))
print("  RESULT:", "PASS - returned [] instead of raising" if res == [] else "FAIL - did not return empty")
