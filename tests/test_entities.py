import tempfile
import unittest
from pathlib import Path

from newsdesk import entities, store


class EntityMasterTests(unittest.TestCase):
    def test_extract_has_canonical_identifier_and_evidence(self):
        links = entities.extract("微软与英伟达推动 AI 市场，黄金价格上涨")
        by_symbol = {x["symbol"]: x for x in links}
        self.assertIn("MSFT", by_symbol)
        self.assertIn("NVDA", by_symbol)
        self.assertIn("XAUUSD", by_symbol)
        self.assertEqual(by_symbol["MSFT"]["relation_type"], "explicit_mention")
        self.assertTrue(by_symbol["MSFT"]["evidence"])

    def test_ambiguous_common_words_are_not_companies(self):
        self.assertFalse(entities.extract("I ate an Apple"))
        self.assertFalse(entities.extract("The Amazon rainforest is shrinking"))
        self.assertFalse(entities.extract("A meta analysis of clinical trials"))

    def test_ambiguous_ai_names_require_technical_context(self):
        self.assertFalse(any(x["entity_id"] == "model_claude" for x in entities.extract(
            "Claude attended the reception in Paris")))
        self.assertFalse(any(x["entity_id"] == "model_gemini" for x in entities.extract(
            "Gemini is an air sign")))
        self.assertTrue(any(x["entity_id"] == "model_claude" for x in entities.extract(
            "Anthropic released a new Claude AI model")))
        self.assertTrue(any(x["entity_id"] == "model_gemini" for x in entities.extract(
            "Google upgraded the Gemini large language model")))
        self.assertFalse(any(x["entity_id"] == "co_intel" for x in entities.extract(
            "New military intel was disclosed")))
        self.assertTrue(any(x["entity_id"] == "co_intel" for x in entities.extract(
            "Intel launched a new semiconductor processor")))
        self.assertTrue(any(x["entity_id"] == "co_apple" for x in entities.extract(
            "Apple CEO Tim Cook spoke in Cupertino")))
        for text in (
            "Children learn the English alphabet at school",
            "The oracle foretold a difficult year",
            "The coating is one micron thick",
            "Tesla was a Serbian-American inventor",
            "The Amazon basin faces drought",
            "Claude Monet paintings drew a crowd",
            "A llama grazed in the field",
        ):
            self.assertFalse(entities.extract(text), text)

    def test_multiple_technology_entity_kinds(self):
        links = entities.extract(
            "DeepMind evaluated Gemini on NVIDIA H100 using PyTorch and Kubernetes")
        by_id = {x["entity_id"]: x for x in links}
        self.assertEqual(by_id["lab_deepmind"]["kind"], "lab")
        self.assertEqual(by_id["model_gemini"]["kind"], "model")
        self.assertEqual(by_id["chip_h100"]["kind"], "chip")
        self.assertEqual(by_id["oss_pytorch"]["kind"], "open_source")

    def test_unicode_normalization_and_token_boundaries(self):
        self.assertTrue(any(x["entity_id"] == "co_nvidia" for x in entities.extract(
            "ＮＶＩＤＩＡ released a chip")))
        self.assertFalse(any(x["entity_id"] == "co_nvidia" for x in entities.extract(
            "NVIDIA123 is an unrelated identifier")))
        self.assertFalse(any(x["entity_id"] == "co_arm" for x in entities.extract(
            "He flexed his arm after exercise")))
        self.assertTrue(any(x["entity_id"] == "co_arm" for x in entities.extract(
            "Arm chip architecture advances")))

    def test_persisted_relation_and_server_side_asset_filter(self):
        with tempfile.TemporaryDirectory() as td:
            conn = store.connect(Path(td) / "e.db"); store.init(conn)
            entities.sync_catalog(conn)
            conn.execute("INSERT INTO clusters(id,headline,cred,last_ts,rank,topics) "
                         "VALUES('c1','Microsoft launches product',80,1000,1,'[]')")
            entities.link_cluster(conn, "c1", "Microsoft launches product")
            conn.commit()
            self.assertEqual(store.feed(conn, asset="MSFT")[0]["id"], "c1")
            self.assertEqual(entities.cluster_links(conn, "c1")[0]["symbol"], "MSFT")
            conn.close()

    def test_catalog_identifiers_and_aliases_are_unique(self):
        catalog = entities.catalog()
        ids = [x["id"] for x in catalog]
        self.assertEqual(len(ids), len(set(ids)))
        for entity in catalog:
            self.assertIn(entity["kind"], {"company", "index", "fx", "rate", "commodity",
                                           "model", "lab", "chip", "open_source"})
            aliases = [x.casefold() for x in entity.get("aliases", [])]
            self.assertEqual(len(aliases), len(set(aliases)), entity["id"])

    def test_sync_catalog_removes_stale_database_entities(self):
        with tempfile.TemporaryDirectory() as td:
            conn = store.connect(Path(td) / "e.db"); store.init(conn)
            conn.execute("INSERT INTO entities(id,kind,name) VALUES('stale','company','Stale')")
            entities.sync_catalog(conn)
            self.assertIsNone(conn.execute(
                "SELECT id FROM entities WHERE id='stale'").fetchone())
            conn.close()

    def test_fts_indexes_latin_item_text(self):
        with tempfile.TemporaryDirectory() as td:
            conn = store.connect(Path(td) / "e.db"); store.init(conn)
            conn.execute("INSERT INTO clusters(id,headline,cred,last_ts,rank,topics) "
                         "VALUES('c1','Other headline',80,1000,1,'[]')")
            conn.execute("INSERT INTO items(id,source_id,title,summary,cluster_id) "
                         "VALUES('i1','s','Quarterly results','semiconductor demand','c1')")
            conn.commit()
            self.assertEqual(store.feed(conn, q="semiconductor")[0]["id"], "c1")
            conn.close()


if __name__ == "__main__":
    unittest.main()
