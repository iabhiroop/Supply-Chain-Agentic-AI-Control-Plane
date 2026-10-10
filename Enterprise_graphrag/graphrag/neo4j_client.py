"""Shared Neo4j driver. Labels and rel types must be ontology-allowlisted."""

from __future__ import annotations

import json
import logging
from typing import Any

from graphrag.config import settings
from graphrag.ontology import NODE_TYPES, REL_TYPES

logger = logging.getLogger(__name__)

_HOP_PATTERNS = {1: "*1..1", 2: "*1..2", 3: "*1..3"}


def _label(value: str) -> str:
    if value not in NODE_TYPES:
        raise ValueError(f"Off-ontology label {value}")
    return value


def _rel(value: str) -> str:
    if value not in REL_TYPES:
        raise ValueError(f"Off-ontology rel {value}")
    return value


class Neo4jClient:
    def __init__(self) -> None:
        self._driver: Any = None
        self._schema_ok = False
        self.last_error: str | None = None

    @property
    def enabled(self) -> bool:
        return settings.neo4j_enabled

    def connect(self) -> Any | None:
        if not self.enabled:
            return None
        if self._driver is None:
            try:
                from neo4j import GraphDatabase
            except ImportError as exc:
                self.last_error = "neo4j package not installed"
                logger.warning("%s: %s", self.last_error, exc)
                return None
            try:
                logging.getLogger("neo4j").setLevel(logging.ERROR)
                self._driver = GraphDatabase.driver(
                    settings.neo4j_uri,
                    auth=(settings.neo4j_user, settings.neo4j_password),
                )
                self._driver.verify_connectivity()
                self.last_error = None
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
                logger.warning("Neo4j unreachable at %s: %s", settings.neo4j_uri, exc)
                self._driver = None
                return None
        return self._driver

    def ping(self) -> bool:
        driver = self.connect()
        if driver is None:
            return False
        try:
            driver.verify_connectivity()
            self.last_error = None
            return True
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            logger.warning("Neo4j ping failed: %s", exc)
            return False

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None
            self._schema_ok = False

    def run(self, cypher: str, params: dict | None = None) -> list[dict[str, Any]]:
        driver = self.connect()
        if driver is None:
            return []
        with driver.session() as session:
            result = session.run(cypher, params or {})
            return [dict(r) for r in result]

    def ensure_schema(self) -> None:
        if not self.ping() or self._schema_ok:
            return
        try:
            self.run(
                "CREATE CONSTRAINT entity_nid IF NOT EXISTS FOR (n:Entity) REQUIRE n.nid IS UNIQUE"
            )
            self.run("CREATE INDEX entity_key IF NOT EXISTS FOR (n:Entity) ON (n.key)")
            self.run("CREATE INDEX entity_tenant IF NOT EXISTS FOR (n:Entity) ON (n.tenant_id)")
            self._schema_ok = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Neo4j schema setup failed (writes still attempted): %s", exc)

    def merge_node(self, label: str, key: str, name: str, properties: dict | None = None) -> None:
        self.ensure_schema()
        if not self.ping():
            return
        label = _label(label)
        nid = f"{settings.tenant_id}:{label}:{key}"
        cypher = (
            f"MERGE (n:Entity:{label} {{nid: $nid}}) "
            "SET n.key = $key, n.tenant_id = $tenant, n.label = $label, n.name = $name"
        )
        self.run(
            cypher,
            {
                "nid": nid,
                "key": key,
                "tenant": settings.tenant_id,
                "label": label,
                "name": name,
            },
        )
        extra = _flat_props(properties or {})
        if extra:
            self.run(
                "MATCH (n:Entity {nid: $nid}) SET n += $props",
                {"nid": nid, "props": extra},
            )

    def merge_edge(self, rel_type: str, src_nid: str, dst_nid: str, confidence: float = 1.0) -> None:
        if not self.ping():
            return
        rel_type = _rel(rel_type)
        cypher = (
            "MATCH (a:Entity {nid: $src}), (b:Entity {nid: $dst}) "
            f"MERGE (a)-[r:{rel_type}]->(b) SET r.confidence = $conf"
        )
        self.run(cypher, {"src": src_nid, "dst": dst_nid, "conf": confidence})

    def bulk_sync(self, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, int]:
        self.ensure_schema()
        if not self.ping():
            logger.warning("Neo4j bulk sync skipped: %s", self.last_error)
            return {"nodes": 0, "edges": 0, "error": 1}
        by_label: dict[str, list[dict]] = {}
        for n in nodes:
            lab = n.get("label")
            if lab not in NODE_TYPES:
                continue
            props = n.get("properties") or {}
            if isinstance(props, str):
                try:
                    props = json.loads(props)
                except (TypeError, json.JSONDecodeError):
                    props = {}
            by_label.setdefault(lab, []).append({
                "nid": n["node_id"],
                "key": n["key"],
                "name": n.get("name") or n["key"],
                "label": lab,
                "tenant": n.get("tenant_id") or settings.tenant_id,
                "props": _flat_props(props if isinstance(props, dict) else {}),
            })
        ncount = 0
        try:
            for lab, rows in by_label.items():
                lab = _label(lab)
                cypher = (
                    "UNWIND $rows AS row "
                    f"MERGE (n:Entity:{lab} {{nid: row.nid}}) "
                    "SET n.key = row.key, n.tenant_id = row.tenant, n.label = row.label, n.name = row.name, n += row.props"
                )
                self.run(cypher, {"rows": rows})
                ncount += len(rows)
            ecount = 0
            by_rel: dict[str, list[dict]] = {}
            for e in edges:
                rt = e.get("rel_type")
                if rt not in REL_TYPES:
                    continue
                by_rel.setdefault(rt, []).append({
                    "src": e["src_id"],
                    "dst": e["dst_id"],
                    "conf": float(e.get("confidence") or 1.0),
                })
            for rt, rows in by_rel.items():
                rt = _rel(rt)
                cypher = (
                    "UNWIND $rows AS row "
                    "MATCH (a:Entity {nid: row.src}), (b:Entity {nid: row.dst}) "
                    f"MERGE (a)-[r:{rt}]->(b) SET r.confidence = row.conf"
                )
                self.run(cypher, {"rows": rows})
                ecount += len(rows)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Neo4j bulk sync failed: %s", exc)
            return {"nodes": ncount, "edges": 0, "error": 1}
        logger.info("Neo4j bulk sync nodes=%s edges=%s", ncount, ecount)
        return {"nodes": ncount, "edges": ecount}

    def traverse(self, anchor_keys: list[str], hops: int = 2) -> list[dict[str, Any]] | None:
        """Return [] when Neo4j is up but empty; None when Neo4j is down."""
        if not self.ping():
            return None
        pattern = _HOP_PATTERNS.get(max(1, min(3, hops)), "*1..2")
        cypher = (
            "MATCH (a:Entity {tenant_id: $tenant}) "
            "WHERE a.key IN $keys "
            f"MATCH p = (a)-[{pattern}]-(b:Entity {{tenant_id: $tenant}}) "
            "UNWIND relationships(p) AS r "
            "WITH DISTINCT r, startNode(r) AS s, endNode(r) AS d "
            "RETURN type(r) AS rel_type, "
            "s.label AS src_label, s.key AS src_key, s.name AS src_name, "
            "d.label AS dst_label, d.key AS dst_key, d.name AS dst_name, "
            "s.nid AS src_id, d.nid AS dst_id "
            "LIMIT 200"
        )
        try:
            return self.run(cypher, {"tenant": settings.tenant_id, "keys": list(anchor_keys)})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Neo4j traverse failed: %s", exc)
            return None

    def similar_incidents(self, key: str) -> list[dict[str, Any]] | None:
        if not self.ping():
            return None
        cypher = (
            "MATCH (anchor:Entity {tenant_id: $tenant, key: $key}) "
            "MATCH (anchor)-[*1..2]-(i:Incident {tenant_id: $tenant}) "
            "OPTIONAL MATCH (i)-[:RESOLVED_BY]->(d:Decision) "
            "OPTIONAL MATCH (i)-[:BREACHES]->(pol) "
            "RETURN DISTINCT i.key AS incident_key, i.name AS incident, "
            "i.recovery AS recovery, i.effective AS effective, i.root_cause AS root_cause, "
            "i.slip_days AS slip_days, "
            "d.key AS decision_key, d.name AS decision, d.action AS action, "
            "pol.key AS policy_key "
            "LIMIT 20"
        )
        try:
            return self.run(cypher, {"tenant": settings.tenant_id, "key": key})
        except Exception as exc:  # noqa: BLE001
            logger.warning("similar_incidents failed: %s", exc)
            return None

    def get_entity(self, entity_id: str) -> dict[str, Any] | None:
        if not self.ping():
            return None
        cypher = (
            "MATCH (n:Entity {tenant_id: $tenant}) "
            "WHERE n.nid = $id OR n.key = $id "
            "WITH n LIMIT 1 "
            "OPTIONAL MATCH (n)-[r]->(m:Entity) "
            "WITH n, collect({rel_type: type(r), node_id: m.nid, label: m.label, key: m.key, name: m.name}) AS outgoing "
            "OPTIONAL MATCH (p:Entity)-[r2]->(n) "
            "RETURN n.nid AS node_id, n.tenant_id AS tenant_id, n.label AS label, n.key AS key, "
            "n.name AS name, outgoing, "
            "collect({rel_type: type(r2), node_id: p.nid, label: p.label, key: p.key, name: p.name}) AS incoming"
        )
        try:
            rows = self.run(cypher, {"tenant": settings.tenant_id, "id": entity_id})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Neo4j get_entity failed: %s", exc)
            return None
        if not rows:
            return None
        row = rows[0]
        row["outgoing"] = [x for x in (row.get("outgoing") or []) if x.get("key")]
        row["incoming"] = [x for x in (row.get("incoming") or []) if x.get("key")]
        row["source"] = "neo4j"
        return row

    def fetch_chunks(self, tenant_id: str) -> list[dict[str, Any]] | None:
        """Document chunks stored on :Chunk nodes. None if Neo4j is down."""
        if not self.ping():
            return None
        cypher = (
            "MATCH (c:Chunk {tenant_id: $tenant}) "
            "OPTIONAL MATCH (d:Document {tenant_id: $tenant})-[:CONTAINS]->(c) "
            "RETURN c.key AS chunk_id, coalesce(c.document_id, d.key) AS document_id, "
            "c.text AS text, c.heading AS heading, c.anchor AS anchor, "
            "c.embedding AS embedding, "
            "coalesce(d.name, c.title) AS title, "
            "coalesce(d.source_uri, c.source_uri) AS source_uri, "
            "coalesce(d.current, true) AS current"
        )
        try:
            rows = self.run(cypher, {"tenant": tenant_id})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Neo4j fetch_chunks failed: %s", exc)
            return None
        out = []
        for row in rows:
            if row.get("current") is False:
                continue
            out.append(row)
        return out


def _flat_props(props: dict) -> dict[str, Any]:
    out = {}
    for k, v in (props or {}).items():
        if v is None or isinstance(v, (str, int, float, bool)):
            out[str(k)] = v
        elif isinstance(v, list) and v and all(isinstance(x, (int, float)) for x in v):
            out[str(k)] = [float(x) for x in v]
        else:
            out[str(k)] = str(v)
    return out


client = Neo4jClient()
