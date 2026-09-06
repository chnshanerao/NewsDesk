import tempfile
import unittest
from pathlib import Path

from newsdesk import search, store


class QueryParserTests(unittest.TestCase):
    def test_structured_query(self):
        q = search.parse('"rate cut" source:NPR lang:en topic:macro cred:>=60 after:2d')
        self.assertEqual(q["text"], "rate cut")
        self.assertEqual(q["source"], "NPR")
        self.assertEqual(q["lang"], "en")
        self.assertEqual(q["topic"], "macro")
        self.assertIsNone(q["asset"])
        self.assertEqual(q["min_cred"], 60)
        self.assertEqual(q["hours"], 48)

    def test_quoted_source_and_asset_query(self):
        q = search.parse('source:"Financial Times" asset:^GSPC lang:EN')
        self.assertEqual(q["source"], "Financial Times")
        self.assertEqual(q["asset"], "^GSPC")
        self.assertEqual(q["lang"], "en")

    def test_invalid_operator_is_preserved_as_search_text(self):
        q = search.parse("after:yesterday cred:high lang:de")
        self.assertEqual(q["text"], "after:yesterday cred:high lang:de")
        self.assertIsNone(q["hours"])
        self.assertIsNone(q["min_cred"])
        self.assertIsNone(q["lang"])

    def test_asset_filter(self):
        q = search.parse("asset:XAUUSD gold")
        self.assertEqual(q["asset"], "XAUUSD")
        self.assertEqual(q["text"], "gold")

    def test_summary_and_source_search(self):
        with tempfile.TemporaryDirectory() as td:
            conn = store.connect(Path(td) / "s.db"); store.init(conn)
            conn.execute("INSERT INTO clusters(id,headline,last_ts,cred,rank,topics) "
                         "VALUES('c','Neutral headline',200,80,1,'[]')")
            conn.execute("INSERT INTO items(id,source_id,source_name,title,summary,cluster_id) "
                         "VALUES('i','npr','NPR World','Other','hidden inflation detail','c')")
            conn.commit()
            self.assertEqual(len(store.feed(conn, q="inflation")), 1)
            self.assertEqual(len(store.feed(conn, source="NPR")), 1)
            self.assertEqual(len(store.feed(conn, source="BBC")), 0)
            conn.close()

    def test_special_characters_are_literal_not_wildcards(self):
        with tempfile.TemporaryDirectory() as td:
            conn = store.connect(Path(td) / "special.db"); store.init(conn)
            for cid, text in (("cpp", "C++ compiler update"),
                              ("sp", "S&P 500 closes higher"),
                              ("plain", "Completely unrelated")):
                conn.execute("INSERT INTO clusters(id,headline,last_ts,cred,rank,topics) "
                             "VALUES(?,?,200,80,1,'[]')", (cid, text))
            conn.commit()
            self.assertEqual([x["id"] for x in store.feed(conn, q="C++")], ["cpp"])
            self.assertEqual([x["id"] for x in store.feed(conn, q="S&P 500")], ["sp"])
            self.assertEqual(store.feed(conn, q="%"), [])
            conn.close()


if __name__ == "__main__":
    unittest.main()
