"""Digital Twin read tools. Prefer HTTP :8003; fall back to twin.queries SQL."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from agents.config.settings import settings

TIMEOUT = 8
_TWIN_ROOT = Path(__file__).resolve().parents[3] / "Supply_Chain_Digital_Twin"
_http_ok: bool | None = None


def _sql_fallback():
    root = str(_TWIN_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    from twin import queries as q  # noqa: WPS433

    return q


def _get(path: str, params: dict[str, str] | None = None) -> Any:
    global _http_ok
    if _http_ok is False:
        return None
    qs = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
    url = f"{settings.twin_url.rstrip('/')}{path}"
    if qs:
        url = f"{url}?{qs}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            _http_ok = True
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        _http_ok = False
        return None


def inventory_days_of_cover(material_id: str = "MAT-SEMI-1") -> list[dict[str, Any]]:
    body = _get("/v1/twin/inventory/days-of-cover", {"material": material_id})
    if isinstance(body, dict) and body.get("positions") is not None:
        return list(body["positions"])
    return _sql_fallback().days_of_cover(material_id)


def open_pos(vendor_id: str | None = None) -> list[dict[str, Any]]:
    params = {"vendor": vendor_id} if vendor_id else None
    body = _get("/v1/twin/pos/open", params)
    if isinstance(body, dict) and body.get("purchase_orders") is not None:
        return list(body["purchase_orders"])
    return _sql_fallback().open_pos(vendor_id=vendor_id)


def news_signals(entity_id: str | None = None) -> list[dict[str, Any]]:
    params = {"entity": entity_id} if entity_id else None
    body = _get("/v1/twin/signals/news", params)
    if isinstance(body, dict) and body.get("signals") is not None:
        return list(body["signals"])
    return _sql_fallback().news_signals(entity_id=entity_id)


def plants_at_risk() -> list[dict[str, Any]]:
    body = _get("/v1/twin/plants-at-risk")
    if isinstance(body, dict) and body.get("plants") is not None:
        return list(body["plants"])
    return _sql_fallback().plants_if_abc_down()


def vendor_material(vendor_id: str, material_id: str = "MAT-SEMI-1") -> dict[str, Any] | None:
    body = _get("/v1/twin/vendor-materials", {"vendor": vendor_id, "material": material_id})
    if isinstance(body, dict) and body.get("rows"):
        return body["rows"][0]
    return _sql_fallback().vendor_material(vendor_id, material_id)
