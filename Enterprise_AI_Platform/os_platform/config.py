"""OS BFF configuration. Proxies other layers; holds no provider keys."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO = os.path.dirname(_ROOT)
load_dotenv(os.path.join(_ROOT, ".env"))
load_dotenv(os.path.join(_REPO, "Multi_Agent_Platform", ".env"))
load_dotenv(os.path.join(_REPO, ".env"))


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    host: str = os.getenv("OS_HOST", "127.0.0.1")
    port: int = _int("OS_PORT", 8010)
    tenant_id: str = os.getenv("SCM_TENANT_ID", "default")
    gateway_url: str = os.getenv("SCM_GATEWAY_URL", "http://127.0.0.1:8000")
    gateway_api_key: str = os.getenv("SCM_GATEWAY_API_KEY", "")
    layer2_url: str = os.getenv("SCM_LAYER2_URL", "http://127.0.0.1:8001")
    eval_url: str = os.getenv("SCM_EVAL_URL", "http://127.0.0.1:8002")
    twin_url: str = os.getenv("SCM_TWIN_URL", "http://127.0.0.1:8003")
    graphrag_url: str = os.getenv("SCM_GRAPHRAG_URL", "http://127.0.0.1:8100")
    repo_root: str = _REPO
    python_exe: str = os.getenv(
        "OS_PYTHON",
        os.path.join(_REPO, "venv", "Scripts", "python.exe")
        if os.name == "nt"
        else os.path.join(_REPO, "venv", "bin", "python"),
    )


settings = Settings()
