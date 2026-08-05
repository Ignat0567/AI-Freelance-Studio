from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field, StringConstraints

from ai_utils import ask_studio_ai_with_history
from backend_security import StrictRequestModel
from embeddings import LocalEmbeddingProvider, rank_by_similarity
from order_workflow.execution_config import ExecutionConfigurationProvider

router = APIRouter(prefix="/api/embeddings", tags=["embeddings"])

_MAX_DOCUMENTS = 20
_MAX_DOCUMENT_TEXT_LENGTH = 5000
_MAX_QUERY_LENGTH = 500
_TOP_K = 3
_SNIPPET_LENGTH = 240

_ShortLabel = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)]
_DocumentText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=_MAX_DOCUMENT_TEXT_LENGTH)]
_QueryText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=_MAX_QUERY_LENGTH)]


class EmbeddingDocument(StrictRequestModel):
    id: _ShortLabel
    title: _ShortLabel
    text: _DocumentText


class EmbeddingsAskRequest(StrictRequestModel):
    documents: Annotated[tuple[EmbeddingDocument, ...], Field(min_length=1, max_length=_MAX_DOCUMENTS)]
    query: _QueryText


def get_embedding_provider(request: Request) -> LocalEmbeddingProvider:
    provider = getattr(request.app.state, "embedding_provider", None)
    if provider is None:
        provider = LocalEmbeddingProvider()
        request.app.state.embedding_provider = provider
    return provider


def install_embeddings_api(app, *, embedding_provider: LocalEmbeddingProvider | None = None) -> LocalEmbeddingProvider:
    active = embedding_provider or LocalEmbeddingProvider()
    app.state.embedding_provider = active
    return active


def _snippet(text: str) -> str:
    trimmed = text.strip()
    return trimmed if len(trimmed) <= _SNIPPET_LENGTH else trimmed[:_SNIPPET_LENGTH].rstrip() + "..."


def _build_grounded_prompt(query: str, ranked_documents: list[EmbeddingDocument]) -> str:
    context = "\n\n".join(f"[{document.title}]\n{document.text}" for document in ranked_documents)
    return (
        "Answer the question using ONLY the information in the documents below. "
        "If the answer isn't in the documents, say so honestly rather than guessing.\n\n"
        f"Documents:\n{context}\n\nQuestion: {query}\n\nAnswer concisely, in a short paragraph."
    )


@router.post("/ask")
def ask(payload: EmbeddingsAskRequest, request: Request) -> dict:
    provider = get_embedding_provider(request)
    try:
        corpus_result = provider.embed([document.text for document in payload.documents])
        query_result = provider.embed([payload.query])
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"code": "embedding_model_unavailable", "message": str(exc)[:300]}) from None

    ranked_indices = rank_by_similarity(query_result.vectors[0], corpus_result.vectors, top_k=min(_TOP_K, len(payload.documents)))
    ranked_documents = [payload.documents[index] for index, _score in ranked_indices]
    ranked = [
        {"id": document.id, "title": document.title, "score": score, "snippet": _snippet(document.text)}
        for document, (_index, score) in zip(ranked_documents, ranked_indices)
    ]

    answer = ""
    try:
        snapshot = ExecutionConfigurationProvider().snapshot()
        answer = ask_studio_ai_with_history(
            snapshot.provider.provider,
            snapshot.model.model,
            _build_grounded_prompt(payload.query, ranked_documents),
            [],
            temperature=0.2,
        ).strip()
    except Exception:
        answer = ""
    if not answer:
        answer = "The AI couldn't produce an answer right now; showing the closest matching documents instead."

    return {"ranked": ranked, "answer": answer, "model_id": corpus_result.model_id, "dimension": corpus_result.dimension}
