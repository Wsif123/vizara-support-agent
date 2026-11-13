from pathlib import Path

import chromadb
from chromadb.config import Settings

CHROMA_DIR = Path(__file__).parent.parent / "chroma_db"

_client = None


def get_client():
    """Return one shared Chroma client for the whole process.

    Chroma's PersistentClient keeps its own SQLite/HNSW handles. Creating a
    new instance per caller (as the old retriever/ingest code did) risks lock
    contention and leaks file handles, so everything goes through here.
    """
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(
            path=str(CHROMA_DIR),
            settings=Settings(anonymized_telemetry=False),
        )
    return _client
