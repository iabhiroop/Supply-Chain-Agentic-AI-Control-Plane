"""Scan warehouse/TMS/ASN exceptions into detected_signals."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.ops.detector import scan_and_upsert  # noqa: E402


def main() -> None:
    n = scan_and_upsert()
    print(f"detector upserted/refreshed {n} signal(s)")


if __name__ == "__main__":
    main()
