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
    # AI 实体：中文媒体多保留拉丁原名（OpenAI/GPT/Claude），故英文别名即可命中中文标题；
    # 有通行中文译名的（通义千问/深度求索/昇腾）补上中文侧。刻意不收有歧义的短词
    # （perplexity=困惑度、block、now、figure、together），避免误桥。
    "anthropic": ("anthropic",),
    "cohere": ("cohere",),
    "mistral": ("mistral ai", "mistral"),
    "xai": ("x.ai", "xai"),
    "hugging_face": ("hugging face", "huggingface", "抱抱脸"),
    "databricks": ("databricks",),
    "coreweave": ("coreweave",),
    "cerebras": ("cerebras",),
    "groq": ("groq",),
    "scale_ai": ("scale ai",),
    "stability_ai": ("stability ai", "stable diffusion"),
    "midjourney": ("midjourney",),
    "deepmind": ("deepmind", "谷歌深度思维"),
    "gpt": ("chatgpt", "gpt"),
    "claude": ("claude",),
    "gemini": ("gemini",),
    "llama": ("llama",),
    "qwen": ("qwen", "通义千问", "通义"),
    "deepseek": ("deepseek", "深度求索"),
    "grok": ("grok",),
    "copilot": ("copilot",),
    "nvidia_h100": ("h100",),
    "nvidia_h200": ("h200",),
    "nvidia_b200": ("b200",),
    "google_tpu": ("google tpu",),
    "ascend_910": ("昇腾910", "昇腾 910", "ascend 910"),
    # 中国 AI 大模型厂商：中文名 + 英文报道常用名/产品名，都是 zh↔en 桥接高频实体。
    # 刻意排除歧义短词：不收 "spark"(Apache/通用词)、"yi"(太短)、裸「蚂蚁」(蚂蚁森林)、
    # 裸「阶跃」(阶跃函数)、裸「百川」(海纳百川)。歧义厂商只留英文侧或全称。
    "zhipu": ("智谱", "智谱ai", "zhipu", "zhipu ai", "glm"),
    "moonshot": ("月之暗面", "moonshot ai", "moonshot", "kimi"),
    "baichuan": ("百川智能", "baichuan"),
    "sensetime": ("商汤科技", "商汤", "sensetime"),
    "stepfun": ("阶跃星辰", "stepfun"),
    "minimax_ai": ("minimax", "稀宇科技"),
    "zero_one": ("零一万物", "01.ai", "01ai"),
    "modelbest": ("面壁智能", "minicpm"),
    "iflytek": ("科大讯飞", "讯飞", "iflytek", "讯飞星火"),
    "bytedance_ai": ("字节跳动", "豆包", "doubao", "bytedance"),
    "tencent_ai": ("腾讯", "tencent", "混元", "hunyuan"),
    "baidu_ai": ("百度", "baidu", "文心一言", "ernie"),
    "ant_group": ("蚂蚁集团", "蚂蚁金服", "ant group"),
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
    # 「发布」单收会把「苹果发布财报」↔「Apple launches iPhone」这类同实体异事件
    # 桥错（实测 gold 精度掉到 0.90 < 0.95 闸门），故只收不带歧义的「发布新产品」，
    # 中文上市动词靠「推出/上线」兜底；英文侧动词齐全，桥接两侧都需命中同一事件。
    "launch": ("发布新产品", "推出", "上线", "launch", "launches", "launched",
               "unveils", "unveiled", "released", "debuts", "rolls out"),
    "open_source": ("开源", "开放源代码", "开放权重", "open source", "open-source",
                    "open-sources", "open sources", "open-weight", "open weights"),
    "benchmark": ("跑分", "基准测试", "刷新榜单", "benchmark", "benchmarks"),
    "funding": ("融资", "获投", "估值", "领投", "融到", "funding round",
                "valuation", "series a", "series b", "series c"),
    "partnership": ("携手", "达成合作", "结成联盟", "partners with",
                    "partnership with", "teams up with"),
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

# 宽松事件词表：**只**用于 cluster.similarity 的同脚本补召回，绝不参与跨脚本桥接。
#
# 为什么分两张表：跨脚本桥接没有词面兜底（zh↔en 的 jaccard 近乎 0），
# 一旦实体+事件命中就直接判 0.56/0.72 并合并，所以 EVENT_ALIASES 必须极窄 ——
# 前面那条「不收裸『发布』」的注释就是实测精度掉到 0.90 换来的。
# 同脚本这条新路要求词面重合度先过 CLUSTER_SAME_SCRIPT_FLOOR，事件类型只是
# 第二道证据，因此可以放宽到 AI 行业常见但不够独特的动作词。
# 实测（72h 生产语料）这条路要抓的就是这类：
#   Handelsblatt「OpenAI-Chef verschiebt den Börsengang」
#   Die Zeit「OpenAI-Chef: Börsengang nicht mehr in diesem Jahr」  j=0.364
#   —— 两个独立集团、同一件事，词面够不到 0.5，事件词表里也没有「推迟上市」。
EVENT_ALIASES_BROAD = {
    "ipo": ("首次公开募股", "上市", "挂牌", "ipo", "go public", "goes public",
            "going public", "börsengang", "entrée en bourse", "cotation"),
    "delay": ("推迟", "延期", "跳票", "暂缓", "delays", "delayed", "postpones",
              "postponed", "pushed back", "on hold", "verschiebt", "verschoben",
              "verschieben", "reporté", "reporte"),
    "warning": ("警告", "警示", "示警", "风险提示", "敦促", "呼吁",
                "warns", "warned", "warning", "warnings", "cautions", "urges",
                "alarm", "warnt", "warnung", "fordert", "avertit",
                "met en garde"),
    "talent": ("离职", "出走", "挖来", "挖角", "扩招", "招聘", "入职", "加盟",
               "hires", "hiring", "poaches", "poached", "recruits", "departs",
               "quits", "appointed", "gekündigt", "recrute", "débauche"),
    "retire": ("退役", "停用", "下线", "淘汰", "停止支持", "终止支持",
               "retires", "retired", "deprecates", "deprecated", "sunsets",
               "discontinues", "shuts down", "ends support", "stellt ein"),
    "testing": ("灰度测试", "内测", "公测", "实测", "评测", "试用", "跑分",
                "tests", "testing", "tested", "trial", "beta", "evaluates",
                "testet", "teste"),
    "integration": ("接入", "集成", "打通", "兼容", "内置", "搭载", "整合", "移植",
                    "integrates", "integration", "adds support", "built on",
                    "works with", "joins", "compatible", "integriert"),
    "safety_incident": ("事故", "失控", "越狱", "漏洞", "幻觉", "数据泄露",
                        "incident", "malfunction", "jailbreak", "exploit",
                        "vulnerability", "data leak", "vorfall"),
    "cooperation": ("合作", "联手", "共建", "筹建", "cooperat", "collaborat",
                    "teams up", "arbeitet zusammen", "s'allie", "alliance"),
    "code_of_conduct": ("行为准则", "伦理准则", "code of conduct", "verhaltenskodex",
                        "code de conduite", "guidelines"),
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


# —— 多事汇总稿（digest）——
# 「IT早报 0914：马斯克、奥尔特曼响应 Anthropic 呼吁放缓前沿 AI 开发；华为麒麟 9050 Pro
#   能效实测出炉；智谱官宣 50 亿美元融资」—— 一条标题里装了 N 件事。
# 它对桥接和聚类都是毒药：跟任何共享实体的稿子都「像同一件事」。实测 72h 内 34 个簇
# 拿这种标题当事件门面，其中一个 n_items=8 / n_groups=2 的「独立印证」，
# 完全是它把八件不相干的事粘进同一簇粘出来的 —— 既污染标题，又伪造印证。
# 判据刻意保守：只认栏目名和多段分隔符，不猜语义（猜语义会误伤正常标题）。
_DIGEST_MARKER = re.compile(
    r"早报|晚报|日报|周报|晨报|晨会|午评|晚评|快讯|要闻|速览|盘点|早知道|一周回顾|"
    r"news roundup|roundup|news digest|in brief|week in review|morning brief|"
    r"im überblick|kurz & knapp|news kompakt|en bref|l'essentiel", re.I)
_DIGEST_SEPARATORS = re.compile(r"[；;丨|‖]")


def is_digest(title: str) -> bool:
    """一条标题是否装了多件事（≥3 段短讯，或本身就是栏目化汇总）。"""
    text = title or ""
    return bool(_DIGEST_MARKER.search(text)
                or len(_DIGEST_SEPARATORS.findall(text)) >= 2)


def _contains(text: str, alias: str) -> bool:
    if _LATIN_RE.search(alias):
        # 尾部允许紧跟版本数字（Qwen3 / Llama3 / Grok2 / GPT5），但仍禁尾部字母
        # （meta↛metaverse、grok↛grokking）。模型名+版本号是 zh↔en 桥接的主力场景：
        # 中文侧「通义千问」命中，英文侧却因 qwen 后跟 3 被词边界挡掉，白丢一条印证。
        # 首部边界不放宽（(?<![a-z0-9]) 保留），避免 aqwen / 3qwen 之类误命中。
        return re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z])",
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
    # 多事汇总稿。带默认值：旧调用点/测试里按位置构造 Features 的不受影响。
    digest: bool = False
    # events ∪ 宽松词表。只给同脚本补召回用，跨脚本桥接仍只看 events。
    events_broad: frozenset[str] = frozenset()

    @property
    def bridge_keys(self) -> frozenset[str]:
        # 汇总稿连候选都不进：它提到 Anthropic 不代表它在讲 Anthropic 那件事。
        if self.digest:
            return frozenset()
        return frozenset(f"xl:{entity}:{event}"
                         for entity in self.entities for event in self.events)


def features(title: str) -> Features:
    events = _labels(title, EVENT_ALIASES)
    return Features(
        entities=_labels(title, ENTITY_ALIASES),
        events=events,
        objects=_labels(title, OBJECT_ALIASES),
        numbers=_numbers(title),
        periods=periods(title),
        currencies=_labels(title, CURRENCY_ALIASES),
        has_cjk=bool(_CJK_RE.search(title)),
        has_latin=bool(_LATIN_RE.search(title)),
        digest=is_digest(title),
        events_broad=events | _labels(title, EVENT_ALIASES_BROAD),
    )


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
    # 汇总稿不参与桥接：它提到的实体不等于它在讲那件事。
    if a.digest or b.digest:
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
