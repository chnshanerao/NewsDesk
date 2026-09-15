"""云端语音合成（TTS）+ 每日硬上限。

老人版的「🔊 朗读」默认用浏览器自带语音：零成本、纯本地、不联网。本模块提供
可选的云端合成——音色自然得多，但**按字符计费**（TTS 一律按字符，不按 token），
所以必须有硬上限。上限用满不报错、不拒绝服务：`synthesize()` 返回 ok=False，
前端自动回退到浏览器语音，读者只会觉得声音换了，不会遇到功能不可用。

两道每日闸门同时生效，先撞到哪道就停：
  1. 每天最多合成 TTS_DAILY_ITEMS 条（默认 100 条，用户定的数）；
  2. 每天最多 TTS_DAILY_CHARS 个字符（默认 60000 = 100 条 × 600 字满配）。
只留条数上限，「每条都读全文」时会超支；只留字符上限，「每条都极短」时会被刷成
上千次请求。两道都留着，任何一种使用形态都封得住顶。

只用标准库 urllib（本项目零 pip 依赖）：因此走 DashScope 的 HTTP 非流式接口，
不用只提供 WebSocket 的 CosyVoice。上游返回的音频 URL 只保 24 小时，所以合成后
立刻把音频字节抓回来存进 tts_cache，前端一律播放同源的 /api/tts/audio?h=…
（页面 CSP 是 default-src 'self'，不为一个外部域名开口子），重复朗读同一段文字
直接命中缓存：不计费、不占当天名额。
"""
import hashlib
import json
import time
import urllib.error
import urllib.request

from . import config

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS tts_usage("
    "ts INTEGER NOT NULL, day TEXT NOT NULL, model TEXT NOT NULL, "
    "chars INTEGER NOT NULL DEFAULT 0)",
    "CREATE INDEX IF NOT EXISTS idx_tts_usage_day ON tts_usage(day)",
    "CREATE TABLE IF NOT EXISTS tts_cache("
    "hash TEXT PRIMARY KEY, ts INTEGER NOT NULL, chars INTEGER NOT NULL DEFAULT 0, "
    "mime TEXT NOT NULL DEFAULT 'audio/mpeg', audio BLOB NOT NULL)",
)


def _ensure(conn) -> None:
    for stmt in _SCHEMA:
        conn.execute(stmt)


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def available() -> bool:
    """云端 TTS 是否可用：显式开启 + 配了 key。缺任一条静默回退浏览器语音。"""
    return bool(config.TTS_ENABLED and config.TTS_API_KEY)


def usage(conn) -> dict:
    """当天用量与剩余额度。前端启动时读一次，决定走云端还是浏览器语音。"""
    _ensure(conn)
    row = conn.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(chars),0) c FROM tts_usage WHERE day=?",
        (_today(),)).fetchone()
    items, chars = int(row[0]), int(row[1])
    return {
        "enabled": available(),
        "model": config.TTS_MODEL,
        "voice": config.TTS_VOICE,
        "day": _today(),
        "daily_items": config.TTS_DAILY_ITEMS,
        "daily_chars": config.TTS_DAILY_CHARS,
        "max_chars_per_call": config.TTS_MAX_CHARS,
        "used_items": items,
        "used_chars": chars,
        "remaining_items": max(0, config.TTS_DAILY_ITEMS - items),
        "remaining_chars": max(0, config.TTS_DAILY_CHARS - chars),
        # 单价按万字符计（TTS 按字符计费，不是 token），只用于让管理员看见花了多少。
        "price_cny_per_10k_chars": config.TTS_PRICE_CNY_PER_10K,
        "spent_cny_today": round(chars / 10000 * config.TTS_PRICE_CNY_PER_10K, 4),
        "cap_cny_per_day": round(
            config.TTS_DAILY_CHARS / 10000 * config.TTS_PRICE_CNY_PER_10K, 4),
    }


def _prune(conn) -> None:
    """按 TTL 清过期音频。100 条/天的上限下，缓存体积上界约几十 MB。"""
    conn.execute("DELETE FROM tts_cache WHERE ts<?",
                 (int(time.time()) - config.TTS_CACHE_TTL,))


def digest_of(text: str) -> str:
    return hashlib.sha256(
        f"{config.TTS_MODEL}|{config.TTS_VOICE}|{text}".encode("utf-8")).hexdigest()


def audio(conn, digest: str):
    """取缓存音频字节，供 /api/tts/audio 同源播放。没有则 None。"""
    _ensure(conn)
    if not (digest or "").isalnum() or len(digest) != 64:
        return None
    row = conn.execute("SELECT mime,audio,ts FROM tts_cache WHERE hash=?",
                       (digest,)).fetchone()
    if not row or int(time.time()) - int(row[2]) > config.TTS_CACHE_TTL:
        return None
    return {"mime": row[0], "bytes": bytes(row[1])}


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
    with urllib.request.urlopen(req, timeout=config.TTS_TIMEOUT) as resp:
        return resp.read(8 * 1024 * 1024)


def _call_api(text: str) -> str:
    """调用 DashScope 多模态生成接口，返回音频 URL。失败一律抛异常由上层降级。"""
    payload = json.dumps({
        "model": config.TTS_MODEL,
        "input": {"text": text, "voice": config.TTS_VOICE},
    }).encode("utf-8")
    req = urllib.request.Request(config.TTS_BASE_URL, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {config.TTS_API_KEY}")
    with urllib.request.urlopen(req, timeout=config.TTS_TIMEOUT) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    url = (((body.get("output") or {}).get("audio") or {}).get("url") or "").strip()
    if not url:
        raise ValueError("上游未返回音频 URL")
    return url


def synthesize(conn, text: str) -> dict:
    """合成一段文字。返回 {ok, audio|reason, …}；ok=False 时前端回退浏览器语音。

    永不把异常抛给调用方：任何失败（未配置、超限、上游报错）都是 ok=False + reason。
    朗读是老人版的核心功能，绝不能因为云端不可用就整块坏掉。
    """
    text = " ".join((text or "").split())
    if not text:
        return {"ok": False, "reason": "empty"}
    if not available():
        return {"ok": False, "reason": "disabled"}
    text = text[:config.TTS_MAX_CHARS]

    _ensure(conn)
    _prune(conn)
    digest = digest_of(text)
    if audio(conn, digest):
        # 命中缓存不计费、不占名额：同一条新闻反复朗读不该重复烧配额。
        return {"ok": True, "audio": f"/api/tts/audio?h={digest}", "cached": True,
                "chars": len(text), "usage": usage(conn)}

    quota = usage(conn)
    if quota["remaining_items"] <= 0:
        return {"ok": False, "reason": "daily_items", "usage": quota}
    if len(text) > quota["remaining_chars"]:
        return {"ok": False, "reason": "daily_chars", "usage": quota}

    try:
        blob = _fetch(_call_api(text))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            ValueError, json.JSONDecodeError, OSError) as exc:
        return {"ok": False, "reason": "upstream",
                "detail": type(exc).__name__, "usage": quota}
    if not blob:
        return {"ok": False, "reason": "upstream", "detail": "EmptyAudio",
                "usage": quota}

    now = int(time.time())
    conn.execute("INSERT INTO tts_usage(ts,day,model,chars) VALUES(?,?,?,?)",
                 (now, _today(), config.TTS_MODEL, len(text)))
    conn.execute("INSERT OR REPLACE INTO tts_cache(hash,ts,chars,mime,audio) "
                 "VALUES(?,?,?,?,?)", (digest, now, len(text), "audio/mpeg", blob))
    conn.commit()
    return {"ok": True, "audio": f"/api/tts/audio?h={digest}", "cached": False,
            "chars": len(text), "usage": usage(conn)}
