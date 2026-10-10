"""Feature/Expectation: Embeddings API."""


def test_embeddings_uses_local_stub(client, gateway):
    """Feature: OpenAI-compatible embeddings
    Expectation: POST /v1/embeddings returns 200 with stub vectors and does not call Ollama.
    """
    res = client.post(
        "/v1/embeddings",
        json={"input": "taiwan shutdown", "model": "embeddinggemma:latest"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["data"]
    assert body["data"][0]["embedding"] == gateway["local"].vectors
    assert gateway["local"].calls >= 1
    assert gateway["gemini"].calls == 0
