"""Event evidence view: traceable claims, contradictions and chronology.

The output deliberately says "reported" rather than "true". A claim is promoted only
when distinct owner/groups independently contain materially similar wording.
"""
import hashlib
import re

from .crosslingual import features
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


def _similar(a: str, b: str) -> float:
    ga, gb = gram_set(a), gram_set(b)
    return max(jaccard(ga, gb), overlap(ga, gb) * 0.82)


def _support(claim: str, item: dict) -> bool:
    text = f"{item.get('title', '')} {item.get('summary', '')[:500]}"
    return _similar(claim, text) >= 0.38


def _relation(claim: str, item: dict) -> str | None:
    """Classify how an item relates to a claim without pretending to prove truth."""
    text = f"{item.get('title', '')} {item.get('summary', '')[:500]}"
    fc, fi = features(claim), features(text)
    related = _similar(claim, text) >= 0.26 or bool(fc.entities & fi.entities)
    if not related:
        return None
    for positive, negative in OPPOSITES:
        if (_has_any(claim, positive) and _has_any(text, negative)) or \
                (_has_any(claim, negative) and _has_any(text, positive)):
            return "refute"
    if fc.numbers and fi.numbers and not (fc.numbers & fi.numbers) \
            and _similar(claim, text) >= 0.30:
        return "refute"
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
        supporting = [x for x, relation in related if relation == "support"]
        if not supporting:
            continue
        groups = sorted({x.get("grp") or x.get("source_id") for x in supporting})
        independent_groups = sorted({x.get("grp") or x.get("source_id")
                                     for x in supporting
                                     if x.get("source_role", "reporting")
                                     in ("reporting", "wire")})
        roles = sorted({x.get("source_role", "reporting") for x in supporting})
        refuting = [x for x, relation in related if relation == "refute"]
        if refuting:
            status = "disputed"
        elif len(independent_groups) >= 2:
            status = "independently_reported"
        elif roles == ["official"]:
            status = "official_statement"
        else:
            status = "single_report"
        refs = [{"item_id": x.get("id"), "source_id": x.get("source_id"),
                 "source": x.get("source_name"), "group": x.get("grp"),
                 "url": x.get("url"), "published_ts": x.get("published_ts"),
                 "source_role": x.get("source_role", "reporting"),
                 "relation": relation, "relation_method": "heuristic-relation-v1",
                 **_best_quote(text, x)}
                for x, relation in related]
        relation_counts = {kind: sum(ref["relation"] == kind for ref in refs)
                           for kind in ("support", "refute", "unknown")}
        out.append({
            "id": hashlib.sha1(text.encode("utf-8")).hexdigest()[:12],
            "text": text, "status": status,
            "independent_groups": len(independent_groups),
            "groups": groups, "source_roles": roles, "evidence": refs,
            "relation_counts": relation_counts,
            "unresolved": (["存在方向或关键数字相反的来源，需核对原始材料。"]
                           if refuting else
                           ["尚缺少独立采编来源的交叉验证。"]
                           if len(independent_groups) < 2 else []),
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
