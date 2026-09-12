#!/usr/bin/env python3
"""候选信源可用性实测：只报「能不能抓、抓到几条、最新一条多久以前」。

不写库、不改 sources.json。给人看的判据是「实测通」，不是「看起来像 RSS」。
通过要同时满足三条：抓得到、解析出条目、条目带真实发布时间 —— 没有 published_ts 的
feed 进不了新鲜事件窗口，加了等于白加。「最新一条多久以前」是第四道人工判据：
实测过 europarl 的 top-stories feed 一切正常但最新一条停在 2023 年。

用法：python3 tools/probe_candidates.py                  # 跑 tools/candidates.json
      python3 tools/probe_candidates.py 候选文件.json     # 跑指定候选文件
      python3 tools/probe_candidates.py id1 id2          # 只跑其中几个
"""
import concurrent.futures as cf
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from newsdesk import fetch  # noqa: E402

DEFAULT_CANDIDATES = Path(__file__).parent / "candidates.json"


def load_candidates(path: Path) -> list:
    data = json.loads(path.read_text("utf-8"))
    return data["sources"] if isinstance(data, dict) else data


def probe(src: dict, timeout: int = 25) -> dict:
    t0 = time.time()
    row = {"id": src["id"], "lang": src.get("lang"), "owner": src.get("group"),
           "name": src.get("name"), "url": src["url"]}
    try:
        raw = fetch.http_get(src["url"], timeout=timeout)
        items = fetch.parse(fetch.decode(raw), src)
        dated = [x["published_ts"] for x in items if x.get("published_ts")]
        row.update(ok=bool(items) and bool(dated), n=len(items), bytes=len(raw),
                   dated=len(dated),
                   newest_age_h=round((time.time() - max(dated)) / 3600, 1) if dated else None,
                   sample=(items[0]["title"][:70] if items else ""))
    except Exception as exc:
        row.update(ok=False, n=0, error=f"{type(exc).__name__}: {exc}"[:120])
    row["ms"] = int((time.time() - t0) * 1000)
    return row


def main() -> None:
    args = sys.argv[1:]
    src_file = DEFAULT_CANDIDATES
    if args and args[0].endswith(".json"):
        src_file = Path(args.pop(0))
    only = set(args)
    todo = [c for c in load_candidates(src_file) if not only or c["id"] in only]
    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(probe, todo))
    # 首轮超时不等于源不可用：mitmproxy 出口 + 并发会放大延迟。失败项串行重试一次，
    # 用 60s 上限区分「真的连不上」和「只是慢」。
    retry = [c for c, r in zip(todo, rows) if not r["ok"]]
    if retry:
        print(f"（{len(retry)} 个首轮失败，串行重试 timeout=60）", file=sys.stderr)
        second = {c["id"]: probe(c, timeout=60) for c in retry}
        rows = [second.get(r["id"], r) if not r["ok"] else r for r in rows]
    rows.sort(key=lambda r: (not r["ok"], r.get("lang") or "", r["id"]))
    ok = [r for r in rows if r["ok"]]
    print(f"{'id':26} {'lang':4} {'n':>4} {'带时间':>5} {'新':>7}  标题样本 / 错误")
    for r in rows:
        age = f"{r['newest_age_h']}h" if r.get("newest_age_h") is not None else "-"
        tail = r.get("sample") or r.get("error", "")
        if r["n"] and not r.get("dated"):
            tail = "无 published_ts，进不了新鲜窗口 — " + tail
        flag = " " if r["ok"] else "✗"
        print(f"{flag}{r['id']:25} {(r.get('lang') or '?'):4} {r['n']:>4} "
              f"{r.get('dated', 0):>5} {age:>7}  {tail}")
    print(f"\n通 {len(ok)}/{len(rows)}")
    Path(__file__).parent.joinpath("probe_result.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
