"""正文预览抽取：段数/字数上限、噪音过滤、失败降级。"""
import sqlite3
import time
import unittest

from newsdesk import article, store


ARTICLE_PAGE = """
<html><body>
<nav><p>首页 要闻 财经 科技</p></nav>
<div class="wrap">
  <article>
    <p>中新网 北京9月7日电 记者从有关部门了解到，某项目已完成阶段性验收，累计产值超过四万亿元。</p>
    <p>项目负责人介绍，下一阶段将扩大试点范围，并同步开展第三方评估工作，预计明年上半年公布结果。</p>
    <p>短</p>
    <p>业内人士认为，该结果仍需更多独立机构复核后才能推广，目前不宜过度解读单一批次数据。</p>
    <p>责任编辑：张三</p>
    <p>新华社记者 高静 摄</p>
    <p>第五段正文，按上限不应出现在预览里，用来验证段数截断确实生效了。</p>
  </article>
</div>
<footer><p>版权声明：未经授权不得转载本站任何内容，违者将依法追究责任。</p></footer>
</body></html>
"""

SCRIPT_TEMPLATED = """
<html><body>
<script>
  var tpl = '<p>央视网消息：这条正文被塞进了脚本模板字符串里，剥掉 script 之后一段都不剩。</p>'
          + '<p>兜底链路必须能从原始 HTML 里把它捞回来，否则这类站点永远没有正文预览。</p>';
</script>
</body></html>
"""


class TestParagraphExtraction(unittest.TestCase):
    def test_keeps_body_drops_chrome(self):
        paras = article.paragraphs(ARTICLE_PAGE)
        self.assertIn("累计产值超过四万亿元", paras[0])
        joined = "\n".join(paras)
        self.assertNotIn("首页 要闻", joined)      # 导航
        self.assertNotIn("责任编辑", joined)        # 编辑署名
        self.assertNotIn("高静 摄", joined)         # 图片说明
        self.assertNotIn("版权声明", joined)        # 页脚声明
        self.assertNotIn("短", paras[2])           # 过短段落

    def test_paragraph_limit(self):
        text = article.preview(article.paragraphs(ARTICLE_PAGE),
                               max_paragraphs=2, max_chars=900)
        self.assertEqual(len(text.split("\n\n")), 2)
        self.assertNotIn("第五段正文", text)

    def test_default_limits_cap_output(self):
        text = article.extract(ARTICLE_PAGE)
        self.assertLessEqual(len(text.split("\n\n")), 4)
        self.assertLessEqual(len(text), 900)

    def test_char_limit_trims_at_punctuation(self):
        text = article.extract(ARTICLE_PAGE, max_paragraphs=4, max_chars=120)
        self.assertLessEqual(len(text), 121)        # 120 + 省略号
        self.assertTrue(text.endswith("…"))

    def test_min_preview_chars_rejects_thin_result(self):
        thin = "<html><body><p>这一段刚过段落下限但整体太短了吧</p></body></html>"
        self.assertEqual(article.extract(thin), "")

    def test_recovers_body_from_script_template(self):
        """剥掉 script 后无正文时回到原始 HTML 兜底（央视等站点）。"""
        paras = article.paragraphs(SCRIPT_TEMPLATED)
        self.assertTrue(paras)
        self.assertIn("脚本模板字符串", paras[0])

    def test_empty_html_never_raises(self):
        self.assertEqual(article.paragraphs(""), [])
        self.assertEqual(article.extract("<html></html>"), "")

    def test_bad_url_returns_error_state(self):
        state, text = article.fetch_preview("javascript:alert(1)")
        self.assertEqual((state, text), ("error", ""))


class TestBodyStorage(unittest.TestCase):
    def setUp(self):
        self.now = int(time.time())
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        store.init(self.conn)
        self.conn.executemany(
            "INSERT INTO items(id,source_id,source_name,tier,grp,title,url,"
            "published_ts,cluster_id) VALUES(?,?,?,?,?,?,?,?,?)",
            [("i1", "s1", "S1", 1, "g1", "头条稿", "https://a.example/1", self.now, "c1"),
             ("i2", "s2", "S2", 2, "g2", "跟进稿", "https://b.example/2", self.now, "c1"),
             ("i3", "s3", "S3", 1, "g3", "低排名头条", "https://c.example/3", self.now, "c2")])
        self.conn.executemany(
            "INSERT INTO clusters(id,headline,url,headline_src,last_ts,rank) "
            "VALUES(?,?,?,?,?,?)",
            [("c1", "事件一", "https://a.example/1", "S1", self.now, 9.0),
             ("c2", "事件二", "https://c.example/3", "S3", self.now, 1.0)])
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_only_lead_items_by_rank(self):
        rows = store.items_needing_body(self.conn, 5)
        self.assertEqual([r["id"] for r in rows], ["i1", "i3"])   # 不含跟进稿 i2

    def test_lead_follows_detail_page_not_cluster_url(self):
        """详情页按 headline_src 取领头稿，配额就必须花在同一个稿件上。

        回归：曾经按 `items.url = clusters.url` 选，结果 79 篇抽取成功只覆盖
        50 个事件——剩下 29 篇抓的是没人展示的稿件。
        """
        self.conn.execute("UPDATE clusters SET headline_src='S2' WHERE id='c1'")
        self.conn.commit()
        rows = store.items_needing_body(self.conn, 5)
        self.assertEqual([r["id"] for r in rows], ["i2", "i3"])

    def test_thin_summary_leads_outrank_higher_rank(self):
        """摘要短的优先抽：那些页面没有摘要可退，抽不到正文就是开天窗。

        联合早报等 HTML 类源 summary 恒为空，纯按 rank 排会被中文网媒挤出配额。
        """
        self.conn.execute("UPDATE items SET summary=? WHERE id='i1'", ("长摘要" * 60,))
        self.conn.commit()
        rows = store.items_needing_body(self.conn, 1)
        self.assertEqual([r["id"] for r in rows], ["i3"])   # rank 1.0 但摘要为空

    def test_window_excludes_old_clusters(self):
        self.conn.execute("UPDATE clusters SET last_ts=? WHERE id='c1'",
                          (self.now - 30 * 24 * 3600,))
        self.conn.commit()
        self.assertEqual([r["id"] for r in store.items_needing_body(self.conn, 5)], ["i3"])

    def test_skip_sources_excluded(self):
        rows = store.items_needing_body(self.conn, 5, skip_sources={"s1"})
        self.assertEqual([r["id"] for r in rows], ["i3"])

    def test_failed_extraction_is_not_retried(self):
        store.save_item_bodies(self.conn, [("i1", "empty", ""), ("i3", "ok", "正文")])
        self.assertEqual(store.items_needing_body(self.conn, 5), [])
        row = self.conn.execute("SELECT * FROM items WHERE id='i1'").fetchone()
        self.assertEqual(row["body_state"], "empty")
        self.assertIsNone(row["body"])
        stats = store.body_preview_stats(self.conn, 0)
        self.assertEqual((stats["attempted"], stats["ok"]), (2, 1))


if __name__ == "__main__":
    unittest.main()
