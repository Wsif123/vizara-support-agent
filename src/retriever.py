from pathlib import Path

import chromadb
from chromadb.config import Settings

CHROMA_DIR = Path(__file__).parent.parent / "chroma_db"

_collections: dict[str, object] = {}


def _get_collection(client_id: str):
    if client_id not in _collections:
        chroma_client = chromadb.PersistentClient(
            path=str(CHROMA_DIR),
            settings=Settings(anonymized_telemetry=False),
        )
        collection_name = f"client_{client_id}"
        try:
            _collections[client_id] = chroma_client.get_collection(collection_name)
        except Exception:
            # No data ingested for this client yet (or collection missing after
            # a fresh deploy). Return None so the agent escalates cleanly
            # instead of raising a 500.
            return None
    return _collections[client_id]


def query(client_id: str, text: str, top_k: int = None, score_threshold: float = 0.5) -> list[dict]:
    collection = _get_collection(client_id)
    if collection is None:
        return []

    results = collection.query(
        query_texts=[text],
        n_results=top_k or 6,
        include=["documents", "metadatas", "distances"],
    )

    docs = []
    if results["documents"] and results["documents"][0]:
        for i, doc in enumerate(results["documents"][0]):
            distance = results["distances"][0][i] if results["distances"] else 0
            score = 1.0 - (distance / 2.0)
            score = max(0.0, min(1.0, score))
            docs.append({
                "text": doc,
                "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                "score": score,
                "passed": score >= score_threshold,
            })

    return docs
