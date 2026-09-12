"""存疑度必须是独立于可信度的第二个轴，而不是它的反面。"""
import unittest

from newsdesk import credibility, doubt


def _cluster(headline, bodies=(), timing_note="", llm=None):
    score, detail = credibility.content_quality(headline, list(bodies))
    return {"breakdown": {"content": {"score": score, **detail},
                          "timing": {"note": timing_note}},
            "llm": llm}


def _item(source_id="a", grp=None, role="reporting", name=None):
    return {"source_id": source_id, "grp": grp or source_id, "src_role": role,
            "source_name": name or source_id, "tier": 1}


class DoubtAxisTests(unittest.TestCase):
    def test_official_single_source_is_not_suspicious(self):
        """一条只有统计局一家的公报：可信度里印证项拿不到分，但它并不可疑。

        这是两个轴必须分开的核心理由。如果存疑度是可信度的反面，这条会被
        错误地标成存疑，而它恰恰是最不该被质疑的一类。
        """
        cluster = _cluster("2026年5月份居民消费价格同比上涨0.3%",
                           ["国家统计局今日发布数据显示，5月CPI同比上涨0.3%。"])
        result = doubt.assess(cluster, [_item("stats_gov", role="official")])
        self.assertEqual(result["doubt_code"], "CLEAR")
        self.assertEqual(result["doubt"], 0.0)

    def test_widely_republished_hype_is_suspicious_despite_corroboration(self):
        """被多家转载、证据『齐全』，但通篇拉抬情绪 —— 可信度不低，存疑度必须高。"""
        cluster = _cluster("震惊！某股暴涨内幕曝光，网传知情人士称还要翻倍！！")
        items = [_item(f"portal{i}", grp=f"g{i}", role="reporting") for i in range(3)]
        result = doubt.assess(cluster, items)
        self.assertGreaterEqual(result["doubt"], 50)
        self.assertEqual(result["doubt_code"], "SUSPECT")
        reasons = " ".join(r["reason"] for r in result["doubt_detail"]["reasons"])
        self.assertIn("标题党", reasons)
        self.assertIn("未证实措辞", reasons)
        self.assertIn("情绪化", reasons)

    def test_wording_stack_is_a_signal_of_its_own(self):
        """单个『暴涨』可能只是陈述事实；四类措辞同时出现就是成套话术。"""
        one = _cluster("某股今日暴涨7.2%")
        many = _cluster("震惊！某股暴涨内幕曝光，网传知情人士称还要翻倍！！")
        self.assertNotIn("成套话术", " ".join(
            r["reason"] for r in doubt.assess(one, [_item()])["doubt_detail"]["reasons"]))
        self.assertIn("成套话术", " ".join(
            r["reason"] for r in doubt.assess(many, [_item()])["doubt_detail"]["reasons"]))

    def test_refuted_claim_alone_stops_short_of_top_band(self):
        """关系分类器有过整批假阳性，一次误判不该单独把新闻打成高度存疑。"""
        result = doubt.assess(_cluster("某项指标同比下降2.3%"), [_item()],
                              claims=[{"status": "disputed"}])
        self.assertEqual(result["doubt_code"], "QUESTIONABLE")
        self.assertLess(doubt.W_REFUTED, 50)

    def test_refuted_claim_dominates(self):
        cluster = _cluster("某项指标同比下降2.3%")
        result = doubt.assess(cluster, [_item()],
                             claims=[{"status": "disputed"}, {"status": "single_report"}])
        self.assertGreaterEqual(result["doubt"], doubt.W_REFUTED)
        self.assertIn("相反", result["doubt_detail"]["reasons"][0]["reason"])

    def test_attributed_rumor_is_penalised_less_than_bare_rumor(self):
        """『据新华社，疑似……』比『网传疑似……』可疑度低 —— 有出处能核。"""
        bare = doubt.assess(_cluster("网传疑似某公司将裁员"), [_item()])
        cited = doubt.assess(
            _cluster("据新华社报道，疑似某公司将裁员",
                     ["新华社记者获悉，该公司已披露相关文件。"]), [_item()])
        self.assertLess(cited["doubt"], bare["doubt"])

    def test_same_group_republication_is_not_corroboration(self):
        cluster = _cluster("某地发布新政策文件")
        same = [_item(f"ch{i}", grp="chinanews") for i in range(4)]
        result = doubt.assess(cluster, same)
        reasons = " ".join(r["reason"] for r in result["doubt_detail"]["reasons"])
        self.assertIn("同一集团", reasons)

    def test_aggregator_only_sourcing_is_flagged(self):
        cluster = _cluster("某产品据说要发布了")
        result = doubt.assess(cluster, [_item("agg", role="aggregator"),
                                        _item("hn", grp="hn", role="community")])
        reasons = " ".join(r["reason"] for r in result["doubt_detail"]["reasons"])
        self.assertIn("无采编或官方一次源", reasons)

    def test_degraded_source_health_contributes(self):
        cluster = _cluster("某机构发布了年度报告")
        result = doubt.assess(cluster, [_item("zombie", name="僵尸源")],
                              health={"zombie": {"verdict": "degraded"}})
        reasons = " ".join(r["reason"] for r in result["doubt_detail"]["reasons"])
        self.assertIn("健康度已降级", reasons)

    def test_only_unhedged_recycling_note_costs_points(self):
        """追踪报道跨一两天是正常的，不能拿一句『可能是追踪或回炒』扣分。"""
        hedged = _cluster("某地持续推进某项工程", timing_note="报道跨度 30h，可能是追踪或回炒")
        stale = _cluster("某地持续推进某项工程", timing_note="报道跨度 5.2 天，疑似旧闻回炒")
        self.assertEqual(doubt.assess(hedged, [_item()])["doubt"], 0.0)
        self.assertEqual(doubt.assess(stale, [_item()])["doubt"],
                         float(doubt.W_STALE_RECYCLE))

    def test_future_timestamp_outweighs_recycling(self):
        cluster = _cluster("某公司发布新品", timing_note="发布时间在未来，元数据可疑")
        self.assertEqual(doubt.assess(cluster, [_item()])["doubt"],
                         float(doubt.W_FUTURE_TS))

    def test_llm_red_flags_feed_the_score(self):
        cluster = _cluster("某公司宣布重大进展",
                           llm={"red_flags": ["数据来源不明", "缺少第三方验证"]})
        result = doubt.assess(cluster, [_item()])
        self.assertGreaterEqual(result["doubt"], doubt.W_LLM_FLAG_EACH * 2)

    def test_every_point_names_its_reason(self):
        """存疑度不能出现『有分但说不出为什么』。"""
        cluster = _cluster("震惊！网传某事爆料内幕，暴涨！！")
        result = doubt.assess(cluster, [_item("agg", role="aggregator")])
        self.assertTrue(result["doubt_detail"]["reasons"])
        self.assertEqual(result["doubt"],
                         min(100, sum(r["points"] for r in
                                      result["doubt_detail"]["reasons"])))

    def test_clean_item_still_reports_what_was_checked(self):
        """0 分要能区分『查过没命中』和『没算』。"""
        result = doubt.assess(_cluster("国务院印发关于某领域发展的指导意见"),
                              [_item("gov", role="official")])
        self.assertEqual(result["doubt"], 0.0)
        self.assertTrue(result["doubt_detail"]["checked"])

    def test_wordlists_are_shared_with_content_quality(self):
        """两处用同一份词表，否则页面上会出现『内容质量说有、存疑度说没有』。"""
        self.assertIs(doubt.WORDLISTS["clickbait"], credibility.CLICKBAIT)
        self.assertIs(doubt.WORDLISTS["rumor"], credibility.RUMOR)
        self.assertIs(doubt.WORDLISTS["hype"], credibility.HYPE_MOVE)

    def test_doubt_is_not_merely_inverse_of_credibility(self):
        """两个轴要真的分得开：造一对『可信度同向、存疑度反向』的例子。"""
        official = _cluster("国家统计局发布5月份国民经济运行情况数据显示各项指标平稳")
        hype = _cluster("震惊！网传内幕爆料，该股暴涨！！")
        self.assertEqual(doubt.assess(official, [_item("s", role="official")])["doubt"], 0.0)
        self.assertGreater(doubt.assess(hype, [_item("s", role="official")])["doubt"], 40)


class DoubtSummaryTests(unittest.TestCase):
    def test_summary_counts_bands(self):
        rows = [{"doubt": 60, "doubt_code": "SUSPECT"},
                {"doubt": 30, "doubt_code": "QUESTIONABLE"},
                {"doubt": 0, "doubt_code": "CLEAR"}]
        out = doubt.summarize(rows)
        self.assertEqual(out["n"], 3)
        self.assertEqual(out["flagged"], 2)
        self.assertEqual(out["bands"]["SUSPECT"], 1)
        self.assertEqual(out["mean"], 30.0)


if __name__ == "__main__":
    unittest.main()
