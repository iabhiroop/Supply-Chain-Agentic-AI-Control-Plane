"""Mint an AI Gateway virtual key for the Multi-Agent Platform.

Layer 2 must never hold provider credentials (README §2, rule 1). This script
asks the gateway's admin API for a scoped virtual key and writes it into
Multi_Agent_Platform/.env as SCM_GATEWAY_API_KEY.

The plaintext key is returned by the gateway exactly once, at creation, so the
value is written straight to .env and never echoed to stdout.

Usage:
    python scripts/provision_gateway_key.py
    python scripts/provision_gateway_key.py --force   # replace an existing key
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PLATFORM_ROOT = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(PLATFORM_ROOT)
GATEWAY_ENV = os.path.join(REPO_ROOT, "AI_Gateway", ".env")
PLATFORM_ENV = os.path.join(PLATFORM_ROOT, ".env")

GATEWAY_URL = os.getenv("SCM_GATEWAY_URL", "http://localhost:8000")
KEY_NAME = "Multi-Agent Platform (Layer 2)"


def read_env_file(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    if not os.path.exists(path):
        return values
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def request_json(url: str, method: str = "GET", token: str | None = None, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:400]
        raise SystemExit(f"Gateway {method} {url} failed: HTTP {exc.code} {detail}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"Cannot reach the gateway at {url}: {exc.reason}")


def upsert_env(path: str, updates: dict[str, str]) -> None:
    lines: list[str] = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()

    remaining = dict(updates)
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.partition("=")[0].strip()
            if key in remaining:
                out.append(f"{key}={remaining.pop(key)}")
                continue
        out.append(line)

    if remaining:
        if out and out[-1].strip():
            out.append("")
        out.append("# --- AI Gateway (provisioned by scripts/provision_gateway_key.py) ---")
        for key, value in remaining.items():
            out.append(f"{key}={value}")

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out).rstrip() + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true",
                        help="mint a new key even if SCM_GATEWAY_API_KEY is already set")
    args = parser.parse_args()

    existing = read_env_file(PLATFORM_ENV).get("SCM_GATEWAY_API_KEY", "")
    if existing and not args.force:
        print(f"SCM_GATEWAY_API_KEY already set in {PLATFORM_ENV} (use --force to replace).")
        return 0

    admin_key = os.getenv("GATEWAY_ADMIN_KEY") or read_env_file(GATEWAY_ENV).get("GATEWAY_ADMIN_KEY", "")
    if not admin_key:
        raise SystemExit(
            f"GATEWAY_ADMIN_KEY not found. Set it in the environment or in {GATEWAY_ENV}."
        )

    health = request_json(f"{GATEWAY_URL}/health")
    print(f"Gateway at {GATEWAY_URL}: {health.get('status', 'unknown')}")

    payload = {
        "name": KEY_NAME,
        "description": "Supply chain multi-agent platform: orchestrator and domain sub-agents.",
        "organization_id": "default",
        "team_id": "supply-chain",
        "project_id": "multi-agent-platform",
        "permissions": {
            # Scoped to chat + embeddings only; no admin surface.
            "allowed_models": ["*"],
            "allowed_endpoints": ["/v1/chat/completions", "/v1/embeddings"],
            "allowed_tools": ["*"],
            "allow_tool_calls": True,
            "tags": ["supply-chain", "multi-agent", "logistics"],
        },
        # Headroom for a multi-agent workflow: one incident can make a dozen
        # LLM calls across the supervisor and sub-agents.
        "rate_limits": {"rpm": 120, "tpm": 400000},
    }

    created = request_json(f"{GATEWAY_URL}/v1/admin/keys", method="POST",
                           token=admin_key, body=payload)

    # The plaintext key is only present in the creation response.
    plaintext = None
    for field in ("key", "api_key", "virtual_key", "plaintext_key", "secret"):
        if isinstance(created.get(field), str) and created[field].startswith("vk_"):
            plaintext = created[field]
            break
    if not plaintext:
        raise SystemExit(
            "Gateway did not return a plaintext 'vk_' key. Response fields: "
            + ", ".join(sorted(created.keys()))
        )

    upsert_env(PLATFORM_ENV, {
        "SCM_GATEWAY_URL": GATEWAY_URL,
        "SCM_GATEWAY_API_KEY": plaintext,
    })

    print(f"Minted virtual key id={created.get('id')} prefix={plaintext[:8]}…")
    print(f"Written to {PLATFORM_ENV} (SCM_GATEWAY_API_KEY). Not printed in full by design.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
