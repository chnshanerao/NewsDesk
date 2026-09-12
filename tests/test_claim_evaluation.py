import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from newsdesk import claim_evaluation


class ClaimEvaluationTests(unittest.TestCase):
    def _dataset(self, root: Path, same_annotator=False):
        pairs = [{"pair_id": f"p{i:03d}", "claim_text": f"claim {i}",
                  "quote": f"quote {i}"} for i in range(300)]
        raw = "".join(json.dumps(x) + "\n" for x in pairs)
        (root / "pairs.jsonl").write_text(raw, encoding="utf-8")
        predictions = "".join(json.dumps({"pair_id": x["pair_id"],
                                           "system_relation": "support"}) + "\n"
                              for x in pairs)
        (root / "system_predictions.jsonl").write_text(predictions, encoding="utf-8")
        (root / "manifest.json").write_text(json.dumps({
            "snapshot_hash": hashlib.sha256(raw.encode()).hexdigest(),
            "predictions_hash": hashlib.sha256(predictions.encode()).hexdigest(),
            "ready_for_annotation": True,
            "human_attestation": {"confirmed_by_human": True,
                                  "reviewer_ids": ["alice", "bob", "carol"]}
        }), encoding="utf-8")
        for filename, annotator in (("annotations_a.jsonl", "alice"),
                                    ("annotations_b.jsonl", "alice" if same_annotator else "bob"),
                                    ("adjudicated.jsonl", "carol")):
            (root / filename).write_text("".join(json.dumps({
                "pair_id": x["pair_id"], "annotator": annotator, "label": "support"}) + "\n"
                for x in pairs), encoding="utf-8")

    def test_absent_class_is_carried_into_the_verdict(self):
        """语料里没有 refute 就不能宣称验收了 refute。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); self._dataset(root)
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            manifest.update({"ready_for_annotation": False,
                             "natural_distribution_ready": True,
                             "classes_absent": ["refute"]})
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            result = claim_evaluation.validate_dataset(root)
            self.assertTrue(result["valid"])
            self.assertEqual(result["classes_unmeasured"], ["refute"])
            self.assertIn("support/unknown boundary only", result["coverage"])

    def test_unbalanced_sample_without_natural_flag_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); self._dataset(root)
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            manifest["ready_for_annotation"] = False
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not balanced enough"):
                claim_evaluation.validate_dataset(root)

    def test_three_person_adjudicated_dataset_is_verified(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); self._dataset(root)
            result = claim_evaluation.validate_dataset(root)
            self.assertTrue(result["human_adjudicated"])
            self.assertEqual(result["pairs"], 300)
            self.assertEqual(result["cohen_kappa"], 1.0)

    def test_same_annotator_cannot_fake_double_blind_gold(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); self._dataset(root, same_annotator=True)
            with self.assertRaisesRegex(ValueError, "different people"):
                claim_evaluation.validate_dataset(root)

    def test_structure_alone_does_not_claim_human_review(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); self._dataset(root)
            manifest = json.loads((root / "manifest.json").read_text())
            manifest.pop("human_attestation")
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            result = claim_evaluation.validate_dataset(root)
            self.assertTrue(result["structurally_valid"])
            self.assertFalse(result["human_adjudicated"])

    def test_missing_annotations_remains_unverified(self):
        with tempfile.TemporaryDirectory() as td:
            result = claim_evaluation.validate_dataset(Path(td))
            self.assertFalse(result["human_adjudicated"])


if __name__ == "__main__":
    unittest.main()
