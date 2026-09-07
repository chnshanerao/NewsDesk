"""Extractive research answers with a citation attached to every factual sentence."""
import re

from .normalize import gram_set, jaccard, overlap

DOMAIN_TERMS = (
    "人工智能", "大模型", "模型", "芯片", "算力", "监管", "治理", "安全", "开源",
    "机器人", "融资", "并购", "发布", "论文", "基准", "数据集", "云计算", "半导体",
)
STOPWORDS = {"what", "which", "about", "latest", "news", "important", "progress",
             "过去", "一周", "哪些", "什么", "如何", "重要", "进展", "新闻", "最近"}


def _query_terms(query: str) -> list[str]:
    folded = query.casefold()
    terms = [x for x in DOMAIN_TERMS if x in query]
    terms.extend(x for x in re.findall(r"[a-z][a-z0-9.+-]{1,}", folded)
                 if x not in STOPWORDS)
    if not terms:
        terms = [x for x in re.findall(r"[\w\u3400-\u9fff]+", folded)
                 if x not in STOPWORDS and len(x) >= 2]
    return list(dict.fromkeys(terms))


def _score(query: str, text: str) -> float:
    q, t = gram_set(query), gram_set(text)
    if not q or not t:
        return 0.0
    exact = 1.0 if query.casefold() in text.casefold() else 0.0
    terms = re.findall(r"[\w\u3400-\u9fff]+", query.casefold())
    coverage = sum(term in text.casefold() for term in terms) / max(1, len(terms))
    return max(jaccard(q, t), overlap(q, t) * .8, exact, coverage * .75)


def answer(conn, question: str, limit: int = 8) -> dict:
    question = (question or "").strip()[:300]
    if len(question) < 2:
        return {"question": question, "findings": [], "citation_coverage": 0,
                "limitations": ["请输入至少两个字符的研究问题。"]}
    candidates = []
    terms = _query_terms(question)
    rows = conn.execute(
        "SELECT cl.*,c.headline,c.last_ts,c.cred,c.topics FROM claims cl "
        "JOIN clusters c ON c.id=cl.cluster_id ORDER BY c.last_ts DESC")
    status_weight = {"independently_reported": .18, "official_statement": .10,
                     "disputed": .06, "single_report": 0.0}
    for row in rows:
        haystack = f"{row['text']} {row['headline']}".casefold()
        matched = [term for term in terms if term.casefold() in haystack]
        required = 1 if len(terms) <= 1 else max(2, int(len(terms) * .6 + .5))
        if len(matched) < required:
            continue
        score = _score(question, haystack) + len(matched) / max(1, len(terms)) * .25
        if score < .18:
            continue
        refs = [dict(x) for x in conn.execute(
            "SELECT item_id,source_name AS source,source_group AS 'group',source_role,url,"
            "published_ts,quote,quote_field,quote_start,quote_end,quote_hash,similarity,relation,"
            "relation_confidence,relation_method "
            "FROM claim_evidence WHERE claim_id=? ORDER BY similarity DESC,published_ts ASC",
            (row["id"],))]
        if not refs:
            continue
        relation_buckets = {kind: [x for x in refs if x["relation"] == kind]
                            for kind in ("support", "refute", "unknown")}
        candidates.append((score + status_weight.get(row["status"], 0), {
            "sentence": row["text"], "claim_id": row["id"],
            "cluster_id": row["cluster_id"], "headline": row["headline"],
            "status": row["status"], "independent_groups": row["independent_groups"],
            "cred": row["cred"], "last_ts": row["last_ts"], "citations": refs,
            "relation_counts": {kind: sum(x["relation"] == kind for x in refs)
                                for kind in ("support", "refute", "unknown")},
            "relations": relation_buckets,
            "assessment": ("contested" if relation_buckets["support"] and
                            relation_buckets["refute"] else
                            "supported_as_reported" if relation_buckets["support"] and
                            row["independent_groups"] >= 2 else "challenged"
                            if relation_buckets["refute"] else "unresolved"),
            "matched_terms": matched,
        }))
    candidates.sort(key=lambda x: (x[0], x[1]["cred"], x[1]["last_ts"]), reverse=True)
    findings = [x[1] for x in candidates[:max(1, min(limit, 20))]]
    return {
        "question": question, "answer_mode": "extractive_with_citations",
        "query_terms": terms,
        "findings": findings, "grounded_findings": sum(bool(x["citations"])
                                                         for x in findings),
        "total_findings": len(findings),
        "citation_coverage": (sum(bool(x["citations"]) for x in findings) / len(findings)
                              if findings else 0.0),
        "limitations": [
            "每条结论均逐句绑定原始标题或摘要引文；链接可回到原始来源。",
            "这是公开来源检索结果，不代表事实终局；单源与机构声明会保留原标签。",
            "当前不生成超出引文的综合结论，避免在 LLM 关闭时制造无依据推断。",
        ],
    }
