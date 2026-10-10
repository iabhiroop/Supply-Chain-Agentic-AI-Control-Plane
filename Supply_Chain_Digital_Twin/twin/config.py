"""Twin configuration. Same Postgres as Layer 2 (`logistics_scm` / schema `scm`)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_ROOT, ".env"))
load_dotenv(os.path.join(os.path.dirname(_ROOT), "Multi_Agent_Platform", ".env"))
load_dotenv(os.path.join(os.path.dirname(_ROOT), ".env"))


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    host: str = os.getenv("TWIN_HOST", "127.0.0.1")
    port: int = _int("TWIN_PORT", 8003)
    pg_host: str = os.getenv("SCM_PG_HOST", "localhost")
    pg_port: int = _int("SCM_PG_PORT", 5432)
    pg_user: str = os.getenv("SCM_PG_USER", "gateway_user")
    pg_password: str = os.getenv("SCM_PG_PASSWORD", "gateway_pass")
    pg_database: str = os.getenv("SCM_PG_DATABASE", "logistics_scm")
    layer2_root: str = os.getenv(
        "TWIN_LAYER2_ROOT",
        os.path.join(os.path.dirname(_ROOT), "Multi_Agent_Platform"),
    )
    seed: int = _int("TWIN_SEED", 42)
    graphrag_url: str = os.getenv("SCM_GRAPHRAG_URL", "http://127.0.0.1:8100")

    def pg_dsn(self) -> str:
        return (
            f"host={self.pg_host} port={self.pg_port} user={self.pg_user} "
            f"password={self.pg_password} dbname={self.pg_database}"
        )


settings = Settings()
