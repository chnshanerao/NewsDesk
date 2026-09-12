"""Human, double-blind claim-evidence relation evaluation workflow."""
import hashlib
import json
import random
from pathlib import Path

from . import evidence
from .crosslingual import features
from .normalize import gram_set, jaccard, overlap

LABELS = {"support", "refute", "unknown"}


def _candidate(claim: dict, item: dict) -> dict | None:
    """把候选对交给生产分类器打标，而不是由采样器自己断言标签。

    采样器原先直接写 system_relation="refute"，于是 system_predictions.jsonl 记的
    不是系统的判断而是采样器的虚构 —— 拿它去对人工标注，量的是采样器不是系统。
    标签只能有一个来源：evidence._relation。判不出关系（None）的对没有评估价值，
    直接丢掉。
    """
    relation = evidence._relation(claim["claim_text"], item)
    if relation is None:
        return None
    quote = evidence._best_quote(claim["claim_text"], item)
    return {**claim, **item, **quote, "source_role": item.get("source_role") or "unknown",
            "system_relation": relation}


def _polarity_conflict(claim_text: str, item_text: str) -> bool:
    """同期的方向性冲突：表层反义词组或互斥事件标签，任一命中即算。"""
    for positive, negative in evidence.OPPOSITES:
        if (evidence._has_any(claim_text, positive)
                and evidence._has_any(item_text, negative)) or \
                (evidence._has_any(claim_text, negative)
                 and evidence._has_any(item_text, positive)):
            return True
    fc, fi = features(claim_text), features(item_text)
    return any({one, other} <= (fc.events | fi.events)
               and (one in fc.events) != (one in fi.events)
               for one, other in evidence.EVENT_OPPOSITES)


def export_pairs(conn, out_dir: Path, limit: int = 300, seed: int = 20260906) -> dict:
    stored = [dict(x) for x in conn.execute(
        "SELECT ce.claim_id,ce.item_id,ce.quote,"
        "ce.quote_field,ce.quote_start,ce.quote_end,ce.quote_hash,ce.source_role,"
        "c.cluster_id,c.text AS claim_text,i.title,i.summary,i.lang,i.published_ts "
        "FROM claim_evidence ce JOIN claims c ON c.id=ce.claim_id "
        "JOIN items i ON i.id=ce.item_id")]
    # 关系一律现算，不读 claim_evidence.relation 存的值。存的是历史某个版本的分类器
    # 写下的结论；拿它当「系统预测」去对人工标注，量的是那个旧版本，不是当前代码。
    # 引号沿用入库时审计过的那一条，不重算 —— 引号是证据，关系是判断。
    rows = []
    for row in stored:
        relation = evidence._relation(row["claim_text"], row)
        if relation is not None:
            rows.append({**row, "system_relation": relation})
    # 补充候选对，扩大 refute 与 unknown 的召回。这里只负责「找出可能有关系的对」，
    # 标签一律由 evidence._relation 给（见 _candidate）。
    #
    # 原先这段还要求 claim 与 item 不同簇，理由写的是「聚类会把相反方向分开，所以
    # 同簇采样量不到反驳」。这个理由在当前聚类下不成立：聚类严重过分裂（25 篇同事件
    # 英文稿分成 24 簇），不同簇并不意味着不同事件。而反驳本质上是同事件关系，
    # 排除同簇恰好把真反驳全排除了，剩下的全是「同一系列的不同期次」——
    # 国债第五十七期对第五十八期、1月 PPI 对 5月 PPI。所以改为期次相容 + 方向冲突。
    claims = [dict(x) for x in conn.execute(
        "SELECT id AS claim_id,cluster_id,text AS claim_text FROM claims")]
    items = [dict(x) for x in conn.execute(
        "SELECT id AS item_id,cluster_id,title,summary,lang,url,source_id,published_ts "
        "FROM items WHERE cluster_id IS NOT NULL")]
    for claim in claims:
        claim["grams"] = gram_set(claim["claim_text"])
    for item in items:
        item["text"] = f"{item.get('title') or ''} {(item.get('summary') or '')[:500]}"
        item["grams"] = gram_set(item["text"])
    seen = {(x["claim_id"], x["item_id"]) for x in rows}

    def harvest(claim_group, item_group, floor: float, gate) -> None:
        for claim in claim_group:
            for item in item_group:
                key = (claim["claim_id"], item["item_id"])
                if key in seen:
                    continue
                score = max(jaccard(claim["grams"], item["grams"]),
                            overlap(claim["grams"], item["grams"]) * .82)
                if score < floor or not gate(claim, item):
                    continue
                candidate = _candidate(claim, item)
                seen.add(key)
                if candidate is not None:
                    rows.append(candidate)

    # 反驳候选：同期 + 方向冲突。不再看簇归属。
    for positive, negative in evidence.OPPOSITES:
        for left, right in ((positive, negative), (negative, positive)):
            harvest([x for x in claims if evidence._has_any(x["claim_text"], left)],
                    [x for x in items if evidence._has_any(x["text"], right)],
                    .26, lambda c, i: _polarity_conflict(c["claim_text"], i["text"]))
    # 不确定性错配候选：一侧带「可能/预计/reportedly」另一侧不带。这类仍要求不同簇，
    # 因为同簇的措辞差异已由 claim_evidence 主查询覆盖。
    uncertain = (lambda x, k: evidence._has_any(x[k], evidence.UNCERTAINTY))
    for claim_group, item_group in (
            ([x for x in claims if uncertain(x, "claim_text")],
             [x for x in items if not uncertain(x, "text")]),
            ([x for x in claims if not uncertain(x, "claim_text")],
             [x for x in items if uncertain(x, "text")])):
        harvest(claim_group, item_group, .32,
                lambda c, i: c["cluster_id"] != i["cluster_id"])
    rng = random.Random(seed)
    rng.shuffle(rows)
    # Balance relation classes first, then language/source role inside each class.
    per_relation = max(1, limit // len(LABELS))
    selected = []
    relation_distribution = {}
    for relation in sorted(LABELS):
        candidates = [x for x in rows if x.get("system_relation") == relation and
                      (relation != "support" or x.get("claim_text") != x.get("quote"))]
        if relation == "support" and len(candidates) < per_relation:
            candidates.extend(x for x in rows if x.get("system_relation") == relation and
                              x.get("claim_text") == x.get("quote"))
        buckets = {}
        for row in candidates:
            buckets.setdefault((row.get("lang") or "unknown",
                                row.get("source_role") or "unknown"), []).append(row)
        chosen = []
        while len(chosen) < per_relation and any(buckets.values()):
            for key in sorted(buckets):
                if buckets[key] and len(chosen) < per_relation:
                    chosen.append(buckets[key].pop())
        selected.extend(chosen)
        relation_distribution[relation] = len(chosen)
    # 某一类凑不满配额时补足总量，但把缺口如实记进 manifest。绝不靠伪造标签填坑：
    # 上一版就是硬凑 100/100/100，代价是 refute 那 100 对里没有一对是真矛盾。
    shortfall = {r: per_relation - n for r, n in relation_distribution.items()
                 if n < per_relation}
    if len(selected) < limit:
        picked = {id(x) for x in selected}
        for row in rows:
            if len(selected) >= limit:
                break
            if id(row) not in picked:
                selected.append(row)
                relation_distribution[row["system_relation"]] = \
                    relation_distribution.get(row["system_relation"], 0) + 1
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = []
    for row in selected:
        stable = "\0".join(str(row.get(k) or "") for k in
                           ("claim_id", "item_id", "quote_hash"))
        pair = {k: v for k, v in row.items() if k not in ("grams", "text")}
        pair["pair_id"] = hashlib.sha256(stable.encode()).hexdigest()[:20]
        # Predictions are excluded from the blind annotation file.
        pair.pop("system_relation", None)
        payload.append(pair)
    pairs_path = out_dir / "pairs.jsonl"
    pairs_path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n"
                                  for x in payload), encoding="utf-8")
    predictions = [{"pair_id": pair["pair_id"],
                    "system_relation": row["system_relation"]}
                   for pair, row in zip(payload, selected)]
    predictions_path = out_dir / "system_predictions.jsonl"
    predictions_path.write_text("".join(json.dumps(x) + "\n" for x in predictions),
                                encoding="utf-8")
    snapshot_hash = hashlib.sha256(pairs_path.read_bytes()).hexdigest()
    manifest = {"dataset_version": "claim-relations-v1", "seed": seed,
                "pairs": len(payload), "snapshot_hash": snapshot_hash,
                "predictions_hash": hashlib.sha256(predictions_path.read_bytes()).hexdigest(),
                "labels": sorted(LABELS), "relation_distribution": relation_distribution,
                "class_shortfall": shortfall,
                # 判据的来历要跟着数据走。refute 的几条规则（同期、同主体、取值必须是
                # 度量）是通过人工检查本库导出的候选对反推出来的，因此本集上 refute 的
                # 准确率会偏乐观 —— 它衡量的是「已知的坏法子有没有被堵住」，
                # 不是「在没见过的数据上有多准」。support / unknown 未做此类调整。
                "tuning_disclosure": {
                    "refute_rules_derived_from_this_corpus": True,
                    "classes_not_tuned": ["support", "unknown"],
                    "note": "refute precision on this set is optimistic; "
                            "treat out-of-sample data as the real test",
                },
                # 两个门要分开报，因为「凑不出配额」有两种完全不同的原因。
                # balanced：三类各 ≥30，能同时量出三条边界 —— 本库达不到。
                # natural：总量够、且**语料里存在的**类各 ≥30。本库的真实分布是
                # support/unknown 两类，refute 在 10575 条 claim × 14689 篇稿件里
                # 只找到 1 对（65% 对 65.2%，四舍五入差异，算不上矛盾）。
                # 官方统计公报与科技博客各报一次数，同一指标同一期次被两家报出
                # 互斥取值的情形本就极少。此时仍值得标 300 对：它量的是
                # support/unknown 边界，而生产库 14118 条证据行里 13935 条是 support。
                # 未出现的类如实记进 classes_absent，绝不靠伪造标签填满配额。
                "ready_for_annotation": (len(payload) >= 300 and
                                         len(relation_distribution) == len(LABELS) and
                                         min(relation_distribution.values()) >= 30),
                "natural_distribution_ready": (
                    len(payload) >= 300 and
                    all(n >= 30 for n in relation_distribution.values() if n)),
                "classes_absent": sorted(r for r, n in relation_distribution.items()
                                         if not n),
                "human_adjudicated": False}
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _annotation_map(path: Path, expected: set[str]) -> tuple[str, dict[str, str]]:
    rows = _jsonl(path)
    annotators = {str(x.get("annotator", "")).strip() for x in rows}
    if len(annotators) != 1 or not next(iter(annotators), ""):
        raise ValueError(f"{path.name}: exactly one non-empty annotator is required")
    ids = [x.get("pair_id") for x in rows]
    if len(ids) != len(set(ids)) or set(ids) != expected:
        raise ValueError(f"{path.name}: pair ids must match the frozen sample exactly")
    labels = {x["pair_id"]: x.get("label") for x in rows}
    if any(label not in LABELS for label in labels.values()):
        raise ValueError(f"{path.name}: invalid relation label")
    return next(iter(annotators)), labels


def validate_dataset(path: Path) -> dict:
    manifest_path, pairs_path = path / "manifest.json", path / "pairs.jsonl"
    predictions_path = path / "system_predictions.jsonl"
    required = (manifest_path, pairs_path, predictions_path,
                path / "annotations_a.jsonl", path / "annotations_b.jsonl",
                path / "adjudicated.jsonl")
    if not all(x.exists() for x in required):
        return {"valid": False, "human_adjudicated": False,
                "detail": "requires frozen pairs, two blind annotations and adjudication"}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(pairs_path.read_bytes()).hexdigest()
    if digest != manifest.get("snapshot_hash"):
        raise ValueError("pair snapshot hash changed after export")
    if hashlib.sha256(predictions_path.read_bytes()).hexdigest() != \
            manifest.get("predictions_hash"):
        raise ValueError("system prediction snapshot changed after export")
    if not (manifest.get("ready_for_annotation") or
            manifest.get("natural_distribution_ready")):
        raise ValueError("sample is not balanced enough for relation evaluation")
    pairs = _jsonl(pairs_path); expected = {x["pair_id"] for x in pairs}
    if len(expected) != len(pairs) or len(pairs) < 300:
        raise ValueError("human gold requires at least 300 unique pairs")
    author_a, labels_a = _annotation_map(path / "annotations_a.jsonl", expected)
    author_b, labels_b = _annotation_map(path / "annotations_b.jsonl", expected)
    if author_a == author_b:
        raise ValueError("the two blind annotations must be from different people")
    adjudicator, final = _annotation_map(path / "adjudicated.jsonl", expected)
    if adjudicator in (author_a, author_b):
        raise ValueError("adjudicator must be a third person")
    disagreements = sum(labels_a[x] != labels_b[x] for x in expected)
    agreement = 1 - disagreements / len(expected)
    counts_a = {label: sum(x == label for x in labels_a.values()) for label in LABELS}
    counts_b = {label: sum(x == label for x in labels_b.values()) for label in LABELS}
    chance = sum((counts_a[x] / len(expected)) * (counts_b[x] / len(expected))
                 for x in LABELS)
    kappa = (agreement - chance) / (1 - chance) if chance < 1 else 1.0
    attestation = manifest.get("human_attestation") or {}
    attested = bool(attestation.get("confirmed_by_human") and
                    set(attestation.get("reviewer_ids", [])) ==
                    {author_a, author_b, adjudicator})
    # 语料里没有的类要一路带到验收结果里。否则这份金标会被当成「关系分类整体已验收」，
    # 而它实际只验了 support/unknown 两条边界。
    absent = manifest.get("classes_absent") or []
    return {"valid": True, "structurally_valid": True,
            "human_adjudicated": attested, "pairs": len(expected),
            "classes_unmeasured": absent,
            "coverage": ("all relation classes" if not absent else
                         f"support/unknown boundary only; no {'/'.join(absent)} "
                         "pairs exist in this corpus"),
            "annotators": [author_a, author_b], "adjudicator": adjudicator,
            "disagreements": disagreements, "agreement": round(agreement, 4),
            "cohen_kappa": round(kappa, 4), "labels": final,
            "detail": ("human attestation verified" if attested else
                       "structure valid; explicit human attestation is still required")}
