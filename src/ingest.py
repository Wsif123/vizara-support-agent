import re
import json
from pathlib import Path

import chromadb
from chromadb.config import Settings

from src.config import load_client_config
from src.embeddings import GroqEmbeddingFunction, EMBEDDING_MODEL

DATA_DIR = Path(__file__).parent.parent / "data"
CHROMA_DIR = Path(__file__).parent.parent / "chroma_db"
EMBEDDING_MODEL_FILE = CHROMA_DIR / "embedding_model.txt"


def _chunk_markdown(text: str, source: str) -> list[dict]:
    lines = text.split("\n")
    chunks = []
    current_heading = ""
    current_lines = []

    for line in lines:
        if line.startswith("#"):
            if current_lines:
                content = "\n".join(current_lines).strip()
                if content:
                    chunks.append({
                        "text": content,
                        "metadata": {"source": source, "heading": current_heading},
                    })
            current_heading = line.lstrip("#").strip()
            current_lines = []
        elif line.strip():
            current_lines.append(line)
        else:
            if current_lines:
                current_lines.append(line)

    if current_lines:
        content = "\n".join(current_lines).strip()
        if content:
            chunks.append({
                "text": content,
                "metadata": {"source": source, "heading": current_heading},
            })

    return chunks


def _split_long_chunks(chunks: list[dict], max_chars: int = 500) -> list[dict]:
    result = []
    for chunk in chunks:
        text = chunk["text"]
        if len(text) <= max_chars:
            result.append(chunk)
        else:
            paragraphs = re.split(r"\n\s*\n", text)
            current = ""
            for para in paragraphs:
                if len(current) + len(para) + 1 > max_chars:
                    if current.strip():
                        result.append({
                            "text": current.strip(),
                            "metadata": chunk["metadata"].copy(),
                        })
                    current = para
                else:
                    current = (current + "\n\n" + para).strip()
            if current.strip():
                result.append({
                    "text": current.strip(),
                    "metadata": chunk["metadata"].copy(),
                })
    return result


def ingest(client_id: str):
    config = load_client_config(client_id)
    client_name = config["name"]
    raw_dir = DATA_DIR / client_name / "raw"
    processed_dir = DATA_DIR / client_name / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)

    chroma_client = chromadb.PersistentClient(
        path=str(CHROMA_DIR),
        settings=Settings(anonymized_telemetry=False),
    )

    collection_name = f"client_{client_id}"
    try:
        chroma_client.delete_collection(collection_name)
    except Exception:
        pass
    collection = chroma_client.create_collection(
        collection_name,
        metadata={"hnsw:space": "cosine"},
        embedding_function=GroqEmbeddingFunction(),
    )

    all_chunks = []
    for source_file in config["source_files"]:
        filepath = raw_dir / source_file
        if not filepath.exists():
            print(f"  [SKIP] {source_file} not found")
            continue
        text = filepath.read_text(encoding="utf-8")
        chunks = _chunk_markdown(text, source_file)
        chunks = _split_long_chunks(chunks)
        all_chunks.extend(chunks)
        print(f"  [OK]   {source_file} -> {len(chunks)} chunks")

    texts = [c["text"] for c in all_chunks]
    metadatas = [c["metadata"] for c in all_chunks]
    ids = [f"{client_name}_{i}" for i in range(len(texts))]

    print(f"  Embedding {len(texts)} chunks via Groq ({EMBEDDING_MODEL})...")
    collection.add(
        ids=ids,
        documents=texts,
        metadatas=metadatas,
    )

    EMBEDDING_MODEL_FILE.write_text(EMBEDDING_MODEL, encoding="utf-8")

    with open(processed_dir / "chunks.json", "w", encoding="utf-8") as f:
        json.dump(all_chunks, f, indent=2, ensure_ascii=False)

    print(f"  Done. {len(texts)} chunks stored in Chroma collection '{collection_name}'")


if __name__ == "__main__":
    ingest("vizara")
