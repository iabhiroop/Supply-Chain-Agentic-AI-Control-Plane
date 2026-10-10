"""
AI Gateway — Application Configuration

Uses Pydantic Settings for type-safe configuration with environment variable support.
Dev mode: SQLite, in-memory cache, console OTEL.
Prod mode: PostgreSQL, Redis, OTLP Collector.
"""

from __future__ import annotations

import yaml
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class GatewaySettings(BaseSettings):
    """Main gateway configuration, loaded from environment variables prefixed with GATEWAY_."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="GATEWAY_",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Server ---
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False
    log_level: str = "INFO"

    # --- Database ---
    database_url: str = "sqlite+aiosqlite:///./gateway.db"

    # --- Redis (None = in-memory dev mode) ---
    redis_url: str | None = None

    # --- Google Gemini (google-genai SDK; API key only, no base URL) ---
    gemini_api_key: str = ""

    # --- OpenAI / Anthropic (optional env fallbacks; UI keys take precedence) ---
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    anthropic_api_key: str = ""

    # --- Ollama (Local) ---
    ollama_base_url: str = "http://host.docker.internal:11434"

    # --- OpenTelemetry (None = console exporter) ---
    otel_endpoint: str | None = None

    # --- Config Paths ---
    models_config_path: str = "config/models.yaml"
    routes_config_path: str = "config/routes.yaml"

    # --- Admin ---
    admin_key: str = ""

    # --- Secrets (encrypt provider API keys at rest) ---
    secret_key: str = ""

    # --- Alerts ---
    alert_webhooks: str = ""

    # --- Rate Limiting Defaults ---
    default_rpm: int = 60
    default_tpm: int = 100_000
    default_provider_rpm: int = 60
    default_provider_tpm: int = 250_000
    overflow_threshold_pct: float = 80.0

    # --- Budget Defaults ---
    default_daily_budget_usd: float = 50.0

    @property
    def is_dev_mode(self) -> bool:
        """True when running with lightweight dev infrastructure."""
        return "sqlite" in self.database_url

    @property
    def has_redis(self) -> bool:
        return self.redis_url is not None and self.redis_url != ""

    @property
    def has_otel(self) -> bool:
        return self.otel_endpoint is not None and self.otel_endpoint != ""


class ModelsConfig:
    """Loaded from config/models.yaml — provides model catalog and pricing lookup."""

    def __init__(self, config_path: str = "config/models.yaml"):
        self._config_path = config_path
        self._data: dict[str, Any] = {}
        self.reload()

    def reload(self) -> None:
        """Load or reload the models configuration from YAML."""
        path = Path(self._config_path)
        if path.exists():
            with open(path) as f:
                self._data = yaml.safe_load(f) or {}
        else:
            self._data = {"providers": {}}

    @property
    def providers(self) -> dict[str, Any]:
        return self._data.get("providers", {})

    def get_provider(self, provider_name: str) -> dict[str, Any] | None:
        return self.providers.get(provider_name)

    def get_model(self, provider_name: str, model_name: str) -> dict[str, Any] | None:
        provider = self.get_provider(provider_name)
        if provider is None:
            return None
        return provider.get("models", {}).get(model_name)

    def get_pricing(self, provider_name: str, model_name: str) -> dict[str, float]:
        """Get pricing for a specific model. Returns zeros if not found."""
        model = self.get_model(provider_name, model_name)
        if model is None:
            return {
                "input_per_1m_tokens": 0.0,
                "output_per_1m_tokens": 0.0,
                "cached_input_per_1m_tokens": 0.0,
                "thinking_per_1m_tokens": 0.0,
            }
        return model.get("pricing", {})

    def get_all_model_names(self, provider_name: str) -> list[str]:
        """List all model names for a provider."""
        provider = self.get_provider(provider_name)
        if provider is None:
            return []
        return list(provider.get("models", {}).keys())

    def get_all_models_flat(self) -> list[dict[str, Any]]:
        """Return a flat list of all models across all providers with provider info attached."""
        result = []
        for prov_name, prov_data in self.providers.items():
            for model_name, model_data in prov_data.get("models", {}).items():
                result.append({
                    "provider": prov_name,
                    "model": model_name,
                    "display_name": prov_data.get("display_name", prov_name),
                    "type": prov_data.get("type", "unknown"),
                    "auth_type": prov_data.get("auth_type", "api_key"),
                    **model_data,
                })
        return result

    def find_model(self, model_name: str) -> tuple[str, dict[str, Any]] | None:
        for prov_name, prov_data in self.providers.items():
            models = prov_data.get("models", {})
            if model_name in models:
                return prov_name, models[model_name]
        return None

    def get_model_limits(self, provider_name: str, model_name: str) -> dict[str, Any]:
        provider = self.get_provider(provider_name) or {}
        model = self.get_model(provider_name, model_name) or {}
        rpm = model.get("provider_rpm")
        if rpm is None:
            rpm = provider.get("default_provider_rpm") or 0
        tpm = model.get("provider_tpm")
        if tpm is None:
            tpm = provider.get("default_provider_tpm") or 0
        overflow = model.get("overflow_threshold_pct")
        if overflow is None:
            overflow = provider.get("overflow_threshold_pct") or 80.0
        chain = model.get("fallback_chain") or provider.get("fallback_chain") or []
        return {
            "provider_rpm": int(rpm or 0),
            "provider_tpm": int(tpm or 0),
            "overflow_threshold_pct": float(overflow or 80.0),
            "fallback_chain": list(chain),
        }

    def ensure_provider(self, provider_name: str, *, display_name: str | None = None, type_: str = "cloud", auth_type: str = "api_key") -> dict[str, Any]:
        providers = self._data.setdefault("providers", {})
        if provider_name not in providers:
            providers[provider_name] = {
                "display_name": display_name or provider_name.title(),
                "type": type_,
                "auth_type": auth_type,
                "default_timeout_seconds": 60,
                "models": {},
            }
        providers[provider_name].setdefault("models", {})
        return providers[provider_name]

    def upsert_model(
        self,
        provider_name: str,
        model_name: str,
        data: dict[str, Any],
        *,
        display_name: str | None = None,
        type_: str = "cloud",
        auth_type: str = "api_key",
    ) -> dict[str, Any]:
        provider = self.ensure_provider(
            provider_name, display_name=display_name, type_=type_, auth_type=auth_type
        )
        existing = provider["models"].get(model_name, {})
        merged = {**existing, **data}
        provider["models"][model_name] = merged
        return merged

    def delete_model(self, provider_name: str, model_name: str) -> bool:
        provider = self.get_provider(provider_name)
        if not provider:
            return False
        models = provider.get("models", {})
        if model_name not in models:
            return False
        del models[model_name]
        return True

    def cheapest_chat_models(self) -> list[tuple[str, str, float]]:
        ranked: list[tuple[str, str, float]] = []
        for row in self.get_all_models_flat():
            caps = row.get("capabilities") or []
            if row.get("status") not in (None, "active", "preview"):
                continue
            if caps and "chat" not in caps:
                continue
            pricing = row.get("pricing") or {}
            cost = float(pricing.get("input_per_1m_tokens") or 0) + float(
                pricing.get("output_per_1m_tokens") or 0
            )
            ranked.append((row["provider"], row["model"], cost))
        ranked.sort(key=lambda x: x[2])
        return ranked


class RoutesConfig:
    """Loaded from config/routes.yaml — provides routing rules."""

    def __init__(self, config_path: str = "config/routes.yaml"):
        self._config_path = config_path
        self._data: dict[str, Any] = {}
        self.reload()

    def reload(self) -> None:
        path = Path(self._config_path)
        if path.exists():
            with open(path) as f:
                self._data = yaml.safe_load(f) or {}
        else:
            self._data = {}

    @property
    def default_route(self) -> dict[str, Any]:
        return self._data.get("default_route", {
            "provider": "gemini",
            "model": "gemini-2.5-flash",
            "strategy": "static",
        })

    @property
    def rules(self) -> list[dict[str, Any]]:
        rules = self._data.get("rules", [])
        return sorted(rules, key=lambda r: r.get("priority", 999))

    def set_data(self, data: dict[str, Any]) -> None:
        self._data = data or {}

    def as_dict(self) -> dict[str, Any]:
        return {
            "default_route": self.default_route,
            "rules": self.rules,
        }

    def persist(self) -> None:
        path = Path(self._config_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(self._data, f, sort_keys=False)


@lru_cache
def get_settings() -> GatewaySettings:
    """Cached singleton for gateway settings."""
    return GatewaySettings()
