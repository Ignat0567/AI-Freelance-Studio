from __future__ import annotations

from collections.abc import Sequence
import math


def _cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def rank_by_similarity(
    query_vector: Sequence[float],
    corpus_vectors: Sequence[Sequence[float]],
    top_k: int = 5,
) -> tuple[tuple[int, float], ...]:
    """Rank corpus_vectors by cosine similarity to query_vector.

    Returns (index, score) pairs into corpus_vectors, most similar first,
    capped at top_k entries.
    """
    scored = [(index, _cosine_similarity(query_vector, vector)) for index, vector in enumerate(corpus_vectors)]
    scored.sort(key=lambda item: item[1], reverse=True)
    return tuple(scored[: max(0, top_k)])
