"""Assert taiwan-disruption facts and that P2P seed still inserts PR-1001."""

from __future__ import annotations

import sys
from typing import Any

from twin.config import settings
from twin.db import fetch_one, scm_conn
from twin.queries import days_of_cover, news_signals, open_pos, vendor_material


def _fail(failures: list[str], ok: bool, msg: str) -> None:
    if not ok:
        failures.append(msg)


def _verify_p2p_seed() -> str | None:
    """Call Layer 2 seed_execution(golden) and require PR-1001 to exist."""
    root = settings.layer2_root
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from database.seed_execution import seed_execution
    except Exception as exc:  # pragma: no cover - import path
        return f"could not import seed_execution from {root}: {exc}"
    with scm_conn() as conn:
        seed_execution(conn, jobs=("golden",))
    row = fetch_one(
        "SELECT pr_id, material_id, plant_id FROM purchase_requisitions WHERE pr_id = 'PR-1001'"
    )
    if not row:
        return "seed_execution(golden) did not insert PR-1001"
    if row["material_id"] != "MAT-11" or row["plant_id"] != "WH-FR-01":
        return f"PR-1001 expected MAT-11 @ WH-FR-01, got {row}"
    return None


def run_check(*, verify_p2p_seed: bool = True) -> dict[str, Any]:
    failures: list[str] = []

    abc = vendor_material("VEND-ABC")
    _fail(failures, abc is not None, "VEND-ABC missing from vendor_materials")
    if abc:
        _fail(failures, abc.get("country") == "Taiwan", f"VEND-ABC country is {abc.get('country')!r}, want Taiwan")
        _fail(failures, not abc.get("is_alternate"), "VEND-ABC must be the primary (not alternate)")

    xyz = vendor_material("VEND-XYZ")
    _fail(failures, xyz is not None, "VEND-XYZ missing from vendor_materials")
    if abc and xyz:
        abc_px = float(abc["unit_price"])
        xyz_px = float(xyz["unit_price"])
        _fail(
            failures,
            abs((xyz_px / abc_px) - 1.08) < 0.001,
            f"VEND-XYZ price {xyz_px} is not +8% vs ABC {abc_px}",
        )
        _fail(
            failures,
            6 <= int(xyz["lead_time_days"]) <= 7,
            f"VEND-XYZ lead {xyz['lead_time_days']} not in 6–7 days",
        )
        _fail(failures, bool(xyz.get("is_alternate")), "VEND-XYZ must be is_alternate")

    cover = days_of_cover("MAT-SEMI-1")
    _fail(failures, len(cover) >= 4, f"need ≥4 plants on MAT-SEMI-1, got {len(cover)}")
    short = [r for r in cover if r.get("days_of_cover") is not None and r["days_of_cover"] < 10]
    _fail(failures, len(short) >= 2, f"need ≥2 plants with days_of_cover < 10, got {len(short)}")
    for row in short:
        _fail(
            failures,
            bool(row.get("demand_exceeds_cover")),
            f"{row['plant_id']}: horizon must exceed cover ({row.get('horizon_days')} vs {row.get('days_of_cover')})",
        )

    pos = open_pos(vendor_id="VEND-ABC")
    po1842 = next((p for p in pos if p["po_id"] == "PO-1842"), None)
    _fail(failures, po1842 is not None, "PO-1842 missing")
    if po1842:
        _fail(failures, po1842["vendor_id"] == "VEND-ABC", "PO-1842 vendor must be VEND-ABC")
        _fail(failures, po1842["status"] == "OPEN", "PO-1842 must be OPEN")
        _fail(
            failures,
            int(po1842.get("reallocatable_qty") or 0) > 0,
            "PO-1842 must have reallocatable_qty > 0",
        )

    news = news_signals(entity_id="VEND-ABC")
    _fail(failures, len(news) >= 1, "no news signal for VEND-ABC")
    if news:
        payload = news[0].get("payload") or {}
        _fail(
            failures,
            int(payload.get("duration_days") or 0) == 14,
            f"news duration_days want 14, got {payload.get('duration_days')}",
        )
        _fail(
            failures,
            payload.get("event") == "production_shutdown",
            f"news event want production_shutdown, got {payload.get('event')}",
        )

    dairy = fetch_one("SELECT plant_id FROM plants WHERE plant_id = 'WH-FR-01'")
    _fail(failures, dairy is not None, "WH-FR-01 missing after twin load (P2P plant)")
    vend_a = fetch_one("SELECT country FROM vendors WHERE vendor_id = 'VEND-A'")
    # VEND-A may be absent if Layer 2 was never seeded; only require it when present.
    mat = fetch_one("SELECT material_id FROM materials WHERE material_id = 'MAT-11'")

    p2p_error = None
    if verify_p2p_seed:
        p2p_error = _verify_p2p_seed()
        if p2p_error:
            failures.append(p2p_error)
        vend_a = fetch_one("SELECT country FROM vendors WHERE vendor_id = 'VEND-A'")
        mat = fetch_one("SELECT material_id FROM materials WHERE material_id = 'MAT-11'")
        _fail(failures, vend_a is not None, "VEND-A missing after seed_execution")
        _fail(failures, mat is not None, "MAT-11 missing after seed_execution")

    return {
        "passed": not failures,
        "failures": failures,
        "facts": {
            "abc_country": (abc or {}).get("country"),
            "xyz_lead": (xyz or {}).get("lead_time_days"),
            "xyz_price": (xyz or {}).get("unit_price"),
            "plants_on_semi": len(cover),
            "short_cover_plants": [r["plant_id"] for r in short],
            "open_abc_pos": len(pos),
            "po_1842": po1842["po_id"] if po1842 else None,
            "news": news[0]["signal_id"] if news else None,
            "wh_fr_01": bool(dairy),
            "pr_1001": bool(fetch_one("SELECT 1 FROM purchase_requisitions WHERE pr_id = 'PR-1001'")),
        },
    }
