"""生成每日简报（Markdown）与终端文本视图。"""
import json
from datetime import datetime

from . import config, store
from .normalize import CN_TZ, fmt_ts

BADGE = {"CONFIRMED": "✅", "LIKELY": "🟢", "SINGLE": "🟡", "LOW": "🔴"}


def _c(row) -> dict:
    d = dict(row)
    d["topics"] = json.loads(row["topics"] or "[]")
    d["breakdown"] = json.loads(row["breakdown"] or "{}")
    d["llm"] = json.loads(row["llm"]) if row["llm"] else None
    return d


def _points(c: dict) -> list[dict]:
    """把一条事件的要点抽成结构化列表（LLM 摘要优先，否则规则信号）。"""
    pts: list[dict] = []
    if c["llm"]:
        llm = c["llm"]
        if llm.get("summary"):
            pts.append({"kind": "summary", "label": "发生了什么", "text": llm["summary"]})
        if llm.get("so_what"):
            pts.append({"kind": "so_what", "label": "对你意味着", "text": llm["so_what"]})
        if llm.get("red_flags"):
            pts.append({"kind": "red", "label": "可疑点",
                        "text": "；".join(llm["red_flags"][:3])})
        if llm.get("verify_next"):
            pts.append({"kind": "verify", "label": "自己核实", "text": llm["verify_next"]})
    else:
        bd = c["breakdown"]
        pos = bd.get("content", {}).get("positive") or []
        neg = bd.get("content", {}).get("negative") or []
        if pos:
            pts.append({"kind": "pos", "label": "正向信号", "text": "、".join(pos[:3])})
        if neg:
            pts.append({"kind": "neg", "label": "风险信号", "text": "、".join(neg[:3])})
    return pts


def briefing_data(conn, profile: dict, *, hours=24, top=12) -> dict:
    """简报的结构化数据（单一事实源）。briefing() 与 /api/digest?format=json 都读它。"""
    now = int(datetime.now(CN_TZ).timestamp())
    since = now - hours * 3600
    rows = [_c(r) for r in store.feed(conn, limit=400, since_ts=since)]

    min_cred = profile.get("min_credibility", 40)
    min_rel = profile.get("min_relevance", 0.12)
    signal = [c for c in rows if c["cred"] >= min_cred and c["relevance"] >= min_rel]
    unverified = [c for c in rows if c["cred_code"] == "SINGLE"
                  and c["relevance"] >= min_rel][:8]
    noise = [c for c in rows if c["relevance"] < min_rel or c["cred"] < min_cred]

    def _event(c, rank=None):
        return {
            "rank": rank, "cred_code": c["cred_code"],
            "badge": BADGE.get(c["cred_code"], "•"),
            "headline": c["headline"], "cred": round(c["cred"]),
            "cred_label": c["cred_label"], "n_groups": c["n_groups"],
            "n_items": c["n_items"], "best_tier": c["best_tier"],
            "src": c["headline_src"], "ts": fmt_ts(c["last_ts"]),
            "relevance": round(c["relevance"], 2), "url": c["url"],
            "points": _points(c),
        }

    noise_out = []
    for c in noise[:8]:
        why = []
        if c["relevance"] < min_rel:
            nz = c["breakdown"].get("relevance", {}).get("noise") or []
            why.append("与你无关" + (f"（{'/'.join(nz[:2])}）" if nz else ""))
        if c["cred"] < min_cred:
            why.append(f"可信度仅 {c['cred']:.0f}")
        noise_out.append({"headline": c["headline"][:46], "why": "，".join(why)})

    health = []
    for h in store.health(conn):
        health.append({"name": h["name"], "tier": h["tier"],
                       "ok": h["last_error"] is None, "err": h["last_error"],
                       "items": h["last_items"], "ms": h["ms"]})

    return {
        "generated_at": datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M"),
        "profile": profile.get("name"), "window_hours": hours,
        "counts": {"events": len(rows), "signal": len(signal), "noise": len(noise)},
        "signal": [_event(c, i) for i, c in enumerate(signal[:top], 1)],
        "unverified": [_event(c) for c in unverified],
        "noise": noise_out, "health": health,
        "version": __import__("newsdesk").__version__,
    }


def briefing(conn, profile: dict, *, hours=24, top=12) -> str:
    d = briefing_data(conn, profile, hours=hours, top=top)
    cnt = d["counts"]
    L: list[str] = []
    L.append(f"# 新闻简报 · {d['generated_at']}")
    L.append("")
    L.append(f"> 画像：{d['profile']} ｜ 窗口：近 {hours}h ｜ "
             f"事件 {cnt['events']} 个，入选 {cnt['signal']}，噪音过滤 {cnt['noise']}")
    L.append("")

    _KIND_MD = {"summary": "**发生了什么**", "so_what": "**对你意味着**",
                "red": "**⚑ 可疑点**", "verify": "**自己核实**",
                "pos": "正向信号", "neg": "风险信号"}

    L.append("## 一、值得你看的（可信度 × 相关性 排序）")
    L.append("")
    if not d["signal"]:
        L.append("_本窗口内没有同时满足可信度与相关性门槛的事件。_")
    for c in d["signal"]:
        L.append(f"### {c['rank']}. {c['badge']} {c['headline']}")
        L.append(f"`{c['cred']}分/{c['cred_label']}` · "
                 f"{c['n_groups']} 个独立信源 / {c['n_items']} 篇 · "
                 f"T{c['best_tier']} {c['src']} · {c['ts']} · 相关性 {c['relevance']:.2f}")
        for p in c["points"]:
            L.append(f"- {_KIND_MD.get(p['kind'], p['label'])}：{p['text']}")
        if c["url"]:
            L.append(f"- 原文：{c['url']}")
        L.append("")

    L.append("## 二、单源待证（有信息量但只有一家在说，别急着当事实）")
    L.append("")
    if not d["unverified"]:
        L.append("_无。_")
    for c in d["unverified"]:
        L.append(f"- 🟡 `{c['cred']}` {c['headline']} ｜ {c['src']} ｜ {c['ts']}")
    L.append("")

    L.append("## 三、被过滤掉的噪音（抽样）")
    L.append("")
    for c in d["noise"]:
        L.append(f"- ~~{c['headline']}~~ ← {c['why']}")
    L.append("")

    L.append("## 四、信源健康")
    L.append("")
    L.append("| 信源 | 层级 | 状态 | 本次条数 | 延迟 |")
    L.append("|---|---|---|---|---|")
    for h in d["health"]:
        ok = "✅" if h["ok"] else f"❌ {h['err']}"
        L.append(f"| {h['name']} | T{h['tier']} | {ok} | {h['items']} | {h['ms']}ms |")
    L.append("")
    L.append("---")
    L.append(f"_由 newsdesk v{d['version']} 生成。"
             f"可信度 = 40% 信源权威 + 30% 独立交叉印证 + 20% 内容质量 + 10% 时间一致性_")
    return "\n".join(L)


def terminal_view(conn, profile: dict, *, limit=25, min_cred=0.0) -> str:
    rows = [_c(r) for r in store.feed(conn, limit=limit, min_cred=min_cred)]
    W = 96
    out = ["═" * W,
           f" NEWSDESK ─ 事件流 (TOP {len(rows)})".ljust(W - 22)
           + datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S"),
           "═" * W]
    for i, c in enumerate(rows, 1):
        b = BADGE.get(c["cred_code"], "•")
        head = c["headline"]
        if len(head) > 58:
            head = head[:57] + "…"
        out.append(f"{i:>2} {b} [{c['cred']:>4.0f}] {head}")
        out.append(f"     {c['n_groups']}源/{c['n_items']}篇 · T{c['best_tier']} "
                   f"{c['headline_src']} · rel {c['relevance']:.2f} · "
                   f"{fmt_ts(c['last_ts'])} · {'/'.join(c['topics'][:3])}")
        if c["llm"] and c["llm"].get("summary"):
            out.append(f"     ▸ {c['llm']['summary'][:80]}")
    out.append("═" * W)
    return "\n".join(out)
