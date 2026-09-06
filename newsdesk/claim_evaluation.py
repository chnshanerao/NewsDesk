"""Human, double-blind claim-evidence relation evaluation workflow."""
import hashlib
import json
import random
from pathlib import Path

from . import evidence
from .normalize import gram_set, jaccard, overlap

LABELS = {"support", "refute", "unknown"}


def export_pairs(conn, out_dir: Path, limit: int = 300, seed: int = 20260906) -> dict:
    rows = [dict(x) for x in conn.execute(
        "SELECT ce.claim_id,ce.item_id,ce.relation AS system_relation,ce.quote,"
        "ce.quote_field,ce.quote_start,ce.quote_end,ce.quote_hash,ce.source_role,"
        "c.cluster_id,c.text AS claim_text,i.title,i.summary,i.lang "
        "FROM claim_evidence ce JOIN claims c ON c.id=ce.claim_id "
        "JOIN items i ON i.id=ce.item_id")]
    # Add real cross-event hard negatives. Event clustering intentionally separates
    # opposite directions, so an in-cluster-only sample cannot measure refutation.
    claims = [dict(x) for x in conn.execute(
        "SELECT id AS claim_id,cluster_id,text AS claim_text FROM claims")]
    items = [dict(x) for x in conn.execute(
        "SELECT id AS item_id,cluster_id,title,summary,lang,url,source_id "
        "FROM items WHERE cluster_id IS NOT NULL")]
    for claim in claims:
        claim["grams"] = gram_set(claim["claim_text"])
    for item in items:
        item["text"] = f"{item.get('title') or ''} {(item.get('summary') or '')[:500]}"
        item["grams"] = gram_set(item["text"])
    seen = {(x["claim_id"], x["item_id"]) for x in rows}
    for positive, negative in evidence.OPPOSITES:
        left_claims = [x for x in claims if evidence._has_any(x["claim_text"], positive)]
        right_items = [x for x in items if evidence._has_any(x["text"], negative)]
        reverse_claims = [x for x in claims if evidence._has_any(x["claim_text"], negative)]
        reverse_items = [x for x in items if evidence._has_any(x["text"], positive)]
        for claim_group, item_group in ((left_claims, right_items),
                                        (reverse_claims, reverse_items)):
            for claim in claim_group:
                for item in item_group:
                    key = (claim["claim_id"], item["item_id"])
                    if key in seen or claim["cluster_id"] == item["cluster_id"]:
                        continue
                    score = max(jaccard(claim["grams"], item["grams"]),
                                overlap(claim["grams"], item["grams"]) * .82)
                    if score < .26:
                        continue
                    quote = item["title"] or ""
                    rows.append({**claim, **item, "quote": quote, "quote_field": "title",
                                 "quote_start": 0, "quote_end": len(quote),
                                 "quote_hash": hashlib.sha256(quote.encode()).hexdigest()[:16],
                                 "source_role": "unknown", "system_relation": "refute"})
                    seen.add(key)
    uncertain_claims = [x for x in claims if evidence._has_any(x["claim_text"],
                                                               evidence.UNCERTAINTY)]
    certain_items = [x for x in items if not evidence._has_any(x["text"],
                                                               evidence.UNCERTAINTY)]
    certain_claims = [x for x in claims if not evidence._has_any(x["claim_text"],
                                                                 evidence.UNCERTAINTY)]
    uncertain_items = [x for x in items if evidence._has_any(x["text"],
                                                             evidence.UNCERTAINTY)]
    for claim_group, item_group in ((uncertain_claims, certain_items),
                                    (certain_claims, uncertain_items)):
        for claim in claim_group:
            for item in item_group:
                key = (claim["claim_id"], item["item_id"])
                if key in seen or claim["cluster_id"] == item["cluster_id"]:
                    continue
                score = max(jaccard(claim["grams"], item["grams"]),
                            overlap(claim["grams"], item["grams"]) * .82)
                if score < .32:
                    continue
                quote = item["title"] or ""
                rows.append({**claim, **item, "quote": quote, "quote_field": "title",
                             "quote_start": 0, "quote_end": len(quote),
                             "quote_hash": hashlib.sha256(quote.encode()).hexdigest()[:16],
                             "source_role": "unknown", "system_relation": "unknown"})
                seen.add(key)
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
                "ready_for_annotation": (len(payload) >= 300 and
                                         min(relation_distribution.values()) >= 30),
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
    if not manifest.get("ready_for_annotation"):
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
    return {"valid": True, "structurally_valid": True,
            "human_adjudicated": attested, "pairs": len(expected),
            "annotators": [author_a, author_b], "adjudicator": adjudicator,
            "disagreements": disagreements, "agreement": round(agreement, 4),
            "cohen_kappa": round(kappa, 4), "labels": final,
            "detail": ("human attestation verified" if attested else
                       "structure valid; explicit human attestation is still required")}
