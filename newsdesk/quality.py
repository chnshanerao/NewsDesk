"""Machine-verifiable product quality gates for NEWSDESK.

The scorecard deliberately reports evidence and boundaries instead of collapsing
everything into a marketing score.  A failed gate remains visible to operators.
"""
import time
import json

from . import evaluation, store


def _gate(name, value, target, passed, detail="", severity="required"):
    return {"name": name, "value": value, "target": target,
            "status": "pass" if passed else ("fail" if severity == "required" else "warn"),
            "detail": detail}


def scorecard(conn, registry: dict, now: int | None = None) -> dict:
    now = int(now or time.time())
    enabled = [s for s in registry.get("sources", []) if s.get("enabled", True)]
    languages = {s.get("lang", "zh") for s in enabled}
    regions = {s.get("region", "cn" if s.get("lang", "zh") == "zh" else "global")
               for s in enabled}
    owners = {s.get("owner") or s.get("group") or s["id"] for s in enabled}
    health = {r["source_id"]: dict(r) for r in store.health(conn)}
    observed = [health[s["id"]] for s in enabled if s["id"] in health]
    healthy = [r for r in observed if r.get("verdict") == "healthy"]
    healthy_rate = len(healthy) / len(enabled) if enabled else 0
    probes = list(conn.execute("SELECT verdict FROM source_probes WHERE ts>=?", (now - 86400,)))
    probe_success = (sum(x["verdict"] == "healthy" for x in probes) / len(probes)
                     if probes else 0)

    day = now - 86400
    lang_counts = {r["lang"] or "unknown": r["n"] for r in conn.execute(
        "SELECT lang,COUNT(*) n FROM items WHERE published_ts>=? GROUP BY lang", (day,))}
    recent_items = sum(lang_counts.values())
    international = sum(n for lang, n in lang_counts.items() if lang != "zh")
    international_share = international / recent_items if recent_items else 0
    source_counts = {r["source_id"]: r["n"] for r in conn.execute(
        "SELECT source_id,COUNT(*) n FROM items WHERE published_ts>=? GROUP BY source_id",
        (day,))}
    owner_counts = {}
    source_owner = {s["id"]: s.get("owner") or s.get("group") or s["id"] for s in enabled}
    for source_id, count in source_counts.items():
        owner = source_owner.get(source_id, source_id)
        owner_counts[owner] = owner_counts.get(owner, 0) + count
    largest_owner_share = (max(owner_counts.values(), default=0) / recent_items
                           if recent_items else 0)
    recent_clusters = conn.execute(
        "SELECT COUNT(*) FROM clusters WHERE last_ts>=?", (day,)).fetchone()[0]
    corroborated = conn.execute(
        "SELECT COUNT(*) FROM clusters WHERE last_ts>=? AND n_groups>=2", (day,)).fetchone()[0]
    ai_sources = [s for s in enabled
                  if any(t.startswith("ai_") for t in s.get("topics", []))]
    ai_primary_sources = [s for s in ai_sources
                          if s.get("source_role") == "official"]
    ai_since = now - 72 * 3600
    ai_clusters = []
    for row in conn.execute("SELECT n_groups,topics FROM clusters WHERE last_ts>=?", (ai_since,)):
        if any(t.startswith("ai_") for t in json.loads(row["topics"] or "[]")):
            ai_clusters.append(row)
    ai_corroborated = sum(row["n_groups"] >= 2 for row in ai_clusters)
    orphan = conn.execute(
        "SELECT COUNT(*) FROM clusters c WHERE NOT EXISTS "
        "(SELECT 1 FROM items i WHERE i.cluster_id=c.id)").fetchone()[0]
    dangling_items = conn.execute(
        "SELECT COUNT(*) FROM items i WHERE i.cluster_id IS NOT NULL AND NOT EXISTS "
        "(SELECT 1 FROM clusters c WHERE c.id=i.cluster_id)").fetchone()[0]

    market = conn.execute(
        "SELECT COUNT(DISTINCT symbol) instruments,COUNT(DISTINCT asset) classes,MAX(ts) newest "
        "FROM market_ticks").fetchone()
    market_span = conn.execute("SELECT COALESCE(MAX(ts)-MIN(ts),0) FROM market_ticks").fetchone()[0]
    schema = store.schema_status(conn)
    entity_count = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    company_count = conn.execute("SELECT COUNT(*) FROM entities WHERE kind='company'").fetchone()[0]
    entity_kind_counts = {row["kind"]: row["n"] for row in conn.execute(
        "SELECT kind,COUNT(*) n FROM entities GROUP BY kind")}
    entity_links = conn.execute("SELECT COUNT(*) FROM cluster_entities").fetchone()[0]
    fts_count = conn.execute("SELECT COUNT(*) FROM items_fts").fetchone()[0]
    item_count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    last_run = store.last_run(conn)
    last_run_age = now - last_run["ended_ts"] if last_run and last_run["ended_ts"] else None
    content_newest = conn.execute("SELECT MAX(published_ts) FROM items").fetchone()[0]
    content_age = now - content_newest if content_newest else None
    backups = list(store.config.BACKUP_DIR.glob("newsdesk-*.db"))
    newest_backup_age = (now - int(max(p.stat().st_mtime for p in backups))
                         if backups else None)
    recovery_path = store.config.DATA_DIR / "recovery-status.json"
    try:
        recovery = json.loads(recovery_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        recovery = {}
    recovery_age = now - int(recovery.get("verified_ts", 0)) if recovery else None
    gold_items, gold_pairs = evaluation.load_gold(
        store.config.DATA_DIR / "crosslingual_gold.json")
    clustering = evaluation.evaluate(gold_items, gold_pairs)

    gates = [
        _gate("enabled_sources", len(enabled), ">=20", len(enabled) >= 20),
        _gate("source_owners", len(owners), ">=15", len(owners) >= 15),
        _gate("source_languages", len(languages), ">=3", len(languages) >= 3),
        _gate("source_regions", len(regions), ">=4", len(regions) >= 4),
        _gate("ai_specialist_sources", len(ai_sources), ">=10", len(ai_sources) >= 10),
        _gate("ai_primary_sources", len(ai_primary_sources), ">=6",
              len(ai_primary_sources) >= 6),
        _gate("healthy_source_rate", round(healthy_rate, 4), ">=0.80",
              healthy_rate >= .8, f"{len(healthy)}/{len(enabled)} enabled sources healthy"),
        _gate("source_probe_success_24h", round(probe_success, 4), ">=0.80",
              probe_success >= .8, f"{len(probes)} probes observed"),
        _gate("international_items_24h_share", round(international_share, 4), ">=0.10",
              recent_items > 0 and international_share >= .10,
              f"{international}/{recent_items} recent items"),
        _gate("global_depth_target", round(international_share, 4), ">=0.20",
              international_share >= .20, "advisory Bloomberg-like coverage target",
              severity="advisory"),
        _gate("largest_owner_share_24h", round(largest_owner_share, 4), "<=0.35",
              largest_owner_share <= .35, "advisory concentration target",
              severity="advisory"),
        _gate("independently_corroborated_24h", corroborated, ">=1", corroborated >= 1,
              f"out of {recent_clusters} recent clusters"),
        _gate("independent_corroboration_rate", round(
            corroborated / recent_clusters, 4) if recent_clusters else 0, ">=0.10",
              bool(recent_clusters) and corroborated / recent_clusters >= .10,
              "advisory quality target", severity="advisory"),
        _gate("ai_events_72h", len(ai_clusters), ">=20", len(ai_clusters) >= 20,
              "AI research/model/compute/open-source/governance/industry taxonomy"),
        _gate("ai_independent_corroboration_rate", round(
            ai_corroborated / len(ai_clusters), 4) if ai_clusters else 0, ">=0.10",
              bool(ai_clusters) and ai_corroborated / len(ai_clusters) >= .10,
              "advisory AI intelligence depth target", severity="advisory"),
        _gate("orphan_clusters", orphan, "=0", orphan == 0),
        _gate("dangling_cluster_references", dangling_items, "=0", dangling_items == 0),
        _gate("market_instruments", market["instruments"], ">=10",
              market["instruments"] >= 10),
        _gate("market_asset_classes", market["classes"], ">=4", market["classes"] >= 4),
        _gate("market_history_span_seconds", market_span, ">=604800",
              market_span >= 604800, "history accumulates over wall-clock time",
              severity="advisory"),
        _gate("canonical_entities", entity_count, ">=20", entity_count >= 20),
        _gate("company_security_master_depth", company_count, ">=100",
              company_count >= 100, "advisory coverage target", severity="advisory"),
        _gate("ai_model_entities", entity_kind_counts.get("model", 0), ">=5",
              entity_kind_counts.get("model", 0) >= 5),
        _gate("ai_lab_entities", entity_kind_counts.get("lab", 0), ">=5",
              entity_kind_counts.get("lab", 0) >= 5),
        _gate("ai_chip_entities", entity_kind_counts.get("chip", 0), ">=5",
              entity_kind_counts.get("chip", 0) >= 5),
        _gate("open_source_entities", entity_kind_counts.get("open_source", 0), ">=5",
              entity_kind_counts.get("open_source", 0) >= 5),
        _gate("persisted_entity_links", entity_links, ">=1", entity_links >= 1),
        _gate("fulltext_index_coverage", fts_count, f"={item_count}", fts_count == item_count),
        _gate("schema_version", schema["current"], f"={schema['expected']}",
              schema["current"] == schema["expected"]),
        _gate("successful_pipeline_run", bool(last_run and last_run["ended_ts"]), "true",
              bool(last_run and last_run["ended_ts"])),
        _gate("pipeline_freshness_seconds", last_run_age if last_run_age is not None else -1,
              f"<={max(1800, store.config.AUTO_REFRESH_SECONDS * 2)}",
              last_run_age is not None and
              last_run_age <= max(1800, store.config.AUTO_REFRESH_SECONDS * 2)),
        _gate("content_freshness_seconds", content_age if content_age is not None else -1,
              "<=86400", content_age is not None and content_age <= 86400),
        _gate("verified_backups_present", len(backups), ">=1", bool(backups)),
        _gate("newest_backup_age_seconds",
              newest_backup_age if newest_backup_age is not None else -1, "<=172800",
              newest_backup_age is not None and newest_backup_age <= 172800),
        _gate("recovery_drill_age_seconds",
              recovery_age if recovery_age is not None else -1, "<=604800",
              bool(recovery.get("ok")) and recovery_age is not None and recovery_age <= 604800),
        _gate("clustering_pair_precision", clustering["pair_precision"], ">=0.95",
              clustering["pair_precision"] >= .95),
        _gate("clustering_pair_recall", clustering["pair_recall"], ">=0.80",
              clustering["pair_recall"] >= .80),
        _gate("clustering_bcubed_f1", clustering["bcubed_f1"], ">=0.88",
              clustering["bcubed_f1"] >= .88),
        _gate("human_adjudicated_gold", False, "true", False,
              "requires independent human annotation and adjudication",
              severity="advisory"),
    ]
    counts = {s: sum(g["status"] == s for g in gates) for s in ("pass", "warn", "fail")}
    return {
        "status": ("degraded" if counts["fail"] else
                   "healthy_with_warnings" if counts["warn"] else "healthy"),
        "generated_ts": now,
        "counts": counts,
        "gates": gates,
        "coverage": {"languages": sorted(languages), "regions": sorted(regions),
                     "owners": len(owners), "items_24h_by_language": lang_counts},
        "market_data": {"delayed": True, "execution_grade": False,
                        "newest_ts": market["newest"]},
        "entity_master": {"entities": entity_count, "companies": company_count,
                          "persisted_links": entity_links,
                          "fulltext_rows": fts_count},
        "ai_intelligence": {"sources": len(ai_sources),
                            "primary_sources": len(ai_primary_sources),
                            "events_72h": len(ai_clusters),
                            "independently_corroborated": ai_corroborated},
        "recovery": recovery,
        "clustering_benchmark": {**clustering, "dataset": "synthetic-reviewed-v1",
                                 "human_adjudicated": False},
        "boundaries": [
            "No licensed Bloomberg/Reuters/AP proprietary content",
            "Public delayed market context; not suitable for trade execution",
            "No order routing, regulated messaging, or compliance workflow",
        ],
    }
