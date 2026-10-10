"""Ask path: local model generates restricted SQL, we run it, local model answers."""

from __future__ import annotations

import json
import re
from typing import Any

from agents.chat.schema_card import WarehouseRole, schema_card
from agents.chat.sql_sandbox import run_readonly
from agents.config.settings import get_llm

_JSON_RE = re.compile(r"\{[\s\S]*\}")
_SQL_FENCE_RE = re.compile(r"```(?:sql)?\s*([\s\S]*?)```", re.I)


def _content(result: Any) -> str:
    text = getattr(result, "content", None)
    if text is None:
        return str(result)
    if isinstance(text, list):
        parts = []
        for part in text:
            if isinstance(part, dict):
                parts.append(str(part.get("text") or part.get("content") or ""))
            else:
                parts.append(str(part))
        return " ".join(parts)
    return str(text)


def _invoke_chat(messages: list[dict[str, str]]) -> str:
    llm = get_llm(tier="chat")
    return _content(llm.invoke(messages)).strip()


def extract_sql(text: str) -> str | None:
    """Pull a SELECT from model output (JSON, fenced block, or raw)."""
    if not text:
        return None
    fence = _SQL_FENCE_RE.search(text)
    if fence:
        candidate = fence.group(1).strip()
        if candidate.lower().lstrip().startswith(("select", "with")):
            return candidate.rstrip(";")

    blob = _JSON_RE.search(text)
    if blob:
        try:
            data = json.loads(blob.group(0))
            sql = data.get("sql") if isinstance(data, dict) else None
            if isinstance(sql, str) and sql.strip().lower().lstrip().startswith(("select", "with")):
                return sql.strip().rstrip(";")
        except json.JSONDecodeError:
            pass

    for line_block in text.split("\n\n"):
        chunk = line_block.strip().rstrip(";")
        if chunk.lower().startswith(("select", "with")):
            return chunk
    lowered = text.lower()
    idx = lowered.find("select ")
    if idx >= 0:
        return text[idx:].strip().rstrip(";")
    idx = lowered.find("with ")
    if idx >= 0:
        return text[idx:].strip().rstrip(";")
    return None


def _sql_prompt(question: str, role: WarehouseRole | str, prior_error: str | None = None, job_hint: str | None = None) -> list[dict[str, str]]:
    extra = ""
    if prior_error:
        extra = (
            f"\nThe previous SQL was rejected or failed:\n{prior_error}\n"
            "Write a corrected query. Still SELECT-only against the allowed views.\n"
        )
    job = ""
    if job_hint:
        job = (
            f"\nThe operator has a purchase job selected: {job_hint}. "
            "Prefer v_execution_transactions, v_quotes, v_purchase_requisitions, "
            "v_bookings, v_channel_messages, v_exec_purchase_orders filtered to that transaction_id or pr_id. "
            "Do not query warehouse bins unless they asked about stock.\n"
        )
    return [
        {
            "role": "system",
            "content": (
                "You are a supply-chain data copilot for operators. "
                "Translate the question into one PostgreSQL read query.\n"
                f"{schema_card(role)}\n"
                f"{job}"
                "Reply with JSON only, no markdown, of the form "
                '{"sql":"SELECT ... LIMIT 50","rationale":"..."}. '
                "Do not invent tables. Do not mutate data."
            ),
        },
        {"role": "user", "content": f"Question: {question}{extra}"},
    ]


def _answer_prompt(question: str, sql: str, rows: list[dict[str, Any]], error: str | None) -> list[dict[str, str]]:
    payload = json.dumps({"sql": sql, "rows": rows[:50], "error": error}, default=str)
    return [
        {
            "role": "system",
            "content": (
                "You are speaking to a warehouse worker or manager. "
                "Answer ONLY from QUERY_RESULT. If rows are empty, say you found no matching records. "
                "If error is set, say the query could not be run and suggest a simpler question. "
                "Never invent quantities, dates, carriers, or customer names. "
                "Do not claim you changed stock or booked a carrier. "
                "Keep it to a short operational brief."
            ),
        },
        {"role": "user", "content": f"QUESTION:\n{question}\n\nQUERY_RESULT:\n{payload}"},
    ]


def answer_with_sql(
    question: str,
    role: WarehouseRole | str,
    transaction_id: str | None = None,
) -> dict[str, Any]:
    """Generate SQL with the local model, run the sandbox, phrase the answer.

    Always calls get_llm(tier="chat"). Gateway may fall back to Gemini only if
    Ollama returns 5xx or an empty completion; copilot does not switch models.
    """
    job_hint = None
    if transaction_id:
        job_hint = f"transaction_id={transaction_id}"
        try:
            from agents.execution import store as exec_store
            snap = exec_store.get_snapshot(transaction_id)
            tx = snap.get("transaction") or {}
            job_hint = (
                f"transaction_id={transaction_id} pr_id={tx.get('pr_id')} "
                f"stage={tx.get('stage')} status={tx.get('status')} halt={tx.get('halt_reason')}"
            )
        except Exception:  # noqa: BLE001
            pass
    try:
        raw = _invoke_chat(_sql_prompt(question, role, job_hint=job_hint))
    except Exception as exc:  # noqa: BLE001
        return {
            "reply": (
                "The local warehouse model is unavailable, so I cannot look up live data. "
                f"({type(exc).__name__}: {exc})"
            ),
            "sql": None,
            "rows": [],
            "error": str(exc),
            "ok": False,
        }

    sql = extract_sql(raw)
    if not sql:
        return {
            "reply": (
                "I could not form a read query from that question. Try naming a bin, "
                "product, or order (for example: how many units of product 11 are in bin A1-01?)."
            ),
            "sql": None,
            "rows": [],
            "error": "no_sql_extracted",
            "ok": False,
            "raw": raw[:500],
        }

    result = run_readonly(sql, role)
    if not result["ok"]:
        try:
            raw2 = _invoke_chat(_sql_prompt(question, role, prior_error=result["error"], job_hint=job_hint))
            sql2 = extract_sql(raw2)
            if sql2:
                result = run_readonly(sql2, role)
                sql = sql2
        except Exception as exc:  # noqa: BLE001
            result = {
                "ok": False,
                "sql": sql,
                "rows": [],
                "error": f"rewrite failed: {type(exc).__name__}: {exc}",
                "row_count": 0,
            }

    try:
        reply = _invoke_chat(_answer_prompt(question, result.get("sql") or sql, result.get("rows") or [], result.get("error")))
    except Exception as exc:  # noqa: BLE001
        if result["ok"] and result["rows"]:
            reply = (
                "Local model could not phrase a reply, but the query succeeded. "
                f"{result['row_count']} row(s): {json.dumps(result['rows'][:5], default=str)}"
            )
        else:
            reply = (
                "The local warehouse model is unavailable, so I cannot summarise the result. "
                f"({type(exc).__name__}: {exc})"
            )

    return {
        "reply": reply,
        "sql": result.get("sql") or sql,
        "rows": result.get("rows") or [],
        "error": result.get("error"),
        "ok": bool(result.get("ok")),
        "row_count": result.get("row_count", 0),
    }
