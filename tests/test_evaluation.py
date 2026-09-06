import unittest

from newsdesk import config, evaluation


class ClusteringEvaluationTests(unittest.TestCase):
    def test_gold_set_size_and_quality_gates(self):
        items, pairs = evaluation.load_gold(config.DATA_DIR / "crosslingual_gold.json")
        result = evaluation.evaluate(items, pairs)
        self.assertEqual(result["pairs"], 300)
        self.assertGreaterEqual(result["pair_precision"], 0.95, result)
        self.assertGreaterEqual(result["pair_recall"], 0.80, result)
        self.assertGreaterEqual(result["bcubed_f1"], 0.88, result)


if __name__ == "__main__":
    unittest.main()
