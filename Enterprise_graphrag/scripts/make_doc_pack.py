"""Generate the GraphRAG demo PDF pack.

    python -m graphrag.docpack
    python scripts/make_doc_pack.py
"""

from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from graphrag.docpack import write_pack  # noqa: E402


def main() -> int:
    paths = write_pack()
    print(json.dumps(paths, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
