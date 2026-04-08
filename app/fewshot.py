"""Few-shot memory: store successful Q&A pairs and retrieve similar examples for worker prompts.

Uses a separate ChromaDB collection (`fewshot_examples`) with the same embedding model
as the RAG pipeline. Examples are scoped per-category and retrieved by semantic similarity
to incoming queries.
"""

import chromadb
from chromadb.config import Settings
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

from rag import CHROMA_DIR

import os

FEWSHOT_COLLECTION = "fewshot_examples"
SIMILARITY_THRESHOLD = 0.3

_fewshot_collection = None
_embed_fn = None


def _get_embed_fn():
    global _embed_fn
    if _embed_fn is None:
        _embed_fn = DefaultEmbeddingFunction()
    return _embed_fn


def _get_collection():
    global _fewshot_collection
    if _fewshot_collection is None:
        os.makedirs(CHROMA_DIR, exist_ok=True)
        client = chromadb.PersistentClient(
            path=CHROMA_DIR,
            settings=Settings(anonymized_telemetry=False),
        )
        _fewshot_collection = client.get_or_create_collection(
            name=FEWSHOT_COLLECTION,
            metadata={"hnsw:space": "cosine"},
            embedding_function=_get_embed_fn(),
        )
    return _fewshot_collection


def add_example(example_id: str, question: str, answer: str, category: str, confidence: int) -> None:
    """Upsert a successful Q&A pair into the few-shot collection."""
    collection = _get_collection()
    collection.upsert(
        ids=[example_id],
        documents=[question],
        metadatas=[{
            "answer": answer[:1000],  # cap stored answer length
            "category": category,
            "confidence": confidence,
        }],
    )


def remove_example(example_id: str) -> None:
    """Delete a Q&A pair from the few-shot collection."""
    collection = _get_collection()
    try:
        collection.delete(ids=[example_id])
    except Exception:
        pass  # ignore if not found


def retrieve_examples(query: str, category: str, top_k: int = 2) -> list[dict]:
    """Retrieve the most similar successful Q&A examples for a given category.

    Returns list of dicts with keys: question, answer, score.
    Only returns examples above SIMILARITY_THRESHOLD.
    """
    collection = _get_collection()
    if collection.count() == 0:
        return []

    results = collection.query(
        query_texts=[query],
        n_results=top_k,
        where={"category": category},
        include=["documents", "metadatas", "distances"],
    )

    output = []
    if results and results["documents"]:
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            score = round(1 - dist, 3)
            if score < SIMILARITY_THRESHOLD:
                continue
            output.append({
                "question": doc,
                "answer": meta.get("answer", ""),
                "score": score,
            })

    return output


def format_fewshot_context(examples: list[dict]) -> str:
    """Format retrieved few-shot examples into a prompt string."""
    if not examples:
        return ""
    parts = ["[SUCCESSFUL_EXAMPLES_START]",
             "Here are examples of previously successful responses for similar queries:"]
    for i, ex in enumerate(examples, 1):
        parts.append(f"\nExample {i}:")
        parts.append(f"  Employee asked: {ex['question']}")
        parts.append(f"  Good response: {ex['answer']}")
    parts.append("[SUCCESSFUL_EXAMPLES_END]")
    return "\n".join(parts)
