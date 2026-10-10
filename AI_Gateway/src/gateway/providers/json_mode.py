"""Map OpenAI response_format onto provider-native JSON mode."""

from __future__ import annotations

import json
from typing import Any


def json_mode_instruction(response_format: dict[str, Any] | None) -> str | None:
    if not response_format:
        return None
    kind = str(response_format.get("type") or "").lower()
    if kind in ("json_object", "json_mode"):
        return "Reply with a single valid JSON object only. Do not wrap it in markdown."
    if kind == "json_schema":
        schema = response_format.get("json_schema") or response_format.get("schema") or {}
        if isinstance(schema, dict) and "schema" in schema and "type" not in schema:
            schema = schema.get("schema") or schema
        return (
            "Reply with JSON that matches this schema exactly. "
            "Do not wrap it in markdown.\n"
            f"{json.dumps(schema)}"
        )
    return None


def gemini_json_config(response_format: dict[str, Any] | None) -> dict[str, Any]:
    if not response_format:
        return {}
    kind = str(response_format.get("type") or "").lower()
    if kind not in ("json_object", "json_mode", "json_schema"):
        return {}
    config: dict[str, Any] = {"response_mime_type": "application/json"}
    if kind == "json_schema":
        schema = response_format.get("json_schema") or response_format.get("schema") or {}
        if isinstance(schema, dict) and isinstance(schema.get("schema"), dict):
            schema = schema["schema"]
        if isinstance(schema, dict) and schema:
            config["response_schema"] = schema
    return config
