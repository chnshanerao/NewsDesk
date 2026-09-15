import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from newsdesk import quality, store


class QualityScorecardTests(unittest.TestCase):
    def test_empty_install_fails_honestly_and_keeps_boundaries(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        store.init(conn)
        with tempfile.TemporaryDirectory() as td, mock.patch.object(
                store.config, "BACKUP_DIR", Path(td)):
            result = quality.scorecard(conn, {"sources": []}, now=1_000_000)
        self.assertEqual(result["status"], "degraded")
        self.assertGreater(result["counts"]["fail"], 0)
        self.assertFalse(result["market_data"]["execution_grade"])
        self.assertTrue(any("trade execution" in x for x in result["boundaries"]))


    def test_gate_targets_are_set_from_measured_ceilings(self):
        """闸门的目标值必须落在实测能动的范围里，否则它只会永远黄着：
        看不出回归，也看不出进展。这里锁住两处按实测量出来的口径。"""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        store.init(conn)
        with tempfile.TemporaryDirectory() as td, mock.patch.object(
                store.config, "BACKUP_DIR", Path(td)):
            result = quality.scorecard(conn, {"sources": []}, now=1_000_000)
        gates = {g["name"]: g for g in result["gates"]}

        # 流水线新鲜度分两档：少一轮只提示，少四轮才算硬失败。
        # 实测 9.4 天 842 轮里有 21 次（2.5%）间隔超过 2× 周期（重启/某轮跑久），
        # 硬闸门卡在 2× 会被正常抖动触发，把真正的停摆淹掉。
        hard = gates["pipeline_freshness_seconds"]
        soft = gates["pipeline_freshness_seconds_advisory"]
        self.assertEqual(hard["target"], "<=3600")
        self.assertEqual(soft["target"], "<=1800")
        self.assertEqual(soft["status"], "warn", "空库没跑过流水线，提示档应为 warn")
        self.assertEqual(hard["status"], "fail", "空库没跑过流水线，硬档应为 fail")

        # AI 印证率：这条闸门只管防回归。0.10 是愿望，0.032 是实测天花板
        # （桥接全开 0.0317 / 同脚本补召回 0.0274 / 放宽防漂移 0.0260），
        # 所以地板设在 0.025，愿望与上限都写在 detail 里，不藏。
        rate = gates["ai_independent_corroboration_rate"]
        self.assertEqual(rate["target"], ">=0.025")
        self.assertIn("0.10", rate["detail"])
        self.assertIn("ceiling", rate["detail"])
        self.assertIn("source mix", rate["detail"])
        # 真正的成因另立一条：85% 的 AI 事件只有一篇稿子，没有第二家可印证。
        # 这条只能靠信源结构改善，写出来才不会有人再去聚类算法里找答案。
        self.assertIn("ai_single_source_share", gates)
        self.assertEqual(gates["ai_single_source_share"]["target"], "<=0.80")


if __name__ == "__main__":
    unittest.main()
