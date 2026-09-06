import unittest

from newsdesk import config
from newsdesk.server import _concentration, _owner_capped, _source_metadata


class DiversityTests(unittest.TestCase):
    def test_source_metadata_defaults(self):
        meta = _source_metadata({"id": "paper", "group": "owner", "tier": 1,
                                 "lang": "zh"})
        self.assertEqual(meta["owner"], "owner")
        self.assertEqual(meta["country"], "CN")
        self.assertEqual(meta["editorial_role"], "reporting")

    def test_explicit_metadata_wins(self):
        meta = _source_metadata({"id": "wire", "tier": 0, "lang": "en",
                                 "owner": "wire-owner", "country": "GB",
                                 "source_type": "wire", "editorial_role": "reporting"})
        self.assertEqual(meta["owner"], "wire-owner")
        self.assertEqual(meta["country"], "GB")
        self.assertEqual(meta["source_type"], "wire")

    def test_concentration(self):
        result = _concentration({"a": 3, "b": 1})
        self.assertEqual(result["total"], 4)
        self.assertEqual(result["largest_share"], 0.75)
        self.assertEqual(result["hhi"], 0.625)

    def test_owner_cap_is_stable_and_hard(self):
        items = ([{"id": f"a{i}", "owner": "a"} for i in range(8)]
                 + [{"id": f"b{i}", "owner": "b"} for i in range(4)]
                 + [{"id": f"c{i}", "owner": "c"} for i in range(4)]
                 + [{"id": f"d{i}", "owner": "d"} for i in range(4)])
        page = _owner_capped(items, 10)
        self.assertEqual([x["id"] for x in page[:3]], ["a0", "a1", "a2"])
        self.assertEqual(len(page), 10)
        counts = {}
        for item in page:
            counts[item["owner"]] = counts.get(item["owner"], 0) + 1
        self.assertLessEqual(max(counts.values()), 3)

    def test_owner_cap_applies_to_small_pages(self):
        page = _owner_capped([{"owner": "a"}] * 4 + [{"owner": "b"}] * 4, 4)
        self.assertEqual(len(page), 4)
        self.assertEqual(sum(x["owner"] == "a" for x in page), 2)
        self.assertEqual(sum(x["owner"] == "b" for x in page), 2)

    def test_ai_intelligence_sources_are_enabled_and_role_labeled(self):
        reg = config.load_sources()
        wanted = {"openai_news", "nvidia_blog", "microsoft_research",
                  "mit_ai_news", "arxiv_cs_ai", "techcrunch_ai",
                  "apple_ml_research", "aws_ml_blog", "arstechnica_ai",
                  "wired_ai", "ieee_spectrum_ai"}
        sources = {s["id"]: s for s in reg["sources"] if s["id"] in wanted}
        self.assertEqual(set(sources), wanted)
        self.assertTrue(all(s.get("enabled") for s in sources.values()))
        self.assertEqual(sources["techcrunch_ai"]["source_role"], "reporting")
        self.assertEqual(sources["openai_news"]["source_role"], "official")
        self.assertIn("ai_research", sources["arxiv_cs_ai"]["topics"])


if __name__ == "__main__":
    unittest.main()
