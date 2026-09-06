import tempfile
import unittest
from pathlib import Path

from newsdesk import research, store


class ResearchTests(unittest.TestCase):
    def test_every_finding_has_exact_citation(self):
        with tempfile.TemporaryDirectory() as td:
            conn = store.connect(Path(td) / "r.db"); store.init(conn)
            conn.execute("INSERT INTO items(id,source_id,title,summary,url) VALUES(?,?,?,?,?)",
                         ("i1", "openai", "OpenAI 发布新模型", "模型支持更长上下文。", "https://example/a"))
            conn.execute("INSERT INTO clusters(id,headline,last_ts,cred,topics) VALUES(?,?,?,?,?)",
                         ("c1", "OpenAI 发布新模型", 100, 80, '["ai_models"]'))
            store.replace_cluster_claims(conn, "c1", [{
                "id": "claim1", "text": "OpenAI 发布新模型", "status": "official_statement",
                "independent_groups": 0, "groups": ["openai"], "source_roles": ["official"],
                "method": "extractive-v1", "evidence": [{
                    "item_id": "i1", "source_id": "openai", "source": "OpenAI",
                    "group": "openai", "source_role": "official", "url": "https://example/a",
                    "published_ts": 100, "quote": "OpenAI 发布新模型", "quote_field": "title",
                    "quote_start": 0, "quote_end": 12, "quote_hash": "abc", "similarity": 1.0,
                    "relation": "support",
                }]}])
            conn.commit()
            persisted = store.cluster_claims(conn, "c1")
            self.assertEqual(len(persisted), 1)
            self.assertEqual(persisted[0]["cluster_id"], "c1")
            self.assertEqual(persisted[0]["evidence"][0]["quote_hash"], "abc")
            result = research.answer(conn, "OpenAI 模型")
            self.assertEqual(result["citation_coverage"], 1.0)
            self.assertEqual(result["findings"][0]["citations"][0]["quote"], "OpenAI 发布新模型")
            self.assertEqual(result["findings"][0]["status"], "official_statement")
            self.assertEqual(result["findings"][0]["citations"][0]["relation"], "support")
            conn.close()

    def test_empty_question_is_honest(self):
        with tempfile.TemporaryDirectory() as td:
            conn = store.connect(Path(td) / "r.db"); store.init(conn)
            self.assertFalse(research.answer(conn, "")["findings"])
            conn.close()

    def test_multi_term_query_does_not_return_partial_topic_match(self):
        with tempfile.TemporaryDirectory() as td:
            conn = store.connect(Path(td) / "r.db"); store.init(conn)
            for cid, text in (("c1", "AI 芯片发布"), ("c2", "AI 教育培训")):
                iid = cid + "i"
                conn.execute("INSERT INTO items(id,source_id,title,url) VALUES(?,?,?,?)",
                             (iid, "s", text, "https://example/" + cid))
                conn.execute("INSERT INTO clusters(id,headline,last_ts,cred,topics) "
                             "VALUES(?,?,?,?,?)", (cid, text, 100, 80, "[]"))
                store.replace_cluster_claims(conn, cid, [{
                    "id": cid, "text": text, "status": "single_report",
                    "independent_groups": 1, "groups": ["s"],
                    "source_roles": ["reporting"], "evidence": [{
                        "item_id": iid, "source_id": "s", "source": "s", "group": "s",
                        "source_role": "reporting", "url": "https://example/" + cid,
                        "published_ts": 100, "quote": text, "quote_field": "title",
                        "quote_start": 0, "quote_end": len(text), "quote_hash": cid,
                        "similarity": 1.0}]}])
            conn.commit()
            result = research.answer(conn, "AI 芯片")
            self.assertEqual([x["cluster_id"] for x in result["findings"]], ["c1"])
            conn.close()


if __name__ == "__main__":
    unittest.main()
