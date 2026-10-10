"""Run Layer 2 golden jobs so the gate scores a fresh execution, not leftover ERP."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

from eval_platform.config import settings

_SCRIPT = os.path.join(settings.layer2_root, "scripts", "run_golden_transaction.py")
_DISRUPTION = os.path.join(settings.layer2_root, "scripts", "run_disruption.py")
_GATEWAY_DEMO = os.path.join(settings.layer2_root, "scripts", "run_taiwan_gateway_demo.py")


def _gateway_up() -> bool:
    import urllib.error
    import urllib.request

    url = settings.gateway_url.rstrip("/") + "/health"
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            return 200 <= getattr(resp, "status", 200) < 500
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def execute_golden_suite(*, timeout_s: int | None = None) -> dict[str, Any]:
    """Seed + run PR-1001/1002, PR-1003 HITL, then disruption. Raises on non-zero exit."""
    timeout_s = timeout_s or settings.execute_timeout_s
    if not os.path.isfile(_SCRIPT):
        raise FileNotFoundError(f"Layer 2 golden runner not found: {_SCRIPT}")
    env = os.environ.copy()
    cwd = settings.layer2_root
    steps = [
        (["unattended", [sys.executable, _SCRIPT]], "PR-1001 SETTLED + PR-1002 rebook"),
        (["hitl-hold", [sys.executable, _SCRIPT, "--hitl-only", "--hold"]], "PR-1003 AWAITING_HUMAN"),
        (["disruption", [sys.executable, _DISRUPTION]], "Taiwan ABC disruption recommendation"),
    ]
    if os.path.isfile(_GATEWAY_DEMO) and _gateway_up():
        steps.append(
            (["gateway-demo", [sys.executable, _GATEWAY_DEMO]], "Taiwan Gateway usage demo")
        )
    reports = []
    for name_cmd, label in steps:
        name, cmd = name_cmd
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        reports.append({
            "step": name,
            "label": label,
            "returncode": proc.returncode,
            "tail": (proc.stdout or proc.stderr or "")[-4000:],
        })
        if proc.returncode != 0:
            raise RuntimeError(
                f"Golden execute failed at {name} (exit {proc.returncode}): "
                f"{(proc.stderr or proc.stdout or '')[-1500:]}"
            )
    return {"ok": True, "steps": reports}
