"""Deterministic scorers for the Taiwan disruption recommendation. Independent of Layer 2."""

from __future__ import annotations

from typing import Any

SCORER = "rules"
VERSION = "disruption-1"
HARD_METRICS = {
    "schema",
    "plants_short",
    "plant_cover",
    "po_1842",
    "reallocate",
    "alternate_vendor",
    "lead_days",
    "premium",
    "expedite_rfq",
    "cover_pct_grounded",
    "illegal_vendor",
}


def _score(metric: str, passed: bool, rationale: str, value: float | None = None) -> dict[str, Any]:
    return {
        "scorer": SCORER,
        "scorer_version": VERSION,
        "metric": metric,
        "value": 1.0 if passed else 0.0 if value is None else value,
        "passed": passed,
        "rationale": rationale,
    }


def _rec(snapshot: dict) -> dict:
    rec = snapshot.get("recommendation")
    if isinstance(rec, dict):
        return rec
    payload = snapshot.get("payload")
    if isinstance(payload, dict) and payload.get("short_plants") is not None:
        return payload
    return {}


def score_run(snapshot: dict, expected: dict | None = None) -> list[dict[str, Any]]:
    expected = expected or {}
    rec = _rec(snapshot)
    out: list[dict[str, Any]] = []

    schema_ok = bool(rec) and isinstance(rec.get("short_plants"), list)
    out.append(_score("schema", schema_ok, "Recommendation exposes short_plants." if schema_ok else "Missing recommendation JSON."))
    if not schema_ok:
        out.append(_score("hard_blockers", False, "Missing recommendation schema."))
        return out

    plants = rec.get("short_plants") or []
    ids = {p.get("plant_id") for p in plants if isinstance(p, dict)}
    plants_ok = ids == {"PL-IN-CHN", "PL-IN-BLR"} and len(plants) == 2
    out.append(_score("plants_short", plants_ok, f"short plants={ids}"))

    covers = {p.get("plant_id"): p.get("days_of_cover") for p in plants if isinstance(p, dict)}
    cover_ok = covers.get("PL-IN-CHN") == 5 and covers.get("PL-IN-BLR") == 8
    out.append(_score("plant_cover", bool(cover_ok), f"days_of_cover={covers}"))

    pos = rec.get("affected_po_ids") or []
    po_ok = "PO-1842" in pos
    out.append(_score("po_1842", po_ok, f"affected={pos}"))

    realloc = rec.get("reallocate") or {}
    realloc_ok = (
        realloc.get("po_id") == "PO-1842"
        and float(realloc.get("qty") or 0) == 160
        and abs(float(realloc.get("pct_of_po") or 0) - 40) < 0.01
    )
    out.append(_score("reallocate", realloc_ok, f"reallocate={realloc}"))

    alt = rec.get("alternate") or {}
    vendor = alt.get("vendor_id")
    illegal = vendor in {"VEND-C", "VEND-A", "VEND-B"} or (expected.get("forbid_vendors") and vendor in expected["forbid_vendors"])
    out.append(_score("illegal_vendor", not illegal, f"alternate vendor={vendor}"))
    xyz_ok = vendor == (expected.get("alternate_vendor") or "VEND-XYZ")
    out.append(_score("alternate_vendor", xyz_ok and not illegal, f"vendor={vendor}"))

    lead = int(alt.get("lead_days") or 0)
    lead_ok = lead in (6, 7)
    out.append(_score("lead_days", lead_ok, f"lead_days={lead}"))

    prem = float(alt.get("premium_pct") or 0)
    prem_ok = abs(prem - 8.0) < 0.5
    out.append(_score("premium", prem_ok, f"premium_pct={prem}"))

    out.append(_score("expedite_rfq", rec.get("expedite_rfq") is True, f"expedite_rfq={rec.get('expedite_rfq')}"))

    cover_pct = float(alt.get("cover_pct") or 0)
    capacity = float(alt.get("weekly_capacity") or 0)
    # Claiming ~60% without a capacity fact is the injected hallucination.
    grounded = not (abs(cover_pct - 60.0) < 1.0 and capacity <= 0)
    if expected.get("require_capacity") and capacity <= 0:
        grounded = False
    out.append(
        _score(
            "cover_pct_grounded",
            grounded,
            f"cover_pct={cover_pct} weekly_capacity={capacity}",
        )
    )

    hard = [s for s in out if s["metric"] in HARD_METRICS and s["passed"] is False]
    out.append(_score("hard_blockers", not hard, "No hard blockers." if not hard else "; ".join(s["rationale"] for s in hard)))
    return out
