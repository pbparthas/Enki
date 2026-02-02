"""Embedding operations using sentence-transformers."""

import numpy as np
from typing import Optional
from functools import lru_cache

# Lazy load to avoid import time penalty
_model = None
MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384


def _get_model():
    """Lazy load the sentence transformer model."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def embed(text: str) -> np.ndarray:
    """Generate embedding for text.

    Args:
        text: Text to embed

    Returns:
        384-dimensional float32 numpy array
    """
    model = _get_model()
    embedding = model.encode(text, convert_to_numpy=True)
    return embedding.astype(np.float32)


def embed_batch(texts: list[str]) -> np.ndarray:
    """Generate embeddings for multiple texts.

    Args:
        texts: List of texts to embed

    Returns:
        Array of shape (len(texts), 384)
    """
    model = _get_model()
    embeddings = model.encode(texts, convert_to_numpy=True)
    return embeddings.astype(np.float32)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two vectors.

    Args:
        a: First vector
        b: Second vector

    Returns:
        Similarity score between -1 and 1
    """
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return float(np.dot(a, b) / (norm_a * norm_b))


def vector_to_blob(vector: np.ndarray) -> bytes:
    """Convert numpy array to bytes for SQLite storage."""
    return vector.astype(np.float32).tobytes()


def blob_to_vector(blob: bytes) -> np.ndarray:
    """Convert bytes from SQLite back to numpy array."""
    return np.frombuffer(blob, dtype=np.float32)
