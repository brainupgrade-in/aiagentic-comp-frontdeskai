"""RAG pipeline: load policy documents, embed, store in ChromaDB, retrieve.

Uses sentence-transformers directly (no langchain dependency) for embeddings
and a simple text splitter for chunking.
"""

import os
import re
import glob
import hashlib

import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

# Directories
POLICIES_DIR = os.path.join(os.path.dirname(__file__), "data", "policies")
CHROMA_DIR = os.getenv("CHROMA_DIR", "/shared/chromadb")
COLLECTION_NAME = "unigps_policies"

# Embedding model — small, fast, runs on CPU
EMBEDDING_MODEL = "all-MiniLM-L6-v2"

# Chunking config
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50

# Retrieval config
TOP_K = 4

# Module-level singletons
_collection = None
_model = None


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(EMBEDDING_MODEL)
    return _model


def _get_collection():
    global _collection
    if _collection is None:
        os.makedirs(CHROMA_DIR, exist_ok=True)
        client = chromadb.PersistentClient(
            path=CHROMA_DIR,
            settings=Settings(anonymized_telemetry=False),
        )
        _collection = client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def _split_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into chunks, preferring section boundaries."""
    # Split on markdown headings first, then paragraphs
    sections = re.split(r'(?=\n#{2,3} )', text)
    chunks = []
    for section in sections:
        section = section.strip()
        if not section:
            continue
        if len(section) <= chunk_size:
            chunks.append(section)
        else:
            # Split long sections into smaller chunks by paragraphs
            paragraphs = section.split("\n\n")
            current = ""
            for para in paragraphs:
                if len(current) + len(para) + 2 > chunk_size and current:
                    chunks.append(current.strip())
                    # Keep overlap from end of previous chunk
                    current = current[-overlap:] + "\n\n" + para if overlap else para
                else:
                    current = current + "\n\n" + para if current else para
            if current.strip():
                chunks.append(current.strip())
    return chunks


def _compute_docs_hash() -> str:
    """Hash all policy files to detect changes."""
    h = hashlib.md5()
    for filepath in sorted(glob.glob(os.path.join(POLICIES_DIR, "*.md"))):
        with open(filepath, "rb") as f:
            h.update(f.read())
    return h.hexdigest()


def index_documents(force: bool = False) -> int:
    """Load, chunk, embed, and store policy documents. Returns number of chunks indexed.

    Skips re-indexing if the docs haven't changed (based on content hash),
    unless force=True.
    """
    collection = _get_collection()
    model = _get_model()

    current_hash = _compute_docs_hash()
    hash_path = os.path.join(CHROMA_DIR, ".docs_hash")

    if not force and os.path.exists(hash_path):
        with open(hash_path) as f:
            stored_hash = f.read().strip()
        if stored_hash == current_hash and collection.count() > 0:
            return collection.count()

    # Load all markdown policy files
    md_files = sorted(glob.glob(os.path.join(POLICIES_DIR, "*.md")))
    if not md_files:
        return 0

    all_chunks = []
    all_metadatas = []
    all_ids = []

    for filepath in md_files:
        with open(filepath, encoding="utf-8") as f:
            text = f.read()

        filename = os.path.basename(filepath)
        source_name = filename.replace(".md", "").replace("-", " ").title()

        chunks = _split_text(text)
        for i, chunk in enumerate(chunks):
            chunk_id = f"{filename}::chunk_{i}"
            # Detect section heading from chunk content
            section = ""
            for line in chunk.split("\n"):
                if line.startswith("## ") or line.startswith("### "):
                    section = line.lstrip("#").strip()
                    break

            all_chunks.append(chunk)
            all_metadatas.append({
                "source": source_name,
                "filename": filename,
                "section": section,
                "chunk_index": i,
            })
            all_ids.append(chunk_id)

    if not all_chunks:
        return 0

    # Clear existing and re-index
    existing = collection.get()
    if existing["ids"]:
        collection.delete(ids=existing["ids"])

    # Embed all chunks
    vectors = model.encode(all_chunks, show_progress_bar=False).tolist()

    # Add to ChromaDB in batches
    batch_size = 500
    for start in range(0, len(all_chunks), batch_size):
        end = start + batch_size
        collection.add(
            ids=all_ids[start:end],
            embeddings=vectors[start:end],
            documents=all_chunks[start:end],
            metadatas=all_metadatas[start:end],
        )

    # Save hash to skip re-indexing next time
    with open(hash_path, "w") as f:
        f.write(current_hash)

    return len(all_chunks)


def retrieve(query: str, category: str = "", top_k: int = TOP_K) -> list[dict]:
    """Retrieve relevant policy chunks for a query.

    Args:
        query: The search query.
        category: Optional category to filter results (hr, tech, finance, facilities).
        top_k: Number of results to return.

    Returns:
        List of dicts with keys: content, source, section, score.
    """
    collection = _get_collection()
    model = _get_model()

    if collection.count() == 0:
        return []

    query_vector = model.encode(query).tolist()

    # Map category to source filename for filtering
    category_to_file = {
        "hr": "hr-handbook.md",
        "tech": "it-support.md",
        "finance": "finance-policies.md",
        "facilities": "facilities-guide.md",
    }

    where_filter = None
    if category and category in category_to_file:
        where_filter = {"filename": category_to_file[category]}

    results = collection.query(
        query_embeddings=[query_vector],
        n_results=top_k,
        where=where_filter,
        include=["documents", "metadatas", "distances"],
    )

    output = []
    if results and results["documents"]:
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            output.append({
                "content": doc,
                "source": meta.get("source", ""),
                "section": meta.get("section", ""),
                "score": round(1 - dist, 3),  # cosine similarity
            })

    return output


def format_context(results: list[dict]) -> str:
    """Format retrieval results into a context string for LLM prompts."""
    if not results:
        return ""
    parts = ["Relevant policy information:"]
    for r in results:
        source_label = f"[{r['source']}"
        if r["section"]:
            source_label += f" > {r['section']}"
        source_label += "]"
        parts.append(f"\n{source_label}\n{r['content']}")
    return "\n".join(parts)


def format_sources(results: list[dict]) -> list[str]:
    """Extract unique source citations from retrieval results."""
    seen = set()
    sources = []
    for r in results:
        label = r["source"]
        if r["section"]:
            label += f" > {r['section']}"
        if label not in seen:
            seen.add(label)
            sources.append(label)
    return sources
