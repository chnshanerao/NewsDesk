"""LLM 甄别层（可选）。规则层负责『能算的』，LLM 负责『要读懂的』。

只做四件事，且都要求给出依据：
  1. 事实密度 factuality —— 是可核查的事实陈述，还是包装成新闻的观点/软广/情绪
  2. 夸张程度 sensationalism —— 标题与内容是否不成比例
  3. 与我的相关性 relevance —— 对『投资/择业/个人发展』是否真的有决策价值
  4. red_flags —— 具体的可疑点（无信源、因果跳跃、幸存者偏差、软广、旧闻回炒…）

刻意不让 LLM 判断『新闻真假』：它没有实时事实库，那是交叉印证层的活。
它判断的是『这段文本本身可不可信、值不值得你花时间』。
"""
import json
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import config

PROMPT_VERSION = "3"

SYSTEM = """你是新闻终端的内容甄别引擎，服务对象是一位普通个人投资者/职业发展决策者。
你的任务不是判断新闻真假（你没有实时事实库），而是评估这段文本本身的质量与决策价值。

【硬性约束：不许用你的知识截止日期当判据】
今天是 {today}。你读到的都是刚抓下来的当日稿件，时间必然晚于你的训练数据。
- 严禁把『这件事我不知道』『截至我所知尚未发生』『该日期在未来』写进 red_flags
- 严禁因为事件发生在你的知识截止之后而降低 factuality
- 稿件里的年份/日期只要不晚于今天，就是正常的当期新闻，不是造假信号
你评的是文本质量（有没有主体、数字、出处、是否夸张、是否软广），不是事件是否在你记忆中存在。

必须严格输出 JSON，不要 markdown 代码块，不要任何解释性前后缀。"""

USER_TMPL = """请甄别下面这个新闻事件。今天是 {today}。

【事件标题】{headline}
【报道家数】{n_items} 篇，来自 {n_groups} 个独立信源集团
【最高权威信源】{lead} (T{tier})
【各家标题】
{titles}
【摘要片段】
{bodies}

输出 JSON，字段：
{{
  "factuality": 0~1,        // 可核查事实的密度：有主体/数字/时间/出处=高；纯观点、情绪、软广=低
                            // 注意：不是『这件事是否真的发生过』，你无权也无据判断这个
  "sensationalism": 0~1,    // 标题相对内容的夸张程度，0=克制陈述，1=严重夸张
  "relevance": 0~1,         // 对『投资决策 / 择业与职业发展 / 个人重大选择』的实际参考价值
  "topic": "macro|policy|market|tech|career|industry|risk|world|society|noise",
  "summary": "一句话说清发生了什么，≤50字，只写事实不加评论",
  "so_what": "对一个普通人意味着什么、可采取什么动作，≤40字；若无实际意义就写『无行动价值』",
  "claims": ["文中最关键的1~3条可核查断言"],
  "red_flags": ["具体可疑点，如：无信源/因果跳跃/软广/旧闻回炒/以个例代趋势/数据缺口径。没有就空数组"],
  "verify_next": "若要自己核实，下一步该查什么（≤30字）"
}}"""


def _post(payload: dict, timeout: int = 90) -> dict:
    if not config.LLM_API_KEY:
        raise RuntimeError("未配置 DASHSCOPE_API_KEY；请设置环境变量后再启用 --llm")
    req = urllib.request.Request(
        config.LLM_BASE_URL.rstrip("/") + "/chat/completions",
        json.dumps(payload).encode("utf-8"),
        {"Authorization": f"Bearer {config.LLM_API_KEY}",
         "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    depth, start = 0, -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    start = -1
    raise ValueError("LLM 未返回可解析 JSON: " + text[:160])


# 模型『拿知识截止当造假证据』的典型措辞。命中即丢弃该疑点。
CUTOFF_FLAG_RE = re.compile(
    r"(?:截至(?:我所知|目前|20\d\d)|知识(?:库|截止|更新)|训练数据|我(?:不知道|无法确认该事件是否)"
    r"|尚未发生|未来(?:日期|时间)|日期(?:在未来|超前|异常靠前)|20[2-9]\d年.{0,6}(?:尚未|还没)"
    r"|真实历史中|历史上并无|无该事件记录|事件时间.{0,8}(?:未来|尚未))"
)


def _num(v, default: float = 0.5) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return default


def _strip_cutoff_flags(flags) -> tuple[list[str], list[str]]:
    if not isinstance(flags, list):
        return [], []
    keep, drop = [], []
    for f in flags:
        s = str(f)
        (drop if CUTOFF_FLAG_RE.search(s) else keep).append(s)
    return keep, drop


def _today() -> str:
    from .normalize import CN_TZ
    from datetime import datetime
    return datetime.now(CN_TZ).strftime("%Y年%m月%d日")


def judge(cluster: dict, items: list[dict]) -> dict:
    today = _today()
    titles = "\n".join(
        f"- [{it['source_name']} T{it['tier']}] {it['title']}" for it in items[:8]
    )
    bodies = "\n".join(
        (it.get("summary") or "")[:220] for it in items[:4] if it.get("summary")
    )[:1200] or "（无摘要）"
    prompt = USER_TMPL.format(
        today=today,
        headline=cluster["headline"], n_items=cluster["n_items"],
        n_groups=cluster["n_groups"], lead=cluster["headline_src"],
        tier=cluster["best_tier"], titles=titles, bodies=bodies,
    )
    resp = _post({
        "model": config.LLM_MODEL,
        "messages": [{"role": "system", "content": SYSTEM.format(today=today)},
                     {"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 900,
    })
    data = _extract_json(resp["choices"][0]["message"]["content"])
    data["red_flags"], dropped = _strip_cutoff_flags(data.get("red_flags"))
    if dropped:
        # 模型仍然拿『我不知道这件事』当疑点时，把这些疑点的扣分抹掉，
        # 否则每天的新闻都会因为『比训练数据新』而被系统性打低。
        data["_dropped_flags"] = dropped
        data["factuality"] = max(_num(data.get("factuality")), 0.45)
    data["_model"] = config.LLM_MODEL
    data["_prompt_version"] = PROMPT_VERSION
    return data


def judge_many(pairs: list[tuple[dict, list[dict]]],
               on_result=None) -> list[tuple[dict, dict | None, str | None]]:
    """并发甄别。单条失败不影响其他条——LLM 层是增强，不是依赖。"""
    def one(pair):
        cluster, items = pair
        try:
            res = judge(cluster, items)
            if on_result:
                on_result(cluster, res, None)
            return cluster, res, None
        except urllib.error.HTTPError as e:
            err = f"HTTP {e.code}: {e.read()[:120].decode('utf-8', 'ignore')}"
        except Exception as e:
            err = f"{type(e).__name__}: {e}"[:160]
        if on_result:
            on_result(cluster, None, err)
        return cluster, None, err

    with ThreadPoolExecutor(max_workers=config.LLM_CONCURRENCY) as ex:
        return list(ex.map(one, pairs))
