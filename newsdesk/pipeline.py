"""流水线编排：抓取 → 入库 → 聚类 → 评分 → (LLM 甄别) → 落库。"""
import json
import time

from . import cluster as clustering
from . import alerting, config, credibility, entities, evidence, fetch, store, verify_llm
from .normalize import now_ts


def _health_layers(res: dict, src: dict, now: int) -> dict:
    """Separate reachability, parsing and freshness into explicit states."""
    transport = "ok" if res.get("ok") else "error"
    if transport == "error":
        parse_status, freshness, verdict = "not_attempted", "unknown", "down"
    elif not res.get("n_items"):
        parse_status, freshness, verdict = "empty", "unknown", "unhealthy"
    else:
        parse_status = "ok"
        newest = res.get("newest_ts")
        if newest is None:
            freshness, verdict = "unknown", "degraded"
        else:
            max_age_h = int(src.get("freshness_hours", 72))
            freshness = "fresh" if max(0, now - int(newest)) <= max_age_h * 3600 else "stale"
            verdict = "healthy" if freshness == "fresh" else "degraded"
    return {"transport_status": transport, "parse_status": parse_status,
            "freshness_status": freshness, "verdict": verdict}


def _row_to_item(row) -> dict:
    return {
        "id": row["id"], "source_id": row["source_id"],
        "source_name": row["source_name"], "tier": row["tier"], "grp": row["grp"],
        "title": row["title"], "summary": row["summary"] or "", "url": row["url"] or "",
        "lang": row["lang"], "published_ts": row["published_ts"],
        "fetched_ts": row["fetched_ts"], "simhash": row["simhash"] or 0,
        "grams": set((row["grams"] or "").split()),
        "src_topics": [],
        "src_role": "reporting",
    }


def ingest(conn, reg: dict, only=None, log=print) -> dict:
    results = fetch.fetch_all(reg, only)
    sources = {s["id"]: s for s in reg["sources"]}
    checked_at = now_ts()
    all_items, n_fetched = [], 0
    for res in results:
        res.update(_health_layers(res, sources[res["source_id"]], checked_at))
        n_fetched += res["n_items"]
        new = 0
        if res["ok"] and res["items"]:
            new = store.insert_items(conn, res["items"])
            all_items.extend(res["items"])
        res["n_new"] = new
        store.record_health(conn, res)
        flag = "OK " if res["verdict"] == "healthy" else "ERR"
        log(f"  [{flag}] T{res['tier']} {res['name']:<18} "
            f"{res['n_items']:>3} 条 / 新 {new:>3}  {res['ms']:>5}ms"
            + (f"  ← {res['last_error']}" if res["last_error"] else ""))
    return {"results": results, "n_fetched": n_fetched,
            "n_new": sum(r["n_new"] for r in results), "items": all_items}


def rescore(conn, reg: dict, profile: dict, window_h: int | None = None,
            log=print) -> dict:
    """重建窗口内的事件簇并打分。幂等：可以反复跑。"""
    now = now_ts()
    window_h = window_h or config.CLUSTER_WINDOW_H
    since = now - window_h * 3600
    rows = store.recent_items(conn, since, now + 6 * 3600)
    items = [_row_to_item(r) for r in rows]

    # 信源自带栏目 → item，用于相关性弱先验
    src_topics = {s["id"]: s.get("topics", []) for s in reg["sources"]}
    src_roles = {s["id"]: s.get("source_role", config.source_role(s))
                 for s in reg["sources"]}
    for it in items:
        it["src_topics"] = src_topics.get(it["source_id"], [])
        it["src_role"] = src_roles.get(it["source_id"], "reporting")
        it["source_role"] = it["src_role"]

    log(f"  窗口内稿件 {len(items)} 条（近 {window_h}h）")
    groups = clustering.build(items)
    log(f"  聚成事件 {len(groups)} 个（压缩比 "
        f"{len(items) / max(1, len(groups)):.2f}x）")

    assignments, n_scored, active_ids = [], 0, []
    tier_weight = reg["tier_weight"]
    entities.sync_catalog(conn)
    for cid, members in groups.items():
        c = credibility.score_cluster(members, profile, tier_weight, now)
        c["id"] = cid
        store.upsert_cluster(conn, c)
        entity_text = " ".join(
            [c["headline"], *[m["title"] + " " + (m.get("summary") or "")
                              for m in members]])
        entities.link_cluster(conn, cid, entity_text)
        store.replace_cluster_claims(conn, cid, evidence.claims(members, c.get("llm")))
        active_ids.append(cid)
        assignments.extend((it["id"], cid) for it in members)
        n_scored += 1
    # 在同一事务中替换窗口内的簇，避免重聚类后残留“无成员幽灵事件”。
    conn.execute("UPDATE items SET cluster_id=NULL WHERE published_ts>=? AND published_ts<=?",
                 (since, now + 6 * 3600))
    conn.executemany("UPDATE items SET cluster_id=? WHERE id=?",
                     [(cid, iid) for iid, cid in assignments])
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS active_cluster_ids (id TEXT PRIMARY KEY)")
    conn.execute("DELETE FROM active_cluster_ids")
    conn.executemany("INSERT INTO active_cluster_ids(id) VALUES (?)",
                     [(cid,) for cid in active_ids])
    conn.execute("DELETE FROM clusters WHERE last_ts>=? AND "
                 "id NOT IN (SELECT id FROM active_cluster_ids)", (since,))
    conn.execute("DELETE FROM cluster_entities WHERE cluster_id NOT IN (SELECT id FROM clusters)")
    conn.execute("DELETE FROM claims WHERE cluster_id NOT IN (SELECT id FROM clusters)")
    conn.execute("DELETE FROM claim_evidence WHERE claim_id NOT IN (SELECT id FROM claims) "
                 "OR item_id NOT IN (SELECT id FROM items)")
    conn.execute("UPDATE items SET cluster_id=NULL WHERE cluster_id IS NOT NULL AND "
                 "NOT EXISTS (SELECT 1 FROM clusters c WHERE c.id=items.cluster_id)")
    conn.commit()
    return {"n_items": len(items), "n_clusters": n_scored}


def llm_pass(conn, profile: dict, top_n: int | None = None, log=print,
             source_roles: dict | None = None) -> dict:
    """对排名最高的事件跑 LLM 甄别。已有结果的跳过，省钱。"""
    top_n = top_n or config.LLM_MAX_CLUSTERS
    now = now_ts()
    candidates = conn.execute("SELECT * FROM clusters ORDER BY rank DESC").fetchall()
    rows = []
    for row in candidates:
        cached = json.loads(row["llm"]) if row["llm"] else None
        if cached and cached.get("_model") == config.LLM_MODEL \
                and cached.get("_prompt_version") == verify_llm.PROMPT_VERSION:
            continue
        rows.append(row)
        if len(rows) >= top_n:
            break
    if not rows:
        log("  无需甄别（TOP 事件均已有 LLM 结论）")
        return {"n": 0, "ok": 0, "err": 0}

    pairs = []
    items_by_cluster = {}
    for r in rows:
        c = dict(r)
        c["topics"] = json.loads(r["topics"] or "[]")
        items = [dict(x) for x in store.cluster_items(conn, r["id"])]
        for item in items:
            item["source_role"] = (source_roles or {}).get(item["source_id"], "reporting")
        pairs.append((c, items))
        items_by_cluster[c["id"]] = items

    ok = err = 0
    for c, res, e in verify_llm.judge_many(pairs):
        if res is None:
            err += 1
            log(f"  [LLM ERR] {c['headline'][:34]} ← {e}")
            continue
        cred, code, label, rank, rel = credibility.blend_llm(
            c["cred"], c["relevance"], res, c["last_ts"], now)
        store.save_llm(conn, c["id"], res, cred, code, label, rank)
        store.replace_cluster_claims(
            conn, c["id"], evidence.claims(items_by_cluster[c["id"]], res))
        conn.execute("UPDATE clusters SET relevance=? WHERE id=?", (rel, c["id"]))
        conn.commit()
        ok += 1
        arrow = "↑" if cred > c["cred"] else ("↓" if cred < c["cred"] else "=")
        log(f"  [LLM] {c['cred']:>5.1f}{arrow}{cred:<5.1f} {code:<9} "
            f"{c['headline'][:30]}"
            + (f"  ⚑{len(res.get('red_flags') or [])}" if res.get("red_flags") else ""))
    return {"n": len(pairs), "ok": ok, "err": err}


def run(conn, reg: dict, profile: dict, *, use_llm=False, only=None,
        window_h=None, log=print) -> dict:
    run_id = store.start_run(conn)
    t0 = time.time()
    try:
        log("▸ 抓取信源")
        ing = ingest(conn, reg, only=only, log=log)
        log(f"  合计 {ing['n_fetched']} 条，新增 {ing['n_new']} 条")
        log("▸ 聚类与评分")
        sc = rescore(conn, reg, profile, window_h=window_h, log=log)
        llm = {"n": 0, "ok": 0, "err": 0}
        if use_llm:
            log("▸ LLM 内容甄别")
            source_roles = {s["id"]: s.get("source_role", config.source_role(s))
                            for s in reg["sources"]}
            llm = llm_pass(conn, profile, log=log, source_roles=source_roles)
        alert_results = alerting.evaluate(conn, notify=True)
        alert_new = sum(x["new_count"] for x in alert_results)
        if alert_new:
            log(f"▸ 监控规则命中新事件 {alert_new} 条")
        store.end_run(conn, run_id, n_fetched=ing["n_fetched"], n_new=ing["n_new"],
                      n_clusters=sc["n_clusters"], llm_used=use_llm,
                      note=f"llm_ok={llm['ok']} llm_err={llm['err']} alerts={alert_new}")
        return {**ing, **sc, "llm": llm, "alert_new": alert_new,
                "elapsed": round(time.time() - t0, 1)}
    except Exception as exc:
        store.fail_run(conn, run_id, f"{type(exc).__name__}: {exc}")
        raise
