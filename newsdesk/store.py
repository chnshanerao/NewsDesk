"""SQLite 存储层。单文件库，WAL 模式，可被 server 与 CLI 并发读。"""
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import time
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id            TEXT PRIMARY KEY,
    source_id     TEXT NOT NULL,
    source_name   TEXT,
    tier          INTEGER,
    grp           TEXT,
    title         TEXT NOT NULL,
    summary       TEXT,
    url           TEXT,
    lang          TEXT,
    published_ts  INTEGER,
    fetched_ts    INTEGER,
    simhash       INTEGER,
    grams         TEXT,
    cluster_id    TEXT,
    body          TEXT,
    body_state    TEXT,
    body_ts       INTEGER
);
CREATE INDEX IF NOT EXISTS idx_items_pub     ON items(published_ts DESC);
CREATE INDEX IF NOT EXISTS idx_items_cluster ON items(cluster_id);
CREATE INDEX IF NOT EXISTS idx_items_source  ON items(source_id);

CREATE TABLE IF NOT EXISTS clusters (
    id            TEXT PRIMARY KEY,
    headline      TEXT,
    headline_src  TEXT,
    url           TEXT,
    first_ts      INTEGER,
    last_ts       INTEGER,
    n_items       INTEGER,
    n_groups      INTEGER,
    best_tier     INTEGER,
    topics        TEXT,
    cred          REAL,
    cred_code     TEXT,
    cred_label    TEXT,
    relevance     REAL,
    rank          REAL,
    breakdown     TEXT,
    llm           TEXT,
    content_hash  TEXT,
    updated_ts    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_clusters_rank ON clusters(rank DESC);
CREATE INDEX IF NOT EXISTS idx_clusters_last ON clusters(last_ts DESC);

CREATE TABLE IF NOT EXISTS source_health (
    source_id   TEXT PRIMARY KEY,
    name        TEXT,
    tier        INTEGER,
    enabled     INTEGER,
    last_try_ts INTEGER,
    last_ok_ts  INTEGER,
    last_error  TEXT,
    ok_count    INTEGER DEFAULT 0,
    err_count   INTEGER DEFAULT 0,
    last_items  INTEGER DEFAULT 0,
    last_new    INTEGER DEFAULT 0,
    ms          INTEGER DEFAULT 0,
    undated     INTEGER DEFAULT 0,
    newest_ts   INTEGER,
    transport_status TEXT,
    parse_status     TEXT,
    freshness_status TEXT,
    verdict          TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_ts INTEGER,
    ended_ts   INTEGER,
    n_fetched  INTEGER,
    n_new      INTEGER,
    n_clusters INTEGER,
    llm_used   INTEGER,
    note       TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    q           TEXT DEFAULT '',
    topic       TEXT DEFAULT 'all',
    lang        TEXT DEFAULT 'all',
    min_cred    REAL DEFAULT 40,
    enabled     INTEGER DEFAULT 1,
    last_count  INTEGER DEFAULT 0,
    last_notified_ts INTEGER,
    created_ts  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS market_ticks (
    symbol      TEXT NOT NULL,
    ts          INTEGER NOT NULL,
    price       REAL NOT NULL,
    asset       TEXT,
    currency    TEXT,
    change_pct  REAL,
    change_bps  REAL,
    source      TEXT,
    PRIMARY KEY(symbol, ts)
);
CREATE INDEX IF NOT EXISTS idx_market_ticks_symbol_ts
    ON market_ticks(symbol, ts DESC);

CREATE TABLE IF NOT EXISTS watchlist (
    symbol      TEXT PRIMARY KEY,
    added_ts    INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS alert_outbox (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id      INTEGER NOT NULL,
    payload       TEXT NOT NULL,
    attempts      INTEGER NOT NULL DEFAULT 0,
    next_attempt_ts INTEGER NOT NULL,
    last_error    TEXT,
    delivered_ts  INTEGER,
    created_ts    INTEGER NOT NULL,
    FOREIGN KEY(alert_id) REFERENCES alerts(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_alert_outbox_due
    ON alert_outbox(delivered_ts, next_attempt_ts);
CREATE TABLE IF NOT EXISTS alert_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id      INTEGER NOT NULL,
    cluster_id    TEXT NOT NULL,
    headline      TEXT NOT NULL,
    cred          REAL,
    event_ts      INTEGER,
    detected_ts   INTEGER NOT NULL,
    read_ts       INTEGER,
    UNIQUE(alert_id, cluster_id),
    FOREIGN KEY(alert_id) REFERENCES alerts(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_alert_events_detected ON alert_events(detected_ts DESC);
CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL,
    symbol TEXT, identifiers TEXT DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_symbol ON entities(symbol) WHERE symbol IS NOT NULL;
CREATE TABLE IF NOT EXISTS entity_aliases (
    entity_id TEXT NOT NULL, alias TEXT NOT NULL,
    PRIMARY KEY(entity_id,alias), FOREIGN KEY(entity_id) REFERENCES entities(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS cluster_entities (
    cluster_id TEXT NOT NULL, entity_id TEXT NOT NULL, relation_type TEXT NOT NULL,
    confidence REAL NOT NULL, evidence TEXT NOT NULL,
    PRIMARY KEY(cluster_id,entity_id),
    FOREIGN KEY(cluster_id) REFERENCES clusters(id) ON DELETE CASCADE,
    FOREIGN KEY(entity_id) REFERENCES entities(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_cluster_entities_entity ON cluster_entities(entity_id,cluster_id);
CREATE TABLE IF NOT EXISTS claims (
    id TEXT PRIMARY KEY, cluster_id TEXT NOT NULL, text TEXT NOT NULL,
    status TEXT NOT NULL, independent_groups INTEGER NOT NULL DEFAULT 0,
    groups_json TEXT NOT NULL DEFAULT '[]', source_roles_json TEXT NOT NULL DEFAULT '[]',
    method TEXT NOT NULL, updated_ts INTEGER NOT NULL,
    FOREIGN KEY(cluster_id) REFERENCES clusters(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_claims_cluster ON claims(cluster_id,status);
CREATE TABLE IF NOT EXISTS claim_evidence (
    claim_id TEXT NOT NULL, item_id TEXT NOT NULL, source_id TEXT,
    source_name TEXT, source_group TEXT, source_role TEXT, url TEXT,
    published_ts INTEGER, quote TEXT NOT NULL, quote_field TEXT NOT NULL,
    quote_start INTEGER NOT NULL, quote_end INTEGER NOT NULL, quote_hash TEXT NOT NULL,
    similarity REAL NOT NULL, relation TEXT NOT NULL DEFAULT 'unknown',
    relation_confidence REAL NOT NULL DEFAULT 0,
    relation_method TEXT NOT NULL DEFAULT 'legacy-unclassified',
    PRIMARY KEY(claim_id,item_id,quote_hash),
    FOREIGN KEY(claim_id) REFERENCES claims(id) ON DELETE CASCADE,
    FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_claim_evidence_claim ON claim_evidence(claim_id,similarity DESC);
CREATE TABLE IF NOT EXISTS source_probes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL, ts INTEGER NOT NULL,
    verdict TEXT NOT NULL, ms INTEGER NOT NULL, n_items INTEGER NOT NULL,
    n_new INTEGER NOT NULL, error TEXT
);
CREATE INDEX IF NOT EXISTS idx_source_probes_ts ON source_probes(ts DESC,source_id);
"""

SCHEMA_VERSION = 11


def _ensure_column(conn, table, name, declaration):
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if name not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")


def _baseline(conn):
    conn.executescript(SCHEMA)


def _evidence_health(conn):
    _ensure_column(conn, "clusters", "content_hash", "TEXT")
    for name in ("transport_status", "parse_status", "freshness_status", "verdict"):
        _ensure_column(conn, "source_health", name, "TEXT")


def _alerts(conn):
    _ensure_column(conn, "alerts", "last_count", "INTEGER DEFAULT 0")
    _ensure_column(conn, "alerts", "last_notified_ts", "INTEGER")


def _entity_search(conn):
    conn.executescript(SCHEMA)
    conn.executescript("""
        CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
            title,summary,source_name,content='items',content_rowid='rowid',tokenize='unicode61');
        CREATE TRIGGER IF NOT EXISTS items_ai AFTER INSERT ON items BEGIN
          INSERT INTO items_fts(rowid,title,summary,source_name)
          VALUES(new.rowid,new.title,new.summary,new.source_name);
        END;
        CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON items BEGIN
          INSERT INTO items_fts(items_fts,rowid,title,summary,source_name)
          VALUES('delete',old.rowid,old.title,old.summary,old.source_name);
        END;
        CREATE TRIGGER IF NOT EXISTS items_au AFTER UPDATE ON items BEGIN
          INSERT INTO items_fts(items_fts,rowid,title,summary,source_name)
          VALUES('delete',old.rowid,old.title,old.summary,old.source_name);
          INSERT INTO items_fts(rowid,title,summary,source_name)
          VALUES(new.rowid,new.title,new.summary,new.source_name);
        END;
        INSERT INTO items_fts(items_fts) VALUES('rebuild');
    """)


def _run_outcomes(conn):
    _ensure_column(conn, "runs", "status", "TEXT DEFAULT 'success'")
    _ensure_column(conn, "runs", "error", "TEXT")


def _source_probe_history(conn):
    conn.executescript(SCHEMA)


def _claim_evidence(conn):
    conn.executescript(SCHEMA)


def _claim_relations(conn):
    _ensure_column(conn, "claim_evidence", "relation", "TEXT NOT NULL DEFAULT 'unknown'")
    conn.execute("DELETE FROM claim_evidence WHERE claim_id NOT IN (SELECT id FROM claims) "
                 "OR item_id NOT IN (SELECT id FROM items)")


def _claim_relation_provenance(conn):
    _ensure_column(conn, "claim_evidence", "relation_confidence", "REAL NOT NULL DEFAULT 0")
    _ensure_column(conn, "claim_evidence", "relation_method",
                   "TEXT NOT NULL DEFAULT 'legacy-unclassified'")


def _item_body_preview(conn):
    # body_state 记住上一次抽取结论（ok/empty/error/skip），避免对同一个抽不出
    # 正文的页面每轮重试。
    _ensure_column(conn, "items", "body", "TEXT")
    _ensure_column(conn, "items", "body_state", "TEXT")
    _ensure_column(conn, "items", "body_ts", "INTEGER")


MIGRATIONS = (
    (1, "baseline", _baseline),
    (2, "evidence_and_source_health", _evidence_health),
    (3, "saved_alerts_and_delivery", _alerts),
    (4, "market_history_and_watchlist", _baseline),
    (5, "entity_master_and_fulltext_search", _entity_search),
    (6, "pipeline_run_outcomes", _run_outcomes),
    (7, "source_probe_history", _source_probe_history),
    (8, "claim_level_evidence", _claim_evidence),
    (9, "claim_evidence_relations", _claim_relations),
    (10, "claim_relation_provenance", _claim_relation_provenance),
    (11, "item_body_preview", _item_body_preview),
)


def connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = path or config.DB_PATH
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        Path(db_path).chmod(0o600)
    except OSError:
        pass
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init(conn: sqlite3.Connection) -> None:
    """Apply ordered, auditable and idempotent schema migrations."""
    conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_ts INTEGER NOT NULL
    )""")
    applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    try:
        for version, name, migrate in MIGRATIONS:
            if version not in applied:
                migrate(conn)
                conn.execute(
                    "INSERT INTO schema_migrations(version,name,applied_ts) VALUES(?,?,?)",
                    (version, name, int(time.time())))
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def schema_status(conn: sqlite3.Connection) -> dict:
    init(conn)
    rows = [dict(row) for row in conn.execute(
        "SELECT version,name,applied_ts FROM schema_migrations ORDER BY version")]
    return {"current": conn.execute("PRAGMA user_version").fetchone()[0],
            "expected": SCHEMA_VERSION, "migrations": rows}


# ---------------- items ----------------

def item_exists(conn, item_id: str) -> bool:
    return conn.execute("SELECT 1 FROM items WHERE id=?", (item_id,)).fetchone() is not None


def insert_items(conn, items: list[dict]) -> int:
    rows = [
        (
            it["id"], it["source_id"], it["source_name"], it["tier"], it["grp"],
            it["title"], it.get("summary", ""), it.get("url", ""), it.get("lang", "zh"),
            it.get("published_ts"), it.get("fetched_ts"), it.get("simhash"),
            " ".join(sorted(it.get("grams", ()))), it.get("cluster_id"),
        )
        for it in items
    ]
    cur = conn.executemany(
        "INSERT OR IGNORE INTO items "
        "(id,source_id,source_name,tier,grp,title,summary,url,lang,"
        " published_ts,fetched_ts,simhash,grams,cluster_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return cur.rowcount


def recent_items(conn, since_ts: int, until_ts: int) -> list[sqlite3.Row]:
    """只取有真实发布时间的稿件。没有日期的 feed 不许冒充新鲜内容——
    新华网/人民网 RSS 就是冻结在 2022/2025 的存档，一旦用 fetched_ts 兜底，
    整个终端会被几年前的旧闻灌满。"""
    return conn.execute(
        "SELECT * FROM items WHERE published_ts IS NOT NULL "
        "AND published_ts >= ? AND published_ts <= ? ORDER BY published_ts ASC",
        (since_ts, until_ts),
    ).fetchall()


def set_cluster(conn, assignments: list[tuple[str, str]]) -> None:
    conn.executemany("UPDATE items SET cluster_id=? WHERE id=?",
                     [(cid, iid) for iid, cid in assignments])
    conn.commit()


def cluster_items(conn, cluster_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM items WHERE cluster_id=? ORDER BY tier ASC, "
        "COALESCE(published_ts, fetched_ts) ASC",
        (cluster_id,),
    ).fetchall()


def items_needing_body(conn, limit: int, skip_sources: set[str] | None = None
                       ) -> list[sqlite3.Row]:
    """只给排名靠前事件的头条稿抓正文预览。

    没必要给全库抓：详情页只展示领头稿的『主要内容』，而每次抓正文都是一次
    外网请求。`body_state IS NULL` 保证每篇最多尝试一次——抽不出来的页面记下
    `empty`/`error` 就不再回头。

    领头稿的选取必须和详情页一致（`source_name = clusters.headline_src`，再按
    tier、时间排序）。曾经按 `items.url = clusters.url` 选，实测 79 篇抽取成功
    只覆盖 50 个事件——剩下 29 篇抓的正是没人展示的稿件，白烧配额。

    排序把摘要过短的稿件排在前面：那些页面的『主要内容』面板没有摘要可退，
    抽不到正文就是开天窗。联合早报等 HTML 类源 summary 恒为空，纯按 rank
    排会被中文网媒挤出每轮 80 篇的配额——实测 40 条里只有 1 条被抽到。
    """
    rows = conn.execute(
        """SELECT i.* FROM items i JOIN clusters c ON c.id = i.cluster_id
           WHERE c.last_ts >= ? AND i.body_state IS NULL AND i.url LIKE 'http%'
             AND i.id = COALESCE(
               (SELECT j.id FROM items j WHERE j.cluster_id = c.id
                  AND j.source_name = c.headline_src
                ORDER BY j.tier ASC, COALESCE(j.published_ts, j.fetched_ts) ASC LIMIT 1),
               (SELECT j.id FROM items j WHERE j.cluster_id = c.id
                ORDER BY j.tier ASC, COALESCE(j.published_ts, j.fetched_ts) ASC LIMIT 1))
           ORDER BY CASE WHEN LENGTH(COALESCE(i.summary, '')) < ? THEN 0 ELSE 1 END,
                    c.rank DESC LIMIT ?""",
        (int(time.time()) - config.CLUSTER_WINDOW_H * 3600,
         config.BODY_THIN_SUMMARY_CHARS, max(limit, 0) * 3),
    ).fetchall()
    skip = skip_sources or set()
    return [r for r in rows if r["source_id"] not in skip][:limit]


def save_item_bodies(conn, results: list[tuple[str, str, str]]) -> None:
    """results 为 (item_id, state, text)。state 一律写入，空正文也要记账。"""
    now = int(time.time())
    conn.executemany(
        "UPDATE items SET body=?, body_state=?, body_ts=? WHERE id=?",
        [(text or None, state, now, iid) for iid, state, text in results])
    conn.commit()


def body_preview_stats(conn, since_ts: int) -> dict:
    row = conn.execute(
        "SELECT COUNT(*) AS attempted, "
        "SUM(CASE WHEN body_state='ok' THEN 1 ELSE 0 END) AS ok "
        "FROM items WHERE body_ts IS NOT NULL AND body_ts >= ?",
        (since_ts,)).fetchone()
    return {"attempted": row["attempted"] or 0, "ok": row["ok"] or 0}


# ---------------- clusters ----------------

def upsert_cluster(conn, c: dict) -> None:
    conn.execute(
        "INSERT INTO clusters (id,headline,headline_src,url,first_ts,last_ts,n_items,"
        "n_groups,best_tier,topics,cred,cred_code,cred_label,relevance,rank,breakdown,"
        "llm,content_hash,updated_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET headline=excluded.headline,"
        "headline_src=excluded.headline_src,url=excluded.url,first_ts=excluded.first_ts,"
        "last_ts=excluded.last_ts,n_items=excluded.n_items,n_groups=excluded.n_groups,"
        "best_tier=excluded.best_tier,topics=excluded.topics,cred=excluded.cred,"
        "cred_code=excluded.cred_code,cred_label=excluded.cred_label,"
        "relevance=excluded.relevance,rank=excluded.rank,breakdown=excluded.breakdown,"
        # 只有事件成员与文本内容未变化时才复用旧 LLM 结论。
        "llm=CASE WHEN clusters.content_hash=excluded.content_hash "
        "THEN COALESCE(excluded.llm,clusters.llm) ELSE excluded.llm END,"
        "content_hash=excluded.content_hash,updated_ts=excluded.updated_ts",
        (
            c["id"], c["headline"], c.get("headline_src"), c.get("url"),
            c["first_ts"], c["last_ts"], c["n_items"], c["n_groups"], c["best_tier"],
            json.dumps(c.get("topics", []), ensure_ascii=False),
            c["cred"], c["cred_code"], c["cred_label"], c["relevance"], c["rank"],
            json.dumps(c.get("breakdown", {}), ensure_ascii=False),
            json.dumps(c["llm"], ensure_ascii=False) if c.get("llm") else None,
            c.get("content_hash"),
            int(time.time()),
        ),
    )


def save_llm(conn, cluster_id: str, payload: dict, cred: float,
             cred_code: str, cred_label: str, rank: float) -> None:
    conn.execute(
        "UPDATE clusters SET llm=?, cred=?, cred_code=?, cred_label=?, rank=?, "
        "updated_ts=? WHERE id=?",
        (json.dumps(payload, ensure_ascii=False), cred, cred_code, cred_label,
         rank, int(time.time()), cluster_id),
    )
    conn.commit()


def feed(conn, *, limit=80, min_cred=0.0, topic=None, q=None, source=None, asset=None,
         lang=None,
         since_ts=None, order="rank") -> list[sqlite3.Row]:
    sql = "SELECT * FROM clusters WHERE cred >= ?"
    args: list = [min_cred]
    if topic and topic != "all":
        sql += " AND topics LIKE ?"
        args.append(f'%"{topic}"%')
    if q:
        for term in str(q).split():
            escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            if re.fullmatch(r"[A-Za-z0-9.-]+", term) and re.search(r"[A-Za-z]", term):
                fts = '"' + term.replace('"', '""') + '"'
                sql += (" AND (headline LIKE ? ESCAPE '\\' OR EXISTS (SELECT 1 FROM items si "
                        "JOIN items_fts sf ON sf.rowid=si.rowid WHERE si.cluster_id=clusters.id "
                        "AND items_fts MATCH ?))")
                args.extend([pattern, fts])
            else:
                sql += (" AND (headline LIKE ? ESCAPE '\\' OR EXISTS (SELECT 1 FROM items si "
                        "WHERE si.cluster_id=clusters.id AND (si.title LIKE ? ESCAPE '\\' OR "
                        "si.summary LIKE ? ESCAPE '\\' OR si.source_name LIKE ? ESCAPE '\\')))" )
                args.extend([pattern, pattern, pattern, pattern])
    if source:
        sql += (" AND EXISTS (SELECT 1 FROM items ss WHERE ss.cluster_id=clusters.id "
                "AND (ss.source_name LIKE ? OR ss.source_id LIKE ? OR ss.grp LIKE ?))")
        pattern = f"%{source}%"
        args.extend([pattern, pattern, pattern])
    if asset:
        sql += (" AND EXISTS (SELECT 1 FROM cluster_entities ce JOIN entities e "
                "ON e.id=ce.entity_id WHERE ce.cluster_id=clusters.id "
                "AND (e.symbol=? COLLATE NOCASE OR e.id=? COLLATE NOCASE))")
        args.extend([asset, asset])
    if lang and lang != "all":
        sql += (" AND EXISTS (SELECT 1 FROM items sl WHERE sl.cluster_id=clusters.id "
                "AND sl.lang=?)")
        args.append(lang)
    if since_ts:
        sql += " AND last_ts >= ?"
        args.append(since_ts)
    sql += f" ORDER BY {'rank' if order == 'rank' else 'last_ts'} DESC LIMIT ?"
    args.append(limit)
    return conn.execute(sql, args).fetchall()


def get_cluster(conn, cluster_id: str):
    return conn.execute("SELECT * FROM clusters WHERE id=?", (cluster_id,)).fetchone()


def replace_cluster_claims(conn, cluster_id: str, claims: list[dict]) -> None:
    conn.execute("DELETE FROM claim_evidence WHERE claim_id IN "
                 "(SELECT id FROM claims WHERE cluster_id=?)", (cluster_id,))
    conn.execute("DELETE FROM claims WHERE cluster_id=?", (cluster_id,))
    now = int(time.time())
    for claim in claims:
        claim_id = hashlib.sha1(
            f"{cluster_id}\0{claim['id']}".encode("utf-8")).hexdigest()[:20]
        conn.execute(
            "INSERT INTO claims(id,cluster_id,text,status,independent_groups,groups_json,"
            "source_roles_json,method,updated_ts) VALUES(?,?,?,?,?,?,?,?,?)",
            (claim_id, cluster_id, claim["text"], claim["status"],
             claim["independent_groups"], json.dumps(claim.get("groups", [])),
             json.dumps(claim.get("source_roles", [])), claim.get("method", "extractive-v1"), now))
        conn.executemany(
            "INSERT OR REPLACE INTO claim_evidence(claim_id,item_id,source_id,source_name,source_group,"
            "source_role,url,published_ts,quote,quote_field,quote_start,quote_end,quote_hash,"
            "similarity,relation,relation_confidence,relation_method) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(claim_id, ref["item_id"], ref.get("source_id"), ref.get("source"),
              ref.get("group"), ref.get("source_role"), ref.get("url"),
              ref.get("published_ts"), ref["quote"], ref["quote_field"],
              ref["quote_start"], ref["quote_end"], ref["quote_hash"],
              ref.get("similarity", 0.0), ref.get("relation", "support"),
              ref.get("relation_confidence", ref.get("similarity", 0.0)),
              ref.get("relation_method", "heuristic-relation-v1"))
             for ref in claim.get("evidence", [])])


def cluster_claims(conn, cluster_id: str) -> list[dict]:
    out = []
    for row in conn.execute(
            "SELECT * FROM claims WHERE cluster_id=? ORDER BY independent_groups DESC,id",
            (cluster_id,)):
        claim = dict(row)
        claim["groups"] = json.loads(claim.pop("groups_json") or "[]")
        claim["source_roles"] = json.loads(claim.pop("source_roles_json") or "[]")
        claim["evidence"] = [dict(x) for x in conn.execute(
            "SELECT item_id,source_id,source_name AS source,source_group AS 'group',"
            "source_role,url,published_ts,quote,quote_field,quote_start,quote_end,quote_hash,"
            "similarity,relation,relation_confidence,relation_method "
            "FROM claim_evidence WHERE claim_id=? "
            "ORDER BY CASE relation WHEN 'refute' THEN 0 WHEN 'support' THEN 1 ELSE 2 END,"
            "similarity DESC,"
            "published_ts ASC", (row["id"],))]
        claim["relation_counts"] = {
            kind: sum(x["relation"] == kind for x in claim["evidence"])
            for kind in ("support", "refute", "unknown")}
        claim["unresolved"] = (["存在方向或关键数字相反的来源，需核对原始材料。"]
                               if claim["relation_counts"]["refute"] else
                               ["尚缺少独立采编来源的交叉验证。"]
                               if claim["independent_groups"] < 2 else [])
        out.append(claim)
    return out


# ---------------- health / runs ----------------

def record_health(conn, h: dict) -> None:
    conn.execute(
        "INSERT INTO source_health (source_id,name,tier,enabled,last_try_ts,last_ok_ts,"
        "last_error,ok_count,err_count,last_items,last_new,ms,undated,newest_ts,"
        "transport_status,parse_status,freshness_status,verdict) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(source_id) DO UPDATE SET name=excluded.name,tier=excluded.tier,"
        "enabled=excluded.enabled,last_try_ts=excluded.last_try_ts,"
        "last_ok_ts=COALESCE(excluded.last_ok_ts,source_health.last_ok_ts),"
        "last_error=excluded.last_error,"
        "ok_count=source_health.ok_count+excluded.ok_count,"
        "err_count=source_health.err_count+excluded.err_count,"
        "last_items=excluded.last_items,last_new=excluded.last_new,ms=excluded.ms,"
        "undated=excluded.undated,newest_ts=excluded.newest_ts,"
        "transport_status=excluded.transport_status,parse_status=excluded.parse_status,"
        "freshness_status=excluded.freshness_status,verdict=excluded.verdict",
        (
            h["source_id"], h["name"], h["tier"], int(h["enabled"]), h["last_try_ts"],
            h.get("last_ok_ts"), h.get("last_error"),
            int(h.get("verdict") == "healthy"),
            int(h.get("verdict") in ("down", "unhealthy")),
            h.get("n_items", 0), h.get("n_new", 0), h.get("ms", 0),
            h.get("n_undated", 0), h.get("newest_ts"), h.get("transport_status"),
            h.get("parse_status"), h.get("freshness_status"), h.get("verdict"),
        ),
    )
    conn.execute(
        "INSERT INTO source_probes(source_id,ts,verdict,ms,n_items,n_new,error) "
        "VALUES(?,?,?,?,?,?,?)",
        (h["source_id"], h["last_try_ts"], h.get("verdict", "unknown"),
         h.get("ms", 0), h.get("n_items", 0), h.get("n_new", 0), h.get("last_error")))
    conn.execute("DELETE FROM source_probes WHERE ts<?", (int(time.time()) - 30 * 86400,))
    conn.commit()


def health(conn) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM source_health ORDER BY tier ASC, source_id ASC"
    ).fetchall()


def start_run(conn) -> int:
    cur = conn.execute("INSERT INTO runs (started_ts,status) VALUES (?,?)",
                       (int(time.time()), "running"))
    conn.commit()
    return cur.lastrowid


def end_run(conn, run_id: int, **kw) -> None:
    conn.execute(
        "UPDATE runs SET ended_ts=?, n_fetched=?, n_new=?, n_clusters=?, llm_used=?, "
        "note=?,status='success',error=NULL WHERE id=?",
        (int(time.time()), kw.get("n_fetched", 0), kw.get("n_new", 0),
         kw.get("n_clusters", 0), int(kw.get("llm_used", False)),
         kw.get("note", ""), run_id),
    )
    conn.commit()


def fail_run(conn, run_id: int, error: str) -> None:
    conn.execute("UPDATE runs SET ended_ts=?,status='failed',error=? WHERE id=?",
                 (int(time.time()), error[:1000], run_id))
    conn.commit()


def last_run(conn):
    return conn.execute(
        "SELECT * FROM runs WHERE ended_ts IS NOT NULL AND "
        "(status='success' OR status IS NULL) ORDER BY id DESC LIMIT 1"
    ).fetchone()


def backup_database(destination: Path | None = None, keep: int | None = None) -> Path:
    """Create a verified backup and atomically publish it at destination."""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    destination = destination or (config.BACKUP_DIR / f"newsdesk-{stamp}.db")
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, staging_name = tempfile.mkstemp(prefix=f".{destination.name}.",
                                        suffix=".tmp", dir=destination.parent)
    os.close(fd)
    staging = Path(staging_name)
    source = connect()
    target = sqlite3.connect(staging)
    try:
        source.backup(target)
        result = target.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"backup integrity check failed: {result}")
        target.close()
        source.close()
        os.replace(staging, destination)
        destination.chmod(0o600)
        staging = None
    finally:
        target.close()
        source.close()
        if staging is not None:
            staging.unlink(missing_ok=True)
    if keep is not None and destination.parent == config.BACKUP_DIR.resolve():
        files = sorted(destination.parent.glob("newsdesk-*.db"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[max(1, keep):]:
            old.unlink()
    return destination


def recovery_drill(backup: Path) -> dict:
    """Restore a backup in isolation and prove it is usable without touching live data."""
    backup = Path(backup).resolve()
    if not backup.is_file():
        raise FileNotFoundError(backup)
    with tempfile.TemporaryDirectory(prefix="newsdesk-recovery-") as td:
        restored = Path(td) / "restored.db"
        source = sqlite3.connect(f"file:{backup}?mode=ro&immutable=1", uri=True)
        target = sqlite3.connect(restored)
        try:
            source.backup(target)
            integrity = target.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise RuntimeError(f"recovery integrity check failed: {integrity}")
            target.row_factory = sqlite3.Row
            init(target)
            tables = {row[0] for row in target.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            required = {"items", "clusters", "source_health", "source_probes", "runs",
                        "entities", "cluster_entities", "market_ticks", "alerts",
                        "alert_outbox", "alert_events"}
            missing = sorted(required - tables)
            if missing:
                raise RuntimeError(f"recovery missing tables: {', '.join(missing)}")
            counts = {name: target.execute(
                f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                for name in sorted(required)}
            return {"ok": True, "backup": str(backup), "integrity": integrity,
                    "schema_version": target.execute(
                        "PRAGMA user_version").fetchone()[0], "counts": counts}
        finally:
            target.close()
            source.close()


# ---------------- saved monitors ----------------

def create_alert(conn, *, name: str, q="", topic="all", lang="all",
                 min_cred=40.0) -> int:
    cur = conn.execute(
        "INSERT INTO alerts(name,q,topic,lang,min_cred,created_ts) VALUES(?,?,?,?,?,?)",
        (name, q, topic, lang, min_cred, int(time.time())))
    conn.commit()
    return cur.lastrowid


def alerts(conn):
    return conn.execute("SELECT * FROM alerts ORDER BY id DESC").fetchall()


def delete_alert(conn, alert_id: int) -> bool:
    conn.execute("DELETE FROM alert_outbox WHERE alert_id=?", (alert_id,))
    conn.execute("DELETE FROM alert_events WHERE alert_id=?", (alert_id,))
    cur = conn.execute("DELETE FROM alerts WHERE id=?", (alert_id,))
    conn.commit()
    return cur.rowcount > 0


# ---------------- markets / watchlist ----------------

def record_market_snapshot(conn, snapshot: dict) -> int:
    ts = int(snapshot.get("asof") or time.time())
    rows = [(x["symbol"], ts, float(x["price"]), x.get("asset"), x.get("currency"),
             x.get("change_pct"), x.get("change_bps"), x.get("source"))
            for x in snapshot.get("instruments", []) if x.get("symbol") and x.get("price") is not None]
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO market_ticks"
        "(symbol,ts,price,asset,currency,change_pct,change_bps,source) VALUES(?,?,?,?,?,?,?,?)",
        rows)
    conn.commit()
    return conn.total_changes - before


def market_history(conn, symbol: str, since_ts: int, limit=2000):
    return conn.execute(
        "SELECT * FROM (SELECT * FROM market_ticks WHERE symbol=? AND ts>=? "
        "ORDER BY ts DESC LIMIT ?) ORDER BY ts ASC",
        (symbol, since_ts, limit)).fetchall()


def prune_market_history(conn, before_ts: int) -> int:
    cur = conn.execute("DELETE FROM market_ticks WHERE ts<?", (before_ts,))
    conn.commit()
    return cur.rowcount


def add_watch(conn, symbol: str) -> None:
    conn.execute("INSERT OR IGNORE INTO watchlist(symbol,added_ts) VALUES(?,?)",
                 (symbol, int(time.time())))
    conn.commit()


def remove_watch(conn, symbol: str) -> bool:
    cur = conn.execute("DELETE FROM watchlist WHERE symbol=?", (symbol,))
    conn.commit()
    return cur.rowcount > 0


def watchlist(conn):
    return conn.execute("SELECT * FROM watchlist ORDER BY added_ts DESC").fetchall()


def enqueue_alert_delivery(conn, alert_id: int, payload: dict, now: int) -> int:
    cur = conn.execute(
        "INSERT INTO alert_outbox(alert_id,payload,next_attempt_ts,created_ts) VALUES(?,?,?,?)",
        (alert_id, json.dumps(payload, ensure_ascii=False), now, now))
    conn.commit()
    return cur.lastrowid


def due_alert_deliveries(conn, now: int, limit=20):
    return conn.execute(
        "SELECT * FROM alert_outbox WHERE delivered_ts IS NULL AND next_attempt_ts<=? "
        "ORDER BY id LIMIT ?", (now, limit)).fetchall()


def finish_alert_delivery(conn, outbox_id: int, now: int) -> None:
    conn.execute("UPDATE alert_outbox SET delivered_ts=?,last_error=NULL WHERE id=?",
                 (now, outbox_id))
    conn.commit()


def fail_alert_delivery(conn, outbox_id: int, attempts: int, error: str, now: int) -> None:
    # 1m, 2m, 4m ... capped at one hour; rows remain auditable until delivered.
    delay = min(3600, 60 * (2 ** min(attempts, 6)))
    conn.execute("UPDATE alert_outbox SET attempts=?,next_attempt_ts=?,last_error=? WHERE id=?",
                 (attempts, now + delay, error[:500], outbox_id))
    conn.commit()


def alert_events(conn, *, unread_only=False, limit=100):
    sql = ("SELECT e.*,a.name alert_name FROM alert_events e JOIN alerts a ON a.id=e.alert_id")
    if unread_only:
        sql += " WHERE e.read_ts IS NULL"
    sql += " ORDER BY e.detected_ts DESC LIMIT ?"
    return conn.execute(sql, (limit,)).fetchall()


def mark_alert_events_read(conn, event_id: int | None = None) -> int:
    now = int(time.time())
    if event_id is None:
        cur = conn.execute("UPDATE alert_events SET read_ts=? WHERE read_ts IS NULL", (now,))
    else:
        cur = conn.execute("UPDATE alert_events SET read_ts=? WHERE id=?", (now, event_id))
    conn.commit()
    return cur.rowcount
