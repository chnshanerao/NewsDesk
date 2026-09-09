"""SEC EDGAR 申报正文解析：把结构化附表变成可直接核验的事实。

只解析 SEC 自己规定的结构化字段（13F 持仓附表 XML），不做自然语言推断 ——
这样自动生成的『对象』和『金额』每一位都能在一次源里逐条对上，
不引入模型猜测，发布门禁的证据链依然成立。

8-K 正文是自由文本，不在此自动解析：那种材料只能由人读完再补全。
"""
from __future__ import annotations

import re
import time
import urllib.error

from . import config
from .fetch import decode, http_get

# SEC 公平访问策略：单 IP 每秒最多 10 次请求。这里保守到每次调用间隔 0.4s，
# 顺序抓取（不并发）—— 被限频要等好几分钟，慢一点比被封值得。
REQUEST_INTERVAL = float(__import__("os").getenv("NEWSDESK_SEC_INTERVAL", "0.4"))
_last_request = [0.0]


def throttle() -> None:
    """所有走 sec.gov 的请求（含 fetch_source 抓 atom）都应先过这里，共用一个节流闸。"""
    wait = REQUEST_INTERVAL - (time.time() - _last_request[0])
    if wait > 0:
        time.sleep(wait)
    _last_request[0] = time.time()


_PAGE_PARAMS = re.compile(r"&(?:start|count)=\d+")


def paged_url(url: str, start: int, count: int = 100) -> str:
    """browse-edgar atom 的历史分页。start 是偏移量，count 上限 100。

    这是拿到多年申报的唯一途径：默认接口只回最近 N 份，看不到长期布局。
    """
    return _PAGE_PARAMS.sub("", url) + f"&start={start}&count={count}"


def _get(url: str) -> str:
    throttle()
    return decode(http_get(url, timeout=config.BODY_TIMEOUT,
                           headers={"User-Agent": config.SEC_UA}))


_HREF = re.compile(r'href="([^"]+\.(?:xml|htm))"', re.I)
_TAG = re.compile(r"<(?:\w+:)?{tag}>(.*?)</(?:\w+:)?{tag}>", re.S | re.I)
_INFO_TABLE = re.compile(r"<(?:\w+:)?infoTable>(.*?)</(?:\w+:)?infoTable>", re.S | re.I)


def _field(block: str, tag: str) -> str:
    m = re.search(_TAG.pattern.format(tag=tag), block, re.S | re.I)
    return (m.group(1) or "").strip() if m else ""


# 索引页上的报告截止日（"Period of Report  2026-06-30"）。13F 申报日和报告截止日不同——
# 仓位快照对应的是截止日那天的持仓，季度相减必须按截止日对齐，用申报日会错季。
_PERIOD = re.compile(r"Period of Report.*?(\d{4}-\d{2}-\d{2})", re.S | re.I)


def _table_url_from_page(page: str, index_url: str) -> str | None:
    base = index_url.rsplit("/", 1)[0]
    candidates = []
    for href in _HREF.findall(page):
        name = href.rsplit("/", 1)[-1].lower()
        if not name.endswith(".xml") or name == "primary_doc.xml":
            continue
        # 索引页链接指向 XSL 渲染版（.../xslForm13F_X02/56757.xml），返回的是 HTML 表格。
        # 去掉那一段路径才是原始 XML —— 我们要解析字段，不要它的渲染结果。
        href = re.sub(r"/xsl[^/]*/", "/", href)
        candidates.append(href if href.startswith("http") else
                          ("https://www.sec.gov" + href if href.startswith("/")
                           else f"{base}/{href}"))
    return candidates[0] if candidates else None


def information_table_url(index_url: str) -> str | None:
    """从申报索引页找到 13F 持仓附表 XML。

    索引页里通常有两个 XML：primary_doc.xml（封面，含总额但不含逐条持仓）
    和一个数字命名的附表（如 56757.xml）。取后者。
    """
    if not index_url or "sec.gov" not in index_url:
        return None
    return _table_url_from_page(_get(index_url), index_url)


def _unit_scale(prices: list[float]) -> int:
    """判定 <value> 的计价单位：整美元还是千美元。

    SEC 2022 年修订后要求按整美元填报，但仍有大量申报人沿用旧的『千美元』惯例
    （实测：伯克希尔按美元，Duquesne / Baupost 按千美元）。附表里没有任何字段声明单位，
    只能反推：value/股数 = 每股单价。按美元填报时它就是股价（几十到几百美元），
    按千美元填报时是股价/1000（0.0x–0.x）。取中位数，避免个别债券/期权行带偏。
    没有股数可用时不缩放 —— 宁可少乘 1000，也不能凭空放大三个数量级。
    """
    if not prices:
        return 1
    prices = sorted(prices)
    median = prices[len(prices) // 2]
    return 1000 if 0 < median < 1 else 1


def parse_information_table(xml: str) -> dict:
    """解析 13F 持仓附表。返回总市值、头寸数与按 CUSIP 合并后的持仓列表。

    同一发行人可能拆成多行（不同管理人/投票权），必须合并，否则『前五大持仓』
    会把同一家公司重复列出来。合并键用 CUSIP —— 它是 SEC 规定的稳定证券标识，
    跨季不变；发行人名会有 "APPLE INC" / "APPLE INC COM" 之类写法漂移，
    直接拿名字当键会导致相邻季 join 不上、误判成清仓+新建仓。
    没有 CUSIP 的行（罕见）退回发行人名做键。
    """
    holdings: dict[str, dict] = {}
    rows = 0
    prices = []
    for block in _INFO_TABLE.findall(xml):
        issuer = _field(block, "nameOfIssuer")
        raw_value = _field(block, "value").replace(",", "")
        if not issuer or not raw_value:
            continue
        try:
            value = float(raw_value)
        except ValueError:
            continue
        try:
            shares = float(_field(block, "sshPrnamt").replace(",", "") or 0)
        except ValueError:
            shares = 0.0
        cusip = _field(block, "cusip").strip().upper()
        rows += 1
        # 债券按面值填报（PRN），单价恒等于 1，会把单位判定拖到阈值边上 —— 只取股票行
        if shares > 0 and value > 0 and _field(block, "sshPrnamtType").upper() in ("SH", ""):
            prices.append(value / shares)
        key = cusip or issuer
        agg = holdings.setdefault(key, {"cusip": cusip, "issuer": issuer,
                                        "value": 0.0, "shares": 0.0})
        agg["value"] += value
        agg["shares"] += shares
    scale = _unit_scale(prices)
    for agg in holdings.values():
        agg["value"] *= scale
    ranked = sorted(holdings.values(), key=lambda x: x["value"], reverse=True)
    return {"total_value": sum(h["value"] for h in ranked), "n_rows": rows,
            "n_issuers": len(ranked), "unit_scale": scale, "holdings": ranked}


# 单位写法必须能被 movements._amount() 解析成 USD 数值，否则金额字段会退化成裸数字。
# 该正则认 million/billion/m/bn，不认单字母 B —— 所以这里刻意不写 "$106.9B"。
def _usd(value: float) -> str:
    """紧凑写法，用在持仓清单里。"""
    if value >= 1e9:
        return f"${value / 1e9:.1f}bn"
    if value >= 1e6:
        return f"${value / 1e6:.0f}m"
    return f"${value:,.0f}"


def _usd_long(value: float) -> str:
    """完整写法，用在金额口径字段（会被解析入库为 amount_usd）。"""
    if value >= 1e9:
        return f"${value / 1e9:.2f} billion"
    if value >= 1e6:
        return f"${value / 1e6:.1f} million"
    return f"${value:,.0f}"


def summarize_13f(table: dict, top: int = 5) -> dict | None:
    """把持仓附表压成一句可核验的『对象』描述 + 组合总市值。

    每个数字都直接来自附表字段，可在 SEC 原文逐条核对。
    """
    if not table["holdings"] or table["total_value"] <= 0:
        return None
    head = table["holdings"][:top]
    parts = "、".join(f"{h['issuer']} {_usd(h['value'])}" for h in head)
    share = sum(h["value"] for h in head) / table["total_value"] * 100
    object_text = (f"{table['n_issuers']} 个头寸；前 {len(head)} 大持仓："
                   f"{parts}（占组合 {share:.0f}%）")
    return {"object_text": object_text,
            "amount_value_text": _usd_long(table["total_value"]),
            "amount_usd": table["total_value"],
            "n_issuers": table["n_issuers"],
            "top": head}


def holdings_from_filing(index_url: str) -> dict | None:
    """索引页 URL → 持仓摘要 + 报告截止日 + 逐条持仓。

    索引页只抓一次，同时拿附表链接和报告截止日（Period of Report）。
    返回的 summary 额外带：
      - ``period``：报告截止日（YYYY-MM-DD），季度相减对齐用；解析不到则为 ""。
      - ``holdings``：全部逐条持仓（含 cusip），供 edgar_holdings 落库算 delta。
      - ``table_url``：持仓附表 XML 链接。
    任何一步拿不到就返回 None（宁缺勿编）。
    """
    try:
        if not index_url or "sec.gov" not in index_url:
            return None
        page = _get(index_url)
        table_url = _table_url_from_page(page, index_url)
        if not table_url:
            return None
        period_m = _PERIOD.search(page)
        table = parse_information_table(_get(table_url))
        summary = summarize_13f(table)
        if summary:
            summary["table_url"] = table_url
            summary["period"] = period_m.group(1) if period_m else ""
            summary["holdings"] = table["holdings"]
        return summary
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError):
        return None
