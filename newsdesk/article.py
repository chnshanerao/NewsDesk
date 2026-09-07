"""正文预览抽取：只取原文前几个自然段，供详情页『主要内容』面板使用。

为什么不入全文：版权与体积都不允许，也没必要——这个面板的唯一职责是让
用户在点『阅读原始新闻』之前判断值不值得读。因此段数与字数双上限，
并始终保留原文链接。

抽取失败不抛异常：拿不到正文就退回 feed 摘要，绝不因为一个页面拖垮抓取。
"""
import re

from . import config
from .fetch import decode, http_get
from .normalize import clean_text

# 整块丢弃：脚本、样式、导航、推荐位——这些区域里的 <p> 全是噪音。
DROP_BLOCK_RE = re.compile(
    r"<(script|style|noscript|template|svg|iframe|form|nav|header|footer|aside|"
    r"figure|figcaption|table|video|audio)\b[^>]*>.*?</\1>", re.S | re.I)
ARTICLE_RE = re.compile(r"<article\b[^>]*>(.*?)</article>", re.S | re.I)
P_RE = re.compile(r"<p\b[^>]*>(.*?)</p>", re.S | re.I)
BR_SPLIT_RE = re.compile(r"<br\s*/?>\s*<br\s*/?>", re.I)

# 版权声明、编辑署名、导流按钮、图片说明——都不是正文。
BOILERPLATE_RE = re.compile(
    r"责任编辑|责编|编辑：|记者：|来源：|本文来源|原标题|版权(声明|所有)|"
    r"未经授权|不得转载|转载请|扫码|扫一扫|关注我们|微信公众号|点击进入专题|"
    r"更多精彩|相关阅读|延伸阅读|推荐阅读|我要反馈|投稿|订阅|下载客户端|"
    r"(摄|摄影|供图|资料图)\s*$|^图[：:]|主办单位|承办单位|网站标识码|"
    r"copyright|all rights reserved|read more|sign up|subscribe|newsletter|"
    r"follow us|share this|advertisement|cookies?\s+policy|terms of (use|service)|"
    r"photo(graph)?\s*:|image\s*:|file photo|getty images|reuters/|/ap\b|"
    # 通用页面装饰：分享栏、图集翻页、cookie 横幅、发布时间戳、图表数据出处。
    # `\bcookies\b`：整段提到 cookie 的几乎都是同意横幅；真谈 cookie 的报道被
    # 误滤也只是退回 feed 摘要，代价远小于让横幅文案冒充正文。
    r"^(facebook|twitter|share|print|email)[\s|·,]|\bcookies\b|"
    r"^(previous|next) image|^updated \d|^data source[：:]",
    re.I)
MIN_PARAGRAPH_CHARS = 24
# 抽出来太短就没有存在价值：还不如退回 feed 摘要，别用一行导航文字冒充正文。
MIN_PREVIEW_CHARS = 120
# 截断优选断点：优先切在句末标点，避免『主要内容』停在半句话上。
TAIL_TRIM_RE = re.compile(r"[，,；;：:\s][^，,；;：:\s]{0,80}$", re.U)


def _candidates(html: str) -> list[str]:
    """从最可信到最兜底的候选区域。

    不用 class/id 正则去猜正文容器：`<div>` 会嵌套，非贪婪匹配会在第一个
    `</div>` 就截断，实测把中新网正文切成 4 段导航（全篇 54 段）。
    改为『按文档顺序取全篇段落 + 段落级过滤』，导航与推荐位靠长度和
    模板词滤掉，反而更稳。
    """
    body = DROP_BLOCK_RE.sub(" ", html)
    out = []
    articles = [m.group(1) for m in ARTICLE_RE.finditer(body)]
    if articles:
        out.append(max(articles, key=lambda b: len(P_RE.findall(b))))
    out.append(body)
    # 央视等站点把正文塞在 <script> 模板字符串里；剥掉 script 后一段不剩，
    # 只能回到原始 HTML 兜底——段落过滤照旧生效，捞不到就仍然返回空。
    out.append(html)
    return out


def _paragraphs_in(block: str) -> list[str]:
    raw = P_RE.findall(block)
    if not raw:
        # 老站点常用 <br><br> 分段而不写 <p>。
        raw = BR_SPLIT_RE.split(block)
    out, seen = [], set()
    for chunk in raw:
        text = clean_text(chunk)
        if len(text) < MIN_PARAGRAPH_CHARS or BOILERPLATE_RE.search(text):
            continue
        if text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def paragraphs(html: str) -> list[str]:
    """抽出正文自然段，按文档顺序返回，已去噪、去重。"""
    for block in _candidates(html):
        found = _paragraphs_in(block)
        if found:
            return found
    return []


def preview(paras: list[str], max_paragraphs: int | None = None,
            max_chars: int | None = None) -> str:
    """段数 + 字数双上限；超出时在标点处截断并加省略号。"""
    max_paragraphs = max_paragraphs or config.BODY_MAX_PARAGRAPHS
    max_chars = max_chars or config.BODY_MAX_CHARS
    kept: list[str] = []
    used = 0
    for text in paras[:max_paragraphs]:
        room = max_chars - used
        if room <= 0:
            break
        if len(text) > room:
            trimmed = TAIL_TRIM_RE.sub("", text[:room]).rstrip()
            kept.append((trimmed or text[:room].rstrip()) + "…")
            break
        kept.append(text)
        used += len(text)
    return "\n\n".join(kept)


def extract(html: str, **limits) -> str:
    text = preview(paragraphs(html), **limits)
    return text if len(text) >= MIN_PREVIEW_CHARS else ""


def fetch_preview(url: str, timeout: int | None = None) -> tuple[str, str]:
    """返回 (state, text)。state ∈ ok / empty / error，用于避免无休止重试。"""
    if not url.startswith(("http://", "https://")):
        return "error", ""
    try:
        raw = http_get(url, timeout=timeout or config.BODY_TIMEOUT)
    except Exception:  # 单篇正文抓不到是常态，不是流水线故障
        return "error", ""
    try:
        text = extract(decode(raw))
    except Exception:
        return "error", ""
    return ("ok", text) if text else ("empty", "")
