"""HTTP 抓取 + 信源解析（RSS / Atom / JSON / JSONP）。

抓取失败不抛出，返回结构化结果，交给上层写入 source_health——
信源挂掉是常态，终端要能看见『哪家不通』而不是整条流水线崩。
"""
import gzip
import json
import re
import time
import urllib.error
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor

from . import config
from .normalize import (clean_text, clean_title, gram_set, item_id, now_ts,
                        parse_time, simhash, tokens)

ENTRY_RE = re.compile(r"<(item|entry)\b[^>]*>(.*?)</\1>", re.S | re.I)


def _tag(block: str, *names: str) -> str:
    for n in names:
        m = re.search(rf"<{n}\b[^>]*>(.*?)</{n}>", block, re.S | re.I)
        if m:
            return m.group(1).strip()
    return ""


def _link(block: str) -> str:
    m = re.search(r"<link\b[^>]*>(.*?)</link>", block, re.S | re.I)
    if m and m.group(1).strip():
        return clean_text(m.group(1))
    m = re.search(r'<link\b[^>]*href=["\']([^"\']+)["\']', block, re.I)
    if m:
        return m.group(1).strip()
    return clean_text(_tag(block, "guid", "id"))


def _cdata(s: str) -> str:
    m = re.match(r"\s*<!\[CDATA\[(.*?)\]\]>\s*$", s, re.S)
    return m.group(1) if m else s


def http_get(url: str, timeout: int | None = None, headers: dict | None = None) -> bytes:
    hdrs = {
        "User-Agent": config.USER_AGENT,
        "Accept": "application/rss+xml, application/xml, application/json, text/html;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "close",
    }
    if headers:  # 按源覆盖（如 SEC EDGAR 要求合规 User-Agent）
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout or config.HTTP_TIMEOUT) as r:
        raw = r.read()
        enc = (r.headers.get("Content-Encoding") or "").lower()
    if enc == "gzip":
        raw = gzip.decompress(raw)
    elif enc == "deflate":
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    return raw


def decode(raw: bytes) -> str:
    m = re.search(rb'encoding=["\']([\w\-]+)["\']', raw[:300], re.I)
    if m:
        try:
            return raw.decode(m.group(1).decode("ascii", "ignore"), "replace")
        except LookupError:
            pass
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


# ---------------- 解析器 ----------------

def parse_rss(text: str, src: dict) -> list[dict]:
    out = []
    for m in ENTRY_RE.finditer(text):
        block = m.group(2)
        title = clean_title(_cdata(_tag(block, "title")))
        if not title:
            continue
        summary = clean_text(_cdata(_tag(block, "description", "summary", "content:encoded", "content")))
        pub = _cdata(_tag(block, "pubDate", "published", "updated", "dc:date", "date"))
        out.append({
            "title": title,
            "summary": summary[:600],
            "url": _cdata(_link(block)),
            "published_ts": parse_time(pub),
            "native_id": clean_text(_cdata(_tag(block, "guid"))),
        })
    return out


def _dig(obj, path: str):
    cur = obj
    for part in path.split("."):
        if part == "":
            continue
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
                continue
            except (ValueError, IndexError):
                return None
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
        if cur is None:
            return None
    return cur


def parse_json(text: str, src: dict) -> list[dict]:
    cfg = src.get("json", {})
    if cfg.get("strip_jsonp") or (text.lstrip()[:1] not in "{["):
        m = re.search(r"[\{\[].*[\}\]]", text, re.S)
        if not m:
            return []
        text = m.group(0)
    data = json.loads(text)
    rows = _dig(data, cfg.get("items_path", "")) if cfg.get("items_path") else data
    if not isinstance(rows, list):
        return []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = clean_title(str(_dig(row, cfg.get("title", "title")) or ""))
        if not title:
            continue
        url = str(_dig(row, cfg.get("url", "url")) or "") if cfg.get("url") else ""
        nid = str(_dig(row, cfg["id"]) or "") if cfg.get("id") else ""
        if not url and cfg.get("url_template"):
            url = re.sub(r"\{(\w+)\}",
                         lambda mm: str(_dig(row, mm.group(1)) or ""),
                         cfg["url_template"])
        summary = str(_dig(row, cfg["summary"]) or "") if cfg.get("summary") else ""
        pub = _dig(row, cfg.get("published", "")) if cfg.get("published") else None
        out.append({
            "title": title,
            "summary": clean_text(summary)[:600],
            "url": url,
            "published_ts": parse_time(pub),
            "native_id": nid,
        })
    return out


def parse_html(text: str, src: dict) -> list[dict]:
    """正则抽取列表页。中文互联网大量权威站点没有 RSS，只能这么接。

    配置示例见 sources.json 里的 pbc_news：item_re 必须含 url / title 命名组。
    """
    cfg = src.get("html", {})
    item_re = re.compile(cfg["item_re"], re.S)
    base = cfg.get("base", "")
    date_url_re = re.compile(cfg["date_from_url_re"]) if cfg.get("date_from_url_re") else None
    limit = int(cfg.get("limit", 40))
    out, seen = [], set()
    for m in item_re.finditer(text):
        g = m.groupdict()
        title = clean_title(g.get("title", ""))
        url = (g.get("url") or "").strip()
        if not title or url in seen:
            continue
        seen.add(url)
        if url.startswith("/"):
            url = base.rstrip("/") + url
        pub = g.get("date")
        if not pub and date_url_re:
            dm = date_url_re.search(url)
            if dm:
                d = dm.group(1)
                pub = f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
        out.append({
            "title": title, "summary": "", "url": url,
            "published_ts": parse_time(pub), "native_id": "",
        })
        if len(out) >= limit:
            break
    return out


_EDGAR_ACCNO = re.compile(r"AccNo:\s*([\d-]+)", re.I)
_EDGAR_FILED = re.compile(r"Filed:\s*([\d-]+)", re.I)


def _edgar_enrich(src: dict, p: dict) -> dict:
    """EDGAR atom 所有条目标题都是 "8-K - Current report"，会被聚类跨公司合并成巨簇。
    用『公司 表单 · 申报日 · 登记号』重写标题：既唯一（聚类分得开），又可读（新闻流/分诊有意义）。
    """
    summary = p.get("summary", "")
    accno = (_EDGAR_ACCNO.search(summary) or [None, ""])[1] if _EDGAR_ACCNO.search(summary) else ""
    filed = (_EDGAR_FILED.search(summary) or [None, ""])[1] if _EDGAR_FILED.search(summary) else ""
    org = src.get("filer_org", "")
    form = src.get("edgar_form", "")
    label = " · ".join(x for x in (f"{org} {form}".strip(), filed, accno) if x)
    if label:
        p["title"] = label
    if accno:
        p["native_id"] = accno
    return p


def parse(text: str, src: dict) -> list[dict]:
    kind = src.get("kind", "rss")
    if kind in ("json", "jsonp"):
        return parse_json(text, src)
    if kind == "html":
        return parse_html(text, src)
    return parse_rss(text, src)


# ---------------- 抓取编排 ----------------

def _safe_published(pub: int | None, fetched: int, tolerance=300):
    """Prevent scheduled/bad-timezone feed dates from creating future news."""
    if pub is not None and pub > fetched + tolerance:
        return fetched, True
    return pub, False

def fetch_source(src: dict, reg: dict) -> dict:
    t0 = time.time()
    res = {
        "source_id": src["id"], "name": src["name"], "tier": src["tier"],
        "enabled": True, "last_try_ts": now_ts(), "ok": False,
        "last_error": None, "items": [], "n_items": 0,
    }
    try:
        raw = http_get(src["url"], timeout=src.get("http_timeout"),
                       headers=src.get("http_headers"))
        parsed = parse(decode(raw), src)
        if src.get("edgar_form"):  # 把 EDGAR 泛化标题富化成唯一可读标题（防跨公司合并）
            parsed = [_edgar_enrich(src, p) for p in parsed]
        max_items = int(src.get("max_items", 0) or 0)
        if max_items > 0:
            parsed = parsed[:max_items]
        fetched = now_ts()
        seen = set()
        items = []
        undated = 0
        future_clamped = 0
        # 默认按标题去重（同源重复稿不虚增印证）。但 EDGAR 这类源所有条目标题相同
        # （"8-K - Current report"），需按 item_key 指定的字段（url）区分每份申报。
        key_field = src.get("item_key")
        for p in parsed:
            identity = (p.get(key_field) or p["title"]) if key_field else p["title"]
            iid = item_id(src["id"], identity)
            if iid in seen:
                continue
            seen.add(iid)
            toks = tokens(p["title"])
            pub = p.get("published_ts")
            if pub is None:
                undated += 1
                # 关键决定：feed 不给日期，就不许它冒充新鲜。
                # 除非信源显式声明 assume_fresh（如只输出当日榜单的接口）。
                pub = fetched if src.get("assume_fresh") else None
            pub, was_future = _safe_published(pub, fetched)
            future_clamped += int(was_future)
            items.append({
                "id": iid,
                "source_id": src["id"],
                "source_name": src["name"],
                "tier": src["tier"],
                "grp": src.get("group", src["id"]),
                "title": p["title"],
                "summary": p.get("summary", ""),
                "url": p.get("url", ""),
                "lang": src.get("lang", "zh"),
                "published_ts": pub,
                "fetched_ts": fetched,
                "simhash": simhash(toks),
                "grams": gram_set(p["title"]),
                "src_topics": src.get("topics", []),
            })
        dated = [it["published_ts"] for it in items if it["published_ts"]]
        res.update(ok=True, items=items, n_items=len(items), last_ok_ts=fetched,
                   n_undated=undated,
                   n_future_clamped=future_clamped,
                   newest_ts=max(dated) if dated else None,
                   oldest_ts=min(dated) if dated else None)
    except urllib.error.HTTPError as e:
        res["last_error"] = f"HTTP {e.code}"
    except urllib.error.URLError as e:
        res["last_error"] = f"NET {getattr(e, 'reason', e)}"[:120]
    except Exception as e:  # 解析异常也要归档，不能炸整条流水线
        res["last_error"] = f"{type(e).__name__}: {e}"[:120]
    res["ms"] = int((time.time() - t0) * 1000)
    return res


def fetch_all(reg: dict, only: list[str] | None = None) -> list[dict]:
    srcs = [s for s in reg["sources"] if s.get("enabled", True)]
    if only:
        srcs = [s for s in reg["sources"] if s["id"] in only]
    with ThreadPoolExecutor(max_workers=config.FETCH_WORKERS) as ex:
        return list(ex.map(lambda s: fetch_source(s, reg), srcs))
