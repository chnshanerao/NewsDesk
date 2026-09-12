"""信源注册表的结构性约束。

这里锁的不是「有多少个源」，而是几条一旦破掉就会让评分说谎的规则 ——
最要紧的是 owner 与 group 的一一对应：group 是判「独立印证」的单位，
一个所有者被拆成两个 group，同一家公司就会被当成两家互相印证。
"""
import collections
import json
import unittest
from pathlib import Path

from newsdesk import config

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = json.loads((ROOT / "sources.json").read_text())
SOURCES = REGISTRY["sources"]


class SourceRegistryTests(unittest.TestCase):
    def test_ids_and_urls_are_unique(self):
        ids = [s["id"] for s in SOURCES]
        dupe_ids = [i for i, n in collections.Counter(ids).items() if n > 1]
        self.assertEqual(dupe_ids, [], "id 重复会让 source_health 互相覆盖")
        urls = [s["url"] for s in SOURCES]
        dupe_urls = [u for u, n in collections.Counter(urls).items() if n > 1]
        self.assertEqual(dupe_urls, [], "同一个 feed 挂两个 id = 自己印证自己")

    def test_owner_and_group_are_one_to_one(self):
        """一个 owner 只能对应一个 group，反之亦然。

        group 是交叉印证的判据单位。Ars Technica 与 WIRED 同属 Condé Nast，
        若各占一个 group，两家同题报道就会被算成「2 个独立集团」，
        直接把可信度推进「已确认」档 —— 而它其实只是一家公司的两个编辑部。
        编辑部各自独立不等于所有权独立，这里按后者定，宁可少给分。
        """
        group_owners = collections.defaultdict(set)
        owner_groups = collections.defaultdict(set)
        for src in SOURCES:
            owner = src.get("owner")
            if not owner:
                continue
            group_owners[src["group"]].add(owner)
            owner_groups[owner].add(src["group"])
        self.assertEqual(
            {g: sorted(o) for g, o in group_owners.items() if len(o) > 1}, {},
            "同一 group 下出现多个 owner")
        self.assertEqual(
            {o: sorted(g) for o, g in owner_groups.items() if len(g) > 1}, {},
            "同一 owner 被拆进多个 group —— 会被误算成独立印证")

    def test_enabled_sources_declare_language(self):
        """lang 缺省会退成 zh，外语源就会被算进中文的相关度基线里。"""
        missing = [s["id"] for s in SOURCES
                   if s.get("enabled", True) and not s.get("lang")]
        self.assertEqual(missing, [], "启用的源必须显式声明 lang")

    def test_tiers_and_topics_come_from_the_declared_vocabulary(self):
        tiers = {int(k) for k in REGISTRY["tiers"]}
        known_topics = {t for s in SOURCES for t in s["topics"]}
        for src in SOURCES:
            self.assertIn(src["tier"], tiers, src["id"])
            self.assertTrue(src["topics"], f"{src['id']} 没有 topics，永远匹配不到画像")
            self.assertTrue(set(src["topics"]) <= known_topics, src["id"])

    def test_each_language_desk_has_at_least_two_independent_owners(self):
        """单一 owner 的语种做不了跨语言印证 —— 那不是一个语种台，是一个源。"""
        owners = collections.defaultdict(set)
        for src in SOURCES:
            if src.get("enabled", True):
                owners[src.get("lang", "zh")].add(
                    src.get("owner") or src.get("group") or src["id"])
        for lang in ("en", "zh", "fr", "de"):
            self.assertGreaterEqual(len(owners[lang]), 2,
                                    f"{lang} 只有 {owners[lang]} 一个所有者")

    def test_official_role_is_resolved_for_primary_sources(self):
        """一次源要能被 source_role 认出来，存疑度和治理评分的豁免都挂在这上面。"""
        reg = config.load_sources(ROOT / "sources.json")
        by_id = {s["id"]: s for s in reg["sources"]}
        for sid in ("ec_presscorner", "europarl_press", "boj_news", "wto_news",
                    "cnil", "bundesregierung", "riksbank"):
            self.assertEqual(by_id[sid]["source_role"], "official", sid)

    def test_same_publisher_subfeeds_share_a_group(self):
        """同一家的多个版面必须共用 group，否则一家能自己凑出交叉印证。"""
        by_id = {s["id"]: s for s in SOURCES}
        for a, b in (("usine_nouvelle", "usine_digitale"),
                     ("derstandard", "derstandard_web"),
                     ("arstechnica_ai", "wired_ai")):
            self.assertEqual(by_id[a]["group"], by_id[b]["group"], f"{a} / {b}")


if __name__ == "__main__":
    unittest.main()
