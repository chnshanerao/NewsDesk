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
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import config
from .normalize import clean_title, gram_set, simhash, tokens

SYSTEM = ("You are a precise news-headline translator. Translate the given "
          "headline into natural English. Output only the translation — no "
          "quotes, no explanation, no source-language text, no trailing period "
          "unless the original has one.")


def _hash(src_lang: str, text: str) -> str:
    return hashlib.sha1(f"{src_lang}\n{text}".encode("utf-8")).hexdigest()


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


def _translate_one(text: str, timeout: int | None = None) -> str:
    """调一次翻译 API。失败抛异常，由调用方回退原文。"""
    payload = {
        "model": config.TRANSLATE_MODEL,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": text}],
        "temperature": 0.0,
        "max_tokens": 200,
    }
    req = urllib.request.Request(
        config.TRANSLATE_BASE_URL.rstrip("/") + "/chat/completions",
        json.dumps(payload).encode("utf-8"),
        {"Authorization": f"Bearer {config.TRANSLATE_API_KEY}",
         "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout or config.TRANSLATE_TIMEOUT) as r:
        resp = json.loads(r.read().decode("utf-8"))
    out = (resp["choices"][0]["message"]["content"] or "").strip()
    return clean_title(out)


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

    if stat["translated"] or stat["failed"] or stat["skipped"]:
        log(f"  [译] 外文 {stat['eligible']} 条：缓存 {stat['cached']} / "
            f"新译 {stat['translated']} / 失败 {stat['failed']} / 缓延 {stat['skipped']}")
    return stat


def _now_ts() -> int:
    from .normalize import now_ts
    return now_ts()
