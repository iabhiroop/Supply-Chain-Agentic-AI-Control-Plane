"""Connected-component community summaries for global questions."""

from __future__ import annotations

import uuid

from graphrag.config import settings
from graphrag.db import fetch_all, get_conn


def rebuild_communities() -> int:
    nodes = fetch_all("SELECT node_id, label, key, name FROM nodes WHERE tenant_id = %s", (settings.tenant_id,))
    edges = fetch_all("SELECT src_id, dst_id FROM edges WHERE tenant_id = %s", (settings.tenant_id,))
    parent = {n["node_id"]: n["node_id"] for n in nodes}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for e in edges:
        if e["src_id"] in parent and e["dst_id"] in parent:
            union(e["src_id"], e["dst_id"])

    groups: dict[str, list[dict]] = {}
    for n in nodes:
        groups.setdefault(find(n["node_id"]), []).append(n)

    with get_conn() as conn:
        conn.execute("DELETE FROM communities WHERE tenant_id = %s", (settings.tenant_id,))
        count = 0
        for members in groups.values():
            if len(members) < 2:
                continue
            labels = sorted({m["label"] for m in members})
            names = [m.get("name") or m["key"] for m in members[:12]]
            summary = (
                f"Community of {len(members)} entities ({', '.join(labels)}). "
                f"Includes {', '.join(names)}."
            )
            cid = str(uuid.uuid5(uuid.NAMESPACE_URL, settings.tenant_id + find(members[0]["node_id"])))
            member_ids = [m["node_id"] for m in members]
            conn.execute(
                """
                INSERT INTO communities (community_id, tenant_id, level, summary, member_ids)
                VALUES (%s,%s,0,%s,%s)
                """,
                (cid, settings.tenant_id, summary, member_ids),
            )
            count += 1
    return count
