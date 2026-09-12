import time
import unittest

from newsdesk import evidence


def item(i, title, group, role="reporting", ts=1):
    return {"id": i, "title": title, "summary": "", "grp": group,
            "source_id": group, "source_name": group, "source_role": role,
            "tier": 1, "published_ts": ts, "url": "https://example/" + i}


class EvidenceTests(unittest.TestCase):
    def test_independent_claim_has_citations(self):
        rows = [item("a", "美联储宣布降息25个基点", "paper-a"),
                item("b", "美联储宣布降息25基点", "paper-b")]
        claims = evidence.claims(rows)
        self.assertEqual(claims[0]["status"], "independently_reported")
        self.assertEqual(claims[0]["independent_groups"], 2)
        self.assertEqual(len(claims[0]["evidence"]), 2)
        ref = claims[0]["evidence"][0]
        self.assertEqual(ref["quote_field"], "title")
        self.assertEqual(ref["quote"], rows[0]["title"])
        self.assertEqual(rows[0]["title"][ref["quote_start"]:ref["quote_end"]], ref["quote"])
        self.assertEqual(len(ref["quote_hash"]), 16)
        self.assertEqual(ref["relation"], "support")

    def test_official_single_is_statement(self):
        row = item("a", "美联储发布利率决定", "fed", "official")
        self.assertEqual(evidence.claims([row])[0]["status"], "official_statement")

    def test_aggregator_is_not_independent_confirmation(self):
        rows = [item("a", "公司发布季度业绩", "paper", "reporting"),
                item("b", "公司发布季度业绩", "portal", "aggregator")]
        claim = evidence.claims(rows)[0]
        self.assertEqual(claim["status"], "single_report")
        self.assertEqual(claim["independent_groups"], 1)

    def test_opposite_directions_are_flagged(self):
        rows = [item("a", "公司收入增长20%", "a"),
                item("b", "公司收入下降20%", "b")]
        result = evidence.contradictions(rows)
        self.assertEqual(result[0]["reason"], "方向性表述相反")

    def test_claim_card_labels_support_refute_and_unknown(self):
        rows = [item("a", "公司收入增长20%", "a"),
                item("b", "公司收入下降20%", "b"),
                item("c", "公司收入可能增长20%", "c")]
        claim = evidence.claims(rows)[0]
        self.assertEqual(claim["status"], "disputed")
        self.assertGreaterEqual(claim["relation_counts"]["support"], 1)
        self.assertGreaterEqual(claim["relation_counts"]["refute"], 1)
        self.assertGreaterEqual(claim["relation_counts"]["unknown"], 1)
        self.assertTrue(claim["unresolved"])

    def _rel(self, claim, title):
        return evidence._relation(claim, {"title": title, "summary": ""})

    def test_different_period_is_not_a_contradiction(self):
        """不同期次的取值不同是两件事，不是一件事的两种说法。"""
        for claim, title in (
                ("2025年1月份工业生产者出厂价格同比下降2.3%",
                 "2025年5月份工业生产者出厂价格同比下降3.3%"),
                ("2024年前三季度营业收入增长5.9%", "2024年全年营业收入增长6.0%"),
                ("记账式附息国债第五十七期发行", "记账式附息国债第五十八期发行"),
                ("因暴雨周三全市停课", "因暴雨周四全市停课")):
            self.assertEqual(self._rel(claim, title), "unknown", claim)

    def test_cross_subject_conflict_is_unknown_not_support(self):
        """同月一涨一跌但是两个指数：既不是矛盾，更不是互相印证。"""
        self.assertEqual(
            self._rel("2024年8月份居民消费价格同比上涨0.6%",
                      "2024年8月份，全国工业生产者出厂价格同比下降1.8%"), "unknown")

    def test_opposite_event_labels_are_a_contradiction(self):
        """降息/加息不在 OPPOSITES 表层词里，靠事件标签判 —— 数字相同时唯一的线索。"""
        self.assertEqual(self._rel("美联储2026年9月降息25个基点",
                                   "美联储2026年9月加息25个基点"), "refute")

    def test_same_subject_same_period_disjoint_values_are_a_contradiction(self):
        self.assertEqual(self._rel("2024年前三季度营业收入增长5.9%",
                                   "2024年前三季度营业收入增长7.2%"), "refute")
        # 同指标同月、取值不同 —— 这才是数字类矛盾该抓的那一类
        self.assertEqual(self._rel("2024年8月份居民消费价格同比上涨0.6%",
                                   "国家统计局：8月份居民消费价格同比上涨0.8%"), "refute")

    def test_cumulative_range_is_a_different_period(self):
        """「1—6月份」是半年累计，不是 6 月。国家统计局的主力口径。"""
        self.assertEqual(self._rel("2024年1—6月份全国固定资产投资增长3.9%",
                                   "2024年全国固定资产投资增长3.2%"), "unknown")

    def test_quarter_and_half_year_are_different_windows(self):
        """一季度(1-3月)与上半年(1-6月)是两个窗口，不能因为种类不同就放过。"""
        self.assertEqual(
            self._rel("2026年一季度全国规模以上文化及相关产业企业营业收入增长6.4%",
                      "2026年上半年全国规模以上文化及相关产业企业营业收入增长4.6%"), "unknown")

    def test_period_is_read_from_the_title_not_only_the_quote(self):
        """期次通常写在标题里，被引的那一句往往只有取值。"""
        self.assertEqual(evidence._relation(
            "2026年4月份工业生产者出厂价格同比上涨2.8% 环比上涨1.7%",
            {"title": "2026年8月份工业生产者出厂价格同比上涨3.8%",
             "summary": "工业生产者出厂价格指数（PPI）环比由上月下降0.7%转为上涨0.4%，"
                        "同比涨幅扩大至3.8%。"}), "unknown")

    def test_version_numbers_do_not_found_a_contradiction(self):
        """版本号/型号是名字的一部分，不是取值。"""
        for claim, title in (
                ("稳定版发布进入最后倒计时，苹果 iOS / iPadOS 27 RC 推送",
                 "苹果 iOS / iPadOS 26.7 正式版发布"),
                ("华为 HarmonyOS 7 正式发布", "HMD Asha 305 手机正式发布"),
                ("DeepSeek V4 Pro 0813 vs Claude Fable 5 on DeepSWE: Cost and Routing",
                 "Kimi K3 vs GPT-5.6 Sol on DeepSWE: Cost and Routing")):
            self.assertNotEqual(self._rel(claim, title), "refute", claim)

    def test_fiscal_periods_are_recognised(self):
        """「2027 财年第一财季」不匹配自然年/季度的写法，漏掉就等于两侧期次全空。"""
        self.assertEqual(self._rel(
            "甲骨文 2027 财年第一财季归母净利润 46.79 亿美元，同比增长 60%",
            "Adobe 2026 财年第三财季归母净利润 18.27 亿美元，同比增长 3.1%"), "unknown")

    def test_omitted_year_comes_from_the_publication_date(self):
        """标题按新闻惯例省略年份时，年份取自发布时间，不再按「可推断为同年」处理。"""
        stamp = int(time.mktime(time.strptime("2026-09-09", "%Y-%m-%d")))
        published = {"title": "国家统计局：8月份居民消费价格同比上涨0.8%",
                     "summary": "", "published_ts": stamp}
        self.assertEqual(evidence._relation(
            "2024年8月份居民消费价格同比上涨0.6%", published), "unknown")
        # 同年同月同指标、取值不同 —— 补年份不能把真矛盾一起挡掉
        self.assertEqual(evidence._relation(
            "2026年8月份居民消费价格同比上涨0.6%", published), "refute")

    def test_abbreviated_magnitudes_are_the_same_value(self):
        """「21 billion」与「21 bn」是同一个数，不该因写法不同判成矛盾。"""
        self.assertEqual(self._rel(
            "French AI startup Mistral valued at over 21 billion euros after funding",
            "French AI firm Mistral valued at 21 bn euros after funding"), "support")

    def test_timeline_is_chronological(self):
        rows = [item("b", "后续", "b", ts=20), item("a", "首发", "a", ts=10)]
        self.assertEqual([x["title"] for x in evidence.timeline(rows)], ["首发", "后续"])


if __name__ == "__main__":
    unittest.main()
