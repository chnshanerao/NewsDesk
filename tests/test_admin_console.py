"""管理后台的路由与授权边界。

前端拆成两张页面之后，最容易悄悄坏掉的是服务端这一侧：/admin 这几个入口有没有真的
指到后台壳、朗读额度接口有没有误加令牌（读者功能不能要令牌）、翻译账单有没有漏加令牌
（那是运营数据）。这三件事各自都是一行代码，也各自都能一行写错。
"""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from newsdesk import config, server, store


class AdminConsoleRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_patch = mock.patch.object(config, "DB_PATH",
                                          Path(self.tmp.name) / "test.db")
        self.db_patch.start()
        conn = store.connect()
        store.init(conn)
        conn.close()
        self.token_patch = mock.patch.object(config, "WRITE_TOKEN", "test-admin-token")
        self.token_patch.start()
        handler = server.make_handler({"sources": [], "tiers": {}},
                                      {"min_relevance": 0.12, "min_credibility": 40},
                                      False)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        self.token_patch.stop()
        self.db_patch.stop()
        self.tmp.cleanup()

    def _get(self, path, token=None):
        port = self.httpd.server_address[1]
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
        if token:
            req.add_header("X-Newsdesk-Token", token)
        try:
            with urllib.request.urlopen(req, timeout=3) as r:
                return r.status, r.read().decode("utf-8"), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8"), dict(e.headers)

    def test_admin_console_is_reachable_by_url_only(self):
        """/admin、/admin.html、/console 都进后台壳；壳本身不含数据，进去还要过令牌。"""
        for path in ("/admin", "/admin.html", "/console"):
            code, body, _ = self._get(path)
            self.assertEqual(code, 200, path)
            self.assertIn("管理员入口", body, path)
            self.assertIn('src="/static/admin.js"', body, path)
            # 壳里不能预置任何治理数据 —— 未登录就拿到数据等于闸门白设
            self.assertNotIn("信源治理台</h2>", body, path)
        # 后台不该被搜索引擎收录（不是安全措施，是别让管理入口出现在搜索结果里）
        self.assertIn('content="noindex, nofollow"', self._get("/admin")[1])
        for asset in ("/static/admin.js", "/static/admin.css"):
            self.assertEqual(self._get(asset)[0], 200, asset)

    def test_reader_page_carries_no_admin_controls(self):
        """读者页由服务端发出去的那份 HTML 里就不该有管理控件。"""
        code, body, _ = self._get("/")
        self.assertEqual(code, 200)
        for gone in ('id="btn-refresh"', 'id="btn-sources"', 'id="btn-lock"',
                     "admin-only"):
            self.assertNotIn(gone, body, gone)

    def test_verify_gate_answers_only_yes_or_no(self):
        code, body, _ = self._get("/api/admin/verify")
        self.assertEqual(code, 401)
        code, body, _ = self._get("/api/admin/verify", token="wrong")
        self.assertEqual(code, 401)
        code, body, _ = self._get("/api/admin/verify", token="test-admin-token")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), {"ok": True})   # 只回对/不对，不带数据

    def test_tts_usage_is_public_and_leaks_no_credentials(self):
        """朗读额度是读者功能的一部分（决定走云端还是浏览器语音），必须匿名可读。"""
        code, body, _ = self._get("/api/tts/usage")
        self.assertEqual(code, 200)
        d = json.loads(body)
        self.assertEqual(d["daily_items"], 100)        # 用户定的：每天不超过 100 条
        self.assertIn("remaining_chars", d)
        self.assertNotIn("api_key", body.lower())
        self.assertNotIn("key", set(d))

    def test_translate_usage_needs_the_admin_token(self):
        """翻译账单是运营数据，不对匿名开放；表还没建时回空而不是 500。"""
        self.assertEqual(self._get("/api/translate-usage")[0], 401)
        code, body, _ = self._get("/api/translate-usage?days=7",
                                  token="test-admin-token")
        self.assertEqual(code, 200)
        d = json.loads(body)
        self.assertEqual(d["days"], 7)
        self.assertEqual(d["rows"], [])
        self.assertEqual(d["total"]["tokens"], 0)

    def test_tts_audio_404s_on_unknown_or_malformed_hash(self):
        for h in ("", "deadbeef", "../../newsdesk/config.py", "a" * 64):
            self.assertEqual(self._get(f"/api/tts/audio?h={h}")[0], 404, h)

    def test_tts_post_is_a_reader_route_but_still_same_origin_only(self):
        """POST /api/tts 不要管理令牌（读者要用），但仍受同源写保护和体积上限约束。"""
        port = self.httpd.server_address[1]
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/tts", method="POST",
            data=json.dumps({"text": "一条测试新闻"}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as r:
            d = json.loads(r.read().decode("utf-8"))
        # 本机没开云端 TTS，所以是 ok=False + disabled —— 但不是 401/403/500
        self.assertFalse(d["ok"])
        self.assertEqual(d["reason"], "disabled")

        cross = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/tts", method="POST", data=b"{}",
            headers={"Content-Type": "application/json",
                     "Origin": "https://evil.example"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(cross, timeout=3)
        self.assertEqual(ctx.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
