"""Runtime model catalog + encrypted provider credentials."""

from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

from gateway.config import GatewaySettings, ModelsConfig
from gateway.providers.factory import KNOWN_PROVIDERS, create_provider, test_provider_connection
from gateway.secrets.crypto import decrypt_secret, encrypt_secret, mask_secret

logger = logging.getLogger(__name__)

_PROVIDER_META = {
    "openai": {"display_name": "OpenAI", "type": "cloud", "auth_type": "api_key"},
    "anthropic": {"display_name": "Anthropic", "type": "cloud", "auth_type": "api_key"},
    "gemini": {"display_name": "Google Gemini", "type": "cloud", "auth_type": "api_key"},
    "local": {"display_name": "Local (Ollama)", "type": "local", "auth_type": "none"},
}


class CatalogRegistry:
    def __init__(self, settings: GatewaySettings, models_config: ModelsConfig, db=None):
        self.settings = settings
        self.models_config = models_config
        self.db = db
        self._secret = settings.secret_key or settings.admin_key or "dev-insecure-gateway-secret"
        self._keys: list[dict[str, Any]] = []

    async def hydrate(self, providers: dict) -> None:
        if self.db:
            try:
                for row in await self.db.list_registered_models():
                    spec = dict(row.get("spec") or {})
                    spec.setdefault("status", row.get("status") or "active")
                    meta = _PROVIDER_META.get(row["provider"], {})
                    self.models_config.upsert_model(
                        row["provider"],
                        row["model"],
                        spec,
                        display_name=meta.get("display_name"),
                        type_=meta.get("type", "cloud"),
                        auth_type=meta.get("auth_type", "api_key"),
                    )
                self._keys = await self.db.list_provider_keys()
            except Exception as exc:
                logger.warning("Catalog hydrate from DB failed: %s", exc)
        await self.apply_credentials(providers)

    def secret_for(self, ciphertext: str) -> str:
        return decrypt_secret(ciphertext, self._secret)

    async def apply_credentials(self, providers: dict) -> None:
        """Rebuild live provider adapters from env + stored keys."""
        from gateway.config import get_settings

        settings = get_settings()
        selected: dict[str, tuple[str, str]] = {}

        if settings.gemini_api_key:
            selected["gemini"] = (settings.gemini_api_key, "")
        if settings.openai_api_key:
            selected["openai"] = (settings.openai_api_key, settings.openai_base_url)
        if settings.anthropic_api_key:
            selected["anthropic"] = (settings.anthropic_api_key, "")
        selected.setdefault("local", ("", settings.ollama_base_url))

        stored = self._keys
        if self.db:
            try:
                stored = await self.db.list_provider_keys()
                self._keys = stored
            except Exception:
                pass

        for row in stored:
            if not row.get("is_active"):
                continue
            full = await self._decrypt_row(row)
            if not full:
                continue
            selected[row["provider"]] = (full, row.get("base_url") or "")

        for name, (api_key, base_url) in selected.items():
            try:
                providers[name] = create_provider(name, api_key=api_key, base_url=base_url)
                logger.info("Registered provider: %s", name)
            except Exception as exc:
                logger.warning("Could not register provider %s: %s", name, exc)

        if "local" not in providers:
            providers["local"] = create_provider("local", base_url=settings.ollama_base_url)

    async def _decrypt_row(self, row: dict[str, Any]) -> str:
        if row.get("key_ciphertext"):
            try:
                return decrypt_secret(row["key_ciphertext"], self._secret)
            except Exception:
                return ""
        if self.db and row.get("id"):
            full = await self.db.get_provider_key(UUID(row["id"]))
            if full and full.get("key_ciphertext"):
                try:
                    return decrypt_secret(full["key_ciphertext"], self._secret)
                except Exception:
                    return ""
        return ""

    async def add_model(self, payload: dict[str, Any], providers: dict) -> dict[str, Any]:
        provider = (payload.get("provider") or "").lower()
        model = payload.get("model") or ""
        if provider not in KNOWN_PROVIDERS:
            raise ValueError(f"Unsupported provider '{provider}'")
        if not model:
            raise ValueError("model is required")

        spec = {
            "context_window": int(payload.get("context_window") or 128000),
            "max_output_tokens": int(payload.get("max_output_tokens") or 8192),
            "capabilities": payload.get("capabilities") or ["chat"],
            "pricing": {
                "input_per_1m_tokens": float(payload.get("input_per_1m_tokens") or 0),
                "output_per_1m_tokens": float(payload.get("output_per_1m_tokens") or 0),
            },
            "status": payload.get("status") or "active",
            "provider_rpm": int(payload.get("provider_rpm") or 0),
            "provider_tpm": int(payload.get("provider_tpm") or 0),
            "overflow_threshold_pct": float(payload.get("overflow_threshold_pct") or 80),
            "fallback_chain": payload.get("fallback_chain") or [],
        }
        meta = _PROVIDER_META.get(provider, {})
        self.models_config.upsert_model(
            provider, model, spec,
            display_name=meta.get("display_name"),
            type_=meta.get("type", "cloud"),
            auth_type=meta.get("auth_type", "api_key"),
        )
        if self.db:
            await self.db.upsert_registered_model(provider, model, spec, spec["status"])

        api_key = payload.get("api_key") or ""
        base_url = payload.get("base_url") or ""
        if api_key or base_url:
            await self.add_provider_key(
                provider,
                name=payload.get("key_name") or model,
                api_key=api_key,
                base_url=base_url,
                providers=providers,
            )
        elif api_key == "" and provider in ("openai", "anthropic", "gemini"):
            pass
        else:
            await self.apply_credentials(providers)

        return self._public_model(provider, model)

    async def add_provider_key(
        self,
        provider: str,
        *,
        name: str,
        api_key: str,
        base_url: str,
        providers: dict,
    ) -> dict[str, Any]:
        ciphertext = encrypt_secret(api_key, self._secret) if api_key else ""
        last4 = mask_secret(api_key) if api_key else ""
        row = {
            "provider": provider,
            "name": name,
            "key_last4": last4,
            "base_url": base_url,
            "is_active": True,
        }
        if self.db:
            row = await self.db.create_provider_key(provider, name, ciphertext, last4, base_url)
        else:
            row["id"] = name
            row["key_ciphertext"] = ciphertext
            self._keys.append(row)
        await self.apply_credentials(providers)
        return {k: v for k, v in row.items() if k != "key_ciphertext"}

    async def delete_model(self, provider: str, model: str) -> bool:
        ok = self.models_config.delete_model(provider, model)
        if self.db:
            await self.db.delete_registered_model(provider, model)
        return ok

    async def test_connection(self, provider: str, api_key: str = "", base_url: str = "") -> dict:
        if not api_key and self.db:
            keys = await self.db.list_provider_keys(provider)
            if keys:
                full = await self.db.get_provider_key(UUID(keys[0]["id"]))
                if full:
                    api_key = decrypt_secret(full.get("key_ciphertext") or "", self._secret)
                    base_url = base_url or full.get("base_url") or ""
        return await test_provider_connection(provider, api_key=api_key, base_url=base_url)

    def _public_model(self, provider: str, model: str) -> dict[str, Any]:
        data = self.models_config.get_model(provider, model) or {}
        return {"provider": provider, "model": model, **data}

    def list_keys_public(self) -> list[dict[str, Any]]:
        return [
            {
                "id": k.get("id"),
                "provider": k.get("provider"),
                "name": k.get("name"),
                "key_last4": k.get("key_last4"),
                "base_url": k.get("base_url"),
                "is_active": k.get("is_active", True),
            }
            for k in self._keys
        ]
