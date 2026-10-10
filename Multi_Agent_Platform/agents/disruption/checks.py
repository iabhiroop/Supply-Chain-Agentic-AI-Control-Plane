"""Shared asserts for the disruption recommendation (twin facts, not a prompt)."""

from __future__ import annotations


def check(checks: list[tuple[str, bool, str]], label: str, ok: bool, detail: str) -> None:
    checks.append((label, ok, detail))


def verify_recommendation(rec: dict) -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []
    plants = rec.get("short_plants") or []
    ids = {p.get("plant_id") for p in plants}
    check(
        checks,
        "two short-cover plants",
        ids == {"PL-IN-CHN", "PL-IN-BLR"} and len(plants) == 2,
        f"{ids}",
    )
    covers = {p.get("plant_id"): p.get("days_of_cover") for p in plants}
    check(
        checks,
        "Chennai 5d / Bangalore 8d cover",
        covers.get("PL-IN-CHN") == 5 and covers.get("PL-IN-BLR") == 8,
        f"{covers}",
    )
    pos = rec.get("affected_po_ids") or []
    check(checks, "PO-1842 among affected POs", "PO-1842" in pos, f"{pos}")
    realloc = rec.get("reallocate") or {}
    check(
        checks,
        "reallocate 160 (40% of PO-1842)",
        realloc.get("po_id") == "PO-1842"
        and float(realloc.get("qty") or 0) == 160
        and abs(float(realloc.get("pct_of_po") or 0) - 40) < 0.01,
        f"{realloc}",
    )
    alt = rec.get("alternate") or {}
    lead = int(alt.get("lead_days") or 0)
    prem = float(alt.get("premium_pct") or 0)
    check(checks, "XYZ alternate vendor", alt.get("vendor_id") == "VEND-XYZ", f"{alt}")
    check(checks, "XYZ lead in 6–7 days", lead in (6, 7), f"lead_days={lead}")
    check(checks, "XYZ premium ~8%", abs(prem - 8.0) < 0.5, f"premium_pct={prem}")
    check(checks, "expedite RFQ flagged", rec.get("expedite_rfq") is True, f"{rec.get('expedite_rfq')}")
    return checks


def report(title: str, checks: list[tuple[str, bool, str]]) -> int:
    print(f"\n{title}")
    failed = 0
    for label, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {detail}")
        if not ok:
            failed += 1
    return failed
