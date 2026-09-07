"""Machine-verifiable product quality gates for NEWSDESK.

The scorecard deliberately reports evidence and boundaries instead of collapsing
everything into a marketing score.  A failed gate remains visible to operators.
"""
import time
import json
import hashlib

from . import claim_evaluation, evaluation, store


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
    ai_primary_owners = {s.get("owner") or s.get("group") or s["id"]
                         for s in ai_primary_sources}
    active_ai_primary = [s for s in ai_primary_sources if s["id"] in health and
                         health[s["id"]].get("verdict") == "healthy" and
                         int(health[s["id"]].get("newest_ts") or 0) >= now - 30 * 86400]
    ai_reporting_sources = [s for s in ai_sources
                            if s.get("source_role") in ("reporting", "wire")]
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
    claim_count = conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
    cited_claims = conn.execute(
        "SELECT COUNT(*) FROM claims c WHERE EXISTS "
        "(SELECT 1 FROM claim_evidence ce WHERE ce.claim_id=c.id)").fetchone()[0]
    related_evidence = conn.execute(
        "SELECT COUNT(*) FROM claim_evidence WHERE relation IN ('support','refute','unknown') "
        "AND relation_method!='legacy-unclassified'"
    ).fetchone()[0]
    evidence_count = conn.execute("SELECT COUNT(*) FROM claim_evidence").fetchone()[0]
    disputed_claims = conn.execute(
        "SELECT COUNT(*) FROM claims WHERE status='disputed'").fetchone()[0]
    claims_without_support = conn.execute(
        "SELECT COUNT(*) FROM claims c WHERE NOT EXISTS (SELECT 1 FROM claim_evidence ce "
        "WHERE ce.claim_id=c.id AND ce.relation='support')").fetchone()[0]
    priority_relation_total = conn.execute(
        "SELECT COUNT(*) FROM claim_evidence ce JOIN claims cl ON cl.id=ce.claim_id "
        "JOIN clusters c ON c.id=cl.cluster_id WHERE c.cred>=60 AND c.last_ts>=?",
        (now - 72 * 3600,)).fetchone()[0]
    priority_relation_classified = conn.execute(
        "SELECT COUNT(*) FROM claim_evidence ce JOIN claims cl ON cl.id=ce.claim_id "
        "JOIN clusters c ON c.id=cl.cluster_id WHERE c.cred>=60 AND c.last_ts>=? "
        "AND ce.relation_method!='legacy-unclassified'", (now - 72 * 3600,)).fetchone()[0]
    # 详情页『主要内容』的原料：只统计近 72h 尝试过抽取的稿件，抽不到的会退回
    # feed 摘要，所以这是 advisory —— 覆盖率下滑说明多家站点改版了，不是故障。
    body_attempted = conn.execute(
        "SELECT COUNT(*) FROM items WHERE body_ts>=?", (now - 72 * 3600,)).fetchone()[0]
    body_ok = conn.execute(
        "SELECT COUNT(*) FROM items WHERE body_ts>=? AND body_state='ok'",
        (now - 72 * 3600,)).fetchone()[0]
    orphan_claim_evidence = conn.execute(
        "SELECT COUNT(*) FROM claim_evidence ce WHERE NOT EXISTS "
        "(SELECT 1 FROM claims c WHERE c.id=ce.claim_id) OR NOT EXISTS "
        "(SELECT 1 FROM items i WHERE i.id=ce.item_id)").fetchone()[0]
    quote_rows = list(conn.execute(
        "SELECT ce.quote,ce.quote_field,ce.quote_start,ce.quote_end,ce.quote_hash,"
        "i.title,i.summary FROM claim_evidence ce JOIN items i ON i.id=ce.item_id"))
    exact_quotes = 0
    for row in quote_rows:
        source_text = str(row[row["quote_field"]] or "") \
            if row["quote_field"] in ("title", "summary") else ""
        quote = str(row["quote"] or "")
        if (source_text[row["quote_start"]:row["quote_end"]] == quote and
                hashlib.sha256(quote.encode()).hexdigest()[:16] == row["quote_hash"]):
            exact_quotes += 1
    priority_clusters = conn.execute(
        "SELECT COUNT(*) FROM clusters WHERE cred>=60 AND last_ts>=?", (now - 72 * 3600,)
    ).fetchone()[0]
    priority_with_claims = conn.execute(
        "SELECT COUNT(*) FROM clusters c WHERE c.cred>=60 AND c.last_ts>=? AND EXISTS "
        "(SELECT 1 FROM claims cl WHERE cl.cluster_id=c.id)", (now - 72 * 3600,)
    ).fetchone()[0]
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
    try:
        human_gold = claim_evaluation.validate_dataset(store.config.DATA_DIR / "claim-eval")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        human_gold = {"valid": False, "human_adjudicated": False, "detail": str(exc)}

    gates = [
        _gate("enabled_sources", len(enabled), ">=20", len(enabled) >= 20),
        _gate("source_owners", len(owners), ">=15", len(owners) >= 15),
        _gate("source_languages", len(languages), ">=3", len(languages) >= 3),
        _gate("source_regions", len(regions), ">=4", len(regions) >= 4),
        _gate("ai_specialist_sources", len(ai_sources), ">=20", len(ai_sources) >= 20),
        _gate("ai_primary_sources", len(ai_primary_sources), ">=10",
              len(ai_primary_sources) >= 10),
        _gate("ai_primary_sources_target", len(ai_primary_sources), ">=20",
              len(ai_primary_sources) >= 20, "advisory first-party depth target",
              severity="advisory"),
        _gate("active_ai_primary_sources_30d", len(active_ai_primary), ">=15",
              len(active_ai_primary) >= 15,
              "must be healthy and publish within the last 30 days"),
        _gate("ai_primary_owners", len(ai_primary_owners), ">=15",
              len(ai_primary_owners) >= 15,
              "duplicate channels from one owner count once"),
        _gate("ai_reporting_sources", len(ai_reporting_sources), ">=15",
              len(ai_reporting_sources) >= 15),
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
        _gate("persisted_claims", claim_count, ">=1", claim_count >= 1),
        _gate("claim_citation_coverage", round(cited_claims / claim_count, 4)
              if claim_count else 0, ">=0.95",
              bool(claim_count) and cited_claims / claim_count >= .95,
              f"{cited_claims}/{claim_count} claims have exact source quotes"),
        _gate("claim_relation_coverage", round(related_evidence / evidence_count, 4)
              if evidence_count else 0, ">=0.80",
              bool(evidence_count) and related_evidence / evidence_count >= .80,
              f"{related_evidence}/{evidence_count} citations classified; legacy unknown excluded",
              severity="advisory"),
        _gate("priority_claim_relation_coverage", round(
            priority_relation_classified / priority_relation_total, 4)
              if priority_relation_total else 0, ">=0.95",
              bool(priority_relation_total) and
              priority_relation_classified / priority_relation_total >= .95,
              f"{priority_relation_classified}/{priority_relation_total} priority citations classified"),
        _gate("claims_with_supporting_quote", claim_count - claims_without_support,
              f"={claim_count}", claims_without_support == 0,
              "each claim must retain at least one supporting source quote"),
        _gate("claim_evidence_integrity", exact_quotes, f"={len(quote_rows)}",
              bool(quote_rows) and exact_quotes == len(quote_rows),
              "quote offsets and hashes must match the current stored item"),
        _gate("orphan_claim_evidence", orphan_claim_evidence, "=0",
              orphan_claim_evidence == 0),
        _gate("body_preview_coverage_72h",
              round(body_ok / body_attempted, 4) if body_attempted else 0, ">=0.70",
              bool(body_attempted) and body_ok / body_attempted >= .70,
              f"{body_ok}/{body_attempted} lead articles yielded a body preview; "
              "misses fall back to the feed summary",
              severity="advisory"),
        _gate("priority_cluster_claim_coverage", round(
            priority_with_claims / priority_clusters, 4) if priority_clusters else 0,
              ">=0.95", bool(priority_clusters) and
              priority_with_claims / priority_clusters >= .95,
              f"{priority_with_claims}/{priority_clusters} high-priority 72h clusters"),
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
        _gate("human_adjudicated_gold", bool(human_gold.get("human_adjudicated")),
              "true", bool(human_gold.get("valid") and human_gold.get("human_adjudicated")),
              human_gold.get("detail", "validated independent annotation and adjudication"),
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
        "claim_evidence": {
            "claims": claim_count, "cited_claims": cited_claims,
            "citation_coverage": round(cited_claims / claim_count, 4) if claim_count else 0,
            "priority_clusters": priority_clusters,
            "priority_clusters_with_claims": priority_with_claims,
            "relation_labeled_citations": related_evidence,
            "legacy_unclassified_citations": evidence_count - related_evidence,
            "priority_relation_coverage": round(
                priority_relation_classified / priority_relation_total, 4)
                if priority_relation_total else 0,
            "disputed_claims": disputed_claims,
            "exact_quotes": exact_quotes,
            "orphan_evidence": orphan_claim_evidence,
        },
        "ai_intelligence": {"sources": len(ai_sources),
                            "primary_sources": len(ai_primary_sources),
                            "active_primary_sources_30d": len(active_ai_primary),
                            "primary_owners": len(ai_primary_owners),
                            "reporting_sources": len(ai_reporting_sources),
                            "events_72h": len(ai_clusters),
                            "independently_corroborated": ai_corroborated},
        "recovery": recovery,
        "clustering_benchmark": {**clustering, "dataset": "synthetic-reviewed-v1",
                                 "human_adjudicated": False},
        "claim_relation_benchmark": human_gold,
        "boundaries": [
            "No licensed Bloomberg/Reuters/AP proprietary content",
            "Public delayed market context; not suitable for trade execution",
            "No order routing, regulated messaging, or compliance workflow",
        ],
    }
