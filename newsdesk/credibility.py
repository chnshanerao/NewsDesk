"""可信度与相关性评分（规则层，不依赖 LLM，可解释、可复现）。

可信度 = 0.40 信源权威 + 0.30 独立交叉印证 + 0.20 内容质量 + 0.10 时间一致性

设计原则：
1. 每一分都要能解释。breakdown 会原样进 UI，用户能看到『为什么给它 82 分』。
2. 交叉印证按『独立集团』计数，不按稿件数。人民网四个频道转同一条 ≠ 四家印证。
3. 一次信源（统计局/通讯社原创）不因『只有一家』被扣成低分——它本身就是源头。
4. 相关性独立于可信度。真但无用的新闻（明星官宣是真的）要被排到噪音区。
"""
import math
import re
import hashlib

from . import config

# ---- 内容质量词表（规则层，故意保持可读、可改）----
CLICKBAIT = [
    "震惊", "惊人", "竟然", "居然", "太可怕", "别再", "速看", "紧急通知", "火了",
    "炸了", "刷屏", "看完沉默", "不转不是", "一定要看", "最后一天", "内部消息",
    "惊天", "揭秘", "内幕", "史上最", "无人敢", "全网", "沸腾", "秒杀", "崩了",
    "破防", "泪目", "笑不活了", "细节曝光", "真相了", "彻底慌了", "傻眼",
]
RUMOR = [
    "网传", "传闻", "疑似", "据传", "知情人士", "爆料", "有消息称", "坊间",
    "据网友", "未经证实", "或将", "传出", "小道消息", "内部人士透露",
]
ATTRIBUTION = [
    "新华社", "据新华社", "通报", "公告", "发布会", "白皮书", "统计局", "央行",
    "国务院", "公报", "数据显示", "官方", "正式发布", "研究显示", "报告显示",
    "披露", "答记者问", "公开信息", "记者", "获悉", "文件", "印发", "批复",
    "年报", "财报", "招股书", "问询函", "判决", "立案",
]
HYPE_MOVE = ["暴涨", "暴跌", "狂飙", "血亏", "腰斩", "崩盘", "清零", "疯抢", "爆火"]
EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF]"
)
NUM_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:%|万|亿|元|人|家|个|点|倍|月|日|年|美元|吨|台|辆)?")
QUOTE_RE = re.compile(r"[“”\"「」]")


def _hits(text: str, words: list[str]) -> list[str]:
    return [w for w in words if w in text]


def content_quality(headline: str, bodies: list[str]) -> tuple[float, dict]:
    """返回 (0~1 分数, 明细)。明细里的正负信号会直接展示给用户。"""
    text = headline + " " + " ".join(bodies)[:1500]
    score = 0.62
    pos, neg = [], []

    cb = _hits(headline, CLICKBAIT)
    if cb:
        score -= min(0.30, 0.10 * len(cb))
        neg.append(f"标题党词 {'/'.join(cb[:3])}")

    rumor = _hits(text, RUMOR)
    attrib = _hits(text, ATTRIBUTION)
    if rumor:
        pen = min(0.28, 0.12 * len(rumor))
        if attrib:                      # 有明确出处的『疑似』可信度惩罚减半
            pen *= 0.5
        score -= pen
        neg.append(f"未证实措辞 {'/'.join(rumor[:3])}")
    if attrib:
        score += min(0.20, 0.07 * len(attrib))
        pos.append(f"信息出处 {'/'.join(attrib[:3])}")

    hype = _hits(headline, HYPE_MOVE)
    if hype:
        score -= min(0.12, 0.06 * len(hype))
        neg.append(f"情绪化涨跌词 {'/'.join(hype[:2])}")

    excl = headline.count("！") + headline.count("!") + headline.count("？") + headline.count("?")
    if excl >= 2:
        score -= 0.10
        neg.append("标题多个感叹/疑问号")
    elif excl == 1 and headline.endswith(("！", "!")):
        score -= 0.04

    if EMOJI_RE.search(headline):
        score -= 0.08
        neg.append("标题含 emoji")

    nums = NUM_RE.findall(text)
    if len(nums) >= 3:
        score += 0.10
        pos.append("含具体数字/日期")
    elif len(nums) >= 1:
        score += 0.04

    if QUOTE_RE.search(text):
        score += 0.04
        pos.append("含直接引语")

    if len(headline) < 8:
        score -= 0.06
        neg.append("标题过短，信息量不足")
    if len(headline) > 60:
        score -= 0.04

    body_len = sum(len(b) for b in bodies)
    if body_len >= 120:
        score += 0.05
        pos.append("有正文摘要")
    elif body_len == 0:
        score -= 0.05
        neg.append("只有标题，无摘要")

    return max(0.0, min(1.0, score)), {"positive": pos, "negative": neg}


def corroboration(n_groups: int, best_tier: int,
                  roles: set[str] | None = None) -> tuple[float, str]:
    raw = 1 - math.exp(-0.85 * max(0, n_groups - 1))
    roles = roles or set()
    # 官方源能证明“该机构确实发布了这项声明”，不能替代独立事实核实。
    # 因此只给有限的一手出处地板，不再把单一官方稿近似成多源印证。
    if "official" in roles:
        floor = 0.25
    elif "wire" in roles:
        floor = 0.15
    else:
        floor = 0.0
    val = max(raw, floor)
    if n_groups >= 2:
        note = f"{n_groups} 个独立信源集团各自报道"
    elif "official" in roles:
        note = "一手机构声明：确认其发布行为，内容尚无独立来源核实"
    elif "wire" in roles:
        note = "单一通讯社原创报道，尚无独立来源核实"
    else:
        note = "孤证：只有一家在说"
    return min(1.0, val), note


def timing_consistency(first_ts: int, last_ts: int, n_items: int, now: int) -> tuple[float, str]:
    if first_ts > now + 3600:
        return 0.30, "发布时间在未来，元数据可疑"
    span_h = max(0, (last_ts - first_ts)) / 3600
    if n_items <= 1:
        return 0.60, "单篇，无时间交叉验证"
    if span_h <= 6:
        return 1.00, f"各家在 {span_h:.1f}h 内集中报道"
    if span_h <= 24:
        return 0.85, f"报道跨度 {span_h:.0f}h"
    if span_h <= 48:
        return 0.60, f"报道跨度 {span_h:.0f}h，可能是追踪或回炒"
    return 0.42, f"报道跨度 {span_h/24:.1f} 天，疑似旧闻回炒"


def authority(items: list[dict], tier_weight: dict) -> tuple[float, int, str]:
    ws = [tier_weight.get(int(it["tier"]), 0.3) for it in items]
    best_tier = min(int(it["tier"]) for it in items)
    val = 0.70 * max(ws) + 0.30 * (sum(ws) / len(ws))
    best_name = next(it["source_name"] for it in items
                     if int(it["tier"]) == best_tier)
    return val, best_tier, f"最高权威信源：{best_name}（T{best_tier}）"


# ---------------- 相关性 ----------------

def relevance(headline: str, bodies: list[str], src_topics: set[str],
              profile: dict) -> tuple[float, list[str], dict]:
    text = headline + " " + " ".join(bodies)[:1200]
    # 英文主题词按大小写不敏感匹配；中文 casefold 后保持不变。
    # 没有这一层，"inflation" 无法命中标题中的 "Inflation"，会系统性压低外媒。
    text_fold = text.casefold()
    headline_fold = headline.casefold()
    detail: dict = {"matched": {}, "noise": [], "boost": []}

    if any(k and k.casefold() in text_fold for k in profile.get("muted_keywords", [])):
        return 0.0, [], {"muted": True}

    scored: list[tuple[float, str]] = []
    for key, spec in profile["topics"].items():
        hit = [k for k in spec["keywords"] if k.casefold() in text_fold]
        if not hit:
            continue
        # 标题命中远比正文命中值钱：标题是编辑认为的『这条是关于什么的』
        h = sum(1 for k in hit if k.casefold() in headline_fold)
        b = len(hit) - h
        strength = min(1.0, 0.34 * h + 0.10 * b)
        if strength <= 0:
            continue
        scored.append((strength * spec["weight"], key))
        detail["matched"][key] = hit[:5]

    for key in src_topics:                     # 信源自带栏目作为弱先验
        spec = profile["topics"].get(key)
        if spec and key not in detail["matched"]:
            scored.append((0.10 * spec["weight"], key))

    if not scored:
        rel = 0.06
        topics: list[str] = sorted(src_topics)[:2]
    else:
        scored.sort(reverse=True)
        rel = scored[0][0]
        if len(scored) > 1:
            rel += 0.10 * scored[1][0]
        if len(scored) > 2:
            rel += 0.05 * scored[2][0]
        topics = [k for _, k in scored[:3]]

    noise = [k for k in profile.get("noise_keywords", []) if k.casefold() in text_fold]
    if noise:
        rel *= max(0.05, 0.35 ** len(noise))
        detail["noise"] = noise[:4]

    boost = [k for k in profile.get("boost_keywords", []) if k.casefold() in text_fold]
    if boost:
        rel += min(0.12, 0.06 * len(boost))
        detail["boost"] = boost[:3]

    return max(0.0, min(1.0, rel)), topics, detail


# ---------------- 汇总 ----------------

def recency_factor(last_ts: int, now: int) -> float:
    age_h = max(0.0, (now - last_ts) / 3600)
    return 0.5 ** (age_h / config.HALF_LIFE_H)


def rank_of(cred: float, rel: float, last_ts: int, now: int) -> float:
    base = config.RANK_W_CRED * (cred / 100.0) + config.RANK_W_RELEVANCE * rel
    return round(base * (0.35 + 0.65 * recency_factor(last_ts, now)), 6)


def score_cluster(items: list[dict], profile: dict, tier_weight: dict,
                  now: int) -> dict:
    """items: 同一事件的所有稿件（dict，含 tier/grp/title/summary/...）。"""
    groups = {it["grp"] for it in items}
    independent_groups = {it["grp"] for it in items
                          if it.get("src_role", "reporting") in ("reporting", "wire")}
    roles = {it.get("src_role", "reporting") for it in items}
    first_ts = min(int(it["published_ts"] or it["fetched_ts"]) for it in items)
    last_ts = max(int(it["published_ts"] or it["fetched_ts"]) for it in items)

    auth, best_tier, auth_note = authority(items, tier_weight)
    corr, corr_note = corroboration(len(independent_groups), best_tier, roles)

    # 用最高权威信源的标题当事件标题——避免用标题党当门面
    lead = sorted(items, key=lambda it: (int(it["tier"]),
                                         -len(it["title"])))[0]
    bodies = [it.get("summary") or "" for it in items]
    cont, cont_detail = content_quality(lead["title"], bodies)
    timing, timing_note = timing_consistency(first_ts, last_ts, len(items), now)

    cred = 100 * (config.W_AUTHORITY * auth + config.W_CORROBORATION * corr
                  + config.W_CONTENT * cont + config.W_TIMING * timing)
    cred = round(max(0.0, min(100.0, cred)), 1)
    code, label = config.band(cred)

    src_topics: set[str] = set()
    for it in items:
        src_topics.update(it.get("src_topics") or [])
    rel, topics, rel_detail = relevance(lead["title"], bodies, src_topics, profile)
    content_material = "\n".join(
        f"{it['id']}|{it['title']}|{it.get('summary') or ''}" for it in
        sorted(items, key=lambda x: x["id"])
    )

    return {
        "headline": lead["title"],
        "headline_src": lead["source_name"],
        "url": lead.get("url") or "",
        "first_ts": first_ts,
        "last_ts": last_ts,
        "n_items": len(items),
        "n_groups": len(independent_groups),
        "best_tier": best_tier,
        "topics": topics,
        "cred": cred,
        "cred_code": code,
        "cred_label": label,
        "relevance": round(rel, 4),
        "rank": rank_of(cred, rel, last_ts, now),
        "content_hash": hashlib.sha256(content_material.encode("utf-8")).hexdigest()[:24],
        "breakdown": {
            "evidence": {
                "status": ("INDEPENDENTLY_SUPPORTED" if len(independent_groups) >= 2 else
                           "PRIMARY_STATEMENT" if "official" in roles else
                           "SINGLE_REPORT"),
                "roles": sorted(roles),
                "note": ("至少两个独立媒体集团报道"
                         if len(independent_groups) >= 2 else corr_note),
            },
            "authority": {"score": round(auth, 3), "note": auth_note,
                          "weight": config.W_AUTHORITY},
            "corroboration": {"score": round(corr, 3), "note": corr_note,
                              "weight": config.W_CORROBORATION,
                               "groups": sorted(independent_groups),
                               "excluded_groups": sorted(groups - independent_groups)},
            "content": {"score": round(cont, 3), "weight": config.W_CONTENT,
                        **cont_detail},
            "timing": {"score": round(timing, 3), "note": timing_note,
                       "weight": config.W_TIMING},
            "relevance": rel_detail,
        },
    }


def blend_llm(cred_rule: float, rel_rule: float, llm: dict, last_ts: int,
              now: int) -> tuple[float, str, str, float, float]:
    """把 LLM 的判断按 25% 权重掺进规则分。规则层永远是主干，LLM 只做修正。

    返回 (cred, code, label, rank, relevance)。
    """
    def num(key, default):
        try:
            return max(0.0, min(1.0, float(llm.get(key, default))))
        except (TypeError, ValueError):
            return default

    quality = max(0.0, min(1.0, 0.6 * num("factuality", 0.5)
                           + 0.4 * (1 - num("sensationalism", 0.5))))
    cred = 0.75 * cred_rule + 0.25 * (100 * quality)
    flags = llm.get("red_flags") or []
    if flags:
        cred -= min(12.0, 4.0 * len(flags))
    cred = round(max(0.0, min(100.0, cred)), 1)
    code, label = config.band(cred)
    rel = round(max(0.0, min(1.0, 0.6 * rel_rule + 0.4 * num("relevance", rel_rule))), 4)
    return cred, code, label, rank_of(cred, rel, last_ts, now), rel
