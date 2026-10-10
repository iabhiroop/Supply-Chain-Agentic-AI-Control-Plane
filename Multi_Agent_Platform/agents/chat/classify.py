"""Intent classification: deterministic rules first, cheap LLM as fallback."""

from __future__ import annotations

import re

from agents.chat.models import ChatIntent, ClassifiedTurn
from agents.config.settings import get_llm

CONFIRM_RE = re.compile(
    r"^\s*(yes|y|yeah|yep|confirm|confirmed|proceed|do it|go ahead|approved|ok|okay|lgtm)\s*[.!]*\s*$",
    re.I,
)
CANCEL_RE = re.compile(
    r"^\s*(no|n|cancel|never mind|nevermind|stop|abort|don't|do not)\b",
    re.I,
)
FOLLOW_RE = re.compile(
    r"\b(what('?s| is) happening|status|incident|progress|where are we|follow up)\b",
    re.I,
)
MISMATCH_RE = re.compile(
    r"\b(stock mismatch|empty bin|bin is empty|physically empty|discrepancy|"
    r"cycle count|counted (zero|0)|no stock)\b",
    re.I,
)
DELAY_RE = re.compile(r"\b(shipment delayed|late truck|missed window|days late|running late)\b", re.I)
REJECTED_RE = re.compile(
    r"\b(carrier (rejected|dropped)|dropped the load|no capacity|refused the load)\b", re.I
)
GR_RE = re.compile(
    r"\b(asn[-\s]?\d*|goods receipt|gr discrepancy|inbound (asn|discrepancy)|counted against (the )?asn)\b",
    re.I,
)


def classify_rules(text: str, *, has_pending: bool, has_incident: bool) -> ClassifiedTurn | None:
    if has_pending and CONFIRM_RE.match(text):
        return ClassifiedTurn(intent=ChatIntent.CONFIRM, rationale="affirmative reply to pending card")
    if has_pending and CANCEL_RE.match(text):
        return ClassifiedTurn(intent=ChatIntent.CANCEL, rationale="cancel pending card")
    if FOLLOW_RE.search(text) and has_incident:
        return ClassifiedTurn(intent=ChatIntent.FOLLOW_INCIDENT, rationale="status question on bound incident")
    if GR_RE.search(text):
        asn_m = re.search(r"ASN-?\s*(\d+)", text, re.I)
        counted_m = re.search(r"counted\s+(\d+)", text, re.I)
        return ClassifiedTurn(
            intent=ChatIntent.PROPOSE_EVENT,
            event_type="GOODS_RECEIPT_DISCREPANCY",
            asn_id=f"ASN-{asn_m.group(1)}" if asn_m else "ASN-1001",
            counted_qty=int(counted_m.group(1)) if counted_m else 0,
            product_id=11,
            rationale="inbound ASN / goods-receipt language",
        )
    if MISMATCH_RE.search(text):
        return ClassifiedTurn(
            intent=ChatIntent.PROPOSE_EVENT,
            event_type="STOCK_MISMATCH",
            rationale="stock-mismatch language",
        )
    if REJECTED_RE.search(text):
        return ClassifiedTurn(
            intent=ChatIntent.PROPOSE_EVENT,
            event_type="CARRIER_REJECTED_LOAD",
            rationale="carrier rejected / no-capacity language",
        )
    if DELAY_RE.search(text):
        return ClassifiedTurn(
            intent=ChatIntent.PROPOSE_EVENT,
            event_type="SHIPMENT_DELAYED",
            rationale="delay language",
        )
    return None


def classify_llm(text: str) -> ClassifiedTurn:
    llm = get_llm(tier="cheap")
    structured = llm.with_structured_output(ClassifiedTurn)
    result = structured.invoke(
        [
            {
                "role": "system",
                "content": (
                    "Classify a supply-chain operator message. "
                    "intents: ask (question, no side effect), propose_event (they want an "
                    "operational action), follow_incident (status of a running incident), "
                    "confirm, cancel. "
                    "event_type if proposing: STOCK_MISMATCH, SHIPMENT_DELAYED, "
                    "CARRIER_REJECTED_LOAD, GOODS_RECEIPT_DISCREPANCY, ORDER_AT_RISK. "
                    "Extract numeric ids when clearly present. Do not invent ids."
                ),
            },
            {"role": "user", "content": text},
        ]
    )
    if isinstance(result, ClassifiedTurn):
        return result
    return ClassifiedTurn.model_validate(result)


def classify(text: str, *, has_pending: bool, has_incident: bool) -> ClassifiedTurn:
    ruled = classify_rules(text, has_pending=has_pending, has_incident=has_incident)
    if ruled is not None:
        return ruled
    try:
        return classify_llm(text)
    except Exception:  # noqa: BLE001
        return ClassifiedTurn(intent=ChatIntent.ASK, rationale="classifier fallback")
