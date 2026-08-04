from __future__ import annotations

import pytest

from embeddings import DIMENSION, MODEL_ID, EmbeddingResult, LocalEmbeddingProvider, rank_by_similarity

pytestmark = pytest.mark.unit


class _FakeModel:
    """Mimics fastembed.TextEmbedding's real interface (embed(list[str]) -> iterable
    of vector-like objects) without downloading anything."""

    def __init__(self, vectors_by_text: dict[str, list[float]]) -> None:
        self._vectors_by_text = vectors_by_text
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]):
        self.calls.append(texts)
        return [self._vectors_by_text[text] for text in texts]


def test_rank_by_similarity_orders_most_similar_first():
    query = [1.0, 0.0]
    corpus = [[0.0, 1.0], [1.0, 0.0], [0.7, 0.7]]

    ranked = rank_by_similarity(query, corpus, top_k=3)

    assert [index for index, _score in ranked] == [1, 2, 0]
    assert ranked[0][1] == pytest.approx(1.0)
    assert ranked[-1][1] == pytest.approx(0.0)


def test_rank_by_similarity_respects_top_k():
    query = [1.0, 0.0]
    corpus = [[1.0, 0.0], [0.9, 0.1], [0.1, 0.9], [0.0, 1.0]]

    ranked = rank_by_similarity(query, corpus, top_k=2)

    assert len(ranked) == 2
    assert [index for index, _score in ranked] == [0, 1]


def test_rank_by_similarity_handles_zero_vector_without_dividing_by_zero():
    query = [1.0, 0.0]
    corpus = [[0.0, 0.0], [1.0, 0.0]]

    ranked = rank_by_similarity(query, corpus, top_k=2)

    scores = dict(ranked)
    assert scores[0] == 0.0
    assert scores[1] == pytest.approx(1.0)


def test_local_embedding_provider_uses_injected_model_loader_not_real_fastembed():
    fake_model = _FakeModel({"hello": [0.1, 0.2, 0.3], "world": [0.4, 0.5, 0.6]})
    provider = LocalEmbeddingProvider(model_loader=lambda: fake_model)

    result = provider.embed(["hello", "world"])

    assert isinstance(result, EmbeddingResult)
    assert result.model_id == MODEL_ID
    assert result.dimension == DIMENSION
    assert result.vectors == ((0.1, 0.2, 0.3), (0.4, 0.5, 0.6))
    assert fake_model.calls == [["hello", "world"]]


def test_local_embedding_provider_loads_model_lazily_and_reuses_it():
    load_count = {"value": 0}

    def _loader():
        load_count["value"] += 1
        return _FakeModel({"a": [1.0], "b": [2.0]})

    provider = LocalEmbeddingProvider(model_loader=_loader)
    assert load_count["value"] == 0  # not loaded until first embed() call

    provider.embed(["a"])
    provider.embed(["b"])

    assert load_count["value"] == 1  # loaded once, reused


def test_local_embedding_provider_handles_empty_input_without_touching_the_model():
    def _loader():
        raise AssertionError("model_loader should not be called for empty input")

    provider = LocalEmbeddingProvider(model_loader=_loader)

    result = provider.embed([])

    assert result.vectors == ()
    assert result.dimension == DIMENSION
