#!/usr/bin/env python3
"""信源「战绩单」：从已入库数据回算每个源的编辑表现，不需要新采集。

与 source_health 的分工要说清楚：
  source_health  = 运维健康（能不能抓通、解析对不对、新鲜不新鲜）
  source_scorecard = 编辑战绩（它首发的事，后来有没有别人独立跟进）

判据全部可从 items + clusters 回算：
  lead        该源是某事件簇里最早发布的那条
  corroborated 该簇后来出现了不同 owner 的稿件
  solo        该簇至今只有这一个 owner
  lead_time   从该源首发到第二个 owner 跟进的时间差（中位数）

「单源报道」因此不再是一刀切扣分，而是：某源的单源报道该给多少信任，
取决于它历史上的单源报道后来被印证的比例。
"""
import json
import sqlite3
import statistics
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "newsdesk.db"
MIN_ITEMS = int(sys.argv[1]) if len(sys.argv) > 1 else 30


def main() -> None:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT source_id, grp, cluster_id, "
        "COALESCE(published_ts, fetched_ts) AS ts "
        "FROM items WHERE cluster_id IS NOT NULL AND COALESCE(published_ts, fetched_ts) IS NOT NULL"
    ).fetchall()

    clusters: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        clusters.setdefault(r["cluster_id"], []).append(r)

    stat: dict[str, dict] = {}

    def slot(sid):
        return stat.setdefault(sid, {"items": 0, "clusters": 0, "leads": 0,
                                     "leads_corroborated": 0, "solo": 0, "lead_times": []})

    for members in clusters.values():
        members.sort(key=lambda r: r["ts"])
        owners = {r["grp"] for r in members}
        first_owner = members[0]["grp"]
        # 第二个 owner 的首次跟进时间 —— 领先时间的度量终点
        follow_ts = next((r["ts"] for r in members if r["grp"] != first_owner), None)
        for sid in {r["source_id"] for r in members}:
            slot(sid)["clusters"] += 1
            if len(owners) == 1:
                slot(sid)["solo"] += 1
        for r in members:
            slot(r["source_id"])["items"] += 1
        lead_src = members[0]["source_id"]
        s = slot(lead_src)
        s["leads"] += 1
        if len(owners) > 1:
            s["leads_corroborated"] += 1
            if follow_ts is not None:
                s["lead_times"].append(follow_ts - members[0]["ts"])

    names = {r["source_id"]: r["name"] for r in
             conn.execute("SELECT source_id, name FROM source_health")}

    out = []
    for sid, s in stat.items():
        if s["items"] < MIN_ITEMS:
            continue
        out.append({
            "source_id": sid,
            "name": names.get(sid, sid),
            "items": s["items"],
            "clusters": s["clusters"],
            "leads": s["leads"],
            # 首发被印证率：它抢到的独家，后来有多少被别的 owner 跟上
            "lead_corroboration_rate": round(s["leads_corroborated"] / s["leads"], 3) if s["leads"] else None,
            # 孤证率：它参与的事件里，至今仍只有它一家在报
            "solo_rate": round(s["solo"] / s["clusters"], 3) if s["clusters"] else None,
            "lead_time_median_h": (round(statistics.median(s["lead_times"]) / 3600, 1)
                                   if s["lead_times"] else None),
        })
    out.sort(key=lambda r: (-(r["lead_corroboration_rate"] or 0), r["solo_rate"] or 1))

    print(f"信源战绩单（稿件数 ≥{MIN_ITEMS}，共 {len(out)} 个源）\n")
    print(f"{'source_id':24}{'稿':>6}{'首发':>6}{'首发被印证':>11}{'孤证率':>9}{'领先中位':>9}")
    for r in out:
        lcr = f"{r['lead_corroboration_rate']:.0%}" if r["lead_corroboration_rate"] is not None else "-"
        solo = f"{r['solo_rate']:.0%}" if r["solo_rate"] is not None else "-"
        lt = f"{r['lead_time_median_h']}h" if r["lead_time_median_h"] is not None else "-"
        print(f"{r['source_id']:24}{r['items']:>6}{r['leads']:>6}{lcr:>11}{solo:>9}{lt:>9}")

    Path(__file__).parent.joinpath("source_scorecard.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
