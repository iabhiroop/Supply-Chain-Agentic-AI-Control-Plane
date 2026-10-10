"""Create provider adapters from registry entries."""

from __future__ import annotations

from gateway.providers.base import BaseProvider

KNOWN_PROVIDERS = ("openai", "anthropic", "gemini", "local")


def create_provider(
    name: str,
    *,
    api_key: str = "",
    base_url: str = "",
) -> BaseProvider:
    key = (name or "").lower()
    if key == "openai":
        from gateway.providers.openai_provider import OpenAIProvider
        return OpenAIProvider(api_key=api_key or None, base_url=base_url or None)
    if key == "anthropic":
        from gateway.providers.anthropic_provider import AnthropicProvider
        return AnthropicProvider(api_key=api_key or None, base_url=base_url or None)
    if key == "gemini":
        try:
            from gateway.providers.gemini_provider import GeminiProvider
        except ImportError as exc:
            raise ValueError(
                "Gemini SDK is not installed. Add google-genai to the gateway image."
            ) from exc
        return GeminiProvider(api_key=api_key or None)
    if key in ("local", "ollama"):
        from gateway.providers.local_provider import LocalProvider
        return LocalProvider(base_url=base_url or None)
    raise ValueError(f"Unknown provider '{name}'")


async def test_provider_connection(name: str, *, api_key: str = "", base_url: str = "") -> dict:
    try:
        provider = create_provider(name, api_key=api_key, base_url=base_url)
    except Exception as exc:
        return {"ok": False, "provider": name, "error": str(exc)}
    try:
        ok = await provider.health_check()
        return {"ok": ok, "provider": name}
    except Exception as exc:
        return {"ok": False, "provider": name, "error": str(exc)}
    finally:
        try:
            await provider.close()
        except Exception:
            pass
