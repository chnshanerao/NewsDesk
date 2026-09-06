"""Canonical entity/security master and explainable text linkage."""
import json
import re
import unicodedata
from functools import lru_cache

from . import config


@lru_cache(maxsize=1)
def catalog() -> list[dict]:
    payload = json.loads((config.DATA_DIR / "entities.json").read_text(encoding="utf-8"))
    return payload["entities"]


def _contains(text: str, alias: str) -> bool:
    text = unicodedata.normalize("NFKC", text)
    alias = unicodedata.normalize("NFKC", alias)
    if re.search(r"[A-Za-z]", alias):
        return re.search(r"(?<![A-Za-z0-9])" + re.escape(alias) +
                         r"(?![A-Za-z0-9])", text, re.I) is not None
    return alias in text


def extract(text: str) -> list[dict]:
    links = []
    for entity in catalog():
        hits = []
        seen_hits = set()
        for alias in entity.get("aliases", []):
            if _contains(text or "", alias) and alias.casefold() not in seen_hits:
                hits.append(alias)
                seen_hits.add(alias.casefold())
        folded = (text or "").casefold()
        if entity["id"] == "co_apple" and hits == ["Apple"] and not any(
                x in folded for x in ("iphone", "mac", "company", "stock", "shares",
                                      "earnings", "revenue", "launch", "product", "fined",
                                      "ceo", "tim cook", "cupertino")):
            hits = []
        if entity["id"] == "co_amazon" and any(
                x in folded for x in ("rainforest", "amazon river", "amazon basin")):
            hits = []
        if entity["id"] == "co_meta" and any(
                x in folded for x in ("meta analysis", "meta-analysis")):
            hits = []
        context_terms = entity.get("context_terms", [])
        if hits and context_terms and not any(
                _contains(text or "", term) for term in context_terms):
            hits = []
        if hits:
            links.append({"entity_id": entity["id"], "name": entity["name"],
                          "kind": entity["kind"], "symbol": entity.get("symbol"),
                          "relation_type": "explicit_mention", "confidence": 0.95,
                          "evidence": hits[:3]})
    return links


def sync_catalog(conn) -> None:
    current = catalog()
    wanted = {entity["id"] for entity in current}
    stale = [row[0] for row in conn.execute("SELECT id FROM entities") if row[0] not in wanted]
    if stale:
        marks = ",".join("?" for _ in stale)
        conn.execute(f"DELETE FROM cluster_entities WHERE entity_id IN ({marks})", stale)
        conn.execute(f"DELETE FROM entity_aliases WHERE entity_id IN ({marks})", stale)
        conn.execute(f"DELETE FROM entities WHERE id IN ({marks})", stale)
    for entity in current:
        conn.execute(
            "INSERT INTO entities(id,kind,name,symbol,identifiers) VALUES(?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,name=excluded.name,"
            "symbol=excluded.symbol,identifiers=excluded.identifiers",
            (entity["id"], entity["kind"], entity["name"], entity.get("symbol"),
             json.dumps(entity.get("identifiers", {}), sort_keys=True)))
        conn.execute("DELETE FROM entity_aliases WHERE entity_id=?", (entity["id"],))
        conn.executemany("INSERT INTO entity_aliases(entity_id,alias) VALUES(?,?)",
                         [(entity["id"], a) for a in entity.get("aliases", [])])
    conn.commit()


def link_cluster(conn, cluster_id: str, text: str) -> list[dict]:
    links = extract(text)
    conn.execute("DELETE FROM cluster_entities WHERE cluster_id=?", (cluster_id,))
    conn.executemany(
        "INSERT INTO cluster_entities(cluster_id,entity_id,relation_type,confidence,evidence) "
        "VALUES(?,?,?,?,?)",
        [(cluster_id, x["entity_id"], x["relation_type"], x["confidence"],
          json.dumps(x["evidence"], ensure_ascii=False)) for x in links])
    return links


def cluster_links(conn, cluster_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT ce.entity_id,e.name,e.kind,e.symbol,ce.relation_type,ce.confidence,ce.evidence "
        "FROM cluster_entities ce JOIN entities e ON e.id=ce.entity_id "
        "WHERE ce.cluster_id=? ORDER BY ce.confidence DESC,e.kind,e.name", (cluster_id,))
    out = []
    for row in rows:
        item = dict(row)
        item["evidence"] = json.loads(item["evidence"] or "[]")
        item["matched_terms"] = item["evidence"]
        out.append(item)
    return out
