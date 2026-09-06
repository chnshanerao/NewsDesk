"""Small, auditable delayed-market snapshot using public no-key endpoints.

This is context for news, not an execution-grade price feed. Values are explicitly
marked delayed and must never be used for trading orders.
"""
import json
import csv
import io
import re
import threading
import time
import urllib.request

_lock = threading.Lock()
_cache = {"at": 0, "value": None}
TTL = 120

ASSET_TERMS = {
    "sh000001": ("上证", "沪市", "A股", "Shanghai Composite"),
    "sz399001": ("深证", "深市", "Shenzhen Component"),
    "sh000300": ("沪深300", "CSI 300"),
    "hkHSI": ("恒生", "港股", "Hang Seng", "Hong Kong stocks"),
    "usDJI": ("道琼斯", "道指", "Dow Jones", "DJIA"),
    "usIXIC": ("纳斯达克", "纳指", "Nasdaq", "Nasdaq Composite"),
    "usINX": ("标普500", "标普 500", "S&P 500", "S&P500"),
    "USD/CNY": ("人民币", "美元兑人民币", "汇率", "yuan", "renminbi", "USD/CNY"),
    "USD/EUR": ("欧元", "euro", "USD/EUR"),
    "USD/JPY": ("日元", "yen", "USD/JPY"),
    "UST2Yr": ("美债", "美国国债", "Treasury yield", "2-year Treasury"),
    "UST10Yr": ("美债", "美国国债", "Treasury yield", "10-year Treasury"),
    "UST30Yr": ("美债", "美国国债", "Treasury yield", "30-year Treasury"),
    "XAUUSD": ("黄金", "金价", "gold"),
    "XAGUSD": ("白银", "银价", "silver"),
    "WTI": ("WTI", "原油", "油价", "crude oil", "West Texas Intermediate"),
}


def related_symbols(text: str) -> list[dict]:
    folded = (text or "").casefold()
    out = []
    for symbol, terms in ASSET_TERMS.items():
        hits = [term for term in terms if term.casefold() in folded]
        if hits:
            out.append({"symbol": symbol, "matched_terms": hits[:3]})
    return out


def _get(url: str, encoding="utf-8") -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "newsdesk/1.0"})
    with urllib.request.urlopen(req, timeout=12) as response:
        return response.read(256_000).decode(encoding, "replace")


def _equities() -> list[dict]:
    raw = _get("https://qt.gtimg.cn/q=s_sh000001,s_sz399001,s_sh000300,s_hkHSI,"
               "s_usDJI,s_usIXIC,s_usINX",
               "gb18030")
    labels = {"sh000001": "上证指数", "sz399001": "深证成指",
              "sh000300": "沪深300", "hkHSI": "恒生指数",
              "usDJI": "道琼斯", "usIXIC": "纳斯达克", "usINX": "标普500"}
    out = []
    for code, payload in re.findall(r'v_s_([^=]+)="([^"]*)"', raw):
        fields = payload.split("~")
        if len(fields) < 6:
            continue
        out.append({"symbol": code, "name": labels.get(code, fields[1]),
                    "asset": "equity_index", "price": float(fields[3]),
                    "change": float(fields[4]), "change_pct": float(fields[5]),
                    "currency": ("HKD" if code.startswith("hk") else
                                 "USD" if code.startswith("us") else "CNY"),
                    "source": "Tencent delayed quote"})
    return out


def _fx() -> list[dict]:
    data = json.loads(_get("https://api.frankfurter.app/latest?from=USD&to=CNY,EUR,JPY"))
    return [{"symbol": f"USD/{ccy}", "name": f"美元/{ccy}", "asset": "fx",
             "price": value, "change": None, "change_pct": None, "currency": ccy,
             "source": "Frankfurter / ECB reference rates"}
            for ccy, value in data.get("rates", {}).items()]


def _rates() -> list[dict]:
    year = time.gmtime().tm_year
    url = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
           f"daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve&"
           f"field_tdr_date_value={year}&page&_format=csv")
    rows = list(csv.DictReader(io.StringIO(_get(url))))
    if not rows:
        return []
    latest, previous = rows[0], rows[1] if len(rows) > 1 else {}
    out = []
    for term in ("2 Yr", "10 Yr", "30 Yr"):
        if not latest.get(term):
            continue
        value = float(latest[term])
        old = float(previous[term]) if previous.get(term) else value
        out.append({"symbol": "UST" + term.replace(" ", ""),
                    "name": "美国国债" + term.replace(" Yr", "年"), "asset": "rates",
                    "price": value, "change": round(value - old, 4),
                    "change_bps": round((value - old) * 100, 1), "change_pct": None,
                    "currency": "%", "source": "U.S. Treasury daily curve"})
    return out


def _metals() -> list[dict]:
    out = []
    for symbol, cn in (("XAU", "黄金"), ("XAG", "白银")):
        data = json.loads(_get(f"https://api.gold-api.com/price/{symbol}"))
        out.append({"symbol": symbol + "USD", "name": cn, "asset": "commodity",
                    "price": float(data["price"]), "change": None, "change_pct": None,
                    "currency": "USD", "source": "Gold API spot reference"})
    return out


def _energy() -> list[dict]:
    raw = _get("https://qt.gtimg.cn/q=hf_CL", "gb18030")
    match = re.search(r'v_hf_CL="([^"]*)"', raw)
    if not match:
        return []
    fields = match.group(1).split(",")
    return [{"symbol": "WTI", "name": "WTI原油", "asset": "commodity",
             "price": float(fields[0]), "change": None,
             "change_pct": float(fields[1]), "currency": "USD",
             "source": "Tencent delayed quote"}]


def snapshot(force=False) -> dict:
    now = int(time.time())
    with _lock:
        if not force and _cache["value"] and now - _cache["at"] < TTL:
            return _cache["value"]
        instruments, errors = [], []
        for name, loader in (("equities", _equities), ("fx", _fx),
                             ("rates", _rates), ("metals", _metals),
                             ("energy", _energy)):
            try:
                instruments.extend(loader())
            except Exception as exc:
                errors.append(f"{name}: {type(exc).__name__}: {str(exc)[:100]}")
        value = {"asof": now, "delayed": True, "execution_grade": False,
                 "instruments": instruments, "errors": errors,
                 "disclaimer": "延迟公开数据，仅供新闻背景参考，不可用于交易执行。"}
        _cache.update(at=now, value=value)
        return value
