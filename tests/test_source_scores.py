"""信源治理评分：产出量只做及格线，不做加分项。"""
import tempfile
import unittest
from pathlib import Path

from newsdesk import pipeline, source_scores, store
from newsdesk.normalize import now_ts

PROFILE = {"min_relevance": 0.28}


def _reg(*sources):
    return {"sources": [{"id": s[0], "name": s[0], "tier": 1, "group": s[0],
                         "enabled": True, "source_role": "reporting",
                         **(s[1] if len(s) > 1 else {})} for s in sources]}


def _seed(conn, source_id, n, *, cluster_prefix="c", cred=80, relevance=.5,
          doubt=0, n_groups=2, lead=True, group=None, offset=0, cluster_items=2):
    """给一个源灌 n 条稿件。

    n_groups 默认 2 = 事件里有别的集团也在报（有竞争），首发项才有分母；
    传 1 表示没有跨集团的对手，用来验证「没人跟不算独家」。
    """
    now = now_ts()
    for i in range(n):
        cid = f"{cluster_prefix}{offset + i}"
        conn.execute(
            "INSERT OR IGNORE INTO clusters(id,headline,first_ts,last_ts,n_items,"
            "n_groups,cred,relevance,doubt) VALUES(?,?,?,?,?,?,?,?,?)",
            (cid, f"事件{cid}", now - 600, now - 600, cluster_items, n_groups,
             cred, relevance, doubt))
        conn.execute(
            "INSERT INTO items(id,source_id,source_name,tier,grp,title,url,"
            "published_ts,fetched_ts,cluster_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (f"{source_id}-{i}", source_id, source_id, 1, group or source_id,
             f"稿件{i}", f"https://example/{source_id}/{i}",
             now - 600 if lead else now - 300, now - 600, cid))
    conn.commit()


class SupplyDimensionTests(unittest.TestCase):
    def test_flooding_source_does_not_outscore_a_steady_one(self):
        """核心防线：一天刷 500 条的源，不能因为量大就被评成『重点关注』。

        本库 24 小时内单一信源已占 21.6% 产出。若供稿项线性给分，治理结论会
        退化成「谁刷得多谁重要」—— 而集中度过高本身就是要治的病。
        """
        with tempfile.TemporaryDirectory() as td:
            conn = store.connect(Path(td) / "s.db"); store.init(conn)
            _seed(conn, "flood", 500, cluster_prefix="f")
            _seed(conn, "steady", 20, cluster_prefix="s")
            cards = {c["source_id"]: c
                     for c in source_scores.compute(conn, _reg(("flood",), ("steady",)),
                                                    PROFILE, window_h=24)}
            self.assertEqual(cards["flood"]["breakdown"]["supply"], 1.0)
            self.assertEqual(cards["steady"]["breakdown"]["supply"], 1.0)
            self.assertEqual(cards["flood"]["score"], cards["steady"]["score"])
            conn.close()

    def test_supply_is_log_compressed_below_the_cap(self):
        """5 条不该只拿 1/4 分 —— 少量但稳定的源不应被产出项一票压死。"""
        with tempfile.TemporaryDirectory() as td:
            conn = store.connect(Path(td) / "s.db"); store.init(conn)
            _seed(conn, "small", 5, cluster_prefix="a")
            card = source_scores.compute(conn, _reg(("small",)), PROFILE,
                                         window_h=24)[0]
            self.assertGreater(card["breakdown"]["supply"], 0.5)
            self.assertLess(card["breakdown"]["supply"], 1.0)
            conn.close()


class ContributionDimensionTests(unittest.TestCase):
    def test_same_group_crossposting_is_not_a_race(self):
        """滚动版和财经版同一秒发同一条稿，不能算成两家在赛跑。

        实测：按篇数判定「有竞争」时，最大集团的首发项被虚高到 320/378。
        """
        with tempfile.TemporaryDirectory() as td:
            conn = store.connect(Path(td) / "s.db"); store.init(conn)
            for feed in ("cn_scroll", "cn_finance"):
                _seed(conn, feed, 12, cluster_prefix="cn", group="chinanews", n_groups=1)
            reg = {"sources": [{"id": f, "name": f, "tier": 1, "group": "chinanews",
                                "enabled": True, "source_role": "reporting"}
                               for f in ("cn_scroll", "cn_finance")]}
            cards = {c["source_id"]: c for c in
                     source_scores.compute(conn, reg, PROFILE, window_h=24)}
            for card in cards.values():
                self.assertEqual(card["n_contested"], 0)
                self.assertEqual(card["breakdown"]["lead"], 0.0)
            conn.close()

    def test_follower_gets_no_lead_credit(self):
        """跟风转载的时间戳晚于事件首发时间，首发项应为 0。"""
        with tempfile.TemporaryDirectory() as td:
            conn = store.connect(Path(td) / "s.db"); store.init(conn)
            _seed(conn, "first", 10, cluster_prefix="x")
            _seed(conn, "follow", 10, cluster_prefix="x", lead=False)
            cards = {c["source_id"]: c
                     for c in source_scores.compute(conn, _reg(("first",), ("follow",)),
                                                    PROFILE, window_h=24)}
            self.assertEqual(cards["first"]["n_lead"], 10)
            self.assertEqual(cards["follow"]["n_lead"], 0)
            self.assertGreater(cards["first"]["score"], cards["follow"]["score"])
            conn.close()

    def test_solo_story_is_not_a_scoop(self):
        """没有别的集团在报的事件里当然是最早那篇 —— 那是没人跟，不是抢到独家。

        不修这条的后果实测过：一个窗口内只发 1 条的源六项里四项自动满分，
        评分 0.85 排在天天有产出的通讯社前面，治理建议直接推它进「重点关注」。
        """
        with tempfile.TemporaryDirectory() as td:
            conn = store.connect(Path(td) / "s.db"); store.init(conn)
            _seed(conn, "solo", 12, cluster_prefix="p", n_groups=1)
            _seed(conn, "raced", 12, cluster_prefix="q", n_groups=3)
            cards = {c["source_id"]: c
                     for c in source_scores.compute(conn, _reg(("solo",), ("raced",)),
                                                    PROFILE, window_h=24)}
            self.assertEqual(cards["solo"]["n_contested"], 0)
            self.assertEqual(cards["solo"]["breakdown"]["lead"], 0.0)
            self.assertEqual(cards["raced"]["breakdown"]["lead"], 1.0)
            self.assertGreater(cards["raced"]["score"], cards["solo"]["score"])
            conn.close()

    def test_tiny_sample_cannot_reach_core(self):
        """1 条稿件的比率全是噪音，样本不足不能当成优点。"""
        with tempfile.TemporaryDirectory() as td:
            conn = store.connect(Path(td) / "s.db"); store.init(conn)
            _seed(conn, "tiny", 1, cluster_prefix="t", relevance=.9, cred=95)
            card = source_scores.compute(conn, _reg(("tiny",)), PROFILE,
                                         window_h=24)[0]
            self.assertLess(card["n_items"], source_scores.MIN_ITEMS_FOR_CORE)
            self.assertEqual(card["grade"], "standard")
            self.assertIn("样本不足", card["grade_note"])
            conn.close()

    def test_uncorroborated_output_scores_lower(self):
        with tempfile.TemporaryDirectory() as td:
            conn = store.connect(Path(td) / "s.db"); store.init(conn)
            _seed(conn, "alone", 10, cluster_prefix="a", n_groups=1)
            _seed(conn, "backed", 10, cluster_prefix="b", n_groups=3)
            cards = {c["source_id"]: c
                     for c in source_scores.compute(conn, _reg(("alone",), ("backed",)),
                                                    PROFILE, window_h=24)}
            self.assertEqual(cards["alone"]["n_corroborated"], 0)
            self.assertEqual(cards["backed"]["n_corroborated"], 10)
            conn.close()

    def test_noise_counts_both_low_cred_and_low_relevance(self):
        with tempfile.TemporaryDirectory() as td:
            conn = store.connect(Path(td) / "s.db"); store.init(conn)
            _seed(conn, "weak", 5, cluster_prefix="w", cred=20)
            _seed(conn, "offtopic", 5, cluster_prefix="o", relevance=.05)
            cards = {c["source_id"]: c
                     for c in source_scores.compute(conn, _reg(("weak",), ("offtopic",)),
                                                    PROFILE, window_h=24)}
            self.assertEqual(cards["weak"]["n_noise"], 5)
            self.assertEqual(cards["offtopic"]["n_noise"], 5)
            self.assertEqual(cards["weak"]["breakdown"]["cleanliness"], 0.0)
            conn.close()

    def test_high_doubt_output_erodes_trust_dimension(self):
        with tempfile.TemporaryDirectory() as td:
            conn = store.connect(Path(td) / "s.db"); store.init(conn)
            _seed(conn, "clean", 10, cluster_prefix="c", doubt=0)
            _seed(conn, "shady", 10, cluster_prefix="d", doubt=60)
            cards = {c["source_id"]: c
                     for c in source_scores.compute(conn, _reg(("clean",), ("shady",)),
                                                    PROFILE, window_h=24)}
            self.assertEqual(cards["clean"]["breakdown"]["trust"], 1.0)
            self.assertEqual(cards["shady"]["breakdown"]["trust"], 0.0)
            conn.close()


class GradeTests(unittest.TestCase):
    def test_zero_output_is_dormant_not_bad(self):
        """抓不到的源是运维问题，不是编辑问题：单列 dormant，不当成质量差。"""
        with tempfile.TemporaryDirectory() as td:
            conn = store.connect(Path(td) / "s.db"); store.init(conn)
            card = source_scores.compute(conn, _reg(("silent",)), PROFILE,
                                         window_h=24)[0]
            self.assertEqual(card["grade"], "dormant")
            self.assertEqual(card["n_items"], 0)
            conn.close()

    def test_grade_bands(self):
        self.assertEqual(source_scores.grade_of(.80, 30)[0], "core")
        self.assertEqual(source_scores.grade_of(.65, 30)[0], "core")
        self.assertEqual(source_scores.grade_of(.50, 30)[0], "standard")
        self.assertEqual(source_scores.grade_of(.10, 30)[0], "probation")
        self.assertEqual(source_scores.grade_of(.90, 0)[0], "dormant")

    def test_small_sample_is_clamped_in_both_directions(self):
        """样本不足既不算优点也不算缺点 —— 只压高档会把低产的一次源单向推进观察期。"""
        self.assertEqual(source_scores.grade_of(.90, 3)[0], "standard")
        self.assertEqual(source_scores.grade_of(.10, 3)[0], "standard")
        self.assertIn("样本不足", source_scores.grade_of(.10, 3)[1])

    def test_primary_sources_are_not_measured_by_corroboration(self):
        """ECB 新闻稿没有『首发』可抢，也不需要别人证明『他是否这么说了』。

        实测未修正时 ecb_press / apple_newsroom / sec_tsmc 全被推进观察期 ——
        用不适用的尺子量一次源，等于结构性扣掉它 35% 的分。
        """
        with tempfile.TemporaryDirectory() as td:
            conn = store.connect(Path(td) / "s.db"); store.init(conn)
            # 同样的产出：从不被跟进、也从不抢到首发。
            for sid in ("ecb", "blogger"):
                _seed(conn, sid, 12, cluster_prefix=sid, n_groups=1, relevance=.5)
            reg = {"sources": [
                {"id": "ecb", "name": "ECB", "tier": 0, "group": "ecb",
                 "enabled": True, "source_role": "official"},
                {"id": "blogger", "name": "blogger", "tier": 2, "group": "blogger",
                 "enabled": True, "source_role": "reporting"}]}
            cards = {c["source_id"]: c for c in
                     source_scores.compute(conn, reg, PROFILE, window_h=24)}
            self.assertEqual(cards["ecb"]["not_applicable"], ["corroborated", "lead"])
            self.assertEqual(cards["blogger"]["not_applicable"], [])
            self.assertGreater(cards["ecb"]["score"], cards["blogger"]["score"])
            # 权重摊到适用项后仍然是一整份，不能凭空多出或少掉总量。
            self.assertAlmostEqual(sum(cards["ecb"]["weights"].values()), 1.0, places=6)
            self.assertNotIn("lead", cards["ecb"]["weights"])

    def test_primary_source_still_pays_for_noise_and_irrelevance(self):
        """免掉的只是不适用的两项，净度和相关性照量。"""
        with tempfile.TemporaryDirectory() as td:
            conn = store.connect(Path(td) / "s.db"); store.init(conn)
            _seed(conn, "clean_gov", 12, cluster_prefix="cg", relevance=.5)
            _seed(conn, "spam_gov", 12, cluster_prefix="sg", relevance=.02, cred=20)
            reg = {"sources": [{"id": i, "name": i, "tier": 0, "group": i,
                                "enabled": True, "source_role": "official"}
                               for i in ("clean_gov", "spam_gov")]}
            cards = {c["source_id"]: c for c in
                     source_scores.compute(conn, reg, PROFILE, window_h=24)}
            self.assertGreater(cards["clean_gov"]["score"],
                               cards["spam_gov"]["score"] + .3)

    def test_focus_falls_back_to_standard_for_unlabelled_sources(self):
        """126 个源现在都没标 focus，默认必须是 standard —— 否则排序会无声改变。"""
        self.assertEqual(source_scores.focus_of({}), "standard")
        self.assertEqual(source_scores.focus_of({"focus": "core"}), "core")
        self.assertEqual(source_scores.focus_of({"focus": "vip"}), "standard")


class GovernanceReviewTests(unittest.TestCase):
    def test_review_lists_mismatches_and_concentration(self):
        with tempfile.TemporaryDirectory() as td:
            conn = store.connect(Path(td) / "s.db"); store.init(conn)
            _seed(conn, "hot", 40, cluster_prefix="h")
            # 12 条：够得上定档门槛，probation 才是对它表现的判断而不是样本不足。
            _seed(conn, "meh", 12, cluster_prefix="m", cred=20, relevance=.05,
                  n_groups=1, lead=False)
            reg = _reg(("hot", {"focus": "probation"}), ("meh", {"focus": "core"}),
                       ("gone",))
            review = source_scores.governance_review(conn, reg, PROFILE, window_h=24)
            proposals = {p["source_id"]: p["proposed"] for p in review["proposed_changes"]}
            self.assertEqual(proposals["hot"], "core")
            self.assertEqual(proposals["meh"], "probation")
            self.assertEqual([d["source_id"] for d in review["dormant_enabled"]], ["gone"])
            conc = review["concentration"]
            self.assertEqual(conc["total_items"], 52)
            self.assertEqual(conc["top_source"]["source_id"], "hot")
            self.assertAlmostEqual(conc["top_source"]["share"], 40 / 52, places=3)
            conn.close()

    def test_demotion_of_underserved_language_carries_a_caveat(self):
        """相关度是拿中英文画像匹配的，德语源天然算不高分 —— 那是我方欠工，不是它报得差。

        降档建议照出，但必须带上这条警示：真降了，将来接上翻译也没人记得当初为什么降。
        """
        with tempfile.TemporaryDirectory() as td:
            conn = store.connect(Path(td) / "s.db"); store.init(conn)
            _seed(conn, "en_src", 20, cluster_prefix="e", relevance=.5)
            _seed(conn, "de_src", 20, cluster_prefix="d", relevance=.05, cred=20,
                  n_groups=1, lead=False)
            reg = {"sources": [
                {"id": "en_src", "name": "en", "tier": 1, "group": "en", "lang": "en",
                 "enabled": True, "source_role": "reporting"},
                {"id": "de_src", "name": "de", "tier": 1, "group": "de", "lang": "de",
                 "enabled": True, "source_role": "reporting"}]}
            review = source_scores.governance_review(conn, reg, PROFILE, window_h=24)
            self.assertTrue(review["lang_relevance"]["de"]["under_served"])
            self.assertFalse(review["lang_relevance"]["en"]["under_served"])
            demotion = next(p for p in review["proposed_changes"]
                            if p["source_id"] == "de_src")
            self.assertEqual(demotion["proposed"], "probation")
            self.assertIn("尚未接翻译", demotion["caveat"])
            conn.close()

    def test_promotions_carry_no_caveat(self):
        """警示只加在降档上：抬档不需要为语种偏差辩解。"""
        card = {"grade": "core", "lang": "de", "score": .8}
        self.assertEqual(source_scores._caveat(
            card, {"de": {"avg_relevance": .1, "under_served": True}}), "")

    def test_hhi_detects_concentration_that_group_splitting_hides(self):
        """同一集团拆成多个源可以压低 top-source 份额，HHI 按组算就压不下去。"""
        with tempfile.TemporaryDirectory() as td:
            conn = store.connect(Path(td) / "s.db"); store.init(conn)
            for i in range(4):
                _seed(conn, f"ch{i}", 25, cluster_prefix=f"g{i}", group="chinanews")
            reg = {"sources": [{"id": f"ch{i}", "name": f"ch{i}", "tier": 1,
                                "group": "chinanews", "enabled": True,
                                "source_role": "reporting"} for i in range(4)]}
            conc = source_scores.governance_review(conn, reg, PROFILE,
                                                   window_h=24)["concentration"]
            self.assertAlmostEqual(conc["top_source"]["share"], .25, places=3)
            self.assertEqual(conc["top_group"]["group"], "chinanews")
            self.assertAlmostEqual(conc["top_group"]["share"], 1.0, places=3)
            conn.close()

    def test_persist_keeps_history_instead_of_overwriting(self):
        """降档半年后要说得清『当时看着什么数据做的决定』，所以快照不覆盖。"""
        with tempfile.TemporaryDirectory() as td:
            conn = store.connect(Path(td) / "s.db"); store.init(conn)
            _seed(conn, "a", 6, cluster_prefix="a")
            reg = _reg(("a",))
            first = source_scores.persist(conn, reg, PROFILE, window_h=24)
            self.assertEqual(first["sources"], 1)
            conn.execute("UPDATE source_scorecards SET computed_ts=computed_ts-3600")
            conn.commit()
            source_scores.persist(conn, reg, PROFILE, window_h=24)
            self.assertEqual(len(source_scores_history(conn, "a")), 2)
            latest = store.latest_scorecards(conn)
            self.assertEqual(len(latest), 1)
            self.assertIn("supply", latest["a"]["breakdown"])
            conn.close()


def source_scores_history(conn, source_id):
    return store.scorecard_history(conn, source_id)


class FocusRankTests(unittest.TestCase):
    def test_focus_moves_rank_but_never_credibility(self):
        """『重点关注』是偏好，可信度是证据。偏好绝不能掺进 cred。"""
        members = [{"source_id": "a"}, {"source_id": "b"}]
        base = 10.0
        self.assertEqual(pipeline._focus_ranked(base, members, {}), base)
        self.assertEqual(pipeline._focus_ranked(base, members, {"a": "core"}),
                         round(base * 1.15, 6))
        self.assertEqual(pipeline._focus_ranked(base, members, {"a": "probation",
                                                               "b": "probation"}),
                         round(base * .85, 6))

    def test_best_focus_in_the_cluster_wins(self):
        """一个事件里只要有一家重点源在报，这条就该被抬上来。"""
        members = [{"source_id": "a"}, {"source_id": "b"}]
        self.assertEqual(
            pipeline._focus_ranked(10.0, members, {"a": "probation", "b": "core"}),
            round(10.0 * 1.15, 6))


if __name__ == "__main__":
    unittest.main()
