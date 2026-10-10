"""Feature/Expectation: Semantic cache embeddings — no Gateway process required."""

from gateway.cache.semantic import cosine, embed_text, semantic_text
from gateway.models.request import ChatCompletionRequest, ChatMessage


def test_paraphrase_is_close():
    """Feature: Semantic embed closeness
    Expectation: A Taiwan PO paraphrase scores cosine >= 0.82.
    """
    a = ChatCompletionRequest(
        messages=[
            ChatMessage(
                role="user",
                content=(
                    "A major supplier in Taiwan has announced a production shutdown. "
                    "Which open purchase orders are at risk, and what should procurement do?"
                ),
            )
        ]
    )
    b = ChatCompletionRequest(
        messages=[
            ChatMessage(
                role="user",
                content="Taiwan supplier shutdown — which open POs are exposed, and what should procurement do?",
            )
        ]
    )
    score = cosine(embed_text(semantic_text(a)), embed_text(semantic_text(b)))
    assert score >= 0.82, f"cosine={score}"


def test_unrelated_is_farther():
    """Feature: Semantic embed contrast
    Expectation: A related Taiwan prompt is closer than an unrelated password-reset prompt.
    """
    a = "A major supplier in Taiwan has announced a production shutdown."
    b = "How do I reset the copilot password for the warehouse kiosk?"
    close = cosine(embed_text(a), embed_text("Taiwan supplier shutdown which open POs"))
    far = cosine(embed_text(a), embed_text(b))
    assert close > far


def test_strips_recommendation_json():
    """Feature: Semantic text normalization
    Expectation: semantic_text drops the Recommendation JSON blob and keeps taiwan.
    """
    req = ChatCompletionRequest(
        messages=[
            ChatMessage(role="user", content='Taiwan shutdown?\n\nRecommendation JSON:\n{"a": 1}'),
        ]
    )
    text = semantic_text(req)
    assert "Recommendation JSON" not in text
    assert "taiwan" in text
