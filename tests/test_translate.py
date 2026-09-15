import tempfile
import unittest
from pathlib import Path
from unittest import mock

from newsdesk import cluster, config, store, translate
from newsdesk.normalize import gram_set, simhash, tokens


def _item(iid, title, lang, pub=1_700_000_000):
    return {
        "id": iid, "source_id": f"s_{iid}", "source_name": iid.upper(),
        "tier": 1, "grp": iid, "title": title, "summary": "", "url": "",
        "lang": lang, "published_ts": pub, "fetched_ts": pub,
        "simhash": simhash(tokens(title)), "grams": gram_set(title),
    }


class TranslateEnrichTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.conn = store.connect(Path(self._td.name) / "t.db")
        store.init(self.conn)

    def tearDown(self):
        self.conn.close()
        self._td.cleanup()

    def test_disabled_is_noop(self):
        it = _item("fr1", "La BCE abaisse ses taux", "fr")
        before = dict(it)
        with mock.patch.object(config, "TRANSLATE_ENABLED", False):
            stat = translate.enrich(self.conn, [it])
        self.assertEqual(stat["translated"], 0)
        self.assertIsNone(it.get("canonical_title"))
        self.assertEqual(it["grams"], before["grams"])   # 原文信号不动

    def test_no_key_is_noop(self):
        it = _item("fr1", "La BCE abaisse ses taux", "fr")
        with mock.patch.object(config, "TRANSLATE_ENABLED", True), \
                mock.patch.object(config, "TRANSLATE_API_KEY", ""):
            stat = translate.enrich(self.conn, [it])
        self.assertEqual(stat, {"eligible": 0, "cached": 0, "translated": 0,
                                "failed": 0, "skipped": 0})
        self.assertIsNone(it.get("canonical_title"))

    def test_english_and_chinese_are_skipped(self):
        items = [_item("en1", "ECB cuts rates", "en"),
                 _item("zh1", "欧洲央行降息", "zh")]
        with mock.patch.object(config, "TRANSLATE_ENABLED", True), \
                mock.patch.object(config, "TRANSLATE_API_KEY", "k"), \
                mock.patch.object(translate, "_translate_one") as m:
            stat = translate.enrich(self.conn, items)
        m.assert_not_called()
        self.assertEqual(stat["eligible"], 0)

    def test_translates_sets_canonical_and_recomputes_grams(self):
        it = _item("fr1", "La BCE abaisse ses taux directeurs", "fr")
        eng = "ECB lowers its key interest rates"
        with mock.patch.object(config, "TRANSLATE_ENABLED", True), \
                mock.patch.object(config, "TRANSLATE_API_KEY", "k"), \
                mock.patch.object(translate, "_translate_one", return_value=eng):
            stat = translate.enrich(self.conn, [it])
        self.assertEqual(stat["translated"], 1)
        self.assertEqual(it["canonical_title"], eng)
        self.assertEqual(it["grams"], gram_set(eng))      # 聚类信号已换成英文
        self.assertEqual(it["title"], "La BCE abaisse ses taux directeurs")  # 原文不动

    def test_cache_hit_avoids_second_api_call(self):
        title = "Le gouvernement annonce un plan de relance"
        eng = "The government announces a stimulus plan"
        with mock.patch.object(config, "TRANSLATE_ENABLED", True), \
                mock.patch.object(config, "TRANSLATE_API_KEY", "k"), \
                mock.patch.object(translate, "_translate_one", return_value=eng) as m:
            translate.enrich(self.conn, [_item("fr1", title, "fr")])
            it2 = _item("fr2", title, "fr")            # 同标题同语言 → 命中缓存
            stat = translate.enrich(self.conn, [it2])
        self.assertEqual(m.call_count, 1)              # 只调了一次 API
        self.assertEqual(stat["cached"], 1)
        self.assertEqual(it2["canonical_title"], eng)

    def test_api_failure_falls_back_to_original(self):
        it = _item("fr1", "Titre en français", "fr")
        before_grams = set(it["grams"])
        with mock.patch.object(config, "TRANSLATE_ENABLED", True), \
                mock.patch.object(config, "TRANSLATE_API_KEY", "k"), \
                mock.patch.object(translate, "_translate_one",
                                  side_effect=TimeoutError("boom")):
            stat = translate.enrich(self.conn, [it])
        self.assertEqual(stat["failed"], 1)
        self.assertIsNone(it.get("canonical_title"))    # 回退：无 canonical
        self.assertEqual(it["grams"], before_grams)     # 原文信号不动，抓取不受影响

    def test_budget_cap_defers_overflow(self):
        items = [_item(f"fr{i}", f"Titre numero {i} du jour", "fr") for i in range(3)]
        with mock.patch.object(config, "TRANSLATE_ENABLED", True), \
                mock.patch.object(config, "TRANSLATE_API_KEY", "k"), \
                mock.patch.object(config, "TRANSLATE_MAX_PER_RUN", 1), \
                mock.patch.object(translate, "_translate_one", return_value="EN"):
            stat = translate.enrich(self.conn, items)
        self.assertEqual(stat["translated"], 1)
        self.assertEqual(stat["skipped"], 2)            # 超额留到下一轮

    def test_backfills_existing_db_row(self):
        """存量外文条目（已入库、canonical 为空）应被回填 canonical/grams/simhash。"""
        it = _item("fr1", "La BCE abaisse ses taux directeurs", "fr")
        store.insert_items(self.conn, [it])          # 先入库，无 canonical
        row = self.conn.execute(
            "SELECT canonical_title FROM items WHERE id='fr1'").fetchone()
        self.assertIsNone(row[0])                     # 确认入库时为空
        eng = "ECB lowers its key interest rates"
        # 下一轮 source 复现同一条 → enrich 命中缓存/翻译后回填已在库的行
        again = _item("fr1", "La BCE abaisse ses taux directeurs", "fr")
        with mock.patch.object(config, "TRANSLATE_ENABLED", True), \
                mock.patch.object(config, "TRANSLATE_API_KEY", "k"), \
                mock.patch.object(translate, "_translate_one", return_value=eng):
            translate.enrich(self.conn, [again])
        row = self.conn.execute(
            "SELECT canonical_title, grams FROM items WHERE id='fr1'").fetchone()
        self.assertEqual(row[0], eng)                 # canonical 已回填
        self.assertEqual(set(row[1].split()), gram_set(eng))  # grams 换成英文

    def test_backfill_does_not_clobber_existing_canonical(self):
        """已有 canonical 的行不被回填覆盖（幂等，WHERE 只命中 NULL）。"""
        it = _item("fr1", "Titre français", "fr")
        it["canonical_title"] = "First canonical"
        store.insert_items(self.conn, [it])
        again = _item("fr1", "Titre français", "fr")
        with mock.patch.object(config, "TRANSLATE_ENABLED", True), \
                mock.patch.object(config, "TRANSLATE_API_KEY", "k"), \
                mock.patch.object(translate, "_translate_one", return_value="Second"):
            translate.enrich(self.conn, [again])
        row = self.conn.execute(
            "SELECT canonical_title FROM items WHERE id='fr1'").fetchone()
        self.assertEqual(row[0], "First canonical")   # 未被覆盖

    def test_translated_foreign_clusters_with_english(self):
        """核心目的：外文译成英文后，能和英文报道聚成同一个事件簇。"""
        shared = "ECB cuts interest rates by 25 basis points"
        en = _item("en1", shared, "en", pub=1_700_000_000)
        fr = _item("fr1", "La BCE réduit ses taux de 25 points de base", "fr",
                   pub=1_700_000_100)
        with mock.patch.object(config, "TRANSLATE_ENABLED", True), \
                mock.patch.object(config, "TRANSLATE_API_KEY", "k"), \
                mock.patch.object(translate, "_translate_one", return_value=shared):
            translate.enrich(self.conn, [fr])

        # 翻译前：法文与英文无共同 gram、跨脚本桥也不触发 → 各成一簇
        raw_fr = _item("fr0", "La BCE réduit ses taux de 25 points de base", "fr",
                       pub=1_700_000_100)
        self.assertEqual(len(cluster.build([dict(en), raw_fr])), 2)

        # 翻译后：法文条目带英文 canonical → 与英文报道并成一簇
        groups = cluster.build([dict(en), dict(fr)])
        self.assertEqual(len(groups), 1)


if __name__ == "__main__":
    unittest.main()
