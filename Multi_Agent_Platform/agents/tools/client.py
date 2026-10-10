"""HTTP client shared by all agent tools.

Tools never talk to the database directly: they call the internal ERP/WMS/TMS
API, which owns transactions, guardrails and the audit trail. Errors are
returned as data rather than raised, so an agent can see what went wrong and
react (or report low confidence) instead of the whole workflow crashing.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from agents.config.settings import settings

TIMEOUT_SECONDS = 30


def _request(method: str, path: str, body: dict | None = None) -> Any:
    url = f"{settings.api_base_url}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        detail = raw
        try:
            parsed = json.loads(raw)
            detail = parsed.get("detail", raw)
        except Exception:  # noqa: BLE001
            pass
        # Surfaced to the model as a result, so it can adapt (e.g. pick another
        # carrier when one is refused for having no capacity).
        return {"error": True, "status_code": exc.code, "detail": detail}
    except urllib.error.URLError as exc:
        return {"error": True, "detail": f"Internal API unreachable at {url}: {exc.reason}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": True, "detail": f"{type(exc).__name__}: {exc}"}


def api_get(path: str) -> Any:
    return _request("GET", path)


def api_post(path: str, body: dict) -> Any:
    return _request("POST", path, body)
