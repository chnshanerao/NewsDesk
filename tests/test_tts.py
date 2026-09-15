"""云端朗读的两道每日闸门 + 缓存 + 优雅降级。

这层的价值全在「不会花超」和「花超了也不坏」这两件事上，所以测试重点不是能不能合成，
而是：撞上限时返回 ok=False（不抛异常、不返错误页）、缓存命中不计费、缺 key 直接降级。
"""
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from newsdesk import config, store, tts


class _Blob:
    """假装是 urlopen 的返回：合成接口回 JSON，音频 URL 回字节。"""

    def __init__(self, data):
        self._data = data

    def read(self, *_a):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


AUDIO = b"ID3fake-mp3-bytes"
REPLY = b'{"output":{"audio":{"url":"https://dashscope.example/a.mp3"}}}'


def _fake_urlopen(req, timeout=None):
    url = req if isinstance(req, str) else req.full_url
    return _Blob(AUDIO if url.endswith(".mp3") else REPLY)


class TTSQuotaTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.conn = store.connect(Path(self._td.name) / "t.db")
        store.init(self.conn)
        self._on = mock.patch.multiple(
            config, TTS_ENABLED=True, TTS_API_KEY="test-key",
            TTS_DAILY_ITEMS=3, TTS_DAILY_CHARS=40, TTS_MAX_CHARS=20)
        self._on.start()
        self._net = mock.patch("urllib.request.urlopen", _fake_urlopen)
        self._net.start()

    def tearDown(self):
        self._net.stop()
        self._on.stop()
        self.conn.close()
        self._td.cleanup()

    def test_disabled_or_keyless_degrades_instead_of_failing(self):
        """未开启 / 没 key 时返回 ok=False，不抛异常 —— 前端据此回退浏览器语音。"""
        with mock.patch.object(config, "TTS_ENABLED", False):
            self.assertEqual(tts.synthesize(self.conn, "一段话")["reason"], "disabled")
            self.assertFalse(tts.available())
        with mock.patch.object(config, "TTS_API_KEY", ""):
            self.assertEqual(tts.synthesize(self.conn, "一段话")["reason"], "disabled")
        self.assertEqual(tts.synthesize(self.conn, "   ")["reason"], "empty")

    def test_success_records_usage_and_serves_same_origin_audio(self):
        r = tts.synthesize(self.conn, "央行宣布降息")
        self.assertTrue(r["ok"])
        self.assertFalse(r["cached"])
        # 播放地址必须是同源路径：页面 CSP 是 default-src 'self'，外链音频会被拦。
        self.assertTrue(r["audio"].startswith("/api/tts/audio?h="))
        clip = tts.audio(self.conn, tts.digest_of("央行宣布降息"))
        self.assertEqual(clip["bytes"], AUDIO)
        u = tts.usage(self.conn)
        self.assertEqual((u["used_items"], u["used_chars"]), (1, 6))
        self.assertEqual(u["remaining_items"], 2)

    def test_cache_hit_is_free_and_does_not_consume_quota(self):
        """同一段文字重复朗读不该重复烧钱，也不该占当天名额。"""
        tts.synthesize(self.conn, "央行宣布降息")
        with mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("缓存命中不该再打上游")):
            again = tts.synthesize(self.conn, "央行宣布降息")
        self.assertTrue(again["ok"])
        self.assertTrue(again["cached"])
        self.assertEqual(tts.usage(self.conn)["used_items"], 1)

    def test_item_gate_stops_at_the_daily_count(self):
        """条数闸：用户定的『每天不超过 100 条』，这里按 3 条压缩验证。"""
        for i in range(3):
            self.assertTrue(tts.synthesize(self.conn, f"第{i}条不同的新闻")["ok"])
        blocked = tts.synthesize(self.conn, "第四条新闻")
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["reason"], "daily_items")
        self.assertEqual(tts.usage(self.conn)["remaining_items"], 0)

    def test_char_gate_stops_before_the_money_ceiling(self):
        """字符闸：TTS 按字符计费，条数没用完也可能先撞字符上限（钱的天花板）。"""
        with mock.patch.multiple(config, TTS_DAILY_ITEMS=100, TTS_DAILY_CHARS=25):
            self.assertTrue(tts.synthesize(self.conn, "十五个字的一段新闻标题啊啊啊")["ok"])
            blocked = tts.synthesize(self.conn, "又一段十五个字的新闻标题啊啊啊")
            self.assertFalse(blocked["ok"])
            self.assertEqual(blocked["reason"], "daily_chars")
            self.assertGreater(tts.usage(self.conn)["remaining_items"], 0)  # 条数还有

    def test_upstream_failure_degrades_and_bills_nothing(self):
        """上游报错不计费、不抛异常：朗读是老人版核心功能，不能整块坏掉。"""
        with mock.patch("urllib.request.urlopen", side_effect=OSError("boom")):
            r = tts.synthesize(self.conn, "上游会挂掉的一条")
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "upstream")
        self.assertEqual(tts.usage(self.conn)["used_items"], 0)

    def test_long_text_is_truncated_not_rejected(self):
        r = tts.synthesize(self.conn, "长" * 500)
        self.assertTrue(r["ok"])
        self.assertEqual(r["chars"], config.TTS_MAX_CHARS)

    def test_usage_reports_the_real_price_unit_and_daily_ceiling(self):
        """按字符计价、当天花费、当天上限都要能报给管理员看，否则没法判断要不要调。"""
        with mock.patch.multiple(config, TTS_DAILY_ITEMS=100, TTS_DAILY_CHARS=60000,
                                 TTS_PRICE_CNY_PER_10K=0.8):
            u = tts.usage(self.conn)
        self.assertEqual(u["price_cny_per_10k_chars"], 0.8)
        self.assertEqual(u["cap_cny_per_day"], 4.8)     # 6 万字符 × ¥0.8/万
        self.assertEqual(u["spent_cny_today"], 0)
        self.assertEqual(u["day"], tts._today())

    def test_openai_chat_protocol_reads_base64_audio(self):
        """Token Plan 那条通路：音频以 base64 回在 message.audio.data 里。

        做成可切换协议是为了「换供应商只改 env 不改代码」—— 所以这条路径必须有测试，
        不能等到真拿到 key 那天才发现解析写错了。
        """

        reply = json.dumps({"choices": [{"message": {
            "audio": {"data": base64.b64encode(AUDIO).decode(), "format": "mp3"}}}]})
        seen = {}

        def fake(req, timeout=None):
            seen["url"] = req.full_url
            seen["body"] = json.loads(req.data)
            return _Blob(reply.encode())

        with mock.patch.multiple(config, TTS_PROTOCOL="openai_chat",
                                 TTS_BASE_URL="https://plan.example/compatible-mode/v1"), \
                mock.patch("urllib.request.urlopen", fake):
            r = tts.synthesize(self.conn, "央行宣布降息")
            digest = tts.digest_of("央行宣布降息")
            clip = tts.audio(self.conn, digest)
        self.assertTrue(r["ok"])
        self.assertEqual(seen["url"],
                         "https://plan.example/compatible-mode/v1/chat/completions")
        self.assertEqual(seen["body"]["modalities"], ["audio"])
        self.assertEqual(clip["bytes"], AUDIO)
        self.assertEqual(clip["mime"], "audio/mpeg")

    def test_openai_chat_protocol_falls_back_to_url_form(self):
        reply = json.dumps({"choices": [{"message": {
            "audio": {"url": "https://cdn.example/x.mp3"}}}]})

        def fake(req, timeout=None):
            url = req if isinstance(req, str) else req.full_url
            return _Blob(AUDIO if url.endswith(".mp3") else reply.encode())

        with mock.patch.object(config, "TTS_PROTOCOL", "openai_chat"), \
                mock.patch("urllib.request.urlopen", fake):
            self.assertTrue(tts.synthesize(self.conn, "换一条新闻")["ok"])

    def test_unparsable_upstream_reply_degrades(self):
        """上游 200 但形状不对（模型列了没真开就是这样）也必须是 ok=false，不能抛。"""
        with mock.patch.object(config, "TTS_PROTOCOL", "openai_chat"), \
                mock.patch("urllib.request.urlopen",
                           lambda *a, **k: _Blob(b'{"choices":[{"message":{}}]}')):
            r = tts.synthesize(self.conn, "上游形状不对的一条")
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "upstream")
        self.assertEqual(tts.usage(self.conn)["used_items"], 0)

    def test_format_is_part_of_the_cache_key(self):
        """mp3 换成 wav 后不能命中旧缓存，否则 mime 和字节对不上。"""
        with mock.patch.object(config, "TTS_FORMAT", "mp3"):
            a = tts.digest_of("同一段话")
        with mock.patch.object(config, "TTS_FORMAT", "wav"):
            b = tts.digest_of("同一段话")
        self.assertNotEqual(a, b)

    def test_audio_lookup_rejects_malformed_hashes(self):
        """/api/tts/audio 的 h 参数直接来自 query string，先挡住非法形状。"""
        for bad in ("", "x", "../../etc/passwd", "a" * 63, "a" * 65, "-" * 64):
            self.assertIsNone(tts.audio(self.conn, bad), bad)


if __name__ == "__main__":
    unittest.main()
