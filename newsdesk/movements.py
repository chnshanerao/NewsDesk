"""Public-professional action ledger: what people did, never what they merely said."""
from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from functools import lru_cache

from . import config


ACTION_RULES = (
    ("acquisition", "completed_acquisition", re.compile(
        r"(?:完成(?:了)?(?:对.{0,40})?收购|完成(?:了)?.{0,40}并购|交割|收购了|"
        r"acquired|completed (?:the )?acquisition|closed (?:the )?(?:deal|acquisition))", re.I)),
    ("capital_allocate", "invested", re.compile(
        r"(?:投资(?!者|人|机构|公司|大师|顾问)(?:了|于|约|超过|至少)?|出资|注资|买入|增持|购入|invested|purchased|bought|acquired (?:a )?stake)", re.I)),
    ("capital_reduce", "sold", re.compile(
        r"(?:出售|售出|减持|清仓|divested|sold(?![- ]out)|reduced (?:its|his|her) stake)", re.I)),
    ("build_or_expand", "built_or_expanded", re.compile(
        r"(?:建成|投产|开工建设|扩建|新增产能|opened (?:a |the )?(?:factory|plant|data center)|began construction|expanded capacity)", re.I)),
    ("contract", "signed_contract", re.compile(
        r"(?:签署.{0,12}(?:合同|协议)|获得.{0,12}(?:订单|合同)|signed.{0,20}(?:contract|agreement)|awarded.{0,20}contract)", re.I)),
    ("workforce", "changed_workforce", re.compile(
        r"(?:裁员|解雇|削减.{0,8}(?:岗位|员工)|招聘.{0,8}(?:员工|工程师)|laid off|cut [\d,]+ jobs|hired [\d,]+)", re.I)),
    ("appointment", "appointed_or_resigned", re.compile(
        r"(?:获任命|正式出任|辞任|离任|appointed|named .{0,20}(?:chief|chair|director)|resigned)", re.I)),
)

PLANNING = re.compile(r"(?:计划|拟|考虑|洽谈|目标|预计|将投入|承诺|plan(?:s|ned)? to|considering|in talks|expects? to|target(?:s|ed)?)", re.I)
SPEECH = re.compile(r"(?:表示|认为|称|呼吁|警告|预测|宣称|said|says|believes|called for|warned|predicted)", re.I)
NEGATION = re.compile(r"(?:没有|并未|否认|不会|不打算|no plans? to|did not|denied|not investing|won't)", re.I)
PERSONAL = re.compile(r"(?:个人(?:资金|投资|出资)|自掏腰包|his own money|her own money|personal(?:ly)? invest)", re.I)
MONEY = re.compile(
    r"(?:(US\$|\$|USD|CNY|RMB|人民币|€|EUR|£|GBP)\s*)?"
    r"([0-9]+(?:\.[0-9]+)?)\s*(万亿|千亿|百亿|十亿|亿|千万|百万|万|trillion|billion|million|bn|m)?"
    r"\s*(美元|美金|元|人民币|欧元|英镑)?", re.I)

THEMES = (
    ("capital_allocation", "资本配置", "Capital allocation", "strategy", ("投资", "收购", "持股", "资本开支", "invest", "acquisition", "capital expenditure")),
    ("ai_infrastructure", "AI 基础设施", "AI infrastructure", "topic", ("AI", "人工智能", "算力", "数据中心", "GPU", "芯片")),
    ("cloud_infrastructure", "云与数据中心", "Cloud and data centers", "topic", ("云", "数据中心", "cloud", "data center")),
    ("digital_platforms", "数字平台", "Digital platforms", "topic", ("平台", "社交媒体", "游戏", "platform", "social media", "gaming")),
    ("energy", "能源", "Energy", "topic", ("能源", "电力", "核聚变", "太阳能", "energy", "fusion", "power")),
    ("japan", "日本", "Japan", "region", ("日本", "Japan", "Tokyo", "东京")),
    ("latin_america", "拉丁美洲", "Latin America", "region", ("拉美", "拉丁美洲", "Brazil", "Mexico", "Argentina", "巴西", "墨西哥", "阿根廷")),
    ("semiconductors", "半导体", "Semiconductors", "topic", ("半导体", "芯片", "semiconductor", "chip", "GPU")),
    ("organization", "组织与人才", "Organization and talent", "strategy", ("裁员", "招聘", "任命", "辞任", "laid off", "hired", "appointed", "resigned")),
)


def _norm(value: str) -> str:
    return unicodedata.normalize("NFKC", value or "").casefold().strip()


def _contains(text: str, alias: str) -> bool:
    if re.search(r"[A-Za-z]", alias):
        return re.search(r"(?<![A-Za-z0-9])" + re.escape(alias) + r"(?![A-Za-z0-9])", text, re.I) is not None
    return alias in text


@lru_cache(maxsize=1)
def catalog() -> list[dict]:
    return json.loads((config.DATA_DIR / "movement_people.json").read_text(encoding="utf-8"))["people"]


def sync_catalog(conn) -> None:
    now = int(time.time())
    wanted = {person["id"] for person in catalog()}
    stale = [r[0] for r in conn.execute("SELECT id FROM persons") if r[0] not in wanted]
    if stale:
        marks = ",".join("?" for _ in stale)
        # Historical actors are never deleted: published ledgers must retain attribution.
        conn.execute(f"UPDATE persons SET review_status='retired',updated_ts=? "
                     f"WHERE id IN ({marks})", [now, *stale])
    for person in catalog():
        conn.execute(
            "INSERT INTO persons(id,name,name_zh,category,roles_json,regions_json,signal_prior,"
            "commercial_conflict,political_conflict,promotional_intensity,privacy_class,"
            "review_status,created_ts,updated_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name,name_zh=excluded.name_zh,"
            "category=excluded.category,roles_json=excluded.roles_json,regions_json=excluded.regions_json,"
            "signal_prior=excluded.signal_prior,commercial_conflict=excluded.commercial_conflict,"
            "political_conflict=excluded.political_conflict,promotional_intensity=excluded.promotional_intensity,"
            "privacy_class=excluded.privacy_class,review_status=excluded.review_status,updated_ts=excluded.updated_ts",
            (person["id"], person["name"], person.get("name_zh"), person["category"],
             json.dumps(person.get("roles", []), ensure_ascii=False),
             json.dumps(person.get("regions", []), ensure_ascii=False), person["signal_prior"],
             person.get("commercial_conflict", 0), person.get("political_conflict", 0),
             person.get("promotional_intensity", 0), "public_professional_actions_only",
             "human_approved", now, now))
        conn.execute("DELETE FROM person_aliases WHERE person_id=?", (person["id"],))
        conn.executemany(
            "INSERT INTO person_aliases(person_id,alias,lang,normalized_alias) VALUES(?,?,?,?)",
            [(person["id"], alias, "zh" if re.search(r"[\u3400-\u9fff]", alias) else "en", _norm(alias))
             for alias in person.get("aliases", [])])
        for affiliation in person.get("affiliations", []):
            aid = "aff_" + hashlib.sha256(
                f"{person['id']}|{affiliation['organization']}|{affiliation['role_code']}".encode()).hexdigest()[:20]
            conn.execute(
                "INSERT INTO person_affiliations(id,person_id,entity_id,organization,role_code,"
                "relationship_type,control_level,time_precision,review_status,created_ts,updated_ts) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET entity_id=excluded.entity_id,"
                "organization=excluded.organization,role_code=excluded.role_code,"
                "relationship_type=excluded.relationship_type,control_level=excluded.control_level,"
                "updated_ts=excluded.updated_ts",
                (aid, person["id"], affiliation.get("entity_id"), affiliation["organization"],
                 affiliation["role_code"], affiliation["relationship_type"], affiliation["control_level"],
                 "current", "human_approved", now, now))
    conn.commit()


def sync_themes(conn) -> None:
    for slug, zh, en, kind, _ in THEMES:
        conn.execute("INSERT INTO movement_theme_catalog(id,slug,name_zh,name_en,kind,review_status) "
                     "VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name_zh=excluded.name_zh,name_en=excluded.name_en",
                     (slug, slug, zh, en, kind, "human_approved"))
    conn.commit()


def _person_terms(person: dict) -> list[str]:
    """Names plus currently-reviewed affiliations used for institutional attribution."""
    return [*person.get("aliases", []),
            *[a["organization"] for a in person.get("affiliations", []) if a.get("organization")]]


def _people_in(text: str) -> list[dict]:
    return [p for p in catalog() if any(_contains(text, term) for term in _person_terms(p))]


def _amount(text: str) -> dict:
    multipliers = {"万": 1e4, "百万": 1e6, "千万": 1e7, "亿": 1e8, "十亿": 1e9,
                   "百亿": 1e10, "千亿": 1e11, "万亿": 1e12,
                   "million": 1e6, "billion": 1e9, "trillion": 1e12, "m": 1e6, "bn": 1e9}
    found = []
    for match in MONEY.finditer(text):
        prefix, number, unit, suffix = match.groups()
        if not (prefix or suffix or unit):
            continue
        value = float(number) * multipliers.get((unit or "").casefold(), 1)
        marker = f"{prefix or ''} {suffix or ''}".casefold()
        currency = "USD" if "$" in marker or "usd" in marker or "美元" in marker or "美金" in marker else (
            "CNY" if "cny" in marker or "rmb" in marker or "人民币" in marker or suffix == "元" else (
            "EUR" if "€" in marker or "eur" in marker or "欧元" in marker else
            "GBP" if "£" in marker or "gbp" in marker or "英镑" in marker else None))
        found.append((value, currency, match.group(0).strip()))
    if not found:
        return {"value": None, "currency": None, "text": None, "usd": None}
    value, currency, raw = max(found, key=lambda x: x[0])
    return {"value": value, "currency": currency, "text": raw,
            "usd": value if currency == "USD" else None}


def _materiality(action_type: str, amount_usd: float | None) -> float:
    if amount_usd:
        if amount_usd >= 10_000_000_000: return 1.0
        if amount_usd >= 1_000_000_000: return .9
        if amount_usd >= 100_000_000: return .78
        if amount_usd >= 10_000_000: return .62
    return .68 if action_type in {"acquisition", "build_or_expand"} else .52


def _object_after_action(sentence: str, action_match) -> tuple[str, str]:
    raw = sentence[action_match.end():].strip(" ：:，,.-")[:240]
    normalized = MONEY.sub(" ", raw).strip()
    normalized = re.sub(r"^(?:了|约|至少|超过|向|对|于|in|into|to|for)\s*", "", normalized, flags=re.I)
    normalized = re.sub(r"[^0-9a-z\u3400-\u9fff]+", " ", _norm(normalized)).strip()
    return raw, normalized


def _compatible_fact(base, other) -> bool:
    _, _, base_sentence, _, _, _, base_action = base
    _, _, other_sentence, _, _, _, other_action = other
    _, base_object = _object_after_action(base_sentence, base_action)
    _, other_object = _object_after_action(other_sentence, other_action)
    if not base_object or not other_object or base_object != other_object:
        return False
    a, b = _amount(base_sentence), _amount(other_sentence)
    if a["value"] is not None and b["value"] is not None:
        if a["currency"] and b["currency"] and a["currency"] != b["currency"]:
            return False
        if abs(a["value"] - b["value"]) / max(a["value"], b["value"]) > .05:
            return False
    return True


def _themes(text: str, action_type: str) -> list[str]:
    result = [slug for slug, _, _, _, terms in THEMES if any(t.casefold() in text.casefold() for t in terms)]
    if action_type in {"workforce", "appointment"} and "organization" not in result:
        result.append("organization")
    return result


def extract_cluster(conn, cluster_id: str, source_meta: dict[str, dict]) -> list[str]:
    cluster = conn.execute("SELECT * FROM clusters WHERE id=?", (cluster_id,)).fetchone()
    items = list(conn.execute("SELECT * FROM items WHERE cluster_id=? ORDER BY tier,published_ts", (cluster_id,)))
    if not cluster or not items:
        return []
    text = "\n".join(filter(None, [cluster["headline"], *[f"{x['title']}\n{x['summary'] or ''}\n{x['body'] or ''}" for x in items]]))
    people = _people_in(text)
    if not people:
        return []
    now = int(time.time())
    created = []
    for person in people:
        matches = []
        for item in items:
            for field in ("title", "summary", "body"):
                for segment in re.split(r"[\n。！？!?;；]+", item[field] or ""):
                    segment = segment.strip()
                    named = any(_contains(segment, alias) for alias in person.get("aliases", []))
                    affiliated = any(_contains(segment, a["organization"])
                                     for a in person.get("affiliations", []) if a.get("organization"))
                    official = source_meta.get(item["source_id"], {}).get("source_role") == "official"
                    # Reporting that merely mentions a company is not enough to assign
                    # its action to an executive. Institution-based attribution is only
                    # generated from the institution's own reviewed source.
                    if not segment or not (named or (official and affiliated)):
                        continue
                    action = next(((kind, verb, rule, rule.search(segment))
                                   for kind, verb, rule in ACTION_RULES if rule.search(segment)), None)
                    if not action or PLANNING.search(segment) or NEGATION.search(segment):
                        continue
                    colon = min([x for x in (segment.find(":"), segment.find("：")) if x >= 0], default=-1)
                    alias_pos = min((segment.casefold().find(a.casefold()) for a in person.get("aliases", [])
                                     if segment.casefold().find(a.casefold()) >= 0), default=9999)
                    if SPEECH.search(segment) or (0 <= colon < 45 and alias_pos < colon):
                        continue
                    matches.append((item, field, segment, *action))
        if not matches:
            continue
        base_match = matches[0]
        _, _, action_sentence, action_type, verb_code, _, action_match = base_match
        matches = [m for m in matches if m[3] == action_type and m[4] == verb_code
                   and _compatible_fact(base_match, m)]
        support_items = {m[0]["id"]: m[0] for m in matches}
        independent_owners = {
            source_meta.get(x["source_id"], {}).get("owner", x["source_id"])
            for x in support_items.values()
            if source_meta.get(x["source_id"], {}).get("source_role") in ("reporting", "wire")
        }
        primary = any(source_meta.get(x["source_id"], {}).get("source_role") == "official"
                      for x in support_items.values())
        fact_confidence = min(.98, .45 + .12 * min(3, len(independent_owners)) + (.18 if primary else 0))
        verification = "verified" if primary or len(independent_owners) >= 2 else "candidate"
        amount = _amount(action_sentence)
        personal = bool(PERSONAL.search(action_sentence))
        affiliations = person.get("affiliations", [])
        organization_hit = next((a for a in affiliations
                                 if a["organization"].casefold() in action_sentence.casefold()), None)
        actor_kind = "personal" if personal else (
            "controlled_institution" if organization_hit and organization_hit.get("control_level") == "controls"
            else "associated_institution")
        control_basis = "explicit personal capital" if personal else (
            organization_hit.get("control_level", "employment association") if organization_hit else "person named in report")
        dedupe = hashlib.sha256(
            f"{cluster_id}|{person['id']}|{action_type}|{verb_code}".encode()).hexdigest()
        movement_id = "mov_" + dedupe[:24]
        existing = conn.execute(
            "SELECT workflow_status FROM movement_events WHERE dedupe_key=?", (dedupe,)).fetchone()
        if existing and existing["workflow_status"] != "draft":
            created.append(movement_id)
            continue
        title = cluster["headline"]
        object_text, _ = _object_after_action(action_sentence, action_match)
        observed = f"报道材料显示：{action_sentence[:500]}"
        boundary = ("该记录只确认公开材料中的行动，不证明报道所暗示的动机或因果关系。"
                    "机构资本不等同于相关人物的个人出资。")
        conn.execute(
            "INSERT INTO movement_events(id,cluster_id,action_type,verb_code,actor_kind,title,summary,object_text,"
            "occurred_from_ts,time_precision,disclosed_ts,amount_value_text,amount_currency,amount_usd_text,"
            "amount_basis,geography_json,verification_status,workflow_status,confidence,materiality_score,"
            "marketing_risk,observed_fact,analytical_boundary,unknowns_json,extraction_method,dedupe_key,"
            "created_ts,updated_ts,execution_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(dedupe_key) DO UPDATE SET title=excluded.title,summary=excluded.summary,"
            "disclosed_ts=excluded.disclosed_ts,amount_value_text=excluded.amount_value_text,"
            "amount_currency=excluded.amount_currency,amount_usd_text=excluded.amount_usd_text,"
            "confidence=excluded.confidence,materiality_score=excluded.materiality_score,"
            "verification_status=CASE WHEN movement_events.workflow_status='published' "
            "THEN movement_events.verification_status ELSE excluded.verification_status END,"
            "observed_fact=excluded.observed_fact,updated_ts=excluded.updated_ts",
            (movement_id, cluster_id, action_type, verb_code, actor_kind, title,
             (items[0]["body"] or items[0]["summary"] or "")[:900], object_text,
             None, "unknown",
             cluster["last_ts"], amount["text"], amount["currency"],
             str(amount["usd"]) if amount["usd"] is not None else None, "reported_amount",
             "[]", verification, "draft", fact_confidence,
             _materiality(action_type, amount["usd"]), person.get("promotional_intensity", 0),
             observed, boundary, json.dumps(["exact execution date may differ from disclosure date"], ensure_ascii=False),
             "rules-v1", dedupe, now, now, "completed"))
        conn.execute("DELETE FROM movement_persons WHERE movement_id=?", (movement_id,))
        conn.execute(
            "INSERT INTO movement_persons(movement_id,person_id,role,attribution_confidence,control_basis) "
            "VALUES(?,?,?,?,?)", (movement_id, person["id"],
            "beneficial_owner" if personal else "executive", .95 if personal or organization_hit else .65,
            control_basis))
        conn.execute("DELETE FROM movement_evidence WHERE movement_id=?", (movement_id,))
        evidence_ids = []
        evidence_by_item = {}
        evidence_matches = {}
        for item, field, segment, *_ in matches:
            evidence_matches.setdefault(item["id"], (item, field, segment))
        for item, field, quote in evidence_matches.values():
            quote_hash = hashlib.sha256(quote.encode()).hexdigest()
            evidence_id = "mev_" + hashlib.sha256(f"{movement_id}|{item['id']}|{quote_hash}".encode()).hexdigest()[:24]
            meta = source_meta.get(item["source_id"], {})
            role = "institutional_original" if meta.get("source_role") == "official" else (
                "independent_media" if meta.get("source_role") in ("reporting", "wire") else "clue")
            conn.execute(
                "INSERT INTO movement_evidence(id,movement_id,item_id,source_id,source_name,source_owner,"
                "source_role,tier,url,title,published_ts,retrieved_ts,quote,quote_field,quote_start,quote_end,"
                "quote_hash,relation,independence_group,relation_confidence,original_lang) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (evidence_id, movement_id, item["id"], item["source_id"], item["source_name"],
                 meta.get("owner", item["source_id"]), role, item["tier"], item["url"] or "",
                 item["title"], item["published_ts"], item["fetched_ts"], quote,
                 field, 0, len(quote),
                 quote_hash, "support", meta.get("owner", item["source_id"]), .8, item["lang"]))
            evidence_ids.append(evidence_id)
            evidence_by_item[item["id"]] = evidence_id
        conn.execute("DELETE FROM movement_fact_citations WHERE movement_id=?", (movement_id,))
        for field in (["actor", "action", "object"] if object_text else ["actor", "action"]):
            conn.executemany(
                "INSERT INTO movement_fact_citations(movement_id,field_name,evidence_id) VALUES(?,?,?)",
                [(movement_id, field, evidence_id) for evidence_id in evidence_ids])
        for item, _, sentence in evidence_matches.values():
            evidence_id = evidence_by_item[item["id"]]
            candidate_amount = _amount(sentence)
            if amount["value"] is not None and candidate_amount["value"] == amount["value"] \
                    and candidate_amount["currency"] == amount["currency"]:
                conn.execute("INSERT INTO movement_fact_citations VALUES(?,?,?)",
                             (movement_id, "amount", evidence_id))
            if item["published_ts"]:
                conn.execute("INSERT INTO movement_fact_citations VALUES(?,?,?)",
                             (movement_id, "disclosure_date", evidence_id))
        conn.execute("DELETE FROM movement_themes WHERE movement_id=?", (movement_id,))
        for slug in _themes(text, action_type):
            conn.execute("INSERT INTO movement_themes(movement_id,theme_id,assignment_method,confidence) VALUES(?,?,?,?)",
                         (movement_id, slug, "rules-v1", .8))
        created.append(movement_id)
    conn.commit()
    return created


def extract_recent(conn, source_meta: dict[str, dict], since_ts: int) -> dict:
    sync_catalog(conn)
    sync_themes(conn)
    cluster_ids = [r[0] for r in conn.execute("SELECT id FROM clusters WHERE last_ts>=?", (since_ts,))]
    n = 0
    active = []
    for cluster_id in cluster_ids:
        created = extract_cluster(conn, cluster_id, source_meta)
        active.extend(created)
        n += len(created)
    # Re-extraction is a replacement for draft machine candidates. Human-reviewed or
    # published records are immutable here; only stale rules-v1 drafts are removed.
    if cluster_ids:
        cluster_marks = ",".join("?" for _ in cluster_ids)
        if active:
            active_marks = ",".join("?" for _ in active)
            conn.execute(
                f"DELETE FROM movement_events WHERE extraction_method='rules-v1' "
                f"AND workflow_status='draft' AND cluster_id IN ({cluster_marks}) "
                f"AND id NOT IN ({active_marks})", [*cluster_ids, *active])
        else:
            conn.execute(
                f"DELETE FROM movement_events WHERE extraction_method='rules-v1' "
                f"AND workflow_status='draft' AND cluster_id IN ({cluster_marks})", cluster_ids)
    conn.commit()
    return {"clusters_scanned": len(cluster_ids), "candidates": n}


def _event_payload(conn, row) -> dict:
    event = dict(row)
    for key in ("geography_json", "unknowns_json"):
        event[key[:-5]] = json.loads(event.pop(key) or "[]")
    event["persons"] = [dict(x) for x in conn.execute(
        "SELECT p.id,p.name,p.name_zh,p.category,mp.role,mp.attribution_confidence,mp.control_basis "
        "FROM movement_persons mp JOIN persons p ON p.id=mp.person_id WHERE mp.movement_id=?",
        (event["id"],))]
    event["themes"] = [dict(x) for x in conn.execute(
        "SELECT t.slug,t.name_zh,t.name_en,t.kind,mt.confidence FROM movement_themes mt "
        "JOIN movement_theme_catalog t ON t.id=mt.theme_id WHERE mt.movement_id=?", (event["id"],))]
    event["evidence"] = [dict(x) for x in conn.execute(
        "SELECT id,source_name,source_owner,source_role,tier,url,title,published_ts,quote,relation,"
        "independence_group,original_lang FROM movement_evidence WHERE movement_id=? "
        "ORDER BY CASE source_role WHEN 'regulatory_filing' THEN 0 WHEN 'institutional_original' THEN 1 "
        "WHEN 'independent_media' THEN 2 ELSE 3 END,tier", (event["id"],))]
    citations = {}
    for cite in conn.execute("SELECT field_name,evidence_id FROM movement_fact_citations WHERE movement_id=?",
                             (event["id"],)):
        citations.setdefault(cite["field_name"], []).append(cite["evidence_id"])
    event["fact_citations"] = citations
    return event


def list_events(conn, *, person=None, theme=None, action_type=None, region=None,
                workflow="published", verification="verified", order="recent", after=None,
                limit=50, offset=0) -> dict:
    sql = "SELECT DISTINCT me.* FROM movement_events me"
    joins, where, args = [], [], []
    if person:
        joins.append("JOIN movement_persons mp ON mp.movement_id=me.id")
        where.append("mp.person_id=?"); args.append(person)
    if theme:
        joins.append("JOIN movement_themes mt ON mt.movement_id=me.id")
        where.append("mt.theme_id=?"); args.append(theme)
    if action_type:
        where.append("me.action_type=?"); args.append(action_type)
    if region:
        where.append("me.geography_json LIKE ?"); args.append(f'%"{region}"%')
    if after is not None:
        where.append("COALESCE(me.occurred_from_ts,me.disclosed_ts)>=?"); args.append(after)
    if workflow != "all":
        where.append("me.workflow_status=?"); args.append(workflow)
    if verification != "all":
        where.append("me.verification_status=?"); args.append(verification)
    query = " ".join([sql, *joins]) + (" WHERE " + " AND ".join(where) if where else "")
    total = conn.execute("SELECT COUNT(*) FROM (" + query + ")", args).fetchone()[0]
    ordering = ("me.materiality_score DESC,COALESCE(me.occurred_from_ts,me.disclosed_ts) DESC"
                if order == "materiality" else
                "COALESCE(me.occurred_from_ts,me.disclosed_ts) DESC,me.disclosed_ts DESC,me.materiality_score DESC")
    rows = conn.execute(query + f" ORDER BY {ordering} LIMIT ? OFFSET ?",
                        [*args, limit, offset]).fetchall()
    return {"items": [_event_payload(conn, r) for r in rows], "total": total,
            "limit": limit, "offset": offset, "generated_at": int(time.time())}


def get_event(conn, movement_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM movement_events WHERE id=?", (movement_id,)).fetchone()
    return _event_payload(conn, row) if row else None


def review_event(conn, movement_id: str, *, verification_status: str,
                 workflow_status: str, reviewer: str = "admin", reason: str = "") -> dict:
    if verification_status not in {"candidate", "verified", "disputed", "rejected"}:
        raise ValueError("invalid verification_status")
    if workflow_status not in {"draft", "reviewed", "published", "withdrawn"}:
        raise ValueError("invalid workflow_status")
    row = conn.execute("SELECT * FROM movement_events WHERE id=?", (movement_id,)).fetchone()
    if not row:
        raise LookupError("movement not found")
    if workflow_status == "published":
        failures = []
        if verification_status != "verified":
            failures.append("published movement must be verified")
        if not (row["observed_fact"] or "").strip():
            failures.append("observed_fact required")
        if not (row["object_text"] or "").strip():
            failures.append("object_text required")
        if not conn.execute("SELECT 1 FROM movement_persons WHERE movement_id=?", (movement_id,)).fetchone():
            failures.append("person attribution required")
        evidence = list(conn.execute(
            "SELECT source_role,independence_group,relation FROM movement_evidence "
            "WHERE movement_id=? AND relation='support'", (movement_id,)))
        primary = any(x["source_role"] in ("regulatory_filing", "institutional_original")
                      for x in evidence)
        independent = {x["independence_group"] for x in evidence
                       if x["source_role"] == "independent_media" and x["independence_group"]}
        if not primary and len(independent) < 2:
            failures.append("requires a primary record or two independent media owners")
        cited = {x[0] for x in conn.execute(
            "SELECT DISTINCT field_name FROM movement_fact_citations WHERE movement_id=?",
            (movement_id,))}
        required = {"actor", "action", "object", "disclosure_date"}
        if row["amount_value_text"]:
            required.add("amount")
        if not required.issubset(cited):
            failures.append("critical fields require citations")
        if failures:
            raise ValueError("; ".join(failures))
    conn.execute(
        "UPDATE movement_events SET verification_status=?,workflow_status=?,updated_ts=? WHERE id=?",
        (verification_status, workflow_status, int(time.time()), movement_id))
    conn.execute(
        "INSERT INTO movement_reviews(movement_id,previous_verification,new_verification,"
        "previous_workflow,new_workflow,reviewer,reason,reviewed_ts) VALUES(?,?,?,?,?,?,?,?)",
        (movement_id, row["verification_status"], verification_status,
         row["workflow_status"], workflow_status, reviewer[:80], reason[:1000], int(time.time())))
    conn.commit()
    return get_event(conn, movement_id)


def sync_curated(conn, entries: list[dict] | None = None) -> int:
    """Import human-reviewed benchmark actions and pass each through publication gates."""
    if entries is None:
        seed_path = config.DATA_DIR / "movement_seed.json"
        if not seed_path.exists():
            return 0
        entries = json.loads(seed_path.read_text(encoding="utf-8"))["movements"]
    sync_catalog(conn)
    sync_themes(conn)
    now = int(time.time())
    imported = 0
    for entry in entries:
        movement_id = entry["id"]
        conn.execute(
            "INSERT INTO movement_events(id,action_type,verb_code,actor_kind,actor_entity_id,title,summary,"
            "object_text,occurred_from_ts,occurred_to_ts,time_precision,disclosed_ts,amount_value_text,"
            "amount_currency,amount_usd_text,amount_basis,geography_json,verification_status,workflow_status,confidence,"
            "materiality_score,marketing_risk,observed_fact,analytical_boundary,unknowns_json,"
            "extraction_method,dedupe_key,created_ts,updated_ts,execution_status) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO NOTHING",
            (movement_id, entry["action_type"], entry["verb_code"], entry["actor_kind"],
             entry.get("actor_entity_id"), entry["title"], entry.get("summary", ""),
             entry["object_text"], entry.get("occurred_from_ts"), entry.get("occurred_to_ts"),
             entry.get("time_precision", "day"), entry["disclosed_ts"],
             entry.get("amount_value_text"), entry.get("amount_currency"),
             entry.get("amount_usd_text"), entry.get("amount_basis"),
             json.dumps(entry.get("geography", []), ensure_ascii=False),
             "verified", "reviewed", entry["confidence"], entry["materiality_score"],
             entry.get("marketing_risk", 0), entry["observed_fact"], entry["analytical_boundary"],
             json.dumps(entry.get("unknowns", []), ensure_ascii=False), "human-curated-v1",
             entry.get("dedupe_key", movement_id), now, now,
             entry.get("execution_status", "completed")))
        existing = conn.execute(
            "SELECT workflow_status,extraction_method FROM movement_events WHERE id=?", (movement_id,)
        ).fetchone()
        if existing["workflow_status"] == "published":
            # Curated seeds are immutable after publication except for additive normalized
            # fields introduced by a later schema version. Never let machine extraction
            # rewrite a reviewed fact or its evidence ledger.
            if existing["extraction_method"] == "human-curated-v1" and entry.get("amount_usd_text"):
                conn.execute(
                    "UPDATE movement_events SET amount_usd_text=COALESCE(amount_usd_text,?),updated_ts=? "
                    "WHERE id=?",
                    (entry["amount_usd_text"], now, movement_id))
                conn.commit()
            continue
        conn.execute("DELETE FROM movement_persons WHERE movement_id=?", (movement_id,))
        for actor in entry["persons"]:
            conn.execute(
                "INSERT INTO movement_persons(movement_id,person_id,role,attribution_confidence,control_basis) "
                "VALUES(?,?,?,?,?)",
                (movement_id, actor["person_id"], actor["role"], actor["attribution_confidence"],
                 actor["control_basis"]))
        conn.execute("DELETE FROM movement_evidence WHERE movement_id=?", (movement_id,))
        field_citations = []
        for evidence in entry["evidence"]:
            evidence_id = evidence["id"]
            quote = evidence["quote"]
            quote_hash = hashlib.sha256(quote.encode()).hexdigest()
            conn.execute(
                "INSERT INTO movement_evidence(id,movement_id,source_id,source_name,source_owner,source_role,"
                "tier,url,title,published_ts,retrieved_ts,quote,quote_field,quote_start,quote_end,quote_hash,"
                "relation,independence_group,relation_confidence,original_lang,archived_ref) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (evidence_id, movement_id, evidence.get("source_id"), evidence["source_name"],
                 evidence["source_owner"], evidence["source_role"], evidence.get("tier", 0),
                 evidence["url"], evidence.get("title"), evidence.get("published_ts"), now, quote,
                 "curated_quote", 0, len(quote), quote_hash, "support",
                 evidence["independence_group"], 1.0, evidence.get("original_lang", "en"),
                 evidence.get("archived_ref")))
            field_citations.extend((field, evidence_id) for field in evidence["fields"])
        conn.execute("DELETE FROM movement_fact_citations WHERE movement_id=?", (movement_id,))
        conn.executemany(
            "INSERT INTO movement_fact_citations(movement_id,field_name,evidence_id) VALUES(?,?,?)",
            [(movement_id, field, evidence_id) for field, evidence_id in field_citations])
        conn.execute("DELETE FROM movement_themes WHERE movement_id=?", (movement_id,))
        conn.executemany(
            "INSERT INTO movement_themes(movement_id,theme_id,assignment_method,confidence) VALUES(?,?,?,?)",
            [(movement_id, theme, "human-curated-v1", 1.0) for theme in entry.get("themes", [])])
        conn.commit()
        review_event(conn, movement_id, verification_status="verified", workflow_status="published",
                     reviewer="curated-seed", reason="Verified against cited primary records")
        imported += 1
    return imported


def trend_summary(conn, after: int | None = None) -> dict:
    """Aggregate only published actions; an event is counted once per theme."""
    rows = list(conn.execute(
        "SELECT me.id,me.title,me.action_type,me.actor_kind,me.occurred_from_ts,me.disclosed_ts,"
        "me.amount_usd_text,mt.theme_id,t.name_zh theme_name_zh,t.kind,p.id person_id,"
        "p.name person_name,p.name_zh person_name_zh "
        "FROM movement_events me JOIN movement_themes mt ON mt.movement_id=me.id "
        "JOIN movement_theme_catalog t ON t.id=mt.theme_id "
        "JOIN movement_persons mp ON mp.movement_id=me.id JOIN persons p ON p.id=mp.person_id "
        "WHERE me.workflow_status='published' AND me.verification_status='verified' "
        + ("AND COALESCE(me.occurred_from_ts,me.disclosed_ts)>=?" if after is not None else ""),
        (() if after is None else (after,))))
    grouped = {}
    for row in rows:
        trend = grouped.setdefault(row["theme_id"], {
            "id": row["theme_id"], "name": row["theme_name_zh"], "kind": row["kind"],
            "movement_ids": set(), "people": {}, "total_usd": 0.0, "latest_ts": 0,
            "actions": []})
        if row["id"] not in trend["movement_ids"]:
            trend["movement_ids"].add(row["id"])
            trend["total_usd"] += float(row["amount_usd_text"] or 0)
            trend["latest_ts"] = max(trend["latest_ts"], row["occurred_from_ts"] or row["disclosed_ts"] or 0)
            trend["actions"].append({"id": row["id"], "title": row["title"],
                                     "action_type": row["action_type"],
                                     "ts": row["occurred_from_ts"] or row["disclosed_ts"]})
        trend["people"][row["person_id"]] = row["person_name_zh"] or row["person_name"]
    items = []
    for trend in grouped.values():
        trend["movement_count"] = len(trend.pop("movement_ids"))
        trend["people"] = [{"id": k, "name": v} for k, v in trend["people"].items()]
        trend["person_count"] = len(trend["people"])
        trend["total_usd"] = str(round(trend["total_usd"], 2)) if trend["total_usd"] else None
        trend["signal"] = ("collective" if trend["person_count"] >= 3 else
                           "converging" if trend["person_count"] >= 2 else "single_actor")
        trend["actions"].sort(key=lambda x: x["ts"] or 0, reverse=True)
        items.append(trend)
    items.sort(key=lambda x: (x["person_count"], x["movement_count"], x["latest_ts"]), reverse=True)
    people_n = conn.execute(
        "SELECT COUNT(DISTINCT mp.person_id) FROM movement_persons mp JOIN movement_events me "
        "ON me.id=mp.movement_id WHERE me.workflow_status='published' AND me.verification_status='verified' "
        + ("AND COALESCE(me.occurred_from_ts,me.disclosed_ts)>=?" if after is not None else ""),
        (() if after is None else (after,))).fetchone()[0]
    return {"items": items, "movement_count": len({r["id"] for r in rows}),
            "person_count": people_n, "generated_at": int(time.time())}


def monitor_status(conn) -> dict:
    """Expose coverage health without leaking unpublished candidate details."""
    now = int(time.time())
    after = now - 365 * 86400
    tracked = conn.execute(
        "SELECT COUNT(*) FROM persons WHERE review_status='human_approved'").fetchone()[0]
    recent_published = conn.execute(
        "SELECT COUNT(*) FROM movement_events WHERE workflow_status='published' "
        "AND verification_status='verified' AND COALESCE(occurred_from_ts,disclosed_ts)>=?",
        (after,)).fetchone()[0]
    recent_people = conn.execute(
        "SELECT COUNT(DISTINCT mp.person_id) FROM movement_events me "
        "JOIN movement_persons mp ON mp.movement_id=me.id WHERE me.workflow_status='published' "
        "AND me.verification_status='verified' AND COALESCE(me.occurred_from_ts,me.disclosed_ts)>=?",
        (after,)).fetchone()[0]
    candidates = conn.execute(
        "SELECT COUNT(*) FROM movement_events WHERE workflow_status='draft' "
        "AND COALESCE(occurred_from_ts,disclosed_ts)>=?", (after,)).fetchone()[0]
    latest = conn.execute(
        "SELECT MAX(COALESCE(occurred_from_ts,disclosed_ts)) FROM movement_events "
        "WHERE workflow_status='published' AND verification_status='verified'").fetchone()[0]
    return {"tracked_people": tracked, "recent_published": recent_published,
            "recent_people": recent_people, "candidates_pending_review": candidates,
            "latest_published_action_ts": latest, "window_days": 365,
            "coverage_status": "building" if recent_people < max(10, tracked // 2) else "healthy",
            "generated_at": now}


def list_people(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT p.*,COUNT(DISTINCT CASE WHEN me.workflow_status='published' THEN me.id END) published_count,"
        "COUNT(DISTINCT me.id) candidate_count FROM persons p "
        "LEFT JOIN movement_persons mp ON mp.person_id=p.id "
        "LEFT JOIN movement_events me ON me.id=mp.movement_id GROUP BY p.id ORDER BY p.signal_prior DESC,p.name"
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["roles"] = json.loads(item.pop("roles_json") or "[]")
        item["regions"] = json.loads(item.pop("regions_json") or "[]")
        result.append(item)
    return result
