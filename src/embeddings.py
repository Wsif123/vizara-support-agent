import time

import httpx

from chromadb.api.types import EmbeddingFunction, Documents, Embeddings

from src.llm_client import _get_groq_keys

GROQ_EMBEDDINGS_URL = "https://api.groq.com/openai/v1/embeddings"
EMBEDDING_MODEL = "nomic-embed-text-v1.5"

_keys = _get_groq_keys()


class GroqEmbeddingFunction(EmbeddingFunction):
    def __call__(self, input: Documents) -> Embeddings:
        texts = input if isinstance(input, list) else [input]
        texts = [t for t in texts]

        for key in _keys:
            headers = {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            }
            payload = {"model": EMBEDDING_MODEL, "input": texts}
            with httpx.Client(timeout=90) as client:
                resp = client.post(GROQ_EMBEDDINGS_URL, json=payload, headers=headers)
            if resp.status_code != 200:
                print(f"[embeddings] Groq error {resp.status_code}: {resp.text[:500]}")
            if resp.status_code == 429:
                continue
            resp.raise_for_status()
            data = resp.json()
            ordered = sorted(data["data"], key=lambda d: d["index"])
            return [d["embedding"] for d in ordered]

        raise RuntimeError(f"All {len(_keys)} Groq keys exhausted (429) for embeddings")
