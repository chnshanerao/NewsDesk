"""Event evidence view: traceable claims, contradictions and chronology.

The output deliberately says "reported" rather than "true". A claim is promoted only
when distinct owner/groups independently contain materially similar wording.
"""
import hashlib
import re
import time

from .crosslingual import different_periods, features, periods
from .normalize import gram_set, jaccard, overlap

OPPOSITES = [
    ({"上涨", "增长", "上升", "increase", "increased", "rises", "rose"},
     {"下跌", "下降", "减少", "decrease", "decreased", "falls", "fell"}),
    ({"批准", "通过", "获批", "approve", "approved", "passes"},
     {"拒绝", "否决", "驳回", "reject", "rejected", "blocks"}),
    ({"达成", "签署", "同意", "agrees", "signed", "reached"},
     {"破裂", "取消", "退出", "collapses", "cancelled", "withdraws"}),
]
UNCERTAINTY = {"可能", "预计", "或将", "据称", "传闻", "尚未", "待确认",
               "may", "might", "could", "reportedly", "rumor", "expected"}
# 互斥的事件类型。走 EVENT_ALIASES 的标签而不是再堆表层词，这样一处加别名
# 中英文两侧同时生效。OPPOSITES 那张表只覆盖涨跌/批驳/成败三组表层词，
# 漏掉了降息与加息 —— 「9月降息25个基点」对「9月加息25个基点」数字完全相同，
# 数字规则不会触发，结果被判成 support。这是同期同口径的方向性矛盾。
EVENT_OPPOSITES = (("rate_cut", "rate_hike"), ("approval", "ban"))


def _similar(a: str, b: str) -> float:
    ga, gb = gram_set(a), gram_set(b)
    return max(jaccard(ga, gb), overlap(ga, gb) * 0.82)


def _support(claim: str, item: dict) -> bool:
    text = f"{item.get('title', '')} {item.get('summary', '')[:500]}"
    return _similar(claim, text) >= 0.38


def _kinds_of(numbers) -> frozenset[str]:
    return frozenset(x.split(":", 1)[0] for x in numbers)


_DIRECTION_WORDS = sorted({w for pair in OPPOSITES for side in pair for w in side},
                         key=len, reverse=True)


def _subject(text: str) -> str:
    """抹掉方向词与数字，只留「在说什么」。

    直接比 claim 与引句的相似度判不出同主体：字符二元组相似度对短文本极不公平，
    差异词占比大就被压低。「公司收入增长20%」对「公司收入下降20%」是同主体的
    真矛盾，可原样相似度只有 .47 —— 而矛盾恰恰意味着两边必有一处相反，越是
    干净的矛盾对，那处差异在短句里占比越高。用原样相似度当门，等于按「矛盾有
    多明显」反向惩罚。
    掩蔽后两边都成「公司收入%」，相似度 1.0；而跨主体的 CPI 对 PPI 掩蔽后仍是
    「居民消费价格」对「工业生产者出厂价格」，相似度 .410。量出来的分离：真矛盾
    落在 .547–1.0，跨主体误命中落在 .075–.410，取 .48。
    """
    out = text
    for word in _DIRECTION_WORDS:
        out = re.sub(re.escape(word), " ", out, flags=re.I)
    return re.sub(r"\d+(?:[.,]\d+)?", " ", out)


def _same_subject(claim: str, span: str) -> bool:
    """两句是否在说同一个主体。

    用纯 Jaccard，不用 `_similar` 里那个 `max(jaccard, overlap*0.82)`。overlap 的分母
    取短边，短句对长段时几乎必然虚高：「What's new in TensorFlow 2.19」对一整段乌克兰
    和谈报道能拿到 .615，而纯 Jaccard 只有 .105。`_similar` 用在「相关吗」这种召回
    场景上是合适的（宁宽勿漏），但同主体是一道排除门，必须用对长度对称的指标。
    量出来的分离：真矛盾 .533–1.0，跨主体误命中 ≤ .500（那个 .500 是「模型A vs 模型B
    on DeepSWE」这类同模板不同主体的标题，另由「取值必须是度量」那条挡下）。
    """
    return jaccard(gram_set(_subject(claim)), gram_set(_subject(span))) >= 0.48


# 能够支撑「取值互斥」的量纲：百分比与带量级词的绝对量。裸数字（count）被排除，
# 见 crosslingual._numbers 里的说明。
MEASURED_KINDS = frozenset({"pct", "num"})


def _fill_years(item: dict, stated: frozenset[str]) -> frozenset[str]:
    """标题按新闻惯例省略年份时，把年份补回来。

    「2024年8月份CPI同比上涨0.6%」对「国家统计局：8月份居民消费价格同比上涨0.8%」——
    后者是 2026 年 8 月的数（正文写明「2026年8月份」），标题省了年份。省略年份原本
    按「可推断为同年」处理，于是两个不同年份的同月数据被判成同期矛盾。

    按可靠度取三级：标题/引句写明的年份 > 正文写明的年份 > 发布时间。正文的年份只在
    标题和引句都没写时才用 —— 正文可能顺带提到别的年份，不如标题可靠，但比发布时间
    可靠。统计口径的发布普遍滞后（1 月发布的多是上一年数据），所以走到发布时间这一级
    时，1–2 月发布的稿件同时给出「发布年」和「上一年」两个候选，避免跨年边界误判。
    """
    if any(x.startswith(("y:", "fy:")) for x in stated):
        return frozenset()
    body = f"{item.get('title', '')} {item.get('summary', '')[:500]}"
    from_body = frozenset(x for x in periods(body) if x.startswith(("y:", "fy:")))
    if from_body:
        return from_body
    stamp = item.get("published_ts") or item.get("fetched_ts")
    if not stamp:
        return frozenset()
    moment = time.gmtime(int(stamp))
    years = {moment.tm_year}
    if moment.tm_mon <= 2:
        years.add(moment.tm_year - 1)
    return frozenset(f"y:{x}" for x in years)


def _relation(claim: str, item: dict) -> str | None:
    """Classify how an item relates to a claim without pretending to prove truth."""
    text = f"{item.get('title', '')} {item.get('summary', '')[:500]}"
    # 关系只在 claim 与「将被引用的那一句」之间判定，不拿整篇 title+summary 判。
    # 整篇会把矛盾判错：一篇讲增长的稿子只要在别处提过一句「环比下降」，
    # OPPOSITES 就命中，于是与自己完全相同的 claim 也被判成 refute。实测 100 个
    # refute 样本里 36 个是这么来的。引用哪一句、就拿哪一句负责。
    span = _best_quote(claim, item)["quote"] or text
    fc, fi = features(claim), features(span)
    related = _similar(claim, text) >= 0.26 or bool(fc.entities & fi.entities)
    if not related:
        return None
    # 期次要从「标题 + 引句」一起取，不能只看引句。期次通常写在标题或电头里，
    # 被引的那一句往往只有取值：claim 说「2026年4月份PPI同比上涨2.8%」，引句是
    # 「PPI环比由上月下降0.7%转为上涨0.4%」——不带月份，于是同期门形同虚设。
    # 取并集不会反过来放宽：claim 的月份只要出现在并集里就算同期，出现不了就是异期。
    stated = periods(str(item.get("title") or "")) | fi.periods
    item_periods = stated | _fill_years(item, stated)
    # 矛盾必须同期。两条陈述落在不同期次上（不同月份、不同季度、不同发行批次、
    # 不同星期）时，取值不同是常态而不是冲突 —— 那是两件事，不是一件事的两种说法。
    # 曾经缺这道门：「2025年1月 PPI 下降2.3%」与「2025年5月 PPI 下降3.3%」因为
    # 数字不相交被判成 refute，同理还有国债第五十七期/第五十八期、
    # 停课通知周三/周四、贴现会议纪要 7月/6月。100 个 refute 样本无一是真矛盾。
    if different_periods(fc.periods, item_periods):
        return "unknown"
    # 矛盾必须同主体。0.26 的 related 门只够说明「可能相关」，远不够说明两句在说
    # 同一件事，而方向词是极弱证据：「8月CPI同比上涨0.6%」与「8月PPI同比下降1.8%」
    # 同月、一涨一跌，却是两个指数，不构成矛盾。实测把这道门加上之前，94/100 的
    # refute 都是这种跨主体误命中（另有更离谱的：AI 安全评论对以色列关闭领事馆）。
    # 比的是掩蔽掉方向词和数字后的主体（见 _subject），不是原句。
    # 同主体不成立时只跳过三条 refute 规则，后面的 unknown/support 判定保持原样 ——
    # 这次改动只负责撤掉站不住的矛盾判定，不顺手动别的分支。
    same_subject = _same_subject(claim, span)
    conflict = any(
        (_has_any(claim, positive) and _has_any(span, negative)) or
        (_has_any(claim, negative) and _has_any(span, positive))
        for positive, negative in OPPOSITES)
    conflict = conflict or any(
        {one, other} <= (fc.events | fi.events) and (one in fc.events) != (one in fi.events)
        for one, other in EVENT_OPPOSITES)
    # 取值互斥要成为矛盾，还得两边在说同一个量。词法特征认不出「槽位」，只能要求
    # 同一量纲（都是百分比或都是绝对量），并且这个量得是**度量**而不是标识符 ——
    # 版本号、型号、计数不算（见 MEASURED_KINDS）。原先只要求相似度 .30 且数字
    # 不相交，于是「10 个国家参与禁令」对「英法加三国制裁」这种数字毫无对应关系的
    # 也算矛盾，实测 64/100 的 refute 出自这一条。
    ca = frozenset(x for x in fc.numbers if _kinds_of({x}) <= MEASURED_KINDS)
    cb = frozenset(x for x in fi.numbers if _kinds_of({x}) <= MEASURED_KINDS)
    conflict = conflict or bool(
        ca and cb and not (ca & cb) and _kinds_of(ca) == _kinds_of(cb))
    if conflict:
        # 冲突信号成立但主体对不上，只说明「看不清」，不说明「印证」。这里必须显式
        # 收在 unknown，否则会掉到 _support 的 .38 门里被判成 support ——
        # 「8月CPI上涨0.6%」对「8月PPI下降1.8%」相似度 .383，恰好越过那道门。
        # 把一涨一跌的两个指数记成互相印证，比误报矛盾更糟：矛盾只是多一条待核线索，
        # 假印证会直接抬高可信度评分。_support 的阈值一个字没动 —— 这里只拦冲突对。
        return "refute" if same_subject else "unknown"
    if _has_any(text, UNCERTAINTY) != _has_any(claim, UNCERTAINTY):
        return "unknown"
    if _support(claim, item):
        return "support"
    return "unknown"


def _best_quote(claim: str, item: dict) -> dict:
    """Return an exact, auditable quote and character offsets from title/summary."""
    candidates = []
    for field in ("title", "summary"):
        value = str(item.get(field) or "")
        if not value:
            continue
        spans = [(0, len(value))] if field == "title" else [
            (m.start(), m.end()) for m in re.finditer(r"[^。！？!?\n]+[。！？!?]?", value)
            if m.group().strip()]
        for start, end in spans:
            quote = value[start:end].strip()
            if quote:
                left = value.find(quote, start, end)
                candidates.append((_similar(claim, quote), field, left, left + len(quote), quote))
    score, field, start, end, quote = max(candidates, default=(0.0, "title", 0, 0, ""))
    return {"quote": quote, "quote_field": field, "quote_start": start,
            "quote_end": end, "quote_hash": hashlib.sha256(quote.encode()).hexdigest()[:16],
            "similarity": round(score, 4)}


def summarize(refs: list[dict]) -> dict:
    """从证据行推出 claim 的状态与独立集团数。

    状态规则只能有一处实现。`claims()` 首次生成时用它，分类器改版后重刷关系
    （`pipeline.refresh_relations`）时也用它 —— 否则两处各写一遍，早晚出现
    「状态 disputed 但证据行里没有一条 refute」这种库内自相矛盾。
    独立集团数只数 support 的行：被反驳或看不清的来源不构成印证。
    """
    supporting = [x for x in refs if x.get("relation") == "support"]
    groups = sorted({x.get("group") or x.get("source_id") for x in supporting})
    independent = sorted({x.get("group") or x.get("source_id") for x in supporting
                          if (x.get("source_role") or "reporting") in ("reporting", "wire")})
    roles = sorted({x.get("source_role") or "reporting" for x in supporting})
    disputed = any(x.get("relation") == "refute" for x in refs)
    if disputed:
        status = "disputed"
    elif len(independent) >= 2:
        status = "independently_reported"
    elif roles == ["official"]:
        status = "official_statement"
    else:
        status = "single_report"
    return {"status": status, "independent_groups": len(independent),
            "groups": groups, "source_roles": roles, "n_supporting": len(supporting),
            "unresolved": (["存在方向或关键数字相反的来源，需核对原始材料。"] if disputed else
                           ["尚缺少独立采编来源的交叉验证。"] if len(independent) < 2 else [])}


def claims(items: list[dict], llm: dict | None = None) -> list[dict]:
    candidates = [str(x).strip() for x in (llm or {}).get("claims", []) if str(x).strip()]
    if not candidates:
        # Greedy title representatives. Similar titles become one traceable claim.
        for item in sorted(items, key=lambda x: (int(x.get("tier", 9)),
                                                  -len(x.get("title", "")))):
            title = item.get("title", "").strip()
            if title and not any(_similar(title, old) >= 0.52 for old in candidates):
                candidates.append(title)
            if len(candidates) >= 6:
                break

    out = []
    for text in candidates[:8]:
        related = [(x, _relation(text, x)) for x in items]
        related = [(x, relation) for x, relation in related if relation]
        refs = [{"item_id": x.get("id"), "source_id": x.get("source_id"),
                 "source": x.get("source_name"), "group": x.get("grp"),
                 "url": x.get("url"), "published_ts": x.get("published_ts"),
                 "source_role": x.get("source_role", "reporting"),
                 "relation": relation, "relation_method": "heuristic-relation-v1",
                 **_best_quote(text, x)}
                for x, relation in related]
        card = summarize(refs)
        if not card.pop("n_supporting"):
            continue
        relation_counts = {kind: sum(ref["relation"] == kind for ref in refs)
                           for kind in ("support", "refute", "unknown")}
        out.append({
            "id": hashlib.sha1(text.encode("utf-8")).hexdigest()[:12],
            "text": text, **card, "evidence": refs,
            "relation_counts": relation_counts,
            "method": "extractive-v1",
        })
    return out


def _has_any(text: str, words: set[str]) -> bool:
    folded = text.casefold()
    return any(w.casefold() in folded for w in words)


def contradictions(items: list[dict]) -> list[dict]:
    out, seen = [], set()
    for i, left in enumerate(items):
        a = left.get("title", "")
        fa = features(a)
        for right in items[i + 1:]:
            if (left.get("grp") or left.get("source_id")) == \
                    (right.get("grp") or right.get("source_id")):
                continue
            b = right.get("title", "")
            fb = features(b)
            related = _similar(a, b) >= 0.26 or bool(fa.entities & fb.entities)
            if not related:
                continue
            reason = None
            if fa.numbers and fb.numbers and not (fa.numbers & fb.numbers) \
                    and _similar(a, b) >= 0.30:
                reason = "关键数字不一致"
            for positive, negative in OPPOSITES:
                if (_has_any(a, positive) and _has_any(b, negative)) or \
                        (_has_any(a, negative) and _has_any(b, positive)):
                    reason = "方向性表述相反"
                    break
            if not reason:
                continue
            key = tuple(sorted((left.get("id", a), right.get("id", b)))) + (reason,)
            if key in seen:
                continue
            seen.add(key)
            out.append({"reason": reason,
                        "left": {"title": a, "source": left.get("source_name"),
                                 "url": left.get("url")},
                        "right": {"title": b, "source": right.get("source_name"),
                                  "url": right.get("url")}})
    return out[:10]


def timeline(items: list[dict]) -> list[dict]:
    ordered = sorted(items, key=lambda x: (x.get("published_ts") or x.get("fetched_ts") or 0,
                                           int(x.get("tier", 9))))
    return [{"ts": x.get("published_ts") or x.get("fetched_ts"),
             "source": x.get("source_name"), "group": x.get("grp"),
             "source_role": x.get("source_role", "reporting"),
             "title": x.get("title"), "url": x.get("url")}
            for x in ordered[:50]]


def analyze(items: list[dict], llm: dict | None = None) -> dict:
    return {"claims": claims(items, llm), "contradictions": contradictions(items),
            "timeline": timeline(items),
            "method": "title/summary similarity + independent-group provenance",
            "limitations": "自动提取仅表示来源如何报道；未被列为矛盾不等于不存在矛盾。"}
