"""Ingest a text file with TypeSafe Jev and MERGE scm-v1 facts into Neo4j.

Code finds candidate keys in the file. Jev classifies each key and each
compatible pair. This script prints the typed result and, with --write,
MERGE-s Document, business nodes, MENTIONS, and ontology relationships.

  python ingest_graph.py sample_contract.md
  python ingest_graph.py sample_contract.md --write
  python ingest_graph.py sample_contract.md --write --postgres
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
GRAPHRAG_ROOT = HERE.parent
sys.path.insert(0, str(GRAPHRAG_ROOT))

from dotenv import load_dotenv

load_dotenv(HERE / ".env")
load_dotenv()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Review a document with Jev and optionally write nodes/rels to Neo4j."
    )
    parser.add_argument("input_file", type=Path, help="Text or markdown file to ingest")
    parser.add_argument(
        "--write",
        action="store_true",
        help="MERGE accepted entities and relations into Neo4j",
    )
    parser.add_argument(
        "--postgres",
        action="store_true",
        help="With --write, also dual-write through graph_store (Postgres + Neo4j)",
    )
    parser.add_argument("--json", action="store_true", help="Print the full extraction payload")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    path = args.input_file.expanduser().resolve()
    if not path.is_file():
        print(f"error: file not found: {path}", file=sys.stderr)
        return 2
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        print(f"error: empty file: {path}", file=sys.stderr)
        return 2

    from graphrag.jev_extract import apply_neo4j, extract_from_text

    extracted = extract_from_text(text)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    document_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"jevtest:{digest}"))
    result = {
        "source_path": str(path),
        "document_id": document_id,
        "extractor": "jev",
        **extracted,
        "written": None,
    }

    if args.write and not extracted.get("skipped"):
        if args.postgres:
            from graphrag.extract import apply_extraction
            from graphrag.graph_store import merge_node

            doc_nid = merge_node(
                "Document",
                document_id,
                path.name,
                {"source_uri": str(path), "extractor": "jev", "hash": digest},
            )
            result["written"] = apply_extraction(
                extracted,
                document_id=document_id,
                doc_node_id=doc_nid,
                source_text=text,
                extractor="jev",
            )
            result["write_target"] = "postgres+neo4j"
        else:
            result["written"] = apply_neo4j(
                extracted,
                document_id=document_id,
                title=path.name,
                source_uri=str(path),
                source_text=text,
            )
            result["write_target"] = "neo4j"

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0 if not extracted.get("skipped") else 1

    print(f"File: {path}")
    print(f"Model: {extracted.get('model')}")
    print(f"Windows: {extracted.get('windows')}")
    if extracted.get("skipped"):
        print(f"Skipped: {extracted.get('reason')}")
        return 1
    print("Entities:")
    for ent in extracted.get("entities") or []:
        print(f"  {ent['label']:12} {ent['key']}")
    print("Relations:")
    for rel in extracted.get("relations") or []:
        print(f"  ({rel['src']})-[:{rel['rel']}]->({rel['dst']})  conf={rel.get('confidence')}")
    if result.get("written"):
        print(f"Wrote {result['write_target']}: {result['written']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
