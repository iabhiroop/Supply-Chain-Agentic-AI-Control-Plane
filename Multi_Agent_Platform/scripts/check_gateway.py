"""Verify the platform's gateway virtual key and probe candidate models.

Run this after provision_gateway_key.py to confirm Layer 2 can actually reach
Layer 1, and to see which model aliases are usable for agent traffic.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.config.settings import settings  # noqa: E402

CANDIDATES = [
    None,                    # whatever the gateway's default route resolves to
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemma-4-31b-it",
    "gemma4:e2b",
]


def call(model: str | None) -> tuple[bool, str]:
    body: dict = {
        "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
        "max_tokens": 16,
        "temperature": 0,
    }
    if model:
        body["model"] = model

    req = urllib.request.Request(
        f"{settings.gateway_url}/v1/chat/completions",
        data=json.dumps(body).encode(),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {settings.gateway_api_key}")

    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            payload = json.loads(resp.read().decode())
            used = payload.get("model", "?")
            text = payload["choices"][0]["message"]["content"].strip()
            usage = payload.get("usage", {})
            elapsed = int((time.time() - started) * 1000)
            return True, (f"{elapsed}ms model_used={used} tokens="
                          f"{usage.get('total_tokens', '?')} reply={text!r}")
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}: {exc.read().decode()[:220]}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def main() -> int:
    print(f"gateway   : {settings.gateway_url}")
    print(f"key       : {settings.gateway_api_key[:8]}… ({'set' if settings.gateway_api_key else 'MISSING'})")
    print()

    any_ok = False
    for model in CANDIDATES:
        label = model or "<gateway default route>"
        ok, detail = call(model)
        any_ok = any_ok or ok
        print(f"[{'PASS' if ok else 'FAIL'}] {label:28} {detail}")

    return 0 if any_ok else 1


if __name__ == "__main__":
    sys.exit(main())
