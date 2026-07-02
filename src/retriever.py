from pathlib import Path

import chromadb
from chromadb.config import Settings

from src.config import load_client_config

CHROMA_DIR = Path(__file__).parent.parent / "chroma_db"

_collection = None


def _get_collection(client_name: str):
    global _collection
    if _collection is None:
        chroma_client = chromadb.PersistentClient(
            path=str(CHROMA_DIR),
            settings=Settings(anonymized_telemetry=False),
        )
        collection_name = client_name.replace("-", "_")
        _collection = chroma_client.get_collection(collection_name)
    return _collection


def query(client_name: str, text: str, top_k: int = None) -> list[dict]:
    config = load_client_config()
    if top_k is None:
        top_k = config["retrieval"]["top_k"]
    score_threshold = config["retrieval"]["score_threshold"]

    collection = _get_collection(client_name)

    results = collection.query(
        query_texts=[text],
        n_results=top_k,
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
