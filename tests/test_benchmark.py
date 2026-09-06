import tempfile
import unittest
from pathlib import Path

from newsdesk import benchmark, store


class BenchmarkTests(unittest.TestCase):
    def test_report_is_machine_readable_and_has_all_cases(self):
        with tempfile.TemporaryDirectory() as td:
            conn = store.connect(Path(td) / "b.db"); store.init(conn)
            result = benchmark.run(conn, iterations=2)
            self.assertEqual(set(result["cases"]), {
                "ranked_feed", "latin_fulltext", "cjk_text", "entity_filter"})
            self.assertGreaterEqual(result["worst_p95_ms"], 0)
            self.assertEqual(result["target_p95_ms"], 250)
            conn.close()


if __name__ == "__main__":
    unittest.main()
