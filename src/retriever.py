import threading

from src.vectorstore import get_client

_collections: dict[str, object] = {}
_collections_lock = threading.Lock()


def invalidate(client_id=None):
    with _collections_lock:
        if client_id is None:
            _collections.clear()
        else:
            _collections.pop(client_id, None)


def _get_collection(client_id: str):
    with _collections_lock:
        if client_id in _collections:
            return _collections[client_id]

    collection_name = f"client_{client_id}"
    try:
        collection = get_client().get_collection(collection_name)
    except Exception:
        # No data ingested for this client yet (or collection missing after
        # a fresh deploy). Return None so the agent escalates cleanly
        # instead of raising a 500.
        return None

    with _collections_lock:
        _collections[client_id] = collection
    return collection


def query(client_id: str, text: str, top_k: int = None, score_threshold: float = 0.5) -> list[dict]:
    collection = _get_collection(client_id)
    if collection is None:
        return []

    try:
        results = collection.query(
            query_texts=[text],
            n_results=top_k or 6,
            include=["documents", "metadatas", "distances"],
        )
    except Exception:
        invalidate(client_id)
        return []

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
