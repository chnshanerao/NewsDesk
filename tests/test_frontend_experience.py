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

    def test_two_axes_are_explained_and_never_merged(self):
        """存疑度和可信度必须在页面上被讲成两个轴，否则用户会当成同一套分级。"""
        html = (ROOT / "web" / "index.html").read_text()
        script = (ROOT / "web" / "app.js").read_text()
        self.assertIn("存疑标注", html)
        self.assertIn("两个独立的轴", html)
        # 全部新闻都展示、存疑的只标不藏 —— 这是产品承诺，不能悄悄改成过滤。
        self.assertIn("存疑的也不隐藏", html)
        self.assertIn("doubt_detail", script)
        self.assertIn("存疑判定", script)

    def test_source_desk_explains_not_applicable_and_lang_bias(self):
        """治理台要说清『不适用』不是零分，以及低分语种是我方欠工。"""
        script = (ROOT / "web" / "app.js").read_text()
        self.assertIn("not_applicable", script)
        self.assertIn("无从抢首发", script)
        self.assertIn("别拿我们的欠工去降别人的档", script)
        # 定档门槛不能在前端写死一个数字，必须由后端带过来。
        self.assertIn("min_items_for_core", script)

    def test_focus_only_marks_curated_sources(self):
        """focus 缺省必须是 standard：126 个源里只有人工定过的才带这个字段。"""
        registry = json.loads((ROOT / "sources.json").read_text())
        values = {s["id"]: s["focus"] for s in registry["sources"] if "focus" in s}
        self.assertTrue(values, "至少应有人工定档过的源")
        self.assertTrue(set(values.values()) <= {"core", "standard", "probation"}, values)

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
