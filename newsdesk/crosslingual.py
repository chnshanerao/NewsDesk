"""Auditable, high-precision cross-language headline features.

This is intentionally not a translator. A bridge requires a specific named entity and
an event type. Generic countries/regions are deliberately absent from the entity map.
"""
import re
from dataclasses import dataclass


_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")

ENTITY_ALIASES = {
    "federal_reserve": ("美联储", "美国联邦储备委员会", "federal reserve", "the fed"),
    "ecb": ("欧洲央行", "欧洲中央银行", "european central bank", "ecb"),
    "us_sec": ("美国证监会", "美国证券交易委员会", "u.s. sec", "us sec",
               "securities and exchange commission"),
    "united_nations": ("联合国", "united nations"),
    "people_bank_china": ("中国人民银行", "中国央行", "people's bank of china", "pboc"),
    "openai": ("openai", "开放人工智能公司"),
    "apple": ("苹果公司", "apple inc", "apple"),
    "microsoft": ("微软", "microsoft"),
    "google": ("谷歌", "google", "alphabet"),
    "nvidia": ("英伟达", "nvidia"),
    "tesla": ("特斯拉", "tesla"),
    "bank_of_japan": ("日本央行", "bank of japan", "boj"),
    "bank_of_england": ("英格兰银行", "英国央行", "bank of england", "boe"),
    "imf": ("国际货币基金组织", "international monetary fund", "imf"),
    "world_bank": ("世界银行", "world bank"),
    "world_health_organization": ("世界卫生组织", "world health organization", "who"),
    "opec": ("石油输出国组织", "欧佩克", "opec"),
    "amazon": ("亚马逊", "amazon"),
    "meta": ("脸书母公司", "meta platforms", "meta"),
    "samsung": ("三星电子", "samsung electronics", "samsung"),
    "tsmc": ("台积电", "taiwan semiconductor manufacturing", "tsmc"),
    "byd": ("比亚迪", "byd"),
}

EVENT_ALIASES = {
    "rate_cut": ("降息", "下调利率", "cut interest rates", "cuts interest rates",
                 "interest rate cut", "rate cut", "lowered interest rates"),
    "rate_hike": ("加息", "上调利率", "raise interest rates", "raises interest rates",
                  "interest rate hike", "rate hike", "raised interest rates"),
    "fine": ("罚款", "处罚", "处以罚款", "fined", "fine", "penalty"),
    "lawsuit": ("起诉", "诉讼", "sued", "sues", "lawsuit"),
    "investigation": ("调查", "立案", "investigation", "investigates", "probe"),
    "acquisition": ("收购", "并购", "acquire", "acquires", "acquisition", "buyout"),
    "layoff": ("裁员", "裁减岗位", "layoff", "layoffs", "cuts jobs", "job cuts"),
    "earnings": ("财报", "业绩", "营收", "净利润", "earnings", "revenue", "net profit"),
    "launch": ("发布新产品", "推出", "launch", "launches", "unveils", "released"),
    "ban": ("禁止", "禁令", "禁售", "封禁", "ban", "bans", "blocked"),
    "approval": ("批准", "获批", "核准", "approved", "approves", "approval"),
    "recall": ("召回", "recall", "recalls"),
    "resignation": ("辞职", "辞任", "resigns", "resignation", "steps down"),
    "sanction": ("制裁", "sanction", "sanctions"),
    "default": ("违约", "default", "defaults", "defaulted"),
    "ceasefire": ("停火", "ceasefire", "cease-fire"),
    "forecast": ("上调预期", "下调预期", "raises forecast", "cuts forecast",
                 "upgrades forecast", "downgrades forecast"),
}

OBJECT_ALIASES = {
    "iphone": ("iphone",), "mac": ("mac", "macbook"),
    "privacy": ("隐私", "privacy"), "antitrust": ("反垄断", "antitrust"),
    "gaming": ("游戏公司", "gaming company", "game studio"),
    "cybersecurity": ("网络安全公司", "cybersecurity company"),
}

_BPS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:个)?(?:基点|basis points?|bps)", re.I)
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|％|percent|percentage points?)", re.I)
_MAGNITUDE = re.compile(r"(\d+(?:\.\d+)?)\s*([\u4e07\u4ebf])")
_LATIN_MAGNITUDE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(thousand|million|billion|trillion|k|mn|bn|tn)\b", re.I)
_PLAIN_NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?![\w.])")

# 期次标记。作用是把「口径」从「取值」里分出来：两条陈述只有在同一期次上
# 给出互斥的取值才算矛盾。不同期次（不同月份、不同季度、不同发行批次、不同
# 星期）本来就是两件事，取值不同是常态而非冲突。
_PERIOD_PATTERNS = (
    # 财年/财季必须单列，且排在自然年之前。`(\d{4})\s*年` 匹配不到「2027 财年」
    # （中间隔着「财」），`第?([一二三四])\s*季度` 也匹配不到「第一财季」（是「季」不是
    # 「季度」）。结果两侧期次全空 = 判成同期：「甲骨文2027财年第一财季净利润同比
    # 增长60%」对「Adobe 2026财年第三财季同比增长3.1%」就这样成了「矛盾」。
    # 财年与自然年不可比（各公司财年起点不同），所以用独立种类 fy/fq。
    (re.compile(r"(\d{4})\s*财年"), "fy:{}"),
    (re.compile(r"第?\s*([一二三四]|[1-4])\s*财季"), "fq:{}"),
    (re.compile(r"(\d{4})\s*年"), "y:{}"),
    (re.compile(r"\bFY\s*(\d{2,4})\b", re.I), "fy:{}"),
    # 「1—6月份」必须排在单月之前：它是累计区间而不是 6 月，先匹配才能占住区间，
    # 否则单月规则会把它拆成 m:1 和 m:6。国家统计局的主力口径就是这个写法，
    # 漏掉它会把「2024年1—6月份投资增长3.9%」对「2024年全年增长3.2%」判成矛盾 ——
    # 那是半年累计对全年，两个数本就不该相等。
    (re.compile(r"(?<!\d)\d{1,2}\s*[—–~－-]\s*(\d{1,2})\s*月(?:份)?"), "cum:m{}"),
    (re.compile(r"(?<!\d)(\d{1,2})\s*月(?:份)?"), "m:{}"),
    (re.compile(r"(?<!\d)(\d{1,2})\s*日(?![元])"), "d:{}"),
    (re.compile(r"第?\s*([一二三四]|[1-4])\s*季度"), "q:{}"),
    (re.compile(r"\bQ([1-4])\b"), "q:{}"),
    (re.compile(r"\b[Hh]([12])\b"), "h:{}"),
    (re.compile(r"(上|下)半年"), "h:{}"),
    (re.compile(r"\b(first|second)\s+half\b", re.I), "h:{}"),
    # 统计口径的累计区间。「前三季度」与「全年」是不同口径，取值本就不同，
    # 不加这两条会把「2024年前三季度增长5.9%」对「2024年全年增长6.0%」判成矛盾。
    (re.compile(r"前(一|二|三|四|1|2|3|4)(?:个)?季度"), "cum:q{}"),
    (re.compile(r"前(\d{1,2})(?:个)?月"), "cum:m{}"),
    (re.compile(r"(全年|年度累计)"), "cum:y"),
    (re.compile(r"\b(full[- ]year|year[- ]to[- ]date|ytd)\b", re.I), "cum:y"),
    # 发行批次/期号：中文数字不做换算，原样比较即可判等或判不等
    (re.compile(r"第?\s*([一-鿿\d]{1,6})\s*期"), "seq:{}"),
    (re.compile(r"\b(?:tranche|series|issue|no\.)\s*([\w\-]{1,8})\b", re.I), "seq:{}"),
)
_MONTH_NAMES = ("january", "february", "march", "april", "may", "june", "july",
                "august", "september", "october", "november", "december")
_WEEKDAYS = {
    "monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4, "friday": 5,
    "saturday": 6, "sunday": 7,
    "周一": 1, "周二": 2, "周三": 3, "周四": 4, "周五": 5, "周六": 6, "周日": 7,
    "星期一": 1, "星期二": 2, "星期三": 3, "星期四": 4, "星期五": 5,
    "星期六": 6, "星期日": 7,
    # 葡语工作日：库内有 pt 语料，segunda=周一 … sexta=周五
    "segunda-feira": 1, "terça-feira": 2, "quarta-feira": 3,
    "quinta-feira": 4, "sexta-feira": 5, "sábado": 6, "domingo": 7,
}

CURRENCY_ALIASES = {
    "usd": ("美元", "US dollar", "USD", "$"),
    "cny": ("人民币", "CNY", "RMB"),
    "eur": ("欧元", "euro", "EUR", "€"),
    "gbp": ("英镑", "pound sterling", "GBP", "£"),
    "jpy": ("日元", "yen", "JPY", "¥"),
}


def _contains(text: str, alias: str) -> bool:
    if _LATIN_RE.search(alias):
        return re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])",
                         text, re.I) is not None
    return alias in text


def _labels(text: str, aliases: dict[str, tuple[str, ...]]) -> frozenset[str]:
    folded = text.casefold()
    return frozenset(key for key, values in aliases.items()
                     if any(_contains(folded, value.casefold()) for value in values))


def _period_spans(text: str) -> tuple[frozenset[str], list[tuple[int, int]]]:
    """返回期次标记及其占位区间。区间给 _numbers 用，避免月/日/期号被当成取值。"""
    out, spans = set(), []
    for pattern, template in _PERIOD_PATTERNS:
        for match in pattern.finditer(text):
            out.add(template.format(match.group(1).strip()))
            spans.append(match.span())
    folded = text.casefold()
    for index, name in enumerate(_MONTH_NAMES, start=1):
        position = folded.find(name)
        if position >= 0:
            out.add(f"m:{index}")
            spans.append((position, position + len(name)))
    for name, index in _WEEKDAYS.items():
        position = folded.find(name)
        if position >= 0:
            out.add(f"wd:{index}")
            spans.append((position, position + len(name)))
    # 「September 9, 2026」里的 9 是日期而非取值，跟在月名后的裸数字一并占位
    for match in re.finditer(r"(?<!\d)(\d{1,2})(?:st|nd|rd|th)?\s*,?\s*(?=\d{4}\b)", text):
        out.add(f"d:{match.group(1)}")
        spans.append(match.span())
    return frozenset(out), spans


def periods(text: str) -> frozenset[str]:
    return _period_spans(text)[0]


def _numbers(text: str) -> frozenset[str]:
    out, occupied = set(), list(_period_spans(text)[1])
    for match in _BPS.finditer(text):
        out.add(f"pct:{float(match.group(1)) / 100:g}")
        occupied.append(match.span())
    for match in _PERCENT.finditer(text):
        out.add(f"pct:{float(match.group(1)):g}")
        occupied.append(match.span())
    for match in _MAGNITUDE.finditer(text):
        multiplier = 10_000 if match.group(2) == "\u4e07" else 100_000_000
        out.add(f"num:{float(match.group(1)) * multiplier:g}")
        occupied.append(match.span())
    latin_multipliers = {
        "thousand": 1_000, "k": 1_000, "million": 1_000_000, "mn": 1_000_000,
        "billion": 1_000_000_000, "bn": 1_000_000_000,
        "trillion": 1_000_000_000_000, "tn": 1_000_000_000_000,
    }
    for match in _LATIN_MAGNITUDE.finditer(text):
        multiplier = latin_multipliers[match.group(2).lower()]
        out.add(f"num:{float(match.group(1)) * multiplier:g}")
        occupied.append(match.span())
    for match in _PLAIN_NUMBER.finditer(text):
        if any(a <= match.start() < b for a, b in occupied):
            continue
        value = match.group(1).replace(",", "")
        if len(value) == 4 and 1900 <= int(float(value)) <= 2100:
            continue
        # 不带单位也不带量级词的裸数字另立一类。它多半不是「取值」而是标识符或计数：
        # 「TensorFlow 2.19」「iOS 27 RC」「GPT-5.6」「HMD Asha 305」里的数字是名字的
        # 一部分，「9种产品价格上涨」里的是计数。把它和 pct/num 混在一起，就会拿
        # 版本号互不相等去论证两篇稿子互相矛盾 —— 实测这是数字类误报的主要来源。
        out.add(f"count:{float(value):g}")
    return frozenset(out)


@dataclass(frozen=True)
class Features:
    entities: frozenset[str]
    events: frozenset[str]
    objects: frozenset[str]
    numbers: frozenset[str]
    periods: frozenset[str]
    currencies: frozenset[str]
    has_cjk: bool
    has_latin: bool

    @property
    def bridge_keys(self) -> frozenset[str]:
        return frozenset(f"xl:{entity}:{event}"
                         for entity in self.entities for event in self.events)


def features(title: str) -> Features:
    return Features(_labels(title, ENTITY_ALIASES), _labels(title, EVENT_ALIASES),
                    _labels(title, OBJECT_ALIASES), _numbers(title), periods(title),
                    _labels(title, CURRENCY_ALIASES), bool(_CJK_RE.search(title)),
                    bool(_LATIN_RE.search(title)))


_ORDINALS = {"一": 1, "二": 2, "三": 3, "四": 4, "上": 1, "下": 2,
             "first": 1, "second": 2}
# 季度/半年折算成月份区间。分属 q/h 两个种类时，逐种类比较会正好放过
# 「2026年一季度营收增长6.4%」对「2026年上半年营收增长4.6%」—— 一个是 1-3 月、
# 一个是 1-6 月，两个窗口两件事。折算成区间才能直接比，顺带让「一季度」与「Q1」判等。
_WINDOWS = {"q1": (1, 3), "q2": (4, 6), "q3": (7, 9), "q4": (10, 12),
            "h1": (1, 6), "h2": (7, 12)}


def _canonical(mark: str) -> tuple[str, str]:
    kind, _, value = mark.partition(":")
    if kind in ("q", "h"):
        folded = value.strip().casefold()
        window = _WINDOWS.get(f"{kind}{_ORDINALS.get(folded, folded)}")
        if window:
            return "win", f"{window[0]}-{window[1]}"
    return kind, value


def _kinds(marks: frozenset[str]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for mark in marks:
        kind, value = _canonical(mark)
        out.setdefault(kind, set()).add(value)
    return out


def different_periods(a: frozenset[str], b: frozenset[str]) -> bool:
    """两组期次标记是否指向不同期次。

    逐「种类」比较，而不是整体求交。`{y:2026,m:9}` 与 `{y:2026,m:10}` 整体求交非空
    （共有 y:2026），但月份不同就是两件事；反之 `{y:2026,m:9,d:10}` 与 `{m:9,d:10}`
    只是一方省略了年份，不该判为不同期次。只有双方都标注了同一种类且取值不相交，
    才算不同期次。

    `cum`（累计口径）是唯一的例外：一方声明了累计窗口、另一方没有，就是两个口径。
    「2024年前三季度营收增长5.9%」与「2024年全国营收增长6.0%」——后者不带累计标记，
    但它是年度数，跟前三季度累计数不是同一个量。省略年份可以推断，省略累计口径
    不能推断，所以这一种类按「有无」判而不按「取值」判。
    """
    ka, kb = _kinds(a), _kinds(b)
    if ("cum" in ka) != ("cum" in kb):
        return True
    return any(not (ka[kind] & kb[kind]) for kind in ka.keys() & kb.keys())


def bridge_score(a: Features, b: Features) -> float:
    """Return zero when evidence is insufficient or structured values conflict."""
    if not ((a.has_cjk and b.has_latin) or (b.has_cjk and a.has_latin)):
        return 0.0
    if not (a.entities & b.entities) or not (a.events & b.events):
        return 0.0
    if a.objects and b.objects and not (a.objects & b.objects):
        return 0.0
    if different_periods(a.periods, b.periods):
        return 0.0
    if a.numbers and b.numbers and not (a.numbers & b.numbers):
        return 0.0
    if a.currencies and b.currencies and not (a.currencies & b.currencies):
        return 0.0
    if a.numbers & b.numbers or len(a.entities & b.entities) >= 2:
        return 0.72
    return 0.56
