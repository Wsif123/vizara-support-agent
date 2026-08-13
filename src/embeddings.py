import httpx

from chromadb.api.types import EmbeddingFunction, Documents, Embeddings

from src.llm_client import _get_groq_keys

GROQ_EMBEDDINGS_URL = "https://api.groq.com/openai/v1/embeddings"
EMBEDDING_MODEL = "nomic-embed-text-v1_5"
FALLBACK_MODELS = ("nomic-embed-text-v1.5",)

_keys = _get_groq_keys()
_active_model = EMBEDDING_MODEL


def current_model() -> str:
    return _active_model


class GroqEmbeddingFunction(EmbeddingFunction):
    def __call__(self, input: Documents) -> Embeddings:
        global _active_model
        texts = input if isinstance(input, list) else [input]
        texts = [t for t in texts]

        for model in (EMBEDDING_MODEL, *FALLBACK_MODELS):
            for key in _keys:
                headers = {
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                }
                payload = {"model": model, "input": texts}
                with httpx.Client(timeout=90) as client:
                    resp = client.post(GROQ_EMBEDDINGS_URL, json=payload, headers=headers)
                if resp.status_code == 200:
                    if model != _active_model:
                        print(f"[embeddings] using model {model}")
                        _active_model = model
                    data = resp.json()
                    ordered = sorted(data["data"], key=lambda d: d["index"])
                    return [d["embedding"] for d in ordered]
                print(f"[embeddings] Groq error {resp.status_code} for model {model}: {resp.text[:300]}")
                if resp.status_code not in (429, 404):
                    resp.raise_for_status()

        raise RuntimeError(f"All {len(_keys)} Groq keys exhausted for embeddings")
