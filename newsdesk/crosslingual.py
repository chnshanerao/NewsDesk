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
    r"(\d+(?:\.\d+)?)\s*(thousand|million|billion|trillion)\b", re.I)
_PLAIN_NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?![\w.])")

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


def _numbers(text: str) -> frozenset[str]:
    out, occupied = set(), []
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
        "thousand": 1_000, "million": 1_000_000,
        "billion": 1_000_000_000, "trillion": 1_000_000_000_000,
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
        out.add(f"num:{float(value):g}")
    return frozenset(out)


@dataclass(frozen=True)
class Features:
    entities: frozenset[str]
    events: frozenset[str]
    objects: frozenset[str]
    numbers: frozenset[str]
    currencies: frozenset[str]
    has_cjk: bool
    has_latin: bool

    @property
    def bridge_keys(self) -> frozenset[str]:
        return frozenset(f"xl:{entity}:{event}"
                         for entity in self.entities for event in self.events)


def features(title: str) -> Features:
    return Features(_labels(title, ENTITY_ALIASES), _labels(title, EVENT_ALIASES),
                    _labels(title, OBJECT_ALIASES), _numbers(title),
                    _labels(title, CURRENCY_ALIASES), bool(_CJK_RE.search(title)),
                    bool(_LATIN_RE.search(title)))


def bridge_score(a: Features, b: Features) -> float:
    """Return zero when evidence is insufficient or structured values conflict."""
    if not ((a.has_cjk and b.has_latin) or (b.has_cjk and a.has_latin)):
        return 0.0
    if not (a.entities & b.entities) or not (a.events & b.events):
        return 0.0
    if a.objects and b.objects and not (a.objects & b.objects):
        return 0.0
    if a.numbers and b.numbers and not (a.numbers & b.numbers):
        return 0.0
    if a.currencies and b.currencies and not (a.currencies & b.currencies):
        return 0.0
    if a.numbers & b.numbers or len(a.entities & b.entities) >= 2:
        return 0.72
    return 0.56
