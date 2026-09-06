"""Reproducible clustering evaluation with pair and B-cubed metrics."""
import json
from pathlib import Path

from . import cluster
from .normalize import simhash, tokens


def load_gold(path: Path) -> tuple[list[dict], list[dict]]:
    spec = json.loads(path.read_text(encoding="utf-8"))
    items, positives, negatives = [], [], []
    events = spec["events"]
    for ei, entity in enumerate(spec["entities"]):
        entity_items = []
        for vi, event in enumerate(events):
            event_id = f"e{ei:02d}-{vi:02d}"
            zh_id, en_id = event_id + "-zh", event_id + "-en"
            for item_id, title, lang in (
                    (zh_id, event["zh"].format(entity=entity["zh"]), "zh"),
                    (en_id, event["en"].format(entity=entity["en"]), "en")):
                words = tokens(title)
                items.append({"id": item_id, "title": title, "lang": lang,
                              "gold_event": event_id, "grams": set(words),
                              "simhash": simhash(words)})
            positives.append({"a": zh_id, "b": en_id, "same": True})
            entity_items.append((zh_id, en_id))
        # One hard negative per positive: same entity, different event.
        for vi, (zh_id, _) in enumerate(entity_items):
            negatives.append({"a": zh_id,
                              "b": entity_items[(vi + 1) % len(events)][1],
                              "same": False})
    pairs = positives + negatives
    expected = int(spec.get("expected_pairs", len(pairs)))
    if len(pairs) != expected:
        raise ValueError(f"gold set expected {expected} pairs, generated {len(pairs)}")
    return items, pairs


def evaluate(items: list[dict], pairs: list[dict]) -> dict:
    groups = cluster.build([dict(x) for x in items])
    predicted = {item["id"]: cid for cid, members in groups.items() for item in members}
    gold = {item["id"]: item["gold_event"] for item in items}
    tp = fp = fn = tn = 0
    for pair in pairs:
        same_pred = predicted[pair["a"]] == predicted[pair["b"]]
        if pair["same"] and same_pred:
            tp += 1
        elif pair["same"]:
            fn += 1
        elif same_pred:
            fp += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0

    pred_members, gold_members = {}, {}
    for item_id, cid in predicted.items():
        pred_members.setdefault(cid, set()).add(item_id)
        gold_members.setdefault(gold[item_id], set()).add(item_id)
    bp = br = 0.0
    for item_id in gold:
        intersection = pred_members[predicted[item_id]] & gold_members[gold[item_id]]
        bp += len(intersection) / len(pred_members[predicted[item_id]])
        br += len(intersection) / len(gold_members[gold[item_id]])
    n = len(gold) or 1
    bp, br = bp / n, br / n
    bf = 2 * bp * br / (bp + br) if bp + br else 0.0
    return {"items": len(items), "pairs": len(pairs), "clusters": len(groups),
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "pair_precision": round(precision, 4), "pair_recall": round(recall, 4),
            "pair_f1": round(2 * precision * recall / (precision + recall), 4)
            if precision + recall else 0.0,
            "bcubed_precision": round(bp, 4), "bcubed_recall": round(br, 4),
            "bcubed_f1": round(bf, 4)}
