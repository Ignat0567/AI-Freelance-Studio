from __future__ import annotations

import math
import os

import pytest

from embeddings import DIMENSION, LocalEmbeddingProvider, rank_by_similarity

pytestmark = pytest.mark.external


@pytest.mark.skipif(os.environ.get("RUN_EXTERNAL_EMBEDDING_TESTS") != "1", reason="Set RUN_EXTERNAL_EMBEDDING_TESTS=1 to run the real fastembed model download/inference test")
def test_local_embedding_provider_real_model_produces_correct_dimension_and_ranks_semantically():
    provider = LocalEmbeddingProvider()

    corpus_texts = ["The cat sat on the mat.", "Dogs are loyal companions.", "Quantum computing uses qubits."]
    corpus = provider.embed(corpus_texts)
    assert corpus.dimension == DIMENSION
    assert all(len(vector) == DIMENSION for vector in corpus.vectors)

    query = provider.embed(["a feline resting on a rug"]).vectors[0]
    ranked = rank_by_similarity(query, corpus.vectors, top_k=3)

    assert ranked[0][0] == 0  # the cat sentence is the closest match, not dogs or quantum computing
    assert not math.isnan(ranked[0][1])
