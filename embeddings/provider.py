from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

# Pinned rather than caller-selectable, for the same reason website_sections'
# component library and design_system's font whitelist are curated: an
# arbitrary Hugging Face Hub model id could have the wrong vector dimension,
# fail to download, or silently disappoint. fastembed resolves this id to its
# own pre-converted ONNX build (currently hosted under the qdrant org on the
# Hub) and downloads it once; every call after that runs fully offline.
MODEL_ID = "BAAI/bge-small-en-v1.5"
DIMENSION = 384


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    vectors: tuple[tuple[float, ...], ...]
    model_id: str
    dimension: int


def _load_real_model() -> object:
    from fastembed import TextEmbedding

    return TextEmbedding(model_name=MODEL_ID)


class LocalEmbeddingProvider:
    """Turns text into vectors using a single pinned local embedding model.

    fastembed is imported lazily (inside the model loader, not at module import
    time) so importing this module never requires the real package or a model
    download to be present -- callers that only need EmbeddingResult/rank_by_similarity
    or that inject a fake model_loader for tests are unaffected.
    """

    def __init__(self, model_loader: Callable[[], object] | None = None) -> None:
        self._model_loader = model_loader or _load_real_model
        self._model: object | None = None

    def _model_instance(self) -> object:
        if self._model is None:
            self._model = self._model_loader()
        return self._model

    def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(vectors=(), model_id=MODEL_ID, dimension=DIMENSION)
        model = self._model_instance()
        vectors = tuple(tuple(float(value) for value in vector) for vector in model.embed(list(texts)))
        return EmbeddingResult(vectors=vectors, model_id=MODEL_ID, dimension=DIMENSION)
