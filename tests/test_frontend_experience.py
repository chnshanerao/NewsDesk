import json
import unittest
from pathlib import Path

from newsdesk.fetch import parse_html


ROOT = Path(__file__).resolve().parents[1]


class FrontendExperienceTests(unittest.TestCase):
    def test_home_has_welcome_tour_and_no_report_tab(self):
        html = (ROOT / "web" / "index.html").read_text()
        script = (ROOT / "web" / "app.js").read_text()
        self.assertNotIn('href="/report.html"', html)
        self.assertIn('id="btn-about"', html)
        self.assertIn("为什么有 NEWSDESK", script)
        self.assertIn("第一次使用，只记住三点", script)
        self.assertIn("阅读原始新闻", script)
        self.assertIn("最多 900 字", script)
        # 上手按钮的 tooltip 承诺了快捷键，弹窗里就必须真的有一份。
        self.assertIn('title="三步上手与快捷键"', html)
        self.assertIn("键盘快捷键", script)
        self.assertIn("<kbd>Ctrl K</kbd>", script)

    def test_body_preview_prefers_extracted_article(self):
        script = (ROOT / "web" / "app.js").read_text()
        self.assertIn("lead?.body||lead?.summary", script.replace(" ", ""))
        self.assertIn("展示${fromBody?\"原文正文\":\"入库摘要\"}的前", script)

    def test_global_wire_sources_enabled(self):
        """联合早报之外还要有独立于中国大陆媒体集团的通讯社。"""
        registry = json.loads((ROOT / "sources.json").read_text())
        by_id = {s["id"]: s for s in registry["sources"]}
        for sid in ("zaobao_realtime", "yonhap_cn", "kyodo_cn", "ansa_en"):
            self.assertIn(sid, by_id, sid)
            self.assertTrue(by_id[sid].get("enabled"), f"{sid} 应启用")
        owners = {by_id[s]["owner"] for s in ("zaobao_realtime", "yonhap_cn", "kyodo_cn")}
        self.assertEqual(len(owners), 3, "不同通讯社不能共用 owner，否则会被误算成独立印证")

    def test_zaobao_html_source_parses_realistic_links(self):
        registry = json.loads((ROOT / "sources.json").read_text())
        source = next(item for item in registry["sources"]
                      if item["id"] == "zaobao_realtime")
        body = ('<a href="/news/china/story20260907-9635867" '
                'title="广西南宁现场：当城市和政策成为传销话术的剧本素材">新闻</a>')
        items = parse_html(body, source)
        self.assertEqual(len(items), 1)
        self.assertIsNotNone(items[0]["published_ts"])
        self.assertEqual(items[0]["url"],
                         "https://www.zaobao.com.sg/news/china/story20260907-9635867")


if __name__ == "__main__":
    unittest.main()
