"""信源档案与评分：回答『哪些信源该纳入 NewsDesk、哪些该重点关注』。

设计上最重要的一条：**产出量只做及格线，不做加分项。**

  本库 24 小时内 2541 条稿件里，chinanews_scroll 一家占 21.6%，前三家占 42.9%。
  如果评分按产出量线性给分，治理结论会直接变成「谁刷得多谁重要」—— 而信源集中度
  过高本身就是要治的毛病。所以供稿项用 log 压缩并在 SUPPLY_FULL 条封顶：
  一个每天稳定出 20 条的源和一个出 500 条的源，在这一项上同分。

其余五项衡量的是『这个源提供了别处拿不到的东西吗』：

  首发    在**有别家也在报**的事件里，它是不是最早那一篇（只有自己一篇的事件不算独家）
  被印证  它的稿件是否落在有 ≥2 个独立集团的事件里（说明它报的事是真事）
  相关    它的稿件与关注画像的平均相关度（真但无用的新闻不算贡献）
  净度    它的稿件有多少落进噪音区
  存疑    它的稿件平均存疑度（用词可疑、来源结构差的源要被扣）

评分只描述『这个源近期表现如何』，不自动改 sources.json。分档建议由人过一遍再落地：
一个源产出为 0 可能是它没更新，也可能是本机出网被墙 —— 数据分不出这两种，人能。
"""
import math

from . import config
from .normalize import now_ts

SUPPLY_FULL = 20        # 窗口内 20 条即供稿项满分
WEIGHTS = {"supply": .20, "lead": .20, "corroborated": .15,
           "relevance": .20, "cleanliness": .15, "trust": .10}
# 「重点关注」需要的最小样本量。低于这个数，各项比率都是噪音 —— 一个源只发了 1 条、
# 那条恰好干净且相关，六项里有四项自动满分，它就会排在天天有产出的通讯社前面。
# 样本不足不是缺点，但也不是优点：分档压回 standard，等它攒够稿件再说。
MIN_ITEMS_FOR_CORE = 10
# 一次源（官方声明、监管披露）自己就是当事人：它发的东西无从「抢首发」，也不需要别人
# 来印证『他是否这么说了』。拿首发和被印证去量它，等于结构性地扣掉它 35% 的分 ——
# 实测未修正时 ecb_press / apple_newsroom / sec_tsmc 全被推进「观察期」，而这些恰恰是
# 最该留下的源。所以这两项对一次源标为不适用，权重按比例摊到适用项上。
# 这与存疑度里放过一次源是同一个判断，两处必须一致。
NOT_APPLICABLE_DIMS = {"official": {"lead", "corroborated"},
                       "regulatory_filing": {"lead", "corroborated"}}


def _weights_for(role: str) -> dict:
    dropped = NOT_APPLICABLE_DIMS.get(role, set())
    kept = {k: w for k, w in WEIGHTS.items() if k not in dropped}
    total = sum(kept.values()) or 1.0
    return {k: w / total for k, w in kept.items()}
GRADE_BANDS = [(.65, "core", "建议重点关注"),
               (.40, "standard", "常规纳入"),
               (.0001, "probation", "观察期：有产出但贡献弱"),
               (0, "dormant", "近期零产出，需查明原因再决定去留")]

# sources.json 里 focus 的合法取值。core 只影响排序，不影响可信度评分 ——
# 「我更关注谁」和「谁说的话更可信」是两件事，混在一起就等于用偏好污染证据判断。
FOCUS_LEVELS = ("core", "standard", "probation")
FOCUS_RANK_MULTIPLIER = {"core": 1.15, "standard": 1.0, "probation": 0.85}


def focus_of(source: dict) -> str:
    value = source.get("focus")
    return value if value in FOCUS_LEVELS else "standard"


def grade_of(score: float, n_items: int) -> tuple[str, str]:
    if not n_items:
        return "dormant", "近期零产出，需查明原因再决定去留"
    for threshold, code, label in GRADE_BANDS:
        if score >= threshold:
            # 样本不足既不能当优点也不能当缺点：两个方向都压回 standard。
            # 只压高档不压低档的话，低产的一次源会被这条规则单向地推进观察期。
            if code in ("core", "probation") and n_items < MIN_ITEMS_FOR_CORE:
                return "standard", (f"窗口内仅 {n_items} 条，样本不足以定档，"
                                    f"暂按常规纳入")
            return code, label
    return "probation", "观察期：有产出但贡献弱"


def compute(conn, reg: dict, profile: dict, window_h: int | None = None) -> list[dict]:
    """按窗口算每个信源的档案。只读库，不写。"""
    window_h = window_h or config.CLUSTER_WINDOW_H
    now = now_ts()
    since = now - window_h * 3600
    min_rel = float(profile.get("min_relevance", 0.28))
    rows = conn.execute(
        "SELECT i.source_id,i.source_name,i.cluster_id,i.published_ts,i.fetched_ts,"
        "c.cred,c.relevance,c.doubt,c.n_groups,c.first_ts "
        "FROM items i LEFT JOIN clusters c ON c.id=i.cluster_id "
        "WHERE COALESCE(i.published_ts,i.fetched_ts)>=?", (since,)).fetchall()

    stats: dict[str, dict] = {}
    for row in rows:
        st = stats.setdefault(row["source_id"], {
            "n_items": 0, "n_clustered": 0, "n_lead": 0, "n_corroborated": 0,
            "n_noise": 0, "n_contested": 0, "rel": [], "cred": [], "doubt": []})
        st["n_items"] += 1
        if not row["cluster_id"]:
            continue
        st["n_clustered"] += 1
        st["rel"].append(float(row["relevance"] or 0))
        st["cred"].append(float(row["cred"] or 0))
        st["doubt"].append(float(row["doubt"] or 0))
        # 首发只在「有别的集团也在报」的事件里算数，两条理由：
        #   一，只有你一篇的事件里你当然是最早的 —— 那是没人跟，不是抢到独家；
        #   二，「别家」必须跨集团。chinanews 的滚动版和财经版同一秒发同一条稿，
        #      按篇数算就成了两家在赛跑，实测让最大集团的首发项虚高到 320/378。
        # 分母因此取跨集团的事件数，而不是全部事件数。
        if int(row["n_groups"] or 0) >= 2:
            st["n_contested"] += 1
            published = int(row["published_ts"] or row["fetched_ts"] or 0)
            if row["first_ts"] and published <= int(row["first_ts"]):
                st["n_lead"] += 1
        if int(row["n_groups"] or 0) >= 2:
            st["n_corroborated"] += 1
        if float(row["cred"] or 0) < 40 or float(row["relevance"] or 0) < min_rel:
            st["n_noise"] += 1

    health = {h["source_id"]: dict(h) for h in _health(conn)}
    cards = []
    for src in reg["sources"]:
        st = stats.get(src["id"], {"n_items": 0, "n_clustered": 0, "n_lead": 0,
                                   "n_corroborated": 0, "n_noise": 0, "n_contested": 0,
                                   "rel": [], "cred": [], "doubt": []})
        n, clustered = st["n_items"], st["n_clustered"]
        contested = st.get("n_contested", 0)
        mean = lambda xs: (sum(xs) / len(xs)) if xs else 0.0  # noqa: E731
        dims = {
            "supply": min(1.0, math.log1p(n) / math.log1p(SUPPLY_FULL)),
            "lead": (st["n_lead"] / contested) if contested else 0.0,
            "corroborated": (st["n_corroborated"] / clustered) if clustered else 0.0,
            "relevance": min(1.0, mean(st["rel"]) / .6) if clustered else 0.0,
            "cleanliness": (1 - st["n_noise"] / clustered) if clustered else 0.0,
            "trust": max(0.0, 1 - mean(st["doubt"]) / 50) if clustered else 0.0,
        }
        role = src.get("source_role", config.source_role(src))
        weights = _weights_for(role)
        score = sum(w * dims[k] for k, w in weights.items())
        verdict = health.get(src["id"], {}).get("verdict") or "unseen"
        # 抓不到的源不该被评成『质量差』——它是运维问题，不是编辑问题。分档单列 dormant，
        # 但把 transport 状态一起记进档案，人一看就知道该修管道还是该换源。
        grade, grade_note = grade_of(score, n)
        cards.append({
            "source_id": src["id"], "name": src["name"], "tier": src["tier"],
            "group": src.get("group", src["id"]),
            "enabled": bool(src.get("enabled", True)),
            "focus": focus_of(src),
            "source_role": role, "lang": src.get("lang", "zh"),
            "not_applicable": sorted(NOT_APPLICABLE_DIMS.get(role, set())),
            "n_items": n, "n_clustered": clustered, "n_contested": contested,
            "n_lead": st["n_lead"],
            "n_corroborated": st["n_corroborated"], "n_noise": st["n_noise"],
            "avg_relevance": round(mean(st["rel"]), 4),
            "avg_cred": round(mean(st["cred"]), 2),
            "avg_doubt": round(mean(st["doubt"]), 2),
            "score": round(score, 4), "grade": grade, "grade_note": grade_note,
            "health_verdict": verdict,
            "last_error": health.get(src["id"], {}).get("last_error"),
            # 不适用的项照旧留在明细里（值为 0），但 not_applicable 会说明它没参与加权 ——
            # 免得看档案的人以为一次源在首发项上被判了 0 分。
            "breakdown": {k: round(v, 4) for k, v in dims.items()},
            "weights": {k: round(w, 4) for k, w in weights.items()},
        })
    cards.sort(key=lambda c: (-c["score"], c["source_id"]))
    return cards


def _health(conn):
    from . import store
    return store.health(conn)


def persist(conn, reg: dict, profile: dict, window_h: int | None = None) -> dict:
    from . import store
    window_h = window_h or config.CLUSTER_WINDOW_H
    cards = compute(conn, reg, profile, window_h=window_h)
    stamp = now_ts()
    store.save_scorecards(conn, cards, stamp, window_h)
    grades: dict[str, int] = {}
    for card in cards:
        grades[card["grade"]] = grades.get(card["grade"], 0) + 1
    return {"computed_ts": stamp, "window_h": window_h, "sources": len(cards),
            "grades": grades}


def governance_review(conn, reg: dict, profile: dict,
                      window_h: int | None = None) -> dict:
    """把档案和当前 sources.json 的分档对齐，列出需要人来定的分歧。"""
    cards = compute(conn, reg, profile, window_h=window_h)
    mismatches = [c for c in cards
                  if c["grade"] in FOCUS_LEVELS and c["grade"] != c["focus"]]
    dormant = [c for c in cards if c["grade"] == "dormant" and c["enabled"]]
    concentration = _concentration(cards)
    lang_bias = _lang_relevance_bias(cards)
    return {"cards": cards, "n_sources": len(cards),
            "enabled": sum(1 for c in cards if c["enabled"]),
            "producing": sum(1 for c in cards if c["n_items"]),
            # 前端要向用户解释定档门槛，把常量带过去而不是让页面自己写死一个 10。
            "min_items_for_core": MIN_ITEMS_FOR_CORE,
            "lang_relevance": lang_bias,
            "proposed_changes": [
                {"source_id": c["source_id"], "name": c["name"],
                 "current_focus": c["focus"], "proposed": c["grade"],
                 "score": c["score"], "why": c["grade_note"],
                 "lang": c["lang"], "caveat": _caveat(c, lang_bias)}
                for c in mismatches],
            "dormant_enabled": [
                {"source_id": c["source_id"], "name": c["name"],
                 "health_verdict": c["health_verdict"], "last_error": c["last_error"]}
                for c in dormant],
            "concentration": concentration}


LANG_BIAS_RATIO = 0.7   # 某语种均相关度低于最高语种的七成，就认为存在系统性偏差


def _lang_relevance_bias(cards: list[dict]) -> dict:
    """按语种统计平均相关度。

    相关度是拿关注画像的词去匹配的，而画像目前只有中英文。德语、葡语源因此天然算不高分
    —— 实测德语源均相关 0.13、葡语 0.15，英语 0.29。这个差不是它们报得差，是我们还没接
    翻译（跨语言印证那条链尚未完工）。把它算出来摆在治理台上，是为了别拿我方的欠工去降
    别人的档：真降了，将来接上翻译也没人记得当初为什么降。
    """
    agg: dict[str, list] = {}
    for card in cards:
        if not card["n_items"]:
            continue
        bucket = agg.setdefault(card["lang"], [0, 0.0])
        bucket[0] += 1
        bucket[1] += card["avg_relevance"]
    out = {lang: {"sources": n, "avg_relevance": round(total / n, 4)}
           for lang, (n, total) in agg.items()}
    best = max((v["avg_relevance"] for v in out.values()), default=0.0)
    for lang, value in out.items():
        value["under_served"] = bool(best and
                                     value["avg_relevance"] < best * LANG_BIAS_RATIO)
    return out


def _caveat(card: dict, lang_bias: dict) -> str:
    if card["grade"] != "probation":
        return ""
    info = lang_bias.get(card["lang"]) or {}
    if info.get("under_served"):
        return (f"{card['lang']} 语种整体均相关度仅 {info['avg_relevance']:.2f}，"
                f"低于最高语种的 {int(LANG_BIAS_RATIO * 100)}%：先排除是我方尚未接翻译"
                f"导致的偏差，再决定是否降档")
    return ""


def _concentration(cards: list[dict]) -> dict:
    """产出集中度。治理的第一个体检指标：一家占太多，整个终端就是那一家的口径。"""
    total = sum(c["n_items"] for c in cards) or 1
    ranked = sorted(cards, key=lambda c: -c["n_items"])
    by_group: dict[str, int] = {}
    for card in cards:
        by_group[card["group"]] = by_group.get(card["group"], 0) + card["n_items"]
    top_group = max(by_group.items(), key=lambda kv: kv[1]) if by_group else ("-", 0)
    return {
        "total_items": total,
        "top_source": {"source_id": ranked[0]["source_id"],
                       "share": round(ranked[0]["n_items"] / total, 4)} if ranked else {},
        "top3_share": round(sum(c["n_items"] for c in ranked[:3]) / total, 4),
        "top_group": {"group": top_group[0],
                      "share": round(top_group[1] / total, 4)},
        # 赫芬达尔指数：1/n 是完全均衡，1 是一家独占。比 top-N 份额更难被拆分掩盖。
        "hhi": round(sum((c["n_items"] / total) ** 2 for c in cards), 4),
    }
