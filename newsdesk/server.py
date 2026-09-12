"""终端服务：静态页面 + JSON API。标准库 http.server，零依赖。"""
import json
import logging
import math
import mimetypes
import os
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import (alerting, config, digest, entities, evidence, markets, movements, ops,
               pipeline, quality, research, search, source_scores, store)
from .normalize import now_ts

_refresh_lock = threading.Lock()
_refresh_state = {"running": False, "started": 0, "finished": 0, "log": [], "error": None}
_ops_state = {"refresh_skipped": 0, "backup_last_success": 0,
              "backup_last_failure": 0}
_server_started = now_ts()
_logger = ops.get_logger("newsdesk.server")


def _capture_markets(conn) -> int:
    """Persist a best-effort public market snapshot without failing news refresh."""
    try:
        snap = markets.snapshot()
        count = store.record_market_snapshot(conn, snap)
        cutoff = now_ts() - max(1, config.MARKET_RETENTION_DAYS) * 86400
        pruned = store.prune_market_history(conn, cutoff)
        ops.event(_logger, "market_snapshot_recorded", instruments=count, pruned=pruned,
                  asof=snap.get("asof"))
        return count
    except Exception as exc:
        ops.event(_logger, "market_snapshot_failed", str(exc), level=logging.WARNING,
                  error_type=type(exc).__name__)
        return 0


def _start_refresh(reg: dict, profile: dict, use_llm: bool) -> bool:
    if not _refresh_lock.acquire(blocking=False):
        return False
    _refresh_state.update(running=True, started=now_ts(), log=[], error=None)

    def work():
        conn = store.connect()
        try:
            def log(message):
                _refresh_state["log"].append(message)
                del _refresh_state["log"][:-500]
                ops.event(_logger, "refresh_progress", message)
            pipeline.run(conn, reg, profile, use_llm=use_llm, log=log)
            _capture_markets(conn)
        except Exception as exc:
            _refresh_state["error"] = f"{type(exc).__name__}: {exc}"
            ops.event(_logger, "refresh_failed", _refresh_state["error"],
                      level=logging.ERROR, error_type=type(exc).__name__)
        finally:
            conn.close()
            _refresh_state.update(running=False, finished=now_ts())
            _refresh_lock.release()

    threading.Thread(target=work, daemon=True, name="newsdesk-refresh").start()
    return True


def _source_metadata(src: dict) -> dict:
    """Return normalized provenance metadata, with conservative defaults."""
    lang = src.get("lang", "zh")
    region = src.get("region", "cn" if lang == "zh" else "global")
    owner = src.get("owner") or src.get("group") or src["id"]
    country = src.get("country") or ({"cn": "CN", "us": "US", "eu": "EU"}.get(region)
                                      or region.upper())
    source_type = src.get("source_type")
    role = src.get("editorial_role")
    if not source_type:
        if int(src.get("tier", 3)) == 0 and owner not in ("reuters", "xinhua"):
            source_type = "official"
        elif int(src.get("tier", 3)) <= 1:
            source_type = "newsroom"
        elif int(src.get("tier", 3)) == 2:
            source_type = "specialist"
        else:
            source_type = "aggregator"
    if not role:
        role = {"official": "primary_source", "newsroom": "reporting",
                "specialist": "analysis", "aggregator": "aggregation"}.get(
                    source_type, "reporting")
    return {"owner": owner, "country": country, "region": region,
            "source_type": source_type, "editorial_role": role}


def _concentration(counts: dict[str, int]) -> dict:
    total = sum(counts.values())
    shares = {k: v / total for k, v in counts.items()} if total else {}
    return {"counts": counts, "total": total,
            "largest_share": round(max(shares.values(), default=0.0), 4),
            "hhi": round(sum(v * v for v in shares.values()), 4)}


def _owner_capped(items: list[dict], limit: int, share: float = 0.30) -> list[dict]:
    """Stable owner-aware ranking; gracefully relax the cap when owners are scarce."""
    if not items or limit <= 0:
        return []
    available = {}
    for item in items:
        owner = item.get("owner") or "unknown"
        available[owner] = available.get(owner, 0) + 1
    target = 0
    import math
    target = min(limit, len(items))
    owner_n = max(1, min(len(available), target))
    cap = max(1, math.ceil(target * share), math.ceil(target / owner_n))
    while sum(min(n, cap) for n in available.values()) < target:
        cap += 1
    out, counts = [], {}
    for item in items:
        owner = item.get("owner") or "unknown"
        if counts.get(owner, 0) >= cap:
            continue
        out.append(item)
        counts[owner] = counts.get(owner, 0) + 1
        if len(out) >= target:
            break
    return out


def _json_cluster(row) -> dict:
    d = dict(row)
    d["topics"] = json.loads(row["topics"] or "[]")
    d["breakdown"] = json.loads(row["breakdown"] or "{}")
    d["llm"] = json.loads(row["llm"]) if row["llm"] else None
    # 存疑度与可信度并列返回，前端要同时显示两个轴。旧库（迁移前写入的行）没有这几列，
    # 用 keys() 判断而不是 try/except，避免把真正的字段名拼错也一起吞掉。
    keys = row.keys()
    d["doubt"] = float(row["doubt"] or 0) if "doubt" in keys else 0.0
    d["doubt_code"] = (row["doubt_code"] or "CLEAR") if "doubt_code" in keys else "CLEAR"
    d["doubt_detail"] = json.loads(row["doubt_json"] or "{}") if "doubt_json" in keys else {}
    d.pop("doubt_json", None)
    return d


def make_handler(reg: dict, profile: dict, use_llm: bool):
    source_meta = {
        s["id"]: {**s, **_source_metadata(s),
                  "source_role": s.get("source_role", config.source_role(s))}
        for s in reg.get("sources", [])
    }
    # Catalog synchronization is a controlled startup task. Public GET requests
    # remain read-only and can never delete or rewrite person history.
    catalog_conn = store.connect()
    try:
        store.init(catalog_conn)
        movements.sync_catalog(catalog_conn)
        movements.sync_curated(catalog_conn)
    finally:
        catalog_conn.close()
    class Handler(BaseHTTPRequestHandler):
        server_version = "newsdesk/1.0"
        protocol_version = "HTTP/1.1"

        def setup(self):
            super().setup()
            self.request.settimeout(15)

        def log_message(self, fmt, *args):
            if "/api/" in (self.path or ""):
                ops.event(_logger, "http_request", method=self.command,
                          path=urllib.parse.urlsplit(self.path).path,
                          client=self.client_address[0])

        # ---------- helpers ----------
        def _send(self, code, body: bytes, ctype="application/json; charset=utf-8"):
            # A rejected write may leave its request body unread.  Closing the
            # connection prevents those bytes from prefixing a proxy's next request.
            self.close_connection = True
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "default-src 'self'; "
                             "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                             "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; "
                             "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

        def _same_origin_write(self):
            origin = self.headers.get("Origin")
            if origin:
                origin_host = urllib.parse.urlparse(origin).netloc.lower()
                forwarded = self.headers.get("X-Forwarded-Host", "").split(",", 1)[0].strip()
                accepted = {self.headers.get("Host", "").lower(), forwarded.lower()}
                accepted.discard("")
                return origin_host in accepted
            # Non-browser automation without Origin is only trusted over loopback.
            # Public clients must use the same-origin UI (or the refresh bearer token).
            return self.client_address[0] in ("127.0.0.1", "::1")

        def _write_authenticated(self):
            supplied = self.headers.get("X-Newsdesk-Token", "")
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                supplied = auth[7:]
            import hmac
            return bool(config.WRITE_TOKEN and
                        hmac.compare_digest(supplied, config.WRITE_TOKEN))

        def _static(self, rel: str):
            path = (config.WEB_DIR / rel.lstrip("/")).resolve()
            if not path.is_relative_to(config.WEB_DIR.resolve()) or not path.is_file():
                return self._send(404, b"not found", "text/plain; charset=utf-8")
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript",):
                ctype += "; charset=utf-8"
            self._send(200, path.read_bytes(), ctype)

        # ---------- routes ----------
        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
            p = u.path
            try:
                for key in ("limit", "hours", "top"):
                    if key in q:
                        if int(q[key]) <= 0:
                            raise ValueError
                for key in ("min_cred", "min_rel"):
                    if key in q:
                        if not math.isfinite(float(q[key])):
                            raise ValueError
            except (TypeError, ValueError):
                return self._json({"error": "invalid numeric query parameter"}, 400)

            if p == "/" or p == "/index.html":
                return self._static("index.html")
            if p in ("/report", "/report.html"):
                return self._static("benchmark-report.html")
            if p in ("/movement-demo", "/movement-demo.html"):
                return self._static("movement-demo.html")
            if p in ("/movements", "/movements.html"):
                return self._static("movements.html")
            if p.startswith("/static/"):
                return self._static(p[len("/static/"):])
            if p == "/favicon.ico":
                return self._send(204, b"", "image/x-icon")
            if p == "/healthz":
                return self._json({"ok": True, "uptime_s": now_ts() - _server_started})
            if p == "/readyz":
                try:
                    probe = store.connect()
                    probe.execute("SELECT 1").fetchone()
                    probe.close()
                    return self._json({"ready": True})
                except Exception as exc:
                    return self._json({"ready": False, "error": type(exc).__name__}, 503)

            conn = store.connect()
            try:
                if p == "/api/persons":
                    people = movements.list_people(conn)
                    for person in people:
                        person.pop("candidate_count", None)
                    return self._json({"items": people,
                                       "generated_at": now_ts()})

                if p == "/api/movement-themes":
                    rows = [dict(row) for row in conn.execute(
                        "SELECT t.*,COUNT(CASE WHEN me.workflow_status='published' "
                        "AND me.verification_status='verified' THEN mt.movement_id END) movement_count "
                        "FROM movement_theme_catalog t LEFT JOIN movement_themes mt "
                        "ON mt.theme_id=t.id LEFT JOIN movement_events me ON me.id=mt.movement_id "
                        "GROUP BY t.id ORDER BY t.kind,t.name_zh")]
                    return self._json({"items": rows, "generated_at": now_ts()})

                if p == "/api/movement-trends":
                    try:
                        after = int(q["after"]) if q.get("after") else None
                    except ValueError:
                        return self._json({"error": "invalid after"}, 400)
                    return self._json(movements.trend_summary(conn, after=after))

                if p == "/api/movement-monitor-status":
                    return self._json(movements.monitor_status(conn))

                if p == "/api/movement-periods":
                    return self._json(movements.period_counts(conn))

                if p == "/api/movements":
                    try:
                        limit = max(1, min(100, int(q.get("limit", 50))))
                        offset = max(0, int(q.get("offset", 0)))
                    except ValueError:
                        return self._json({"error": "invalid pagination"}, 400)
                    order = q.get("order", "recent")
                    if order not in {"recent", "materiality"}:
                        return self._json({"error": "invalid order"}, 400)
                    try:
                        after = int(q["after"]) if q.get("after") else None
                    except ValueError:
                        return self._json({"error": "invalid after"}, 400)
                    # Anonymous reads are intentionally unable to request drafts,
                    # rejected records or internal review notes.
                    return self._json(movements.list_events(
                        conn, person=q.get("person"), theme=q.get("theme"),
                        action_type=q.get("action_type"), region=q.get("region"),
                        workflow="published", verification="verified",
                        order=order, after=after,
                        limit=limit, offset=offset))

                if p == "/api/admin/movements":
                    if not self._write_authenticated():
                        return self._json({"error": "admin token required"}, 401)
                    try:
                        limit = max(1, min(100, int(q.get("limit", 50))))
                        offset = max(0, int(q.get("offset", 0)))
                    except ValueError:
                        return self._json({"error": "invalid pagination"}, 400)
                    workflow = q.get("workflow", "all")
                    verification = q.get("verification", "all")
                    if workflow not in {"published", "reviewed", "draft", "withdrawn", "all"}:
                        return self._json({"error": "invalid workflow"}, 400)
                    if verification not in {"candidate", "verified", "disputed", "rejected", "all"}:
                        return self._json({"error": "invalid verification"}, 400)
                    return self._json(movements.list_events(
                        conn, person=q.get("person"), theme=q.get("theme"),
                        action_type=q.get("action_type"), region=q.get("region"),
                        workflow=workflow, verification=verification,
                        limit=limit, offset=offset))

                if p.startswith("/api/movement/"):
                    movement_id = p.rsplit("/", 1)[-1]
                    item = movements.get_event(conn, movement_id)
                    if item and not (item["workflow_status"] == "published" and
                                     item["verification_status"] == "verified"):
                        item = None
                    return self._json(item if item else {"error": "not found"},
                                      200 if item else 404)

                if p == "/api/feed":
                    limit = min(300, int(q.get("limit", 80)))
                    parsed = search.parse(q.get("q", ""))
                    hours = parsed["hours"] or int(q.get("hours", 48))
                    view = q.get("view", "signal")
                    min_cred = (parsed["min_cred"] if parsed["min_cred"] is not None
                                else float(q.get("min_cred", 0)))
                    min_rel = float(q.get("min_rel", -1))
                    if min_rel < 0:
                        min_rel = (profile.get("min_relevance", 0.12)
                                   if view == "signal" else -1)
                    wanted_lang = parsed["lang"] or q.get("lang")
                    rows = store.feed(conn, limit=400, min_cred=min_cred,
                                      topic=parsed["topic"] or q.get("topic"),
                                      q=parsed["text"], source=parsed["source"],
                                      asset=parsed["asset"],
                                      lang=wanted_lang,
                                      since_ts=now_ts() - hours * 3600,
                                      order=q.get("order", "rank"))
                    items = [_json_cluster(r) for r in rows]
                    # 事件可能包含多语言报道；批量附加语言，避免逐事件查询。
                    if items:
                        ids = [c["id"] for c in items]
                        headline_sources = {c["id"]: c["headline_src"] for c in items}
                        marks = ",".join("?" for _ in ids)
                        langs = {}
                        provenance = {}
                        for r in conn.execute(
                                f"SELECT cluster_id, source_id, source_name, lang, title, summary FROM items "
                                f"WHERE cluster_id IN ({marks}) GROUP BY cluster_id, source_id, lang", ids):
                            langs.setdefault(r["cluster_id"], []).append(r["lang"] or "unknown")
                            meta = source_meta.get(r["source_id"], {"owner": r["source_id"]})
                            p = provenance.setdefault(r["cluster_id"], {"owners": set(), "lead": None})
                            p["owners"].add(meta["owner"])
                            if r["source_name"] == headline_sources.get(r["cluster_id"]):
                                p["lead"] = meta["owner"]
                        for c in items:
                            c["languages"] = sorted(langs.get(c["id"], []))
                            p = provenance.get(c["id"], {"owners": set(), "lead": None})
                            c["owners"] = sorted(p["owners"])
                            c["owner"] = p["lead"] or (c["owners"][0] if c["owners"] else "unknown")
                            c["entities"] = entities.cluster_links(conn, c["id"])
                            c["assets"] = [x for x in c["entities"] if x.get("symbol")]
                    if view == "signal":
                        items = [c for c in items
                                 if c["relevance"] >= min_rel
                                 and c["cred"] >= profile.get("min_credibility", 40)]
                    elif view == "unverified":
                        items = [c for c in items if c["cred_code"] in ("SINGLE", "LOW")]
                    elif view == "noise":
                        items = [c for c in items
                                 if c["relevance"] < profile.get("min_relevance", 0.12)
                                 or c["cred"] < profile.get("min_credibility", 40)]
                    # “全部”不是“让数量最多的语言霸榜”。保留各语言内部排序，
                    # 每 4 条主要语言内容插入 1 条英文内容，让国际来源在首屏可见。
                    # 显式选择 zh/en 时不做混排。
                    if (not wanted_lang or wanted_lang == "all") and view == "signal":
                        english = [c for c in items if "en" in c.get("languages", [])]
                        other = [c for c in items if "en" not in c.get("languages", [])]
                        mixed = []
                        while other or english:
                            mixed.extend(other[:4])
                            del other[:4]
                            if english:
                                mixed.append(english.pop(0))
                        items = mixed
                    page = (_owner_capped(items, limit) if view == "signal" else items[:limit])
                    return self._json({"items": page, "total": len(items), "view": view,
                                       "parsed_query": parsed})

                if p.startswith("/api/cluster/"):
                    cid = p.rsplit("/", 1)[-1]
                    row = store.get_cluster(conn, cid)
                    if not row:
                        return self._json({"error": "not found"}, 404)
                    c = _json_cluster(row)
                    c["items"] = []
                    for row_item in store.cluster_items(conn, cid):
                        item = dict(row_item)
                        meta = source_meta.get(item["source_id"], {})
                        item["source_role"] = meta.get(
                            "source_role", config.source_role(meta) if meta else "reporting")
                        item["region"] = meta.get(
                            "region", "cn" if item.get("lang", "zh") == "zh" else "global")
                        c["items"].append(item)
                    c["evidence"] = evidence.analyze(c["items"], c.get("llm"))
                    persisted_claims = store.cluster_claims(conn, cid)
                    if persisted_claims:
                        c["evidence"]["claims"] = persisted_claims
                    c["entities"] = entities.cluster_links(conn, cid)
                    c["assets"] = [x for x in c["entities"] if x.get("symbol")]
                    return self._json(c)

                if p == "/api/stats":
                    tot = conn.execute("SELECT COUNT(*) n FROM clusters").fetchone()["n"]
                    it = conn.execute("SELECT COUNT(*) n FROM items").fetchone()["n"]
                    bands = {r["cred_code"]: r["n"] for r in conn.execute(
                        "SELECT cred_code, COUNT(*) n FROM clusters GROUP BY cred_code")}
                    day = now_ts() - 86400
                    fresh = conn.execute(
                        "SELECT COUNT(*) n FROM clusters WHERE last_ts>=?", (day,)
                    ).fetchone()["n"]
                    multi = conn.execute(
                        "SELECT COUNT(*) n FROM clusters WHERE n_groups>=2 AND last_ts>=?",
                        (day,)).fetchone()["n"]
                    noise = conn.execute(
                        "SELECT COUNT(*) n FROM clusters WHERE last_ts>=? AND "
                        "(relevance < ? OR cred < ?)",
                        (day, profile.get("min_relevance", 0.12),
                         profile.get("min_credibility", 40))).fetchone()["n"]
                    llm_n = conn.execute(
                        "SELECT COUNT(*) n FROM clusters WHERE llm IS NOT NULL"
                    ).fetchone()["n"]
                    lr = store.last_run(conn)
                    topics = {}
                    for r in conn.execute(
                            "SELECT topics FROM clusters WHERE last_ts>=?", (day,)):
                        for t in json.loads(r["topics"] or "[]")[:1]:
                            topics[t] = topics.get(t, 0) + 1
                    enabled_langs = {}
                    enabled_regions = {}
                    for s in reg["sources"]:
                        if not s.get("enabled", True):
                            continue
                        lang = s.get("lang", "zh")
                        region = s.get("region", "cn" if lang == "zh" else "global")
                        enabled_langs[lang] = enabled_langs.get(lang, 0) + 1
                        enabled_regions[region] = enabled_regions.get(region, 0) + 1
                    item_langs = {r["lang"] or "unknown": r["n"] for r in conn.execute(
                        "SELECT lang, COUNT(*) n FROM items WHERE published_ts>=? GROUP BY lang",
                        (day,))}
                    owner_counts, region_counts, role_counts = {}, {}, {}
                    for r in conn.execute(
                            "SELECT source_id, COUNT(*) n FROM items WHERE published_ts>=? "
                            "GROUP BY source_id", (day,)):
                        meta = source_meta.get(r["source_id"], {})
                        for counts, key in ((owner_counts, meta.get("owner", "unknown")),
                                            (region_counts, meta.get("region", "unknown")),
                                            (role_counts, meta.get("editorial_role", "unknown"))):
                            counts[key] = counts.get(key, 0) + r["n"]
                    recent_clusters = conn.execute(
                        "SELECT COUNT(*) n FROM clusters WHERE last_ts>=?", (day,)).fetchone()["n"]
                    independent = conn.execute(
                        "SELECT COUNT(*) n FROM clusters WHERE last_ts>=? AND n_groups>=2",
                        (day,)).fetchone()["n"]
                    return self._json({
                        "clusters": tot, "items": it, "bands": bands,
                        "fresh_24h": fresh, "multi_source_24h": multi,
                        "noise_24h": noise, "llm_judged": llm_n,
                        "topics_24h": topics,
                        "source_languages": enabled_langs,
                        "source_regions": enabled_regions,
                        "item_languages_24h": item_langs,
                        "diversity_24h": {
                            "owner": _concentration(owner_counts),
                            "region": _concentration(region_counts),
                            "source_role": _concentration(role_counts),
                            "independent_corroboration": {
                                "clusters": independent, "total": recent_clusters,
                                "rate": round(independent / recent_clusters, 4)
                                if recent_clusters else 0.0,
                            },
                        },
                        "profile": profile.get("name"),
                        "llm_enabled": bool(use_llm and config.LLM_API_KEY),
                        "llm_model": config.LLM_MODEL if use_llm and config.LLM_API_KEY else None,
                        "min_credibility": profile.get("min_credibility", 40),
                        "min_relevance": profile.get("min_relevance", 0.12),
                        "last_run": dict(lr) if lr else None,
                        "refresh": {k: v for k, v in _refresh_state.items() if k != "log"},
                        "server_ts": now_ts(),
                    })

                if p == "/api/quality":
                    return self._json(quality.scorecard(conn, reg, now_ts()))

                if p == "/api/research":
                    question = (q.get("q") or "").strip()
                    limit = max(1, min(20, int(q.get("limit", 8))))
                    return self._json(research.answer(conn, question, limit))

                if p == "/api/ai-radar":
                    hours = min(24 * 30, int(q.get("hours", 72)))
                    since = now_ts() - hours * 3600
                    candidates = list(conn.execute(
                        "SELECT id,headline,headline_src,url,last_ts,n_items,n_groups,cred,"
                        "cred_code,relevance,rank,topics FROM clusters WHERE last_ts>=? "
                        "ORDER BY rank DESC LIMIT 1500", (since,)))
                    ai_rows, topic_counts = [], {}
                    for row in candidates:
                        row_topics = json.loads(row["topics"] or "[]")
                        ai_topics = [t for t in row_topics if t.startswith("ai_")]
                        if not ai_topics:
                            continue
                        item = dict(row)
                        item["topics"] = row_topics
                        ai_rows.append(item)
                        for topic in ai_topics:
                            topic_counts[topic] = topic_counts.get(topic, 0) + 1
                    ids = [row["id"] for row in ai_rows]
                    source_counts = {}
                    official_clusters, reporting_clusters = set(), set()
                    entity_counts = {}
                    if ids:
                        marks = ",".join("?" for _ in ids)
                        for row in conn.execute(
                                f"SELECT cluster_id,source_id,COUNT(*) n FROM items "
                                f"WHERE cluster_id IN ({marks}) GROUP BY cluster_id,source_id", ids):
                            meta = source_meta.get(row["source_id"], {})
                            name = meta.get("name", row["source_id"])
                            source_counts[name] = source_counts.get(name, 0) + row["n"]
                            role = meta.get("source_role", "reporting")
                            if role == "official":
                                official_clusters.add(row["cluster_id"])
                            elif role in ("reporting", "wire"):
                                reporting_clusters.add(row["cluster_id"])
                        for row in conn.execute(
                                f"SELECT e.id,e.name,e.symbol,e.kind,COUNT(DISTINCT ce.cluster_id) n "
                                f"FROM cluster_entities ce JOIN entities e ON e.id=ce.entity_id "
                                f"WHERE ce.cluster_id IN ({marks}) GROUP BY e.id,e.name,e.symbol,e.kind "
                                f"ORDER BY n DESC,e.name LIMIT 15", ids):
                            entity_counts[row["id"]] = dict(row)
                    ai_sources = [s for s in reg["sources"] if s.get("enabled", True)
                                  and any(t.startswith("ai_") for t in s.get("topics", []))]
                    for item in ai_rows:
                        item["evidence_status"] = (
                            "independent" if item["n_groups"] >= 2 else
                            "primary" if item["id"] in official_clusters else "single")
                    ai_rows.sort(key=lambda row: (
                        row["evidence_status"] == "independent",
                        row["evidence_status"] == "primary",
                        row["cred"], row["rank"]), reverse=True)
                    return self._json({
                        "hours": hours, "clusters": len(ai_rows),
                        "official_clusters": len(official_clusters),
                        "reporting_clusters": len(reporting_clusters),
                        "independently_corroborated": sum(
                            1 for row in ai_rows if row["n_groups"] >= 2),
                        "topic_counts": dict(sorted(topic_counts.items(),
                                                    key=lambda x: (-x[1], x[0]))),
                        "top_sources": [{"name": k, "items": v} for k, v in sorted(
                            source_counts.items(), key=lambda x: (-x[1], x[0]))[:12]],
                        "top_entities": list(entity_counts.values()),
                        "sources": {"enabled": len(ai_sources),
                                    "official": sum(config.source_role(s) == "official"
                                                    for s in ai_sources),
                                    "reporting": sum(config.source_role(s) in
                                                     ("reporting", "wire") for s in ai_sources)},
                        "items": ai_rows[:30],
                    })

                if p == "/metrics":
                    now = now_ts()
                    source_rows = store.health(conn)
                    healthy = sum(1 for x in source_rows if x["verdict"] == "healthy")
                    probe_rows = list(conn.execute(
                        "SELECT verdict,ms FROM source_probes WHERE ts>=?", (now - 86400,)))
                    probe_success = (sum(x["verdict"] == "healthy" for x in probe_rows) /
                                     len(probe_rows) if probe_rows else 0)
                    probe_latencies = sorted(x["ms"] for x in probe_rows)
                    probe_p95 = (probe_latencies[int((len(probe_latencies) - 1) * .95)]
                                 if probe_latencies else 0)
                    clusters_n = conn.execute("SELECT COUNT(*) n FROM clusters").fetchone()["n"]
                    items_n = conn.execute("SELECT COUNT(*) n FROM items").fetchone()["n"]
                    orphan_n = conn.execute(
                        "SELECT COUNT(*) n FROM clusters c WHERE NOT EXISTS "
                        "(SELECT 1 FROM items i WHERE i.cluster_id=c.id)").fetchone()["n"]
                    market_row = conn.execute(
                        "SELECT COUNT(DISTINCT symbol) symbols, MAX(ts) newest FROM market_ticks"
                    ).fetchone()
                    entity_n = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
                    entity_links_n = conn.execute(
                        "SELECT COUNT(*) FROM cluster_entities").fetchone()[0]
                    market_age = (max(0, now_ts() - market_row["newest"])
                                  if market_row["newest"] else -1)
                    run_row = conn.execute(
                        "SELECT MAX(ended_ts) ended, MAX(CASE WHEN ended_ts IS NOT NULL "
                        "THEN ended_ts-started_ts END) duration FROM runs "
                        "WHERE status='success' OR status IS NULL").fetchone()
                    run_age = max(0, now - run_row["ended"]) if run_row["ended"] else -1
                    incomplete_runs = conn.execute(
                        "SELECT COUNT(*) FROM runs WHERE ended_ts IS NULL AND started_ts<?",
                        (now - max(300, config.AUTO_REFRESH_SECONDS * 2),)).fetchone()[0]
                    failed_runs = conn.execute(
                        "SELECT COUNT(*) FROM runs WHERE status='failed' AND ended_ts>=?",
                        (now - 86400,)).fetchone()[0]
                    content_newest = conn.execute("SELECT MAX(published_ts) FROM items").fetchone()[0]
                    content_age = max(0, now - content_newest) if content_newest else -1
                    day = now - 86400
                    items_24h = conn.execute(
                        "SELECT COUNT(*) FROM items WHERE published_ts>=?", (day,)).fetchone()[0]
                    unclustered = conn.execute(
                        "SELECT COUNT(*) FROM items WHERE cluster_id IS NULL AND published_ts>=?",
                        (now - config.CLUSTER_WINDOW_H * 3600,)).fetchone()[0]
                    future_items = conn.execute(
                        "SELECT COUNT(*) FROM items WHERE published_ts>?", (now + 300,)).fetchone()[0]
                    outbox = conn.execute(
                        "SELECT COUNT(*) n,MIN(created_ts) oldest,MAX(attempts) attempts "
                        "FROM alert_outbox WHERE delivered_ts IS NULL").fetchone()
                    outbox_age = max(0, now - outbox["oldest"]) if outbox["oldest"] else 0
                    backups = list(config.BACKUP_DIR.glob("newsdesk-*.db"))
                    backup_newest = max((int(x.stat().st_mtime) for x in backups), default=0)
                    backup_age = max(0, now - backup_newest) if backup_newest else -1
                    quality_result = quality.scorecard(conn, reg, now)
                    body = "\n".join([
                        "# HELP newsdesk_sources_healthy Healthy configured sources.",
                        "# TYPE newsdesk_sources_healthy gauge",
                        f"newsdesk_sources_healthy {healthy}",
                        "# TYPE newsdesk_source_probe_success_ratio_24h gauge",
                        f"newsdesk_source_probe_success_ratio_24h {probe_success:.4f}",
                        "# TYPE newsdesk_source_probe_latency_p95_ms gauge",
                        f"newsdesk_source_probe_latency_p95_ms {probe_p95}",
                        "# TYPE newsdesk_items_total gauge", f"newsdesk_items_total {items_n}",
                        "# TYPE newsdesk_clusters_total gauge", f"newsdesk_clusters_total {clusters_n}",
                        "# TYPE newsdesk_orphan_clusters gauge", f"newsdesk_orphan_clusters {orphan_n}",
                        "# TYPE newsdesk_refresh_running gauge",
                        f"newsdesk_refresh_running {1 if _refresh_state['running'] else 0}",
                        "# TYPE newsdesk_alert_events_unread gauge",
                        "newsdesk_alert_events_unread " + str(conn.execute(
                            "SELECT COUNT(*) FROM alert_events WHERE read_ts IS NULL").fetchone()[0]),
                        "# HELP newsdesk_market_symbols Number of instruments with persisted history.",
                        "# TYPE newsdesk_market_symbols gauge",
                        f"newsdesk_market_symbols {market_row['symbols']}",
                        "# HELP newsdesk_market_snapshot_age_seconds Age of newest persisted snapshot; -1 if absent.",
                        "# TYPE newsdesk_market_snapshot_age_seconds gauge",
                        f"newsdesk_market_snapshot_age_seconds {market_age}",
                        "# TYPE newsdesk_entities_total gauge",
                        f"newsdesk_entities_total {entity_n}",
                        "# TYPE newsdesk_entity_links_total gauge",
                        f"newsdesk_entity_links_total {entity_links_n}",
                        "# TYPE newsdesk_pipeline_last_success_age_seconds gauge",
                        f"newsdesk_pipeline_last_success_age_seconds {run_age}",
                        "# TYPE newsdesk_pipeline_last_duration_seconds gauge",
                        f"newsdesk_pipeline_last_duration_seconds {run_row['duration'] or 0}",
                        "# TYPE newsdesk_pipeline_incomplete_runs gauge",
                        f"newsdesk_pipeline_incomplete_runs {incomplete_runs}",
                        "# TYPE newsdesk_pipeline_failed_runs_24h gauge",
                        f"newsdesk_pipeline_failed_runs_24h {failed_runs}",
                        "# TYPE newsdesk_content_newest_age_seconds gauge",
                        f"newsdesk_content_newest_age_seconds {content_age}",
                        "# TYPE newsdesk_items_24h gauge", f"newsdesk_items_24h {items_24h}",
                        "# TYPE newsdesk_unclustered_recent_items gauge",
                        f"newsdesk_unclustered_recent_items {unclustered}",
                        "# TYPE newsdesk_future_dated_items gauge",
                        f"newsdesk_future_dated_items {future_items}",
                        "# TYPE newsdesk_alert_outbox_pending gauge",
                        f"newsdesk_alert_outbox_pending {outbox['n']}",
                        "# TYPE newsdesk_alert_outbox_oldest_age_seconds gauge",
                        f"newsdesk_alert_outbox_oldest_age_seconds {outbox_age}",
                        "# TYPE newsdesk_alert_outbox_max_attempts gauge",
                        f"newsdesk_alert_outbox_max_attempts {outbox['attempts'] or 0}",
                        "# TYPE newsdesk_backups_total gauge",
                        f"newsdesk_backups_total {len(backups)}",
                        "# TYPE newsdesk_backup_newest_age_seconds gauge",
                        f"newsdesk_backup_newest_age_seconds {backup_age}",
                        "# TYPE newsdesk_backup_last_success_timestamp_seconds gauge",
                        "newsdesk_backup_last_success_timestamp_seconds " + str(
                            _ops_state["backup_last_success"] or backup_newest),
                        "# TYPE newsdesk_backup_last_failure_timestamp_seconds gauge",
                        f"newsdesk_backup_last_failure_timestamp_seconds {_ops_state['backup_last_failure']}",
                        "# TYPE newsdesk_refresh_skipped_total counter",
                        f"newsdesk_refresh_skipped_total {_ops_state['refresh_skipped']}",
                        "# HELP newsdesk_quality_gate_failures Required product quality gates failing.",
                        "# TYPE newsdesk_quality_gate_failures gauge",
                        f"newsdesk_quality_gate_failures {quality_result['counts']['fail']}",
                        "# HELP newsdesk_international_items_24h_share Share of recent non-Chinese items.",
                        "# TYPE newsdesk_international_items_24h_share gauge",
                        "newsdesk_international_items_24h_share " + str(next(
                            g["value"] for g in quality_result["gates"]
                            if g["name"] == "international_items_24h_share")),
                        "# TYPE newsdesk_uptime_seconds counter",
                        f"newsdesk_uptime_seconds {now_ts() - _server_started}", "",
                    ])
                    return self._send(200, body.encode(), "text/plain; version=0.0.4; charset=utf-8")

                if p == "/api/source-review":
                    window = str(q.get("window", ""))
                    review = source_scores.governance_review(
                        conn, reg, profile,
                        window_h=int(window) if window.isdigit() else None)
                    return self._json(review)

                if p == "/api/sources":
                    health = {h["source_id"]: dict(h) for h in store.health(conn)}
                    cards = store.latest_scorecards(conn)
                    out = []
                    for s in reg["sources"]:
                        h = health.get(s["id"], {})
                        meta = source_meta[s["id"]]
                        card = cards.get(s["id"], {})
                        enabled = s.get("enabled", True)
                        verdict = h.get("verdict", "unseen") if enabled else "disabled"
                        out.append({
                            "id": s["id"], "name": s["name"], "tier": s["tier"],
                            "group": s.get("group", s["id"]),
                            "enabled": enabled,
                            "focus": source_scores.focus_of(s),
                            "score": card.get("score"),
                            "grade": card.get("grade"),
                            "n_lead": card.get("n_lead"),
                            "n_corroborated": card.get("n_corroborated"),
                            "n_window_items": card.get("n_items"),
                            "avg_relevance": card.get("avg_relevance"),
                            "avg_doubt": card.get("avg_doubt"),
                            "score_breakdown": card.get("breakdown", {}),
                            "scored_ts": card.get("computed_ts"),
                            "topics": s.get("topics", []), "note": s.get("note", ""),
                            "lang": s.get("lang", "zh"),
                            **meta,
                            "source_role": s.get("source_role", "reporting"),
                            "url": s["url"],
                            "ok": verdict == "healthy",
                            "verdict": verdict,
                            "transport_status": h.get("transport_status", "unknown"),
                            "parse_status": h.get("parse_status", "unknown"),
                            "freshness_status": h.get("freshness_status", "unknown"),
                            "last_error": h.get("last_error"),
                            "last_items": h.get("last_items", 0),
                            "last_ok_ts": h.get("last_ok_ts"),
                            "newest_ts": h.get("newest_ts"),
                            "ms": h.get("ms", 0),
                        })
                    return self._json({"tiers": reg["tiers"], "sources": out})

                if p == "/api/profile":
                    return self._json(profile)

                if p == "/api/entities":
                    term = (q.get("q") or "").strip()
                    pattern = f"%{term}%"
                    rows = [dict(x) for x in conn.execute(
                        "SELECT e.*,COUNT(DISTINCT ce.cluster_id) mention_clusters "
                        "FROM entities e LEFT JOIN cluster_entities ce ON ce.entity_id=e.id "
                        "WHERE (?='' OR e.name LIKE ? OR e.symbol LIKE ? OR EXISTS "
                        "(SELECT 1 FROM entity_aliases ea WHERE ea.entity_id=e.id AND ea.alias LIKE ?)) "
                        "GROUP BY e.id ORDER BY mention_clusters DESC,e.kind,e.name LIMIT 100",
                        (term, pattern, pattern, pattern))]
                    for row in rows:
                        row["identifiers"] = json.loads(row["identifiers"] or "{}")
                        row["aliases"] = [x[0] for x in conn.execute(
                            "SELECT alias FROM entity_aliases WHERE entity_id=? ORDER BY alias",
                            (row["id"],))]
                    return self._json({"entities": rows, "total": len(rows)})

                if p == "/api/markets":
                    snap = markets.snapshot()
                    store.record_market_snapshot(conn, snap)
                    return self._json(snap)

                if p.startswith("/api/markets/history/"):
                    symbol = urllib.parse.unquote(p.rsplit("/", 1)[-1])
                    hours = max(1, min(24 * 365, int(q.get("hours", 168))))
                    rows = [dict(x) for x in store.market_history(
                        conn, symbol, now_ts() - hours * 3600)]
                    return self._json({"symbol": symbol, "hours": hours, "points": rows})

                if p == "/api/watchlist":
                    snap = markets.snapshot()
                    store.record_market_snapshot(conn, snap)
                    current = {x["symbol"]: x for x in snap["instruments"]}
                    rows = []
                    for watched in store.watchlist(conn):
                        row = dict(watched)
                        row["market"] = current.get(row["symbol"])
                        rows.append(row)
                    return self._json({"items": rows})

                if p == "/api/alerts":
                    out = []
                    since = now_ts() - 86400
                    for alert in store.alerts(conn):
                        a = dict(alert)
                        matches = alerting.matching_clusters(conn, a, since)
                        a["match_count_24h"] = len(matches)
                        out.append(a)
                    return self._json({"alerts": out})

                if p == "/api/alert-events":
                    unread = q.get("unread", "0") == "1"
                    rows = [dict(x) for x in store.alert_events(
                        conn, unread_only=unread, limit=min(300, int(q.get("limit", 100))))]
                    return self._json({"events": rows,
                                       "unread": sum(1 for x in rows if x["read_ts"] is None)})

                if p == "/api/digest":
                    md = digest.briefing(conn, profile,
                                         hours=int(q.get("hours", 24)),
                                         top=int(q.get("top", 12)))
                    return self._send(200, md.encode("utf-8"),
                                      "text/markdown; charset=utf-8")

                if p == "/api/refresh_status":
                    return self._json(_refresh_state)

                return self._send(404, b"not found", "text/plain; charset=utf-8")
            finally:
                conn.close()

        def do_POST(self):
            u = urllib.parse.urlparse(self.path)
            if u.path not in ("/api/refresh", "/api/alerts", "/api/alert-events/read",
                              "/api/watchlist") and not u.path.startswith("/api/movement-review/") \
                    and not u.path.startswith("/api/movement-complete/"):
                return self._send(404, b"not found", "text/plain; charset=utf-8")
            if not self._same_origin_write():
                return self._json({"ok": False, "msg": "拒绝跨站写操作"}, 403)
            if not self._write_authenticated():
                return self._json({"ok": False, "msg": "需要管理令牌"}, 401)
            if u.path.startswith("/api/movement-review/"):
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if size <= 0 or size > 16384:
                        return self._json({"error": "invalid body"}, 400)
                    body = json.loads(self.rfile.read(size))
                    if not isinstance(body, dict):
                        return self._json({"error": "invalid payload"}, 400)
                    conn = store.connect()
                    try:
                        item = movements.review_event(
                            conn, u.path.rsplit("/", 1)[-1],
                            verification_status=str(body.get("verification_status") or "candidate"),
                            workflow_status=str(body.get("workflow_status") or "draft"),
                            reviewer="token-admin", reason=str(body.get("reason") or ""))
                    finally:
                        conn.close()
                    return self._json({"ok": True, "item": item})
                except LookupError as exc:
                    return self._json({"error": str(exc)}, 404)
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    return self._json({"error": str(exc)}, 400)
            if u.path.startswith("/api/movement-complete/"):
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if size <= 0 or size > 16384:
                        return self._json({"error": "invalid body"}, 400)
                    body = json.loads(self.rfile.read(size))
                    if not isinstance(body, dict):
                        return self._json({"error": "invalid payload"}, 400)
                    conn = store.connect()
                    try:
                        item = movements.complete_and_publish(
                            conn, u.path.rsplit("/", 1)[-1],
                            object_text=str(body.get("object_text") or ""),
                            amount_value_text=(str(body["amount_value_text"])
                                               if body.get("amount_value_text") else None),
                            reviewer="token-admin", reason=str(body.get("reason") or ""))
                    finally:
                        conn.close()
                    return self._json({"ok": True, "item": item})
                except LookupError as exc:
                    return self._json({"error": str(exc)}, 404)
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    return self._json({"error": str(exc)}, 400)
            if u.path == "/api/alert-events/read":
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if size < 0 or size > 16384:
                        return self._json({"error": "invalid body"}, 400)
                    body = json.loads(self.rfile.read(size) or b"{}")
                    if not isinstance(body, dict):
                        return self._json({"error": "invalid payload"}, 400)
                    event_id = body.get("id")
                    conn = store.connect()
                    try:
                        changed = store.mark_alert_events_read(
                            conn, int(event_id) if event_id is not None else None)
                    finally:
                        conn.close()
                    return self._json({"ok": True, "changed": changed})
                except (ValueError, TypeError, json.JSONDecodeError):
                    return self._json({"error": "invalid payload"}, 400)
            if u.path == "/api/watchlist":
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if size <= 0 or size > 16384:
                        return self._json({"error": "invalid body"}, 400)
                    body = json.loads(self.rfile.read(size))
                    if not isinstance(body, dict):
                        return self._json({"error": "invalid payload"}, 400)
                    symbol = str(body.get("symbol") or "").strip()[:24]
                    known = {x["symbol"] for x in markets.snapshot()["instruments"]}
                    if symbol not in known:
                        return self._json({"error": "unknown symbol"}, 400)
                    conn = store.connect()
                    try:
                        store.add_watch(conn, symbol)
                    finally:
                        conn.close()
                    return self._json({"ok": True, "symbol": symbol}, 201)
                except (ValueError, TypeError, json.JSONDecodeError):
                    return self._json({"error": "invalid payload"}, 400)
            if u.path == "/api/alerts":
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if size <= 0 or size > 16384:
                        return self._json({"error": "invalid body"}, 400)
                    body = json.loads(self.rfile.read(size))
                    if not isinstance(body, dict):
                        return self._json({"error": "invalid payload"}, 400)
                    name = str(body.get("name") or "").strip()[:80]
                    if not name:
                        return self._json({"error": "name required"}, 400)
                    lang = body.get("lang", "all")
                    if lang not in ("all", "zh", "en", "pt"):
                        return self._json({"error": "invalid lang"}, 400)
                    conn = store.connect()
                    try:
                        aid = store.create_alert(
                            conn, name=name, q=str(body.get("q") or "")[:160],
                            topic=str(body.get("topic") or "all")[:32], lang=lang,
                            min_cred=max(0, min(100, float(body.get("min_cred", 40)))))
                    finally:
                        conn.close()
                    return self._json({"ok": True, "id": aid}, 201)
                except (ValueError, TypeError, json.JSONDecodeError):
                    return self._json({"error": "invalid payload"}, 400)
            q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
            if config.REFRESH_TOKEN:
                supplied = self.headers.get("X-Refresh-Token", "")
                auth = self.headers.get("Authorization", "")
                if auth.startswith("Bearer "):
                    supplied = auth[7:]
                import hmac
                if not hmac.compare_digest(supplied, config.REFRESH_TOKEN):
                    return self._json({"ok": False, "msg": "未授权"}, 401)
            now = now_ts()
            if (_refresh_state["started"] and
                    now - _refresh_state["started"] < config.REFRESH_COOLDOWN):
                return self._json({"ok": False, "msg": "刷新过于频繁"}, 429)
            # 公网页面不能通过 query 参数偷偷开启付费 LLM；只能由服务启动参数授权。
            want_llm = use_llm and q.get("llm", "1") == "1"
            if not _start_refresh(reg, profile, want_llm):
                return self._json({"ok": False, "msg": "已有刷新任务在跑"}, 429)
            return self._json({"ok": True, "llm": want_llm})

        def do_DELETE(self):
            u = urllib.parse.urlparse(self.path)
            if not self._same_origin_write():
                return self._json({"ok": False, "msg": "拒绝跨站写操作"}, 403)
            if not self._write_authenticated():
                return self._json({"ok": False, "msg": "需要管理令牌"}, 401)
            if u.path.startswith("/api/watchlist/"):
                symbol = urllib.parse.unquote(u.path[len("/api/watchlist/"):])
                conn = store.connect()
                try:
                    found = store.remove_watch(conn, symbol)
                finally:
                    conn.close()
                return self._json({"ok": found}, 200 if found else 404)
            if not u.path.startswith("/api/alerts/"):
                return self._send(404, b"not found", "text/plain; charset=utf-8")
            try:
                alert_id = int(u.path.rsplit("/", 1)[-1])
            except ValueError:
                return self._json({"error": "invalid id"}, 400)
            conn = store.connect()
            try:
                found = store.delete_alert(conn, alert_id)
            finally:
                conn.close()
            return self._json({"ok": found}, 200 if found else 404)

    return Handler


def serve(reg: dict, profile: dict, host=None, port=None, use_llm=False):
    host = host or config.SERVER_HOST
    port = port or config.SERVER_PORT
    if not config.WRITE_TOKEN:
        token_path = config.DATA_DIR / "admin-token"
        if token_path.exists():
            config.WRITE_TOKEN = token_path.read_text(encoding="utf-8").strip()
        else:
            config.WRITE_TOKEN = secrets.token_urlsafe(32)
            fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(config.WRITE_TOKEN + "\n")
    httpd = ThreadingHTTPServer((host, port), make_handler(reg, profile, use_llm))
    httpd.daemon_threads = True
    ops.event(_logger, "server_started", host=host, port=port)
    if config.AUTO_REFRESH_SECONDS > 0:
        def scheduled_refresh():
            while True:
                time.sleep(config.AUTO_REFRESH_SECONDS)
                if not _start_refresh(reg, profile, use_llm):
                    _ops_state["refresh_skipped"] += 1
        threading.Thread(target=scheduled_refresh, daemon=True,
                         name="newsdesk-scheduler").start()
        ops.event(_logger, "scheduler_started", interval_s=config.AUTO_REFRESH_SECONDS)
    if config.AUTO_BACKUP_SECONDS > 0:
        def scheduled_backup():
            while True:
                time.sleep(config.AUTO_BACKUP_SECONDS)
                try:
                    path = store.backup_database(keep=config.BACKUP_KEEP)
                    _ops_state["backup_last_success"] = now_ts()
                    ops.event(_logger, "backup_completed", path=str(path))
                except Exception as exc:
                    _ops_state["backup_last_failure"] = now_ts()
                    ops.event(_logger, "backup_failed", str(exc), level=logging.ERROR,
                              error_type=type(exc).__name__)
        threading.Thread(target=scheduled_backup, daemon=True,
                         name="newsdesk-backup").start()
        ops.event(_logger, "backup_scheduler_started",
                  interval_s=config.AUTO_BACKUP_SECONDS, keep=config.BACKUP_KEEP)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        ops.event(_logger, "server_stopped", reason="keyboard_interrupt")
    finally:
        httpd.server_close()
