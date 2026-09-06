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


if __name__ == "__main__":
    unittest.main()
