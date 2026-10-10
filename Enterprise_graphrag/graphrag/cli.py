"""CLI: apply schema, ingest corpus, project SCM, serve.

    python -m graphrag.cli apply
    python -m graphrag.cli reindex
    python -m graphrag.cli ingest PATH
    python -m graphrag.cli sync
    python -m graphrag.cli golden
    python -m graphrag.cli serve
"""

from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="graphrag")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("apply", help="Create graphrag schema")
    sub.add_parser("reindex", help="Ingest corpus, project SCM, rebuild communities")
    ingest_p = sub.add_parser("ingest", help="Parse a file (PDF/DOCX/image/text) into the graph")
    ingest_p.add_argument("path")
    ingest_p.add_argument(
        "--extractor",
        choices=["llm", "jev"],
        default=None,
        help="Entity/relation extractor. jev uses TypeSafe Jev instead of Gateway chat.",
    )
    sub.add_parser("golden", help="Run golden retrieval questions")
    sub.add_parser("sync", help="Copy Postgres graph + document chunks into Neo4j (primary store)")
    sub.add_parser("serve", help="API on GRAPHRAG_PORT (default 8100)")
    args = parser.parse_args(argv)

    if args.cmd == "apply":
        from graphrag.apply_schema import apply

        apply()
        return 0
    if args.cmd == "reindex":
        from graphrag.main import bootstrap

        print(json.dumps(bootstrap(), default=str, indent=2))
        return 0
    if args.cmd == "ingest":
        import os

        from graphrag.apply_schema import apply
        from graphrag.ingest import ingest_path

        if args.extractor:
            os.environ["GRAPHRAG_EXTRACTOR"] = args.extractor
        apply()
        print(json.dumps(ingest_path(args.path), default=str, indent=2))
        return 0
    if args.cmd == "sync":
        from graphrag.apply_schema import apply
        from graphrag.graph_store import sync_to_neo4j

        apply()
        print(json.dumps(sync_to_neo4j(), default=str, indent=2))
        return 0
    if args.cmd == "golden":
        from graphrag.golden import run_golden

        report = run_golden()
        print(json.dumps(report, default=str, indent=2))
        return 0 if report.get("passed") else 1
    if args.cmd == "serve":
        from graphrag.main import main as serve

        serve()
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
