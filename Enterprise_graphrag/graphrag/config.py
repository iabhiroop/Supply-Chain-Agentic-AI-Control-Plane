"""Layer 3 GraphRAG configuration."""

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
    host: str = os.getenv("GRAPHRAG_HOST", "127.0.0.1")
    port: int = _int("GRAPHRAG_PORT", 8100)
    pg_host: str = os.getenv("SCM_PG_HOST", "localhost")
    pg_port: int = _int("SCM_PG_PORT", 5432)
    pg_user: str = os.getenv("SCM_PG_USER", "gateway_user")
    pg_password: str = os.getenv("SCM_PG_PASSWORD", "gateway_pass")
    pg_database: str = os.getenv("SCM_PG_DATABASE", "logistics_scm")
    gateway_url: str = os.getenv("SCM_GATEWAY_URL", "http://localhost:8000")
    gateway_api_key: str = os.getenv("SCM_GATEWAY_API_KEY", "")
    tenant_id: str = os.getenv("SCM_TENANT_ID", "default")
    eval_url: str = os.getenv("SCM_EVAL_URL", "http://127.0.0.1:8002")
    neo4j_uri: str = os.getenv("SCM_NEO4J_URI", "bolt://localhost:7687")
    neo4j_user: str = os.getenv("SCM_NEO4J_USER", "neo4j")
    neo4j_password: str = os.getenv("SCM_NEO4J_PASSWORD", "password")
    neo4j_enabled: bool = os.getenv("SCM_NEO4J_ENABLED", "true").lower() == "true"
    embed_dim: int = _int("GRAPHRAG_EMBED_DIM", 768)
    ollama_url: str = os.getenv("SCM_OLLAMA_URL", os.getenv("GATEWAY_OLLAMA_BASE_URL", "http://localhost:11434"))
    embed_model: str = os.getenv("GRAPHRAG_EMBED_MODEL", "embeddinggemma:latest")

    def pg_dsn(self) -> str:
        return (
            f"host={self.pg_host} port={self.pg_port} user={self.pg_user} "
            f"password={self.pg_password} dbname={self.pg_database}"
        )


settings = Settings()
