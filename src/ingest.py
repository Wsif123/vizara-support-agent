import hashlib
import re
import json
from pathlib import Path

from src.config import load_client_config
from src.retriever import invalidate
from src.vectorstore import get_client

DATA_DIR = Path(__file__).parent.parent / "data"


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


def _with_heading(chunks: list[dict]) -> list[dict]:
    """Prefix each chunk with its section heading.

    Headings are metadata, not document text, so Chroma never embedded them.
    That made values stored only in a heading (for example the chatbot's
    "$90/month") impossible to retrieve. Prefixing the heading before
    embedding keeps the text in the index while the metadata stays intact.
    """
    for chunk in chunks:
        heading = (chunk["metadata"].get("heading") or "").strip()
        if heading and not chunk["text"].startswith(heading):
            chunk["text"] = f"{heading}\n{chunk['text']}".strip()
    return chunks


def _source_fingerprint(config: dict) -> str:
    raw_dir = DATA_DIR / config["name"] / "raw"
    digest = hashlib.sha256()
    for source_file in config.get("source_files", []):
        digest.update(source_file.encode("utf-8"))
        digest.update(b"\0")
        path = raw_dir / source_file
        if path.exists():
            digest.update(path.read_bytes())
        else:
            digest.update(b"<missing>")
        digest.update(b"\0")
    return digest.hexdigest()


def _manifest_path(config: dict) -> Path:
    return DATA_DIR / config["name"] / "processed" / "manifest.json"


def index_is_stale(client_id: str) -> bool:
    """True when the Chroma collection is missing or older than its sources.

    This is what lets the server heal an index that was built before a source
    file was added (the local DB here was built a month before
    chatbot-service.md existed and silently kept serving 20 stale chunks).
    """
    try:
        config = load_client_config(client_id)
    except Exception:
        return False

    if not config.get("source_files"):
        return False

    manifest_path = _manifest_path(config)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return True

    if manifest.get("fingerprint") != _source_fingerprint(config):
        return True

    try:
        collection = get_client().get_collection(f"client_{client_id}")
        return collection.count() != manifest.get("chunks", -1)
    except Exception:
        return True


def ingest(client_id: str):
    config = load_client_config(client_id)
    client_name = config["name"]
    raw_dir = DATA_DIR / client_name / "raw"
    processed_dir = DATA_DIR / client_name / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)

    client = get_client()

    collection_name = f"client_{client_id}"
    try:
        client.delete_collection(collection_name)
    except Exception:
        pass
    invalidate(client_id)
    collection = client.create_collection(
        collection_name,
        metadata={"hnsw:space": "cosine"},
    )

    all_chunks = []
    for source_file in config.get("source_files", []):
        filepath = raw_dir / source_file
        if not filepath.exists():
            print(f"  [SKIP] {source_file} not found")
            continue
        # utf-8-sig strips a BOM if the file has one. With plain utf-8 a BOM
        # makes the first "# Heading" invisible to the chunker.
        text = filepath.read_text(encoding="utf-8-sig")
        chunks = _chunk_markdown(text, source_file)
        chunks = _split_long_chunks(chunks)
        all_chunks.extend(_with_heading(chunks))
        print(f"  [OK]   {source_file} -> {len(chunks)} chunks")

    texts = [c["text"] for c in all_chunks]
    metadatas = [c["metadata"] for c in all_chunks]
    ids = [f"{client_name}_{i}" for i in range(len(texts))]

    print(f"  Embedding {len(texts)} chunks via Chroma (ONNX)...")
    if texts:
        collection.add(
            ids=ids,
            documents=texts,
            metadatas=metadatas,
        )

    with open(processed_dir / "chunks.json", "w", encoding="utf-8") as f:
        json.dump(all_chunks, f, indent=2, ensure_ascii=False)

    manifest = {
        "client_id": client_id,
        "name": client_name,
        "chunks": len(texts),
        "sources": list(config.get("source_files", [])),
        "fingerprint": _source_fingerprint(config),
    }
    with open(processed_dir / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    invalidate(client_id)
    print(f"  Done. {len(texts)} chunks stored in Chroma collection '{collection_name}'")


if __name__ == "__main__":
    ingest("vizara")
