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


def briefing(conn, profile: dict, *, hours=24, top=12) -> str:
    now = int(datetime.now(CN_TZ).timestamp())
    since = now - hours * 3600
    rows = [_c(r) for r in store.feed(conn, limit=400, since_ts=since)]

    min_cred = profile.get("min_credibility", 40)
    min_rel = profile.get("min_relevance", 0.12)
    signal = [c for c in rows if c["cred"] >= min_cred and c["relevance"] >= min_rel]
    unverified = [c for c in rows if c["cred_code"] == "SINGLE"
                  and c["relevance"] >= min_rel][:8]
    noise = [c for c in rows if c["relevance"] < min_rel or c["cred"] < min_cred]

    L: list[str] = []
    L.append(f"# 新闻简报 · {datetime.now(CN_TZ).strftime('%Y-%m-%d %H:%M')}")
    L.append("")
    L.append(f"> 画像：{profile.get('name')} ｜ 窗口：近 {hours}h ｜ "
             f"事件 {len(rows)} 个，入选 {len(signal)}，噪音过滤 {len(noise)}")
    L.append("")

    L.append("## 一、值得你看的（可信度 × 相关性 排序）")
    L.append("")
    if not signal:
        L.append("_本窗口内没有同时满足可信度与相关性门槛的事件。_")
    for i, c in enumerate(signal[:top], 1):
        b = BADGE.get(c["cred_code"], "•")
        L.append(f"### {i}. {b} {c['headline']}")
        line = (f"`{c['cred']:.0f}分/{c['cred_label']}` · "
                f"{c['n_groups']} 个独立信源 / {c['n_items']} 篇 · "
                f"T{c['best_tier']} {c['headline_src']} · {fmt_ts(c['last_ts'])} · "
                f"相关性 {c['relevance']:.2f}")
        L.append(line)
        if c["llm"]:
            llm = c["llm"]
            if llm.get("summary"):
                L.append(f"- **发生了什么**：{llm['summary']}")
            if llm.get("so_what"):
                L.append(f"- **对你意味着**：{llm['so_what']}")
            if llm.get("red_flags"):
                L.append(f"- **⚑ 可疑点**：{'；'.join(llm['red_flags'][:3])}")
            if llm.get("verify_next"):
                L.append(f"- **自己核实**：{llm['verify_next']}")
        else:
            bd = c["breakdown"]
            pos = bd.get("content", {}).get("positive") or []
            neg = bd.get("content", {}).get("negative") or []
            if pos:
                L.append(f"- 正向信号：{'、'.join(pos[:3])}")
            if neg:
                L.append(f"- 风险信号：{'、'.join(neg[:3])}")
        if c["url"]:
            L.append(f"- 原文：{c['url']}")
        L.append("")

    L.append("## 二、单源待证（有信息量但只有一家在说，别急着当事实）")
    L.append("")
    if not unverified:
        L.append("_无。_")
    for c in unverified:
        L.append(f"- 🟡 `{c['cred']:.0f}` {c['headline']} ｜ {c['headline_src']}"
                 f" ｜ {fmt_ts(c['last_ts'])}")
    L.append("")

    L.append("## 三、被过滤掉的噪音（抽样）")
    L.append("")
    for c in noise[:8]:
        why = []
        if c["relevance"] < min_rel:
            nz = c["breakdown"].get("relevance", {}).get("noise") or []
            why.append("与你无关" + (f"（{'/'.join(nz[:2])}）" if nz else ""))
        if c["cred"] < min_cred:
            why.append(f"可信度仅 {c['cred']:.0f}")
        L.append(f"- ~~{c['headline'][:46]}~~ ← {'，'.join(why)}")
    L.append("")

    L.append("## 四、信源健康")
    L.append("")
    L.append("| 信源 | 层级 | 状态 | 本次条数 | 延迟 |")
    L.append("|---|---|---|---|---|")
    for h in store.health(conn):
        ok = "✅" if h["last_error"] is None else f"❌ {h['last_error']}"
        L.append(f"| {h['name']} | T{h['tier']} | {ok} | {h['last_items']} "
                 f"| {h['ms']}ms |")
    L.append("")
    L.append("---")
    L.append(f"_由 newsdesk v{__import__('newsdesk').__version__} 生成。"
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
