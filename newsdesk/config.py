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
