"""全局配置与路径。纯标准库，不依赖 pip。"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
WEB_DIR = ROOT / "web"
SOURCES_FILE = ROOT / "sources.json"
PROFILE_DIR = ROOT / "profiles"
DB_PATH = DATA_DIR / "newsdesk.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)
try:
    DATA_DIR.chmod(0o700)
except OSError:
    pass
BACKUP_DIR = Path(os.getenv("NEWSDESK_BACKUP_DIR", str(DATA_DIR / "backups")))
BACKUP_DIR.mkdir(parents=True, exist_ok=True)
try:
    BACKUP_DIR.chmod(0o700)
    # Upgrades must also harden backups created by older releases.
    for _backup_artifact in BACKUP_DIR.glob("newsdesk-*.db*"):
        if _backup_artifact.is_file():
            _backup_artifact.chmod(0o600)
except OSError:
    pass

USER_AGENT = os.getenv(
    "NEWSDESK_UA",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 newsdesk/1.0",
)
# SEC EDGAR 的公平访问策略要求 User-Agent 声明可识别身份+联系方式，
# 否则一律返回 403（实测浏览器 UA 被拒）。这不是密钥，生产用 env 覆盖成真实联系人。
SEC_UA = os.getenv("NEWSDESK_SEC_UA", "NEWSDESK filings monitor (contact ops@newsdesk.local)")
# 监管申报的抽取窗口。申报是稀疏事件（8-K 约每月、13F 每季度），不能用新闻的
# CLUSTER_WINDOW_H（72h）去卡，否则一份都进不来。默认按年，看得见长期布局。
EDGAR_WINDOW_DAYS = int(os.getenv("NEWSDESK_EDGAR_WINDOW_DAYS", "365"))
# 每轮最多自动解析多少份 13F 持仓附表（每份要 2 次 SEC 请求，限速 0.4s/次）。
EDGAR_13F_MAX_PER_RUN = int(os.getenv("NEWSDESK_EDGAR_13F_MAX_PER_RUN", "12"))
# ---- 13F 相邻季仓位变化（delta）的发布门槛 ----
# 一份 13F 只报当季一张快照，加/减/清/建仓要靠相邻季按 CUSIP 相减算出来。
# 仓位变化按『股数变化』度量（市值随股价波动，不能反映实际交易）；成交额 ≈ Δ股数 × 当季单价，
# 把主动交易和被动的价格涨跌分开。命中任一门槛即自动发布（B 模式，激进）：
#   1) 绝对成交额 ≥ 10 亿美元；
#   2) 相对仓位变动 ≥ 25% 且 成交额 ≥ 2 亿美元（小仓位翻倍不值当，加下限）；
#   3) 清仓/新建仓：期初或期末仓位市值 ≥ 2 亿美元。
# 连续 ≥2 季同向只作 materiality 加分，不单独触发（避免每季小额调仓刷屏）。
EDGAR_DELTA_ABS_USD = float(os.getenv("NEWSDESK_EDGAR_DELTA_ABS_USD", "1e9"))
EDGAR_DELTA_REL = float(os.getenv("NEWSDESK_EDGAR_DELTA_REL", "0.25"))
EDGAR_DELTA_REL_MIN_USD = float(os.getenv("NEWSDESK_EDGAR_DELTA_REL_MIN_USD", "2e8"))
EDGAR_DELTA_NEWEXIT_MIN_USD = float(os.getenv("NEWSDESK_EDGAR_DELTA_NEWEXIT_MIN_USD", "2e8"))
HTTP_TIMEOUT = int(os.getenv("NEWSDESK_TIMEOUT", "20"))
SQLITE_BUSY_TIMEOUT = int(os.getenv("NEWSDESK_SQLITE_BUSY_TIMEOUT", "120"))
FETCH_WORKERS = int(os.getenv("NEWSDESK_WORKERS", "6"))

# ---- 正文预览（详情页『主要内容』）----
# 只抽事件头条源的前几段：段数与字数双上限，不入全文。设 0 关闭抽取。
BODY_MAX_PER_RUN = int(os.getenv("NEWSDESK_BODY_MAX_PER_RUN", "80"))
BODY_TIMEOUT = int(os.getenv("NEWSDESK_BODY_TIMEOUT", "12"))
BODY_MAX_PARAGRAPHS = int(os.getenv("NEWSDESK_BODY_MAX_PARAGRAPHS", "4"))
BODY_MAX_CHARS = int(os.getenv("NEWSDESK_BODY_MAX_CHARS", "900"))
# 摘要短于这个字数的稿件优先抽正文：那些页面没有摘要可退，抽不到就是开天窗。
BODY_THIN_SUMMARY_CHARS = int(os.getenv("NEWSDESK_BODY_THIN_SUMMARY_CHARS", "80"))

# ---- 聚类 ----
CLUSTER_WINDOW_H = int(os.getenv("NEWSDESK_CLUSTER_WINDOW_H", "72"))  # 只在窗口内合并
JACCARD_THRESHOLD = 0.42        # 字符二元组 Jaccard 相似度阈值
SIMHASH_MAX_DIST = 6            # simhash 汉明距离阈值（64 位）
# 同脚本（en↔de / en↔en / zh↔zh）「同实体 + 同事件类型 + 无期次/取值冲突」
# 补召回的词面下限。取 0.20 是实测出来的：72h 生产语料里靠这条路够格的 61 对，
# 逐条人工看过，49 对合并中 48 对确为同一件事（唯一的例外还是链式漂移带进来的，
# 不是这条规则直接判的）。再高就开始漏真事：0.28 只剩 22 对，0.35 只剩 13 对，
# 而 OpenAI「今年不上市」那件 12 家报道的事正好卡在 0.20–0.23 这一段。
CLUSTER_SAME_SCRIPT_FLOOR = float(
    os.getenv("NEWSDESK_CLUSTER_SAME_SCRIPT_FLOOR", "0.20"))

# ---- 可信度权重（四个维度，和为 1）----
W_AUTHORITY = 0.40      # 信源权威度
W_CORROBORATION = 0.30  # 独立交叉印证
W_CONTENT = 0.20        # 内容质量信号（规则层）
W_TIMING = 0.10         # 时间一致性 / 时效

CRED_BANDS = [
    (75, "CONFIRMED", "多源印证"),
    (60, "LIKELY", "较可信"),
    (40, "SINGLE", "单源待证"),
    (0, "LOW", "低可信/情绪化"),
]

# ---- 排序 ----
RANK_W_CRED = 0.55
RANK_W_RELEVANCE = 0.45
HALF_LIFE_H = 18.0      # 时效半衰期

# ---- LLM 甄别层（可选，默认关闭；用 --llm 打开）----
LLM_BASE_URL = os.getenv(
    "NEWSDESK_LLM_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1"
)
LLM_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
LLM_MODEL = os.getenv("NEWSDESK_LLM_MODEL", "qwen-plus")
LLM_MAX_CLUSTERS = int(os.getenv("NEWSDESK_LLM_TOPN", "25"))
LLM_CONCURRENCY = int(os.getenv("NEWSDESK_LLM_CONCURRENCY", "4"))

# ---- 入库翻译层（可选，默认关闭）----
# 把「非中/英」外文标题译成英文 canonical，让它能进英文主聚类与 zh↔en 桥，
# 从而参与交叉印证。默认关：只有显式设 NEWSDESK_TRANSLATE=1 且配了 key 才生效。
# 无 key / 报错一律回退原文，绝不阻断抓取。成本护栏见 translate.py。
TRANSLATE_ENABLED = os.getenv("NEWSDESK_TRANSLATE", "0") == "1"
TRANSLATE_BASE_URL = os.getenv(
    "NEWSDESK_TRANSLATE_BASE",
    "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1")
# key 只从环境变量读，绝不写进源码/仓库。缺失即静默关闭翻译（优雅降级）。
TRANSLATE_API_KEY = os.getenv("TOKEN_PLAN_KEY", "")
TRANSLATE_MODEL = os.getenv("NEWSDESK_TRANSLATE_MODEL", "qwen3.8-flash")
TRANSLATE_TIMEOUT = int(os.getenv("NEWSDESK_TRANSLATE_TIMEOUT", "15"))
TRANSLATE_CONCURRENCY = int(os.getenv("NEWSDESK_TRANSLATE_CONCURRENCY", "4"))
# 单轮翻译新条数上限：突发大量外文时留到下一轮，防止一次把配额打爆。
TRANSLATE_MAX_PER_RUN = int(os.getenv("NEWSDESK_TRANSLATE_MAX_PER_RUN", "200"))
# 需要翻译的语言：既不是英文(已 canonical)也不是中文(靠实体词表桥)的外文。
TRANSLATE_SOURCE_LANGS = frozenset(
    x for x in os.getenv("NEWSDESK_TRANSLATE_LANGS", "fr,de,pt,es,it,ru,ja,ko").split(",")
    if x.strip())

# 展示翻译（独立于上面的 canonical 层）：把外文（含英文）的簇标题与领头稿正文译成
# 中文，写入 clusters.headline_zh / items.body_zh，供默认「全中文」展示；原文列不动，
# 前端可切回「原文」。默认关：显式设 NEWSDESK_TRANSLATE_DISPLAY=1 且配了 key 才生效。
# 复用同一把 TOKEN_PLAN_KEY 与模型；无 key / 报错一律回退原文，绝不阻断。
TRANSLATE_DISPLAY_ZH = os.getenv("NEWSDESK_TRANSLATE_DISPLAY", "0") == "1"
# 单轮译中文的新条数上限（标题、正文各一档），防突发打爆配额，超出留到下一轮。
TRANSLATE_ZH_MAX_PER_RUN = int(os.getenv("NEWSDESK_TRANSLATE_ZH_MAX", "150"))
# 正文档默认 150：外文正文入流约 65 条/小时，40/轮跟不上导致新详情长期显示原文；
# 150/轮 > 入流，既跟得上又能逐轮把存量正文补齐（flash 模型，成本极低）。
TRANSLATE_ZH_BODY_MAX_PER_RUN = int(os.getenv("NEWSDESK_TRANSLATE_ZH_BODY_MAX", "150"))

# ---- 云端语音合成 TTS（可选，默认关闭）----
# 老人版的朗读默认用浏览器自带语音：零成本、纯本地。开这一层是为了换更自然的音色，
# 代价是**按字符计费**（TTS 一律按字符，不按 token）。所以两道每日硬闸门同时生效：
# 条数上限（用户定的 100 条/天）+ 字符上限（100×600 满配 = 60000）。撞到任一道就
# 回退浏览器语音，不报错、不拒绝服务。详见 tts.py。
TTS_ENABLED = os.getenv("NEWSDESK_TTS", "0") == "1"
# key 只从环境变量读，绝不写进源码/仓库。缺失即静默关闭云端 TTS（优雅降级）。
# 单列一个 NEWSDESK_TTS_KEY 是因为 TTS 与 LLM 可能不在同一个账号/地域的模型目录里。
TTS_API_KEY = os.getenv("NEWSDESK_TTS_KEY", "") or os.getenv("DASHSCOPE_API_KEY", "")
TTS_BASE_URL = os.getenv(
    "NEWSDESK_TTS_BASE",
    "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation")
# 上游协议。两家接口形状不同，所以做成可切换，换供应商时只动 env 不动代码：
#   dashscope   —— DashScope 原生多模态生成，回一个 24 小时有效的音频 URL
#   openai_chat —— OpenAI 兼容的 chat/completions + modalities:["audio"]，
#                  音频以 base64 回在 message.audio.data 里（Token Plan 走这条）
# 实测记录：本机那把 TOKEN_PLAN_KEY 绑在 ap-southeast-1，目录里列着
# qwen-audio-3.0-tts-plus，但该模型每种请求形状都回 InternalError（同 key 打
# qwen3.8-flash 正常）—— 即「列了但没真开」。等它可用或换一把 DashScope key，
# 只需改 env 三行：NEWSDESK_TTS=1 / NEWSDESK_TTS_PROTOCOL / NEWSDESK_TTS_BASE。
TTS_PROTOCOL = os.getenv("NEWSDESK_TTS_PROTOCOL", "dashscope")
TTS_MODEL = os.getenv("NEWSDESK_TTS_MODEL", "qwen3-tts-flash")
TTS_FORMAT = os.getenv("NEWSDESK_TTS_FORMAT", "mp3")
TTS_VOICE = os.getenv("NEWSDESK_TTS_VOICE", "Cherry")
TTS_TIMEOUT = int(os.getenv("NEWSDESK_TTS_TIMEOUT", "20"))
# 每天最多合成多少条（用户设定：100 条/天）。
TTS_DAILY_ITEMS = int(os.getenv("NEWSDESK_TTS_DAILY_ITEMS", "100"))
# 每天字符上限：默认 = 100 条 × 单条 600 字满配。这是钱的那道闸。
TTS_DAILY_CHARS = int(os.getenv("NEWSDESK_TTS_DAILY_CHARS", "60000"))
# 单次请求字符上限：qwen-tts 系列上游限 512 token / 其它模型 600 字符，取 600 保守值。
TTS_MAX_CHARS = int(os.getenv("NEWSDESK_TTS_MAX_CHARS", "600"))
# 上游音频 URL 保 24 小时；缓存按 20 小时过期，留足安全边际。
TTS_CACHE_TTL = int(os.getenv("NEWSDESK_TTS_CACHE_TTL", str(20 * 3600)))
# 计价单位：元 / 万字符（cosyvoice-v3.5-flash 档 ¥0.8）。只用于后台展示花了多少。
TTS_PRICE_CNY_PER_10K = float(os.getenv("NEWSDESK_TTS_PRICE", "0.8"))

SERVER_HOST = os.getenv("NEWSDESK_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("NEWSDESK_PORT", "8899"))
REFRESH_TOKEN = os.getenv("NEWSDESK_REFRESH_TOKEN", "")
WRITE_TOKEN = os.getenv("NEWSDESK_WRITE_TOKEN", "")
REFRESH_COOLDOWN = int(os.getenv("NEWSDESK_REFRESH_COOLDOWN", "30"))
AUTO_REFRESH_SECONDS = int(os.getenv(
    "NEWSDESK_AUTO_REFRESH_SECONDS", os.getenv("NEWSDESK_AUTO_REFRESH", "900")))
AUTO_BACKUP_SECONDS = int(os.getenv("NEWSDESK_AUTO_BACKUP_SECONDS", "86400"))
BACKUP_KEEP = int(os.getenv("NEWSDESK_BACKUP_KEEP", "7"))
MARKET_RETENTION_DAYS = int(os.getenv("NEWSDESK_MARKET_RETENTION_DAYS", "90"))
ALERT_WEBHOOK = os.getenv("NEWSDESK_ALERT_WEBHOOK", "")
ALERT_WEBHOOK_TOKEN = os.getenv("NEWSDESK_ALERT_WEBHOOK_TOKEN", "")


def source_role(source: dict) -> str:
    """来源的编辑角色不同于权威等级：官方声明、独立采编和聚合线索不能混为一谈。"""
    if source.get("source_role"):
        return source["source_role"]
    sid = source.get("id", "")
    if sid.startswith(("gov_", "pbc_", "stats_", "fed_", "ecb_", "sec_", "un_")):
        return "official"
    if sid.startswith(("xinhua_", "reuters_")):
        return "wire"
    if sid == "hackernews":
        return "community"
    if source.get("tier") == 3:
        return "aggregator"
    return "reporting"


def load_sources(path: Path | None = None) -> dict:
    with open(path or SOURCES_FILE, encoding="utf-8") as f:
        reg = json.load(f)
    reg["tier_weight"] = {
        int(k): v["weight"] for k, v in reg.get("tiers", {}).items()
    }
    reg["tier_label"] = {
        int(k): v["label"] for k, v in reg.get("tiers", {}).items()
    }
    for source in reg["sources"]:
        source["source_role"] = source_role(source)
        # SEC EDGAR 需要合规 UA，否则 403。按源注入，不影响其他信源。
        if "sec.gov" in source.get("url", ""):
            source.setdefault("http_headers", {}).setdefault("User-Agent", SEC_UA)
    return reg


def load_profile(name: str = "default") -> dict:
    p = PROFILE_DIR / f"{name}.json"
    if not p.exists():
        raise SystemExit(f"画像不存在: {p}")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def band(score: float) -> tuple[str, str]:
    for threshold, code, label in CRED_BANDS:
        if score >= threshold:
            return code, label
    return "LOW", "低可信"
