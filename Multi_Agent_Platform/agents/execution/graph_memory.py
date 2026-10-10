"""Best-effort GraphRAG writes for episodic Incident/Decision nodes. Never raises."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

from agents.config.settings import settings
from agents.execution.trace import current_trace_id

logger = logging.getLogger(__name__)


def _post(path: str, body: dict) -> dict[str, Any] | None:
    url = f"{settings.graphrag_url.rstrip('/')}{path}"
    headers = {"Content-Type": "application/json", "X-Tenant-Id": settings.tenant_id}
    tid = current_trace_id()
    if tid:
        headers["X-Trace-Id"] = tid
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None


def write_episode(
    *,
    incident_key: str,
    incident_name: str,
    properties: dict,
    rel_type: str,
    dst_key: str,
    dst_label: str,
    decision_key: str | None = None,
    decision_name: str | None = None,
    decision_props: dict | None = None,
) -> None:
    if os.getenv("SCM_GRAPHRAG_WRITE", "true").lower() != "true":
        return
    _post("/v1/graph/nodes", {
        "label": "Incident",
        "key": incident_key,
        "name": incident_name,
        "properties": properties,
        "rel_type": rel_type,
        "dst_key": dst_key,
        "dst_label": dst_label,
    })
    if decision_key:
        _post("/v1/graph/nodes", {
            "label": "Decision",
            "key": decision_key,
            "name": decision_name or decision_key,
            "properties": decision_props or {},
        })
        _post("/v1/graph/nodes", {
            "label": "Incident",
            "key": incident_key,
            "name": incident_name,
            "properties": properties,
            "rel_type": "RESOLVED_BY",
            "dst_key": decision_key,
            "dst_label": "Decision",
        })


def emit_from_workflow(result: dict[str, Any]) -> None:
    tx_id = result.get("transaction_id") or ""
    status = result.get("status")
    snap = result.get("snapshot") or {}
    tx = snap.get("transaction") or {}
    ctx = tx.get("context") or {}
    pr = (snap.get("pr") or {}).get("pr_id") or tx.get("pr_id") or tx_id[:8]
    po = snap.get("po") or {}
    vendor = po.get("vendor_id") or "VEND-A"
    po_id = po.get("po_id") or pr
    book = snap.get("booking") or {}
    short = tx_id.replace("-", "")[:8]

    if status == "SETTLED":
        try:
            from agents.memory.store import record_experience

            record_experience(
                flow="p2p_settled",
                summary=f"Four-way match settled {pr}",
                outcome={"status": status, "vendor_id": vendor, "po_id": po_id},
                vendor_id=vendor,
                po_id=po_id,
                pr_id=pr,
                trace_id=result.get("trace_id"),
            )
        except Exception:  # noqa: BLE001
            pass
        write_episode(
            incident_key=f"INC-SETTLE-{pr}",
            incident_name=f"Four-way match settled {pr}",
            properties={"root_cause": "none", "recovery": "SETTLED", "effective": True, "transaction_id": tx_id},
            rel_type="AFFECTED_BY",
            dst_key=po_id,
            dst_label="Order",
            decision_key=f"DEC-SETTLE-{short}",
            decision_name="SETTLED four-way match",
            decision_props={"action": "SETTLED", "effective": True},
        )
        write_episode(
            incident_key=f"INC-SETTLE-{pr}",
            incident_name=f"Four-way match settled {pr}",
            properties={"effective": True},
            rel_type="AFFECTED_BY",
            dst_key=vendor,
            dst_label="Supplier",
        )
        awarded = vendor
        for q in snap.get("quotes") or []:
            vid = q.get("vendor_id")
            if vid and vid != awarded:
                write_episode(
                    incident_key=f"INC-PRICE-{vid}",
                    incident_name=f"Bid from {vid} not awarded on {pr}",
                    properties={
                        "root_cause": "price_policy",
                        "recovery": "reject bid",
                        "effective": True,
                        "unit_price": q.get("unit_price"),
                    },
                    rel_type="AFFECTED_BY",
                    dst_key=vid,
                    dst_label="Supplier",
                    decision_key=f"DEC-REJECT-{vid}-{short}",
                    decision_name=f"Reject {vid} bid",
                    decision_props={"action": "reject_bid", "effective": True},
                )
        return

    if status == "AWAITING_HUMAN":
        try:
            from agents.memory.store import record_experience

            record_experience(
                flow="p2p_hitl",
                summary=result.get("halt_reason") or f"HITL on {pr}",
                outcome={"status": status, "slip_days": ctx.get("slip_days")},
                vendor_id=vendor,
                po_id=po_id,
                pr_id=pr,
                trace_id=result.get("trace_id"),
            )
        except Exception:  # noqa: BLE001
            pass
        write_episode(
            incident_key=f"INC-HITL-{pr}",
            incident_name=result.get("halt_reason") or f"HITL on {pr}",
            properties={
                "root_cause": "policy",
                "recovery": "AWAITING_HUMAN",
                "effective": True,
                "transaction_id": tx_id,
                "slip_days": ctx.get("slip_days"),
            },
            rel_type="AFFECTED_BY",
            dst_key=vendor,
            dst_label="Supplier",
            decision_key=f"DEC-HITL-{short}",
            decision_name="Hold for human",
            decision_props={"action": "AWAITING_HUMAN", "effective": True},
        )
        return

    if book.get("status") == "REBOOKED" or ctx.get("rebooked"):
        try:
            from agents.memory.store import record_experience

            record_experience(
                flow="p2p_rebook",
                summary=f"Supplier delay recovered on {pr}",
                outcome={"slip_days": ctx.get("slip_days"), "recovery": "rebook"},
                vendor_id=vendor,
                po_id=po_id,
                pr_id=pr,
                trace_id=result.get("trace_id"),
            )
        except Exception:  # noqa: BLE001
            pass
        write_episode(
            incident_key=f"INC-DELAY-{pr}",
            incident_name=f"Supplier delay recovered on {pr}",
            properties={
                "root_cause": "supplier_delay",
                "recovery": "rebook Speedy Express",
                "effective": True,
                "slip_days": ctx.get("slip_days"),
                "transaction_id": tx_id,
            },
            rel_type="AFFECTED_BY",
            dst_key=vendor,
            dst_label="Supplier",
            decision_key=f"DEC-REBOOK-{short}",
            decision_name="Rebook within slip",
            decision_props={"action": "rebook_within_slip", "effective": True},
        )
