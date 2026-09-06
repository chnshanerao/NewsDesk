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

    def test_timeline_is_chronological(self):
        rows = [item("b", "后续", "b", ts=20), item("a", "首发", "a", ts=10)]
        self.assertEqual([x["title"] for x in evidence.timeline(rows)], ["首发", "后续"])


if __name__ == "__main__":
    unittest.main()
