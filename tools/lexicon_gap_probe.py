#!/usr/bin/env python3
"""量化 crosslingual 词典的覆盖缺口 —— 不改生产代码，只在内存里打补丁对比。

已知事实（本脚本验证）：
  - bridge_score 要求 实体∩ 且 事件∩ 才生成桥接键
  - EVENT_ALIASES['launch'] 收了 'released' 但漏了 'releases'/'announces'/'introduces'/
    'rolls out'，收了 '发布新产品' 但漏了最常用的裸 '发布'
  - 结果：25 篇英文 GPT-6 Astra 稿只有 1 篇命中事件词典，实体∩事件∩ 配对 0/300
"""
import itertools
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import newsdesk.crosslingual as xl  # noqa: E402

# 只补最高频的表层形式，不引入新事件类型 —— 把「词典漏词」和「词典缺类」分开度量
PATCH = {
    "launch": ("发布", "上线", "登场", "问世", "releases", "release", "announces",
               "announce", "introduces", "introducing", "unveil", "unveiled",
               "rolls out", "rolling out", "debuts", "ships"),
    "earnings": ("同比增长", "营收", "季度业绩", "reports revenue", "posts revenue"),
    "partnership": ("达成合作", "合作", "携手", "partners", "partnership", "teams up",
                    "signs deal", "deal with", "collaborate", "collaboration"),
    "availability": ("开放", "全面可用", "上市", "general availability", "now available",
                     "reaches general availability", "arrives"),
}


def coverage(rows, tag: str) -> None:
    feats = {r["id"]: xl.features(r["title"]) for r in rows}
    n_ent = sum(1 for f in feats.values() if f.entities)
    n_ev = sum(1 for f in feats.values() if f.events)
    both = sum(1 for a, b in itertools.combinations(rows, 2)
               if (feats[a["id"]].entities & feats[b["id"]].entities)
               and (feats[a["id"]].events & feats[b["id"]].events))
    total = len(rows) * (len(rows) - 1) // 2
    print(f"  {tag:10} 命中实体 {n_ent:>3}/{len(rows)}   命中事件 {n_ev:>3}/{len(rows)}"
          f"   实体∩事件∩ 配对 {both:>4}/{total}")


def main() -> None:
    conn = sqlite3.connect("file:data/newsdesk.db?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    probes = [("GPT-6 Astra", "en"), ("GPT-6 Astra", "zh"),
              ("Qualcomm", "en"), ("高通", "zh"), ("Apple Watch", "en")]
    for kw, lang in probes:
        rows = conn.execute(
            "SELECT id,title FROM items WHERE lang=? AND title LIKE ? "
            "AND cluster_id IS NOT NULL", (lang, f"%{kw}%")).fetchall()
        if len(rows) < 2:
            continue
        print(f"{lang} “{kw}”  {len(rows)} 篇")
        coverage(rows, "补丁前")
        merged = {k: tuple(v) + PATCH.get(k, ()) for k, v in xl.EVENT_ALIASES.items()}
        for k, v in PATCH.items():
            merged.setdefault(k, tuple(v))
        orig = xl.EVENT_ALIASES
        try:
            xl.EVENT_ALIASES = merged
            coverage(rows, "补丁后")
        finally:
            xl.EVENT_ALIASES = orig


if __name__ == "__main__":
    main()
