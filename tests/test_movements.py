import json
import tempfile
import unittest
from pathlib import Path

from newsdesk import movements, store


SOURCE_META = {
    "wire_a": {"owner": "wire-a", "source_role": "wire"},
    "paper_b": {"owner": "paper-b", "source_role": "reporting"},
}


class MovementLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.connect(Path(self.tmp.name) / "movement.db")
        store.init(self.conn)
        movements.sync_catalog(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _cluster(self, cid, title, summary="", second=False):
        self.conn.execute(
            "INSERT INTO clusters(id,headline,headline_src,url,first_ts,last_ts,n_items,n_groups,"
            "best_tier,topics,cred,relevance,rank) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, title, "Wire A", "https://example.test/a", 1000, 1100,
             2 if second else 1, 2 if second else 1, 1, "[]", 80, .8, 70))
        rows = [(f"{cid}-a", "wire_a", "Wire A", title, summary, "https://example.test/a")]
        if second:
            rows.append((f"{cid}-b", "paper_b", "Paper B", title, summary,
                         "https://example.test/b"))
        self.conn.executemany(
            "INSERT INTO items(id,source_id,source_name,tier,grp,title,summary,url,lang,"
            "published_ts,fetched_ts,cluster_id) VALUES(?,?,?,1,?,?,?,?, 'en',1000,1100,?)",
            [(iid, sid, source, source, title, summary, url, cid)
             for iid, sid, source, title, summary, url in rows])
        self.conn.commit()

    def test_schema_v12_is_idempotent_and_persons_are_separate(self):
        store.init(self.conn)
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], 13)
        self.assertGreaterEqual(self.conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0], 30)
        self.assertIsNotNone(self.conn.execute(
            "SELECT 1 FROM persons WHERE id='person_sam_altman'").fetchone())

    def test_catalog_sync_retires_but_never_deletes_historical_people(self):
        self.conn.execute(
            "INSERT INTO persons(id,name,category,signal_prior,review_status,created_ts,updated_ts) "
            "VALUES('person_historical','Historical Person','investor',.5,'human_approved',1,1)")
        self.conn.commit()
        movements.sync_catalog(self.conn)
        row = self.conn.execute(
            "SELECT review_status FROM persons WHERE id='person_historical'").fetchone()
        self.assertEqual(row["review_status"], "retired")

    def test_completed_action_creates_auditable_candidate(self):
        self._cluster("c-action", "Sam Altman invested $375 million in Helion", second=True)
        result = movements.extract_recent(self.conn, SOURCE_META, 0)
        self.assertEqual(result["candidates"], 1)
        event = movements.list_events(
            self.conn, workflow="all", verification="all")["items"][0]
        self.assertEqual(event["actor_kind"], "associated_institution")
        self.assertEqual(event["amount_currency"], "USD")
        self.assertEqual(event["verification_status"], "verified")
        self.assertEqual(event["workflow_status"], "draft")
        self.assertIsNone(event["occurred_from_ts"])
        self.assertEqual(len(event["evidence"]), 2)
        self.assertIn("amount", event["fact_citations"])

    def test_explicit_personal_money_is_not_confused_with_company_money(self):
        self._cluster("c-personal", "Sam Altman personally invested $375 million in Helion")
        movements.extract_recent(self.conn, SOURCE_META, 0)
        event = movements.list_events(
            self.conn, workflow="all", verification="all")["items"][0]
        self.assertEqual(event["actor_kind"], "personal")
        self.assertEqual(event["persons"][0]["role"], "beneficial_owner")

    def test_speech_and_plans_do_not_become_movements(self):
        self._cluster("c-speech", "Elon Musk says AI will change every industry")
        self._cluster("c-plan", "Mark Zuckerberg plans to invest $60 billion in AI")
        result = movements.extract_recent(self.conn, SOURCE_META, 0)
        self.assertEqual(result["candidates"], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM movement_events").fetchone()[0], 0)

    def test_company_action_and_person_in_separate_fields_are_not_joined(self):
        self._cluster("c-misattribution", "Tesla invested $5 billion",
                      "Elon Musk attended a conference", second=True)
        result = movements.extract_recent(self.conn, SOURCE_META, 0)
        self.assertEqual(result["candidates"], 0)

    def test_different_objects_and_amounts_never_corroborate_each_other(self):
        self._cluster("c-conflict", "placeholder", second=True)
        self.conn.execute(
            "UPDATE items SET title='Sam Altman invested $100 million in Helion',summary='' "
            "WHERE id='c-conflict-a'")
        self.conn.execute(
            "UPDATE items SET title='Sam Altman invested $200 million in Reddit',summary='' "
            "WHERE id='c-conflict-b'")
        self.conn.commit()
        movements.extract_recent(self.conn, SOURCE_META, 0)
        event = movements.list_events(
            self.conn, workflow="all", verification="all")["items"][0]
        self.assertEqual(event["verification_status"], "candidate")
        self.assertEqual(len(event["evidence"]), 1)
        self.assertEqual(len(event["fact_citations"]["amount"]), 1)

    def test_missing_amount_can_support_action_but_not_amount_field(self):
        self._cluster("c-compatible", "placeholder", second=True)
        self.conn.execute(
            "UPDATE items SET title='Sam Altman invested $100 million in Helion',summary='' "
            "WHERE id='c-compatible-a'")
        self.conn.execute(
            "UPDATE items SET title='Sam Altman invested in Helion',summary='' "
            "WHERE id='c-compatible-b'")
        self.conn.commit()
        movements.extract_recent(self.conn, SOURCE_META, 0)
        event = movements.list_events(
            self.conn, workflow="all", verification="all")["items"][0]
        self.assertEqual(event["verification_status"], "verified")
        self.assertEqual(len(event["evidence"]), 2)
        self.assertEqual(len(event["fact_citations"]["amount"]), 1)

    def test_item_deletion_preserves_long_term_evidence(self):
        self._cluster("c-delete", "Warren Buffett invested $1 billion in a company")
        movements.extract_recent(self.conn, SOURCE_META, 0)
        self.conn.execute("DELETE FROM items WHERE id='c-delete-a'")
        self.conn.commit()
        evidence = self.conn.execute("SELECT item_id,url,quote FROM movement_evidence").fetchone()
        self.assertIsNone(evidence["item_id"])
        self.assertTrue(evidence["url"])
        self.assertTrue(evidence["quote"])

    def test_public_feed_excludes_unreviewed_candidates(self):
        self._cluster("c-draft", "Elon Musk completed the acquisition for $44 billion")
        movements.extract_recent(self.conn, SOURCE_META, 0)
        self.assertEqual(movements.list_events(self.conn)["total"], 0)
        self.assertEqual(movements.list_events(
            self.conn, workflow="all", verification="all")["total"], 1)

    def test_publish_gate_requires_verification_and_independent_evidence(self):
        self._cluster("c-gate", "Sam Altman invested $375 million in Helion")
        movements.extract_recent(self.conn, SOURCE_META, 0)
        movement_id = self.conn.execute("SELECT id FROM movement_events").fetchone()[0]
        with self.assertRaisesRegex(ValueError, "must be verified"):
            movements.review_event(
                self.conn, movement_id, verification_status="candidate",
                workflow_status="published")
        with self.assertRaisesRegex(ValueError, "primary record or two independent"):
            movements.review_event(
                self.conn, movement_id, verification_status="verified",
                workflow_status="published")

    def test_publish_gate_accepts_two_independent_owners(self):
        self._cluster("c-publish", "Sam Altman invested $375 million in Helion", second=True)
        movements.extract_recent(self.conn, SOURCE_META, 0)
        movement_id = self.conn.execute("SELECT id FROM movement_events").fetchone()[0]
        item = movements.review_event(
            self.conn, movement_id, verification_status="verified",
            workflow_status="published")
        self.assertEqual(item["workflow_status"], "published")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM movement_reviews WHERE movement_id=?", (movement_id,)).fetchone()[0], 1)
        self.assertEqual(movements.list_events(self.conn)["total"], 1)
        self.conn.execute("UPDATE movement_events SET observed_fact='human reviewed fact' WHERE id=?",
                          (movement_id,))
        self.conn.commit()
        movements.extract_recent(self.conn, SOURCE_META, 0)
        self.assertEqual(self.conn.execute(
            "SELECT observed_fact FROM movement_events WHERE id=?", (movement_id,)).fetchone()[0],
            "human reviewed fact")

    def test_curated_seed_is_idempotent_and_passes_same_publish_gate(self):
        entry = {
            "id": "mov_curated_test", "action_type": "capital_allocate",
            "verb_code": "invested", "actor_kind": "personal", "title": "Curated action",
            "object_text": "Helion", "occurred_from_ts": 1000, "disclosed_ts": 1100,
            "amount_value_text": "$375 million", "amount_currency": "USD",
            "amount_basis": "personal investment", "confidence": .98,
            "materiality_score": .9, "observed_fact": "Sam Altman invested in Helion.",
            "analytical_boundary": "This establishes the investment, not its future return.",
            "persons": [{"person_id": "person_sam_altman", "role": "beneficial_owner",
                         "attribution_confidence": 1.0, "control_basis": "personal investment"}],
            "themes": ["energy"],
            "evidence": [{"id": "mev_curated_test", "source_name": "Primary filing",
                          "source_owner": "primary", "source_role": "regulatory_filing",
                          "url": "https://example.test/filing", "published_ts": 1100,
                          "quote": "Sam Altman invested $375 million in Helion.",
                          "independence_group": "primary", "fields":
                          ["actor", "action", "object", "amount", "disclosure_date"]}]
        }
        self.assertEqual(movements.sync_curated(self.conn, [entry]), 1)
        self.assertEqual(movements.sync_curated(self.conn, [entry]), 0)
        event = movements.list_events(self.conn)["items"][0]
        self.assertEqual(event["id"], "mov_curated_test")
        self.assertEqual(event["workflow_status"], "published")


if __name__ == "__main__":
    unittest.main()
