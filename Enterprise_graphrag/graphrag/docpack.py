"""Build the small R10 PDF pack: native ABC contract, PO-1842, scanned quality report."""

from __future__ import annotations

import os
from typing import Iterable

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "corpus", "pdf")

ABC_CONTRACT = """Contract C-ABC-2026 — Taiwan Semiconductor Components (VEND-ABC)
Primary source for MAT-SEMI-1 (power-management IC SEMI-X).
Vendor: VEND-ABC, Taiwan
Unit price: 25.00 USD
Standard lead time: 21 days
Allocation: primary source for Chennai, Bangalore, Austin, Munich, Jurong
Open PO PO-1842 (Chennai) is reallocatable up to 40% of remaining quantity
"""

PO_1842 = """Purchase order PO-1842
Vendor: VEND-ABC
Material: MAT-SEMI-1
Plant: PL-IN-CHN Chennai Assembly
Quantity: 400
Reallocatable: 160 (40%)
Status: OPEN
This PO is aligned to twin_purchase_orders.PO-1842
"""

QUALITY = """QUALITY REPORT QR-ABC-2026
Supplier VEND-ABC
Material MAT-SEMI-1
Lot inspected at PL-IN-CHN
Defect: solder void above 8 percent on sample 12
Disposition: hold remaining lots; do not ship against PO-1842 until CAPA closes
"""


def write_text_pdf(path: str, lines: Iterable[str]) -> None:
    """Minimal PDF 1.4 with a Helvetica text layer (no extra PDF library)."""
    wrapped: list[str] = []
    for line in lines:
        line = (line or "").replace("\t", " ")
        if not line:
            wrapped.append("")
            continue
        while len(line) > 92:
            wrapped.append(line[:92])
            line = line[92:]
        wrapped.append(line)
    cmds = ["BT", "/F1 12 Tf", "16 TL", "54 740 Td"]
    for i, line in enumerate(wrapped[:42]):
        esc = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        if i:
            cmds.append("T*")
        cmds.append(f"({esc}) Tj")
    cmds.append("ET")
    stream = "\n".join(cmds).encode("latin-1", errors="replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    header = b"%PDF-1.4\n"
    buf = header
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(buf))
        buf += f"{i} 0 obj\n".encode("ascii") + obj + b"\nendobj\n"
    xref_pos = len(buf)
    xref = [f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"]
    for off in offsets[1:]:
        xref.append(f"{off:010d} 00000 n \n")
    trailer = (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    )
    with open(path, "wb") as fh:
        fh.write(buf + "".join(xref).encode("ascii") + trailer.encode("ascii"))


def write_scanned_pdf(path: str, lines: Iterable[str]) -> None:
    """Image-only PDF so ingest must OCR (no text layer)."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1240, 1754), "white")
    draw = ImageDraw.Draw(img)
    font = _font(40)
    y = 80
    for line in lines:
        draw.text((72, y), line, fill=(0, 0, 0), font=font)
        y += 56
    img.save(path, "PDF", resolution=150.0)


def _font(size: int):
    from PIL import ImageFont

    for candidate in (
        r"C:\Windows\Fonts\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ):
        if os.path.isfile(candidate):
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def write_pack(out_dir: str | None = None) -> dict[str, str]:
    dest = out_dir or OUT
    os.makedirs(dest, exist_ok=True)
    contract = os.path.join(dest, "contract_vend_abc.pdf")
    po = os.path.join(dest, "po_1842.pdf")
    quality = os.path.join(dest, "quality_report_vend_abc_scan.pdf")
    write_text_pdf(contract, ABC_CONTRACT.splitlines())
    write_text_pdf(po, PO_1842.splitlines())
    write_scanned_pdf(quality, QUALITY.splitlines())
    return {"contract": contract, "po": po, "quality_scan": quality}


if __name__ == "__main__":
    import json

    print(json.dumps(write_pack(), indent=2))
