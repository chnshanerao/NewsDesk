"""存疑度：一条新闻有多少『主动的可疑信号』，与可信度是两个独立的轴。

为什么不能直接拿可信度的反面当存疑度：

  可信度答的是「证据有多完整」。它的分母是理想状态 —— 多个独立集团、一次信源、
  时间集中。一条统计局公报只有一家发布，可信度里的『交叉印证』一项拿不到分，
  但没有任何人会说这条新闻可疑。反过来，一条被十家门户转载的『某股暴涨』稿件，
  印证项、时效项都满分，可信度看着不低，可它通篇是拉抬情绪的措辞。

  所以存疑度只数**主动出现的**负面信号：措辞、来源结构、时间线异常、反向证据。
  「缺少印证」这种**证据不足**只给很小的权重（它是可信度的活儿，不是这儿的），
  否则两个轴会高度相关，页面上摆两个数字却说同一件事。

分数越高越可疑。每一分都必须能说出理由 —— `reasons` 会原样进前端，
用户点开就看到「为什么这条被标成存疑」。

本模块不联网、不依赖 LLM；LLM 的 red_flags 若已存在则作为额外信号计入。
"""
from . import credibility

# ---- 信号权重表（故意平铺成一张可读的表，改权重不用读代码逻辑）----
# 反向证据是最重的单项，但**刻意不给到 50**（即不单独顶到「高度存疑」）：关系分类器
# 有过大批假阳性 —— 库里 24 条 refute 修完后全数归零，都是期次不同的同类指标被误判成
# 互相矛盾。让一次误判就把一条新闻打成高度存疑，代价比漏标大。35 分的效果是：单独出现
# 落在「有存疑点」，只要再叠加任何一个措辞或来源信号就进高度存疑。
W_REFUTED = 35
W_RUMOR_UNATTRIBUTED = 18       # 「网传/疑似/知情人士」且全文没有明确出处
W_RUMOR_ATTRIBUTED = 9          # 同上但有出处（『据新华社，疑似……』）
W_CLICKBAIT_EACH = 6
W_CLICKBAIT_CAP = 18
W_HYPE = 10                     # 暴涨/腰斩/崩盘这类情绪化涨跌词
W_PUNCTUATION = 5               # 多个感叹号/疑问号，或标题带 emoji
# 措辞信号叠加：单个『暴涨』可能只是在陈述事实，但「震惊！+ 网传 + 暴涨 + ！！」同时出现
# 就不是偶然用词，而是一套成型的营销/谣言话术。共现本身是独立于各单项的证据。
W_WORDING_STACK = 8
W_WORDING_STACK_MIN = 3         # 四类措辞信号中命中几类才算「成套」
W_AGGREGATOR_ONLY = 15          # 全部来源都是聚合/UGC，没有任何采编或官方源
W_NO_INDEPENDENT = 10           # 无独立采编集团印证（证据不足项，权重刻意压低）
W_SINGLE_GROUP_ECHO = 8         # 多篇稿件但全来自同一集团 —— 转载不是印证
W_SOURCE_DEGRADED = 6           # 供稿源健康度已降级（停更/无日期）
W_STALE_RECYCLE = 10            # 报道跨度过长，疑似旧闻回炒
W_FUTURE_TS = 12                # 发布时间在未来，元数据不可信
W_LLM_FLAG_EACH = 8
W_LLM_FLAG_CAP = 20

BANDS = [
    (50, "SUSPECT", "高度存疑"),
    (25, "QUESTIONABLE", "有存疑点"),
    (10, "MINOR", "轻微存疑"),
    (0, "CLEAR", "无明显存疑"),
]


def band(score: float) -> tuple[str, str]:
    for threshold, code, label in BANDS:
        if score >= threshold:
            return code, label
    return "CLEAR", "无明显存疑"


def _content_flags(cluster: dict) -> dict:
    """取内容质量那一步已经算好的词表命中，绝不在这里重新匹配一遍。"""
    return ((cluster.get("breakdown") or {}).get("content") or {}).get("flags") or {}


def assess(cluster: dict, items: list[dict], claims: list[dict] | None = None,
           health: dict | None = None, llm: dict | None = None) -> dict:
    """算一条事件的存疑度。

    cluster: credibility.score_cluster 的输出（要用它的 breakdown.content.flags）
    items:   该事件的成员稿件
    claims:  evidence.claims 的输出；只用来看有没有 refute 关系
    health:  {source_id: {"verdict": ...}}，信源健康度
    llm:     已有的 LLM 甄别结论（可选）
    """
    points: list[tuple[int, str]] = []
    flags = _content_flags(cluster)
    health = health or {}

    disputed = [c for c in (claims or []) if c.get("status") == "disputed"]
    if disputed:
        points.append((W_REFUTED, f"有 {len(disputed)} 条断言存在方向或关键数字相反的来源"))

    rumor, attrib = flags.get("rumor") or [], flags.get("attribution") or []
    if rumor:
        if attrib:
            points.append((W_RUMOR_ATTRIBUTED,
                           f"含未证实措辞 {'/'.join(rumor[:3])}，但标明了出处"))
        else:
            points.append((W_RUMOR_UNATTRIBUTED,
                           f"含未证实措辞 {'/'.join(rumor[:3])}，且全文无明确出处"))

    clickbait = flags.get("clickbait") or []
    if clickbait:
        points.append((min(W_CLICKBAIT_CAP, W_CLICKBAIT_EACH * len(clickbait)),
                       f"标题党词 {'/'.join(clickbait[:3])}"))
    hype = flags.get("hype") or []
    if hype:
        points.append((W_HYPE, f"情绪化涨跌词 {'/'.join(hype[:2])}"))
    punctuation = bool(flags.get("emoji")) or int(flags.get("exclamations") or 0) >= 2
    if punctuation:
        points.append((W_PUNCTUATION, "标题使用 emoji 或多个感叹/疑问号"))

    stacked = sum(1 for hit in (rumor, clickbait, hype, punctuation) if hit)
    if stacked >= W_WORDING_STACK_MIN:
        points.append((W_WORDING_STACK,
                       f"{stacked} 类可疑措辞同时出现，是成套话术而非偶然用词"))

    roles = {it.get("src_role") or it.get("source_role") or "reporting" for it in items}
    editorial = roles & {"reporting", "wire", "official", "regulatory_filing"}
    # 一次源（官方声明、监管披露）自己就是当事人在说话。「统计局说 CPI 涨 0.3%」这件事
    # 不需要第三方来核实『他是否这么说了』—— 硬扣一笔会让几乎每条公报都挂上「轻微存疑」，
    # 那个标注就此失去意义。至于当事人说的内容是否成立，由可信度的印证项和反向证据管。
    primary = roles & {"official", "regulatory_filing"}
    groups = {it.get("grp") or it.get("source_id") for it in items}
    independent = {it.get("grp") or it.get("source_id") for it in items
                   if (it.get("src_role") or it.get("source_role") or "reporting")
                   in ("reporting", "wire")}
    if not editorial:
        points.append((W_AGGREGATOR_ONLY,
                       f"全部来源为{'/'.join(sorted(roles))}类，无采编或官方一次源"))
    elif not independent and not primary:
        points.append((W_NO_INDEPENDENT, "无独立采编来源，内容未被第三方核实"))
    if len(items) >= 3 and len(groups) == 1:
        points.append((W_SINGLE_GROUP_ECHO,
                       f"{len(items)} 篇稿件全部来自同一集团，转载不构成印证"))

    degraded = sorted({it.get("source_name") or it["source_id"] for it in items
                       if (health.get(it["source_id"], {}).get("verdict"))
                       in ("degraded", "unhealthy", "down")})
    if degraded:
        points.append((W_SOURCE_DEGRADED, f"供稿源健康度已降级：{'/'.join(degraded[:3])}"))

    timing_note = ((cluster.get("breakdown") or {}).get("timing") or {}).get("note") or ""
    if "未来" in timing_note:
        points.append((W_FUTURE_TS, timing_note))
    elif "疑似旧闻回炒" in timing_note:
        # 只认 >48h 那条不带犹豫的判语。24–48h 那条的原文是「可能是追踪或回炒」——
        # 追踪报道跨一两天完全正常，拿一句自己都在犹豫的判语扣分是站不住的；
        # 时效不佳已经在可信度里扣过一次（0.60/1.00），存疑度不该重复计一遍。
        points.append((W_STALE_RECYCLE, timing_note))

    red_flags = ((llm or cluster.get("llm")) or {}).get("red_flags") or []
    if red_flags:
        points.append((min(W_LLM_FLAG_CAP, W_LLM_FLAG_EACH * len(red_flags)),
                       f"模型甄别标出 {len(red_flags)} 处疑点：{'；'.join(str(x) for x in red_flags[:2])}"))

    total = min(100, sum(weight for weight, _ in points))
    code, label = band(total)
    return {
        "doubt": round(float(total), 1),
        "doubt_code": code,
        "doubt_label": label,
        "doubt_detail": {
            "label": label,
            "reasons": [{"points": weight, "reason": text} for weight, text in
                        sorted(points, key=lambda x: -x[0])],
            # 没有命中任何信号时也要说清「查过什么」，否则 0 分看起来像没算。
            "checked": ["反向证据", "未证实措辞", "标题党/情绪化用词", "来源结构",
                        "信源健康度", "时间线一致性", "模型甄别疑点"],
            "method": "rule-doubt-v1",
        },
    }


def summarize(rows: list[dict]) -> dict:
    """给一批事件出存疑分布，用于质量门禁和信源档案。"""
    counts: dict[str, int] = {code: 0 for _, code, _ in BANDS}
    for row in rows:
        counts[row.get("doubt_code") or "CLEAR"] = \
            counts.get(row.get("doubt_code") or "CLEAR", 0) + 1
    scores = [float(row.get("doubt") or 0) for row in rows]
    return {"n": len(rows), "bands": counts,
            "mean": round(sum(scores) / len(scores), 2) if scores else 0.0,
            "flagged": sum(1 for s in scores if s >= 25)}


# 内容质量的词表就是存疑度的词表，这里只做一次显式引用，方便阅读时找到它。
WORDLISTS = {"clickbait": credibility.CLICKBAIT, "rumor": credibility.RUMOR,
             "attribution": credibility.ATTRIBUTION, "hype": credibility.HYPE_MOVE}
