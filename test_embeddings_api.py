from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.embeddings as embeddings_api
from api.embeddings import install_embeddings_api, router
from backend_security import LocalSecurityContext, LocalSecurityMiddleware, set_app_security_context
from embeddings import LocalEmbeddingProvider

pytestmark = pytest.mark.unit
TOKEN = "embeddings-api-focused-token-32-bytes"
ORIGIN = "http://127.0.0.1:8080"


class _FakeModel:
    def __init__(self, vectors_by_text: dict[str, list[float]]) -> None:
        self._vectors_by_text = vectors_by_text

    def embed(self, texts: list[str]):
        return [self._vectors_by_text[text] for text in texts]


_VECTORS = {
    "The cat sat on the mat.": [1.0, 0.0],
    "Dogs are loyal companions.": [0.0, 1.0],
    "a feline resting on a rug": [1.0, 0.0],
}


def _app(*, ai_ask=None) -> FastAPI:
    app = FastAPI()
    app.add_middleware(LocalSecurityMiddleware)
    set_app_security_context(
        app,
        LocalSecurityContext.create(token=TOKEN, bind_host="127.0.0.1", port=8080, launch_id="embeddings-api-test-launch", allow_test_client=True),
    )
    install_embeddings_api(app, embedding_provider=LocalEmbeddingProvider(model_loader=lambda: _FakeModel(_VECTORS)))
    app.include_router(router)
    return app


def _client(**kwargs) -> TestClient:
    headers = {"X-FreelancerStudio-Token": TOKEN, "Origin": ORIGIN}
    return TestClient(_app(**kwargs), base_url=ORIGIN, headers=headers)


def _payload():
    return {
        "documents": [
            {"id": "doc-1", "title": "Cats", "text": "The cat sat on the mat."},
            {"id": "doc-2", "title": "Dogs", "text": "Dogs are loyal companions."},
        ],
        "query": "a feline resting on a rug",
    }


def test_ask_returns_real_ranked_results_and_ai_answer(monkeypatch):
    monkeypatch.setattr(embeddings_api, "ask_studio_ai_with_history", lambda *args, **kwargs: "It's about cats.")

    response = _client().post("/api/embeddings/ask", json=_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "It's about cats."
    assert body["ranked"][0]["id"] == "doc-1"
    assert body["ranked"][0]["title"] == "Cats"
    assert body["ranked"][0]["score"] == pytest.approx(1.0)
    assert body["ranked"][1]["id"] == "doc-2"
    assert body["dimension"] > 0
    assert body["model_id"]


def test_ask_falls_back_gracefully_when_ai_raises(monkeypatch):
    def _raising_ai_ask(*args, **kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(embeddings_api, "ask_studio_ai_with_history", _raising_ai_ask)

    response = _client().post("/api/embeddings/ask", json=_payload())

    assert response.status_code == 200
    body = response.json()
    assert "couldn't produce an answer" in body["answer"]
    assert body["ranked"][0]["id"] == "doc-1"  # ranked results are unaffected by AI failure


def test_ask_rejects_empty_documents(monkeypatch):
    monkeypatch.setattr(embeddings_api, "ask_studio_ai_with_history", lambda *args, **kwargs: "answer")
    payload = {"documents": [], "query": "a feline resting on a rug"}

    response = _client().post("/api/embeddings/ask", json=payload)

    assert response.status_code == 422


def test_ask_rejects_more_than_max_documents(monkeypatch):
    monkeypatch.setattr(embeddings_api, "ask_studio_ai_with_history", lambda *args, **kwargs: "answer")
    payload = {
        "documents": [{"id": f"doc-{i}", "title": f"Doc {i}", "text": f"Content {i}"} for i in range(21)],
        "query": "query",
    }

    response = _client().post("/api/embeddings/ask", json=payload)

    assert response.status_code == 422


def test_ask_requires_the_security_token():
    unauthenticated = TestClient(_app(), base_url=ORIGIN)

    response = unauthenticated.post("/api/embeddings/ask", json=_payload())

    assert response.status_code in (401, 403)
