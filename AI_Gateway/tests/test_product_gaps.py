"""Catalog of unimplemented vision items. These must skip, not fake-green."""

import pytest


@pytest.mark.skip(reason="product gap: semantic cache is opt-in via X-Semantic-Cache, not on by default")
def test_semantic_cache_on_by_default_for_all_traffic():
    """Feature: Default semantic cache
    Expectation: Paraphrase hits without X-Semantic-Cache (not implemented).
    """
    raise AssertionError("unreachable")


@pytest.mark.skip(reason="product gap: PII redaction is log-only, not a prompt guardrail / RBAC product")
def test_pii_product_blocks_prompts():
    """Feature: PII product
    Expectation: Prompts containing PAN/SSN are rejected before the provider (not implemented).
    """
    raise AssertionError("unreachable")


@pytest.mark.skip(reason="product gap: Grafana is not the sitting dashboard")
def test_grafana_sitting_dashboard():
    """Feature: Grafana dashboard
    Expectation: A Grafana sitting dashboard is wired for gateway metrics (not implemented).
    """
    raise AssertionError("unreachable")


@pytest.mark.skip(reason="product gap: Kubernetes deploy is not in the laptop slice")
def test_kubernetes_rollout():
    """Feature: Kubernetes
    Expectation: Gateway rolls out via cluster manifests as the laptop default (not implemented).
    """
    raise AssertionError("unreachable")


@pytest.mark.skip(reason="product gap: fallback is Gemini ↔ local, not GPT→Claude→Gemini")
def test_gpt_claude_gemini_fallback_chain():
    """Feature: Literal GPT→Claude→Gemini chain
    Expectation: Primary OpenAI failure hops to Claude then Gemini (not implemented).
    """
    raise AssertionError("unreachable")


@pytest.mark.skip(reason="product gap: continuous twin load generator is not built")
def test_continuous_twin_load_generator():
    """Feature: Twin load generator
    Expectation: A continuous twin workload generator drives gateway traffic (not implemented).
    """
    raise AssertionError("unreachable")


@pytest.mark.skip(reason="product gap: native eval llm spans / OTLP from the gateway are still open")
def test_native_eval_llm_spans():
    """Feature: Eval llm spans
    Expectation: Gateway emits native eval `llm` spans on each completion (not implemented).
    """
    raise AssertionError("unreachable")
