"""标题清洗、中英混合分词、simhash 指纹、时间解析。全部纯标准库。

中文没有空格，也不引入 jieba（无 pip）。做法：CJK 取字符二元组，
拉丁取小写单词。字符二元组在近重复检测上表现足够好——中文新闻的转载
差异主要是前后缀（『(图)』『-新华网』），二元组集合的 Jaccard 很稳。
"""
import hashlib
import html
import re
import time
from datetime import datetime, timedelta, timezone

CJK = r"㐀-䶿一-鿿豈-﫿"
CJK_RE = re.compile(f"[{CJK}]+")
LATIN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-]{1,}")
NUM_RE = re.compile(r"\d+(?:\.\d+)?%?")
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")

# 转载会加的尾巴/前缀噪音
TRAIL_RE = re.compile(
    r"(?:[-—_|｜]\s*(?:新华网|人民网|中新网|央视网|新浪(?:新闻|财经)?|36氪|IT之家|"
    r"少数派|Solidot|财联社|每经网|中国新闻网)\s*$)"
)
BRACKET_NOISE_RE = re.compile(
    r"[（(【\[](?:图|组图|视频|多图|实录|全文|直播|独家|转载|来源[:：][^)）】\]]{0,20})[)）】\]]"
)

CJK_STOP = set("的了是在和与及对为以于不有个我你他她它们这那就都很更最也还又要会能将把被从到并且或而如但因所以了着过吗呢啊吧呀哦嗯")


def clean_text(s: str | None) -> str:
    if not s:
        return ""
    s = html.unescape(s)
    s = TAG_RE.sub(" ", s)
    s = s.replace("　", " ").replace("\xa0", " ")
    return WS_RE.sub(" ", s).strip()


def clean_title(s: str | None) -> str:
    t = clean_text(s)
    t = BRACKET_NOISE_RE.sub("", t)
    t = TRAIL_RE.sub("", t)
    return t.strip(" -—_|｜·、,，。")


def tokens(text: str) -> list[str]:
    """返回用于相似度/指纹的 token 序列（CJK 二元组 + 拉丁词 + 数字）。"""
    out: list[str] = []
    for run in CJK_RE.findall(text):
        run = "".join(ch for ch in run if ch not in CJK_STOP) or run
        if len(run) == 1:
            out.append(run)
        else:
            out.extend(run[i:i + 2] for i in range(len(run) - 1))
    out.extend(w.lower() for w in LATIN_RE.findall(text))
    out.extend(NUM_RE.findall(text))
    return out


def gram_set(text: str) -> set[str]:
    return set(tokens(text))


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / len(a | b)


def overlap(a: set[str], b: set[str]) -> float:
    """包含度：短标题被长标题覆盖的比例。防止『长稿 vs 短讯』被 Jaccard 拉低。"""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def simhash(toks: list[str], bits: int = 63) -> int:
    """63 位而非 64 位：SQLite INTEGER 是有符号 64 位，装不下无符号 64 位指纹。"""
    if not toks:
        return 0
    weights = [0] * bits
    for t in toks:
        h = int.from_bytes(hashlib.md5(t.encode("utf-8")).digest()[:8], "big")
        for i in range(bits):
            weights[i] += 1 if (h >> i) & 1 else -1
    out = 0
    for i in range(bits):
        if weights[i] > 0:
            out |= 1 << i
    return out


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


# ---------------- 时间解析 ----------------

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
CN_TZ = timezone(timedelta(hours=8))

_PATTERNS = [
    "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d",
    "%Y年%m月%d日 %H:%M", "%Y年%m月%d日",
]


def parse_time(raw, fallback: int | None = None) -> int | None:
    """尽力解析各种时间格式 → epoch 秒。解析不出来返回 fallback。"""
    if raw is None or raw == "":
        return fallback
    if isinstance(raw, (int, float)):
        v = float(raw)
        if v > 1e11:          # 毫秒
            v /= 1000.0
        if 9.4e8 < v < 4e9:   # 2000~2096 之间才认
            return int(v)
        return fallback
    s = str(raw).strip()
    if s.isdigit():
        return parse_time(int(s), fallback)

    # RFC 822: Wed, 04 Jun 2026 10:22:00 +0800
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3})[a-z]*\s+(\d{4})"
                  r"(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?\s*([+-]\d{4}|GMT|UTC)?", s)
    if m and m.group(2).lower() in _MONTHS:
        d, mon, y = int(m.group(1)), _MONTHS[m.group(2).lower()], int(m.group(3))
        hh, mm, ss = int(m.group(4) or 0), int(m.group(5) or 0), int(m.group(6) or 0)
        tzs = m.group(7)
        tz = CN_TZ
        if tzs and tzs.startswith(("+", "-")):
            sign = 1 if tzs[0] == "+" else -1
            tz = timezone(sign * timedelta(hours=int(tzs[1:3]), minutes=int(tzs[3:5])))
        elif tzs in ("GMT", "UTC"):
            tz = timezone.utc
        try:
            return int(datetime(y, mon, d, hh, mm, ss, tzinfo=tz).timestamp())
        except ValueError:
            return fallback

    s2 = s.replace("Z", "+0000")
    for p in _PATTERNS:
        try:
            dt = datetime.strptime(s2, p)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=CN_TZ)
            return int(dt.timestamp())
        except ValueError:
            continue
    try:  # 3.11+ 宽松 ISO
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CN_TZ)
        return int(dt.timestamp())
    except ValueError:
        pass
    # 只有 月-日 时:分（央视 focus_date 之类）
    m = re.search(r"(\d{1,2})[-/月](\d{1,2})日?(?:\s+(\d{1,2}):(\d{2}))?", s)
    if m:
        now = datetime.now(CN_TZ)
        try:
            dt = datetime(now.year, int(m.group(1)), int(m.group(2)),
                          int(m.group(3) or 0), int(m.group(4) or 0), tzinfo=CN_TZ)
            if dt - now > timedelta(days=2):   # 跨年
                dt = dt.replace(year=now.year - 1)
            return int(dt.timestamp())
        except ValueError:
            return fallback
    return fallback


def item_id(source_id: str, title: str) -> str:
    """按『信源 + 标题』去重。同一家把同一条挂在多个栏目/多个 URL 下很常见
    （央视 JSON 一条能出现三次），按 URL 去重会让同源重复稿虚增印证数。"""
    return hashlib.sha1(f"{source_id}|{title}".encode("utf-8")).hexdigest()[:20]


def now_ts() -> int:
    return int(time.time())


def fmt_ts(ts: int | None) -> str:
    if not ts:
        return "—"
    dt = datetime.fromtimestamp(ts, CN_TZ)
    now = datetime.now(CN_TZ)
    if dt.year != now.year:          # 跨年一定要显示年份，否则僵尸 feed 看不出来
        return dt.strftime("%Y-%m-%d")
    return dt.strftime("%m-%d %H:%M")
