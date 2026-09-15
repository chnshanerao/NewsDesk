"""可选的入库翻译层：把外文（非中/英）标题译成英文 canonical。

为什么需要：`crosslingual.bridge_score` 只在跨脚本（CJK↔Latin）时触发，
所以法/德/葡等拉丁语系彼此、以及与英文之间永远桥不上（同脚本），
`cluster` 的字符 gram 也不会跨语言重合。结果这些外文稿完全孤立，
无法参与交叉印证。把它们译成英文后：
  1. 英文译文与英文报道靠 gram 直接聚成一簇（走英文主聚类）；
  2. 译成英文（Latin）后还能与中文报道走 zh↔en 桥。
两条通道都打开，外文稿才真正进印证。

设计铁律（这是「增强层」不是「依赖」）：
- 可选：未开启 / 无 API key → 整个模块是 no-op，原样返回，绝不阻断抓取。
- 缓存：按 (src_lang, 原文) 的 hash 落库，每条只译一次 —— 成本的真正开关。
- 有界：单轮最多译 N 条新标题，超出留到下一轮，防突发打爆配额。
- 可审计：原文 + 译文 + 模型 + 时间都留档，随时可复核/回溯，也便于估真实成本。
- 忠实展示：只写入 items.canonical_title 供聚类/实体抽取，items.title（原文）不动，
  用户看到的仍是原始外文标题。
"""
import hashlib
import json
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import config
from .normalize import clean_title, gram_set, simhash, tokens

# —— 真实 token 消耗计量 ——
# _translate_one 每次成功调用把 API 回传的 usage 累加进 _usage（线程安全）；
# 每个翻译入口（enrich / _fill_zh）结束时 _flush_usage 把累计值落一行 translate_usage，
# 之后清零。mock 掉 _translate_one 的测试不会触碰 _usage，故计量对测试透明。
_usage_lock = threading.Lock()
_usage = {"prompt": 0, "completion": 0, "calls": 0}


def _add_usage(resp: dict) -> None:
    u = resp.get("usage") or {}
    pt = int(u.get("prompt_tokens") or 0)
    ct = int(u.get("completion_tokens") or 0)
    with _usage_lock:
        _usage["prompt"] += pt
        _usage["completion"] += ct
        _usage["calls"] += 1


def _flush_usage(conn, kind: str) -> None:
    """把自上次 flush 以来累计的 token 用量落一行，然后清零。零调用则跳过。"""
    with _usage_lock:
        pt, ct, n = _usage["prompt"], _usage["completion"], _usage["calls"]
        _usage["prompt"] = _usage["completion"] = _usage["calls"] = 0
    if n == 0:
        return
    conn.execute(
        "CREATE TABLE IF NOT EXISTS translate_usage("
        "ts INTEGER, model TEXT, kind TEXT, calls INTEGER, "
        "prompt_tokens INTEGER, completion_tokens INTEGER)")
    conn.execute(
        "INSERT INTO translate_usage(ts, model, kind, calls, "
        "prompt_tokens, completion_tokens) VALUES (?,?,?,?,?,?)",
        (_now_ts(), config.TRANSLATE_MODEL, kind, n, pt, ct))
    conn.commit()

SYSTEM = ("You are a precise news-headline translator. Translate the given "
          "headline into natural English. Output only the translation — no "
          "quotes, no explanation, no source-language text, no trailing period "
          "unless the original has one.")

SYSTEM_ZH = ("你是专业的新闻翻译。把给定的新闻文本准确、自然地翻译成简体中文。"
             "只输出译文本身——不要加引号、不要解释、不要保留原文。"
             "人名、机构、公司、产品沿用通用中文译名；确无通用译名的专有名词可保留原文。"
             "保持新闻语体，不要增删信息。")


def _hash(src_lang: str, text: str, target: str = "en") -> str:
    # target 折进 key：en(canonical) 保持旧 key 不变（存量缓存不失效），
    # zh(展示) 加前缀走独立命名空间，同一条外文的英文/中文译文各存各的。
    prefix = "" if target == "en" else f"{target}\n"
    return hashlib.sha1(f"{prefix}{src_lang}\n{text}".encode("utf-8")).hexdigest()


def _is_cjk(text: str) -> bool:
    """判断是否已是中文文本：CJK 汉字占字母类字符的比例够高即视为中文，无需再译。"""
    if not text:
        return False
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    letters = sum(1 for ch in text if ch.isalpha() or "一" <= ch <= "鿿")
    return letters > 0 and cjk / letters >= 0.30


def _cache_get(conn, keys: list[str]) -> dict[str, str]:
    if not keys:
        return {}
    marks = ",".join("?" * len(keys))
    rows = conn.execute(
        f"SELECT hash,translated FROM translation_cache WHERE hash IN ({marks})",
        keys).fetchall()
    return {r[0]: r[1] for r in rows}


def _cache_put(conn, rows: list[tuple]) -> None:
    if not rows:
        return
    conn.executemany(
        "INSERT OR IGNORE INTO translation_cache"
        "(hash,src_lang,original,translated,model,ts) VALUES(?,?,?,?,?,?)", rows)
    conn.commit()


def _translate_one(text: str, target: str = "en", timeout: int | None = None,
                   max_tokens: int = 200) -> str:
    """调一次翻译 API。失败抛异常，由调用方回退原文。target 决定目标语言与清洗方式。"""
    payload = {
        "model": config.TRANSLATE_MODEL,
        "messages": [{"role": "system", "content": SYSTEM if target == "en" else SYSTEM_ZH},
                     {"role": "user", "content": text}],
        "temperature": 0.0,
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(
        config.TRANSLATE_BASE_URL.rstrip("/") + "/chat/completions",
        json.dumps(payload).encode("utf-8"),
        {"Authorization": f"Bearer {config.TRANSLATE_API_KEY}",
         "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout or config.TRANSLATE_TIMEOUT) as r:
        resp = json.loads(r.read().decode("utf-8"))
    _add_usage(resp)
    out = (resp["choices"][0]["message"]["content"] or "").strip()
    # 标题走 clean_title（去引号/尾标点）；正文是多段文本，只做首尾清洗，保留段落。
    return clean_title(out) if target == "en" else out.strip()


def _apply_canonical(item: dict, canonical: str) -> None:
    """把英文 canonical 写进 item，并据它重算聚类信号（原文 title 不动）。"""
    if not canonical or canonical == item.get("title"):
        return
    item["canonical_title"] = canonical
    item["grams"] = gram_set(canonical)
    item["simhash"] = simhash(tokens(canonical))


def enrich(conn, items: list[dict], log=print) -> dict:
    """就地翻译 items 里的外文条目，返回本轮统计。

    未开启 / 无 key → 直接返回零统计，items 一字不改。
    """
    stat = {"eligible": 0, "cached": 0, "translated": 0, "failed": 0, "skipped": 0}
    if not (config.TRANSLATE_ENABLED and config.TRANSLATE_API_KEY):
        return stat

    todo = [it for it in items
            if it.get("lang") in config.TRANSLATE_SOURCE_LANGS
            and (it.get("title") or "").strip()]
    stat["eligible"] = len(todo)
    if not todo:
        return stat

    keys = {id(it): _hash(it["lang"], it["title"]) for it in todo}
    cached = _cache_get(conn, list(set(keys.values())))

    fresh, budget = [], config.TRANSLATE_MAX_PER_RUN
    for it in todo:
        hit = cached.get(keys[id(it)])
        if hit is not None:
            _apply_canonical(it, hit)
            stat["cached"] += 1
        elif budget > 0:
            fresh.append(it)
            budget -= 1
        else:
            stat["skipped"] += 1   # 超出单轮上限，留到下一轮（原文照常入库）

    if fresh:
        def one(it):
            try:
                return it, _translate_one(it["title"]), None
            except Exception as e:                       # noqa: BLE001 —— 翻译失败必须软着陆
                return it, None, f"{type(e).__name__}: {e}"[:160]

        put_rows, ts = [], _now_ts()
        with ThreadPoolExecutor(max_workers=config.TRANSLATE_CONCURRENCY) as ex:
            for it, translated, err in ex.map(one, fresh):
                if translated:
                    _apply_canonical(it, translated)
                    put_rows.append((keys[id(it)], it["lang"], it["title"],
                                     translated, config.TRANSLATE_MODEL, ts))
                    stat["translated"] += 1
                else:
                    stat["failed"] += 1
        _cache_put(conn, put_rows)

    # 回填已在库的外文条目：sources 每轮会重新列出最近若干条，其中已入库的
    # 会被 insert 的 OR IGNORE 跳过，canonical 落不进去。这里按 id 主动补写
    # canonical_title 及据它重算的 grams/simhash（聚类信号也要对齐英文）。
    # 只填 canonical 仍为空的行 —— 幂等，不覆盖已有；未入库的新条目命中 0 行，
    # 稍后由 insert_items 写入。这样存量外文稿也能随 sources 复现逐步进印证。
    done = [it for it in todo if it.get("canonical_title")]
    if done:
        conn.executemany(
            "UPDATE items SET canonical_title=?, grams=?, simhash=? "
            "WHERE id=? AND (canonical_title IS NULL OR canonical_title='')",
            [(it["canonical_title"], " ".join(sorted(it["grams"])),
              it["simhash"], it["id"]) for it in done])
        conn.commit()

    _flush_usage(conn, "canonical_en")   # 记账：入库英文 canonical 翻译的真实 token
    if stat["translated"] or stat["failed"] or stat["skipped"]:
        log(f"  [译] 外文 {stat['eligible']} 条：缓存 {stat['cached']} / "
            f"新译 {stat['translated']} / 失败 {stat['failed']} / 缓延 {stat['skipped']}")
    return stat


def _fill_zh(conn, table: str, src_col: str, dst_col: str, *, budget: int,
             max_tokens: int, stat: dict, stat_key: str, order_col: str,
             log=print) -> None:
    """把 table.src_col 的外文文本译成中文写进 dst_col（dst_col IS NULL 的行）。

    - 已是中文的行：直接把原文拷进 dst_col（不调 API），让 NULL 查询逐轮收敛、不再重扫。
    - 外文行：先查缓存，未命中且预算内才真译；超预算留到下一轮（dst_col 仍为 NULL）。
    - 按 order_col 倒序取：库里有上万条历史簇，必须先补当前可见窗口，
      而不是被老旧不可见的簇耗光单轮预算。簇按 rank（前端 feed 就按 rank 排），
      正文按 body_ts（正文只在点开详情时展示，取最近抽取的领头稿）。
    """
    rows = conn.execute(
        f"SELECT id, {src_col} AS txt FROM {table} "
        f"WHERE {dst_col} IS NULL AND {src_col} IS NOT NULL AND {src_col} != '' "
        f"ORDER BY {order_col} DESC LIMIT ?", (budget * 5,)).fetchall()
    if not rows:
        return

    cjk_updates, foreign = [], []
    for r in rows:
        txt = (r["txt"] or "").strip()
        if not txt:
            continue
        (cjk_updates if _is_cjk(txt) else foreign).append((r["id"], txt))
    # 已是中文：原样落 dst_col，标记「已处理」，避免每轮重复扫描。
    if cjk_updates:
        conn.executemany(f"UPDATE {table} SET {dst_col}=? WHERE id=?",
                         [(txt, iid) for iid, txt in cjk_updates])

    keys = {iid: _hash("", txt, "zh") for iid, txt in foreign}
    cached = _cache_get(conn, list(set(keys.values())))
    done, fresh = [], []
    for iid, txt in foreign:
        hit = cached.get(keys[iid])
        if hit is not None:
            done.append((hit, iid))
            stat["cached"] += 1
        elif budget > 0:
            fresh.append((iid, txt))
            budget -= 1
        # 超预算：留 NULL，下一轮再补

    put_rows, ts = [], _now_ts()
    if fresh:
        def one(job):
            iid, txt = job
            try:
                return iid, txt, _translate_one(txt, "zh", max_tokens=max_tokens), None
            except Exception as e:                       # noqa: BLE001 —— 软着陆
                return iid, txt, None, f"{type(e).__name__}: {e}"[:160]

        with ThreadPoolExecutor(max_workers=config.TRANSLATE_CONCURRENCY) as ex:
            for iid, txt, zh, err in ex.map(one, fresh):
                if zh:
                    done.append((zh, iid))
                    put_rows.append((keys[iid], "", txt, zh, config.TRANSLATE_MODEL, ts))
                    stat[stat_key] += 1
                else:
                    stat["failed"] += 1

    if done:
        conn.executemany(f"UPDATE {table} SET {dst_col}=? WHERE id=?", done)
    _cache_put(conn, put_rows)
    conn.commit()
    _flush_usage(conn, stat_key)   # 记账：本次 headline / body 翻译的真实 token


def enrich_display_zh(conn, log=print) -> dict:
    """展示翻译：把外文（含英文）簇标题与领头稿正文译成中文，写入 *_zh 列。

    未开启 / 无 key → no-op。原文列（headline/body）一字不动，前端可切回「原文」。
    clusters 每轮重建，换了领头稿 upsert 会把 headline_zh 置空，这里据 NULL 重译，
    故全量事件的中文标题会随刷新逐步补齐；正文只译已抽取到的领头稿。
    """
    stat = {"headline": 0, "body": 0, "cached": 0, "failed": 0}
    if not (config.TRANSLATE_DISPLAY_ZH and config.TRANSLATE_API_KEY):
        return stat
    _fill_zh(conn, "clusters", "headline", "headline_zh",
             budget=config.TRANSLATE_ZH_MAX_PER_RUN, max_tokens=300,
             stat=stat, stat_key="headline", order_col="rank", log=log)
    _fill_zh(conn, "items", "body", "body_zh",
             budget=config.TRANSLATE_ZH_BODY_MAX_PER_RUN, max_tokens=1024,
             stat=stat, stat_key="body", order_col="body_ts", log=log)
    if stat["headline"] or stat["body"] or stat["failed"]:
        log(f"  [译中] 标题 {stat['headline']} / 正文 {stat['body']} / "
            f"缓存命中 {stat['cached']} / 失败 {stat['failed']}")
    return stat


def _now_ts() -> int:
    from .normalize import now_ts
    return now_ts()
