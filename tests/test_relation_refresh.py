"""分类器改版后，库里存的旧判断必须能被重刷，且状态跟着改。"""
import tempfile
import unittest
from pathlib import Path

from newsdesk import evidence, pipeline, store


def _seed(conn, claim_text, title, summary, stored_relation, status):
    conn.execute("INSERT INTO items(id,source_id,title,summary,url,published_ts) "
                 "VALUES(?,?,?,?,?,?)", ("i1", "stats", title, summary,
                                         "https://example/a", 100))
    conn.execute("INSERT INTO clusters(id,headline,last_ts,cred,topics) VALUES(?,?,?,?,?)",
                 ("c1", title, 100, 80, "[]"))
    store.replace_cluster_claims(conn, "c1", [{
        "id": "claim1", "text": claim_text, "status": status,
        "independent_groups": 0, "groups": ["stats"], "source_roles": ["reporting"],
        "method": "extractive-v1", "evidence": [{
            "item_id": "i1", "source_id": "stats", "source": "国家统计局",
            "group": "stats", "source_role": "reporting", "url": "https://example/a",
            "published_ts": 100, "quote": title, "quote_field": "title",
            "quote_start": 0, "quote_end": len(title), "quote_hash": "abc",
            "similarity": 1.0, "relation": stored_relation}]}])
    conn.commit()


class RelationRefreshTests(unittest.TestCase):
    def test_stale_refute_is_reclassified_and_status_restated(self):
        """1月 PPI 对 5月 PPI 是两个期次。旧分类器记的 refute 必须被撤掉，
        而 claims.status 不能继续停留在 disputed。"""
        with tempfile.TemporaryDirectory() as td:
            conn = store.connect(Path(td) / "r.db"); store.init(conn)
            _seed(conn, "2025年1月份工业生产者出厂价格同比下降2.3%",
                  "2025年5月份工业生产者出厂价格同比下降3.3%", "",
                  "refute", "disputed")
            result = pipeline.refresh_relations(conn)
            self.assertEqual(result["evidence_rows"], 1)
            self.assertEqual(result["relations_changed"], 1)
            self.assertEqual(result["claims_restated"], 1)
            row = conn.execute("SELECT relation,relation_method FROM claim_evidence").fetchone()
            self.assertEqual(row["relation"], "unknown")
            self.assertEqual(row["relation_method"], "heuristic-relation-v2")
            claim = conn.execute("SELECT status FROM claims").fetchone()
            self.assertEqual(claim["status"], "single_report")
            conn.close()

    def test_refresh_keeps_the_audited_quote(self):
        """判断可以改，引号不能动 —— 它是入库时审计过的证据。"""
        with tempfile.TemporaryDirectory() as td:
            conn = store.connect(Path(td) / "r.db"); store.init(conn)
            _seed(conn, "2025年1月份工业生产者出厂价格同比下降2.3%",
                  "2025年5月份工业生产者出厂价格同比下降3.3%", "", "refute", "disputed")
            pipeline.refresh_relations(conn)
            row = conn.execute("SELECT item_id,quote,quote_hash FROM claim_evidence").fetchone()
            self.assertEqual(row["quote"], "2025年5月份工业生产者出厂价格同比下降3.3%")
            self.assertEqual(row["quote_hash"], "abc")
            self.assertEqual(conn.execute("SELECT COUNT(*) c FROM claim_evidence")
                             .fetchone()["c"], 1)
            conn.close()

    def test_refresh_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            conn = store.connect(Path(td) / "r.db"); store.init(conn)
            _seed(conn, "2025年1月份工业生产者出厂价格同比下降2.3%",
                  "2025年5月份工业生产者出厂价格同比下降3.3%", "", "refute", "disputed")
            pipeline.refresh_relations(conn)
            again = pipeline.refresh_relations(conn)
            self.assertEqual(again["relations_changed"], 0)
            self.assertEqual(again["claims_restated"], 0)
            conn.close()

    def test_status_rules_live_in_one_place(self):
        """summarize 是 claims() 与重刷共用的唯一状态实现。"""
        refs = [{"group": "a", "source_role": "reporting", "relation": "support"},
                {"group": "b", "source_role": "reporting", "relation": "support"}]
        self.assertEqual(evidence.summarize(refs)["status"], "independently_reported")
        self.assertEqual(evidence.summarize(refs)["independent_groups"], 2)
        disputed = refs + [{"group": "c", "source_role": "reporting", "relation": "refute"}]
        self.assertEqual(evidence.summarize(disputed)["status"], "disputed")
        aggregated = [{"group": "a", "source_role": "aggregator", "relation": "support"},
                      {"group": "b", "source_role": "aggregator", "relation": "support"}]
        self.assertEqual(evidence.summarize(aggregated)["status"], "single_report")


if __name__ == "__main__":
    unittest.main()
