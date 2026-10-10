"""Layer 4 configuration. Independent of Layer 2 agent code."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_ROOT, ".env"))
load_dotenv(os.path.join(os.path.dirname(_ROOT), "Multi_Agent_Platform", ".env"))


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    host: str = os.getenv("EVAL_HOST", "127.0.0.1")
    port: int = _int("EVAL_PORT", 8002)
    pg_host: str = os.getenv("SCM_PG_HOST", "localhost")
    pg_port: int = _int("SCM_PG_PORT", 5432)
    pg_user: str = os.getenv("SCM_PG_USER", "gateway_user")
    pg_password: str = os.getenv("SCM_PG_PASSWORD", "gateway_pass")
    pg_database: str = os.getenv("SCM_PG_DATABASE", "logistics_scm")
    gateway_url: str = os.getenv("SCM_GATEWAY_URL", "http://localhost:8000")
    gateway_api_key: str = os.getenv("SCM_GATEWAY_API_KEY", "")
    layer2_url: str = os.getenv("SCM_API_BASE_URL", "http://127.0.0.1:8001").rsplit("/api", 1)[0]
    tenant_id: str = os.getenv("SCM_TENANT_ID", "default")
    judge_model: str = os.getenv("EVAL_JUDGE_MODEL", "gemini-3.5-flash-lite")
    success_sample_rate: float = float(os.getenv("EVAL_SUCCESS_SAMPLE_RATE", "1.0"))
    layer2_root: str = os.getenv(
        "EVAL_LAYER2_ROOT",
        os.path.join(os.path.dirname(_ROOT), "Multi_Agent_Platform"),
    )
    gateway_pg_database: str = os.getenv("GATEWAY_PG_DATABASE", "ai_gateway")
    execute_timeout_s: int = _int("EVAL_EXECUTE_TIMEOUT_S", 900)

    def pg_dsn(self) -> str:
        return (
            f"host={self.pg_host} port={self.pg_port} user={self.pg_user} "
            f"password={self.pg_password} dbname={self.pg_database}"
        )


settings = Settings()
