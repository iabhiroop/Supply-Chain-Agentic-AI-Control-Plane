"""Platform configuration and the gateway-backed LLM factory.

Every LLM call in Layer 2 goes through the AI Gateway (README §2, rule 1). We
therefore talk to an OpenAI-compatible endpoint hosted by the gateway rather
than instantiating provider SDKs. That is what gives us central cost
attribution, quota enforcement, caching, fallback, and `llm` spans for Layer 4.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache

from dotenv import load_dotenv
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI

_PLATFORM_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Platform .env wins, then the repo-root .env for anything shared.
load_dotenv(os.path.join(_PLATFORM_ROOT, ".env"))
load_dotenv(os.path.join(os.path.dirname(_PLATFORM_ROOT), ".env"))


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Runtime configuration, all overridable by environment variable."""

    # --- AI Gateway (Layer 1) ---
    gateway_url: str = os.getenv("SCM_GATEWAY_URL", "http://localhost:8000")
    gateway_api_key: str = os.getenv("SCM_GATEWAY_API_KEY", "")

    # Logical model tiers. These are aliases as far as the platform is
    # concerned: the gateway decides the concrete provider and model, so
    # changing a tier is a config change here, not a code change.
    # Tiers were chosen from measured tool-loop probes. gemini-3.5-flash is
    # capable but its free tier allows only 5 requests/minute, which one
    # incident exhausts on its own, so the reasoning tier uses gemma-4-31b-it:
    # free, thinking-capable, and a separate quota bucket from the flash-lite
    # models used elsewhere. Spreading tiers across models is what keeps a
    # single workflow inside per-model rate limits.
    model_reasoning: str = os.getenv("SCM_MODEL_REASONING", "gemma-4-31b-it")
    model_default: str = os.getenv("SCM_MODEL_DEFAULT", "gemini-3.5-flash-lite")
    model_cheap: str = os.getenv("SCM_MODEL_CHEAP", "gemini-3.5-flash-lite")
    # Warehouse copilot: local Ollama via the gateway (gemma4:e2b). Layer 2
    # always names this model; it does not pick Gemini itself. The gateway may
    # retry once on gemini-3.5-flash-lite if Ollama returns HTTP 5xx or an empty
    # completion (not a default route away from local).
    model_chat: str = os.getenv("SCM_MODEL_CHAT", "gemma4:e2b")

    # Thinking-capable models spend budget on reasoning tokens before emitting
    # any content. A small budget yields zero completion tokens, which the
    # gateway currently rejects while parsing usage, so never send a tiny cap.
    max_output_tokens: int = _int("SCM_MAX_OUTPUT_TOKENS", 2048)

    # --- Internal mock ERP/WMS/TMS API (Layer 2's own data plane) ---
    # Port 8001: the gateway already owns 8000.
    api_base_url: str = os.getenv("SCM_API_BASE_URL", "http://127.0.0.1:8001/api")
    api_host: str = os.getenv("SCM_API_HOST", "127.0.0.1")
    api_port: int = _int("SCM_API_PORT", 8001)

    # --- PostgreSQL (relational system of record, plan.md §6.2) ---
    pg_host: str = os.getenv("SCM_PG_HOST", "localhost")
    pg_port: int = _int("SCM_PG_PORT", 5432)
    pg_user: str = os.getenv("SCM_PG_USER", "gateway_user")
    pg_password: str = os.getenv("SCM_PG_PASSWORD", "gateway_pass")
    pg_database: str = os.getenv("SCM_PG_DATABASE", "logistics_scm")
    # Postgres role used to *execute* copilot SELECTs (SET ROLE). The connecting
    # user must be a member of this role. See database/chat_ro.sql.
    chat_pg_role: str = os.getenv("SCM_CHAT_PG_ROLE", "scm_chat_reader")
    chat_sql_timeout_ms: int = _int("SCM_CHAT_SQL_TIMEOUT_MS", 3000)
    chat_sql_row_limit: int = _int("SCM_CHAT_SQL_ROW_LIMIT", 50)

    # --- Bounded autonomy (plan.md §3.3) ---
    # Caps per sub-agent invocation. Exceeding any of them returns control to
    # the orchestrator instead of letting an agent spin.
    agent_max_tool_calls: int = _int("SCM_AGENT_MAX_TOOL_CALLS", 8)
    agent_max_llm_calls: int = _int("SCM_AGENT_MAX_LLM_CALLS", 6)
    agent_timeout_seconds: int = _int("SCM_AGENT_TIMEOUT_SECONDS", 120)
    workflow_max_steps: int = _int("SCM_WORKFLOW_MAX_STEPS", 24)
    workflow_cost_budget_usd: float = float(os.getenv("SCM_WORKFLOW_COST_BUDGET_USD", "0.10"))

    # Workflow may transfer pick-face stock without a second click. Detector
    # never books freight or emails; SCM_DETECTOR_AUTO_RUN is a comma list of
    # event types that confirm themselves (default empty = proposal only).
    auto_transfer: bool = os.getenv("SCM_AUTO_TRANSFER", "true").lower() == "true"
    detector_interval_seconds: int = _int("SCM_DETECTOR_INTERVAL_SECONDS", 30)
    detector_auto_run: str = os.getenv("SCM_DETECTOR_AUTO_RUN", "")

    tenant_id: str = os.getenv("SCM_TENANT_ID", "default")
    graphrag_url: str = os.getenv("SCM_GRAPHRAG_URL", "http://127.0.0.1:8100")
    twin_url: str = os.getenv("SCM_TWIN_URL", "http://127.0.0.1:8003")

    # --- AgentMail (B2B edge only; never internal specialist mail) ---
    # Key lives in Multi_Agent_Platform/.env. Do not log or commit it.
    agentmail_api_key: str = os.getenv("AGENTMAIL_API_KEY", "")
    agentmail_inbox_buyer: str = os.getenv("AGENTMAIL_INBOX_BUYER", "")
    # Fixture partner replies stay on when there is no AgentMail key (CI/offline).
    # With a key, live vendor agents own A/C bids and the supplier invoice.
    email_fixture_replies: bool = (
        os.getenv(
            "SCM_EMAIL_FIXTURE_REPLIES",
            "false" if os.getenv("AGENTMAIL_API_KEY", "").strip() else "true",
        ).lower()
        == "true"
    )
    agentmail_poll_seconds: int = _int("SCM_AGENTMAIL_POLL_SECONDS", 5)
    partner_wait_seconds: int = _int("SCM_PARTNER_WAIT_SECONDS", 45)

    def detector_auto_event_types(self) -> set[str]:
        return {part.strip() for part in self.detector_auto_run.split(",") if part.strip()}

    def pg_dsn(self) -> str:
        return (
            f"host={self.pg_host} port={self.pg_port} user={self.pg_user} "
            f"password={self.pg_password} dbname={self.pg_database}"
        )

    def require_gateway_key(self) -> str:
        if not self.gateway_api_key:
            raise RuntimeError(
                "SCM_GATEWAY_API_KEY is not set. Run:\n"
                "    python scripts/provision_gateway_key.py\n"
                "Layer 2 must not hold provider credentials directly."
            )
        return self.gateway_api_key


settings = Settings()


@lru_cache(maxsize=32)
def _build_llm(
    model: str,
    temperature: float,
    max_tokens: int | None,
    trace_id: str,
    agent_id: str = "",
) -> ChatOpenAI:
    headers = {
        "X-Tenant-Id": settings.tenant_id,
        "X-Route-Hint": "balanced",
    }
    if trace_id:
        headers["X-Trace-Id"] = trace_id
    if agent_id:
        headers["X-Agent-Id"] = agent_id
    return ChatOpenAI(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=settings.agent_timeout_seconds,
        max_retries=2,
        base_url=f"{settings.gateway_url}/v1",
        api_key=settings.require_gateway_key(),
        default_headers=headers,
    )


def get_llm(
    config: RunnableConfig | None = None,
    *,
    tier: str = "default",
    temperature: float = 0.0,
    max_tokens: int | None = None,
) -> ChatOpenAI:
    """Return a gateway-backed chat model.

    Temperature defaults to 0: supervisor routing and tool-argument generation
    need to be repeatable, and a non-deterministic router makes evaluation in
    Layer 4 meaningless.

    A per-node override can be supplied at runtime via
    ``config["configurable"]["model"]`` or ``["tier"]``.
    """
    configurable = (config or {}).get("configurable", {}) if config else {}

    tier = configurable.get("tier", tier)
    model = configurable.get("model") or {
        "reasoning": settings.model_reasoning,
        "cheap": settings.model_cheap,
        "default": settings.model_default,
        "chat": settings.model_chat,
    }.get(tier, settings.model_default)

    if "temperature" in configurable:
        temperature = float(configurable["temperature"])

    tid = str(configurable.get("trace_id") or "")
    if not tid:
        try:
            from agents.execution.trace import current_trace_id

            tid = current_trace_id() or ""
        except Exception:  # noqa: BLE001
            tid = ""
    agent_id = str(configurable.get("agent_name") or configurable.get("agent_id") or "")
    return _build_llm(
        model,
        temperature,
        max_tokens or settings.max_output_tokens,
        tid,
        agent_id,
    )
