import json
import unittest

from newsdesk import config, evaluation


class ClusteringEvaluationTests(unittest.TestCase):
    def test_gold_set_size_and_quality_gates(self):
        path = config.DATA_DIR / "crosslingual_gold.json"
        items, pairs = evaluation.load_gold(path)
        result = evaluation.evaluate(items, pairs)
        # 规模跟着金标准文件走，不在测试里写死数字：v1 是 300 对（央行/财报口径），
        # v2 补进 AI 域实体与事件后是 532 对。load_gold 已经校验过文件自报的
        # expected_pairs 与实际生成数一致，这里再对一次，防止两边一起漂移。
        spec = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(result["pairs"], spec["expected_pairs"])
        self.assertGreaterEqual(result["pairs"], 532, "金标准不许缩水")
        # v2 必须真的覆盖 AI 域：否则这条主线一直测不到，只在央行口径上刷满分。
        self.assertTrue(any(e["zh"] in ("OpenAI", "智谱", "月之暗面", "字节跳动")
                            for e in spec["entities"]), spec["entities"])
        self.assertGreaterEqual(result["pair_precision"], 0.95, result)
        self.assertGreaterEqual(result["pair_recall"], 0.80, result)
        self.assertGreaterEqual(result["bcubed_f1"], 0.88, result)


if __name__ == "__main__":
    unittest.main()
