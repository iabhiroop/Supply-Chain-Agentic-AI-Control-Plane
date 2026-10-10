"""Versioned SCM ontology. Extraction may only emit these types."""

from __future__ import annotations

import re

ONTOLOGY_VERSION = "scm-v1"

NODE_TYPES = [
    "Supplier", "Material", "Product", "Order", "Shipment", "Carrier",
    "Customer", "Warehouse", "Site", "Contract", "SLA", "Policy",
    "Incident", "Decision", "Document", "Chunk", "Person", "Region", "Community",
]

REL_TYPES = [
    "SUPPLIES", "COMPONENT_OF", "CONTAINS", "SHIPPED_VIA", "ORDERED_BY",
    "STORED_AT", "GOVERNED_BY", "BREACHES", "AFFECTED_BY", "RESOLVED_BY",
    "MENTIONS", "DERIVED_FROM", "ALTERNATE_FOR", "LOCATED_IN", "LEARNED_FROM",
    "USES",
]

ENTITY_KEY_RE = re.compile(
    r"\b(VEND-[A-Z0-9]+|MAT-[\w-]+|PR-\d+|PO-\d+|WH-[\w-]+|PL-[\w-]+)\b",
    re.I,
)


def extract_entity_keys(text: str) -> list[str]:
    return list(dict.fromkeys(m.upper() for m in ENTITY_KEY_RE.findall(text or "")))


def label_for_key(key: str) -> str:
    k = (key or "").upper()
    if k.startswith("VEND"):
        return "Supplier"
    if k.startswith("MAT"):
        return "Material"
    if k.startswith(("PR", "PO")):
        return "Order"
    if k.startswith(("WH", "PL")):
        return "Site"
    return "Chunk"


def schema() -> dict:
    return {
        "version": ONTOLOGY_VERSION,
        "nodes": NODE_TYPES,
        "relationships": REL_TYPES,
        "writes": "GraphRAG owns document entities; agents write Incident/Decision via POST /v1/graph/nodes.",
    }
