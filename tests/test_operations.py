import unittest
from unittest import mock

from newsdesk import server


class RefreshCoordinatorTests(unittest.TestCase):
    def test_rejects_parallel_refresh(self):
        server._refresh_lock.acquire()
        try:
            self.assertFalse(server._start_refresh({}, {}, False))
        finally:
            server._refresh_lock.release()


class RunOutcomeTests(unittest.TestCase):
    def test_failed_run_is_closed_and_auditable(self):
        import tempfile
        from pathlib import Path
        from newsdesk import pipeline, store
        with tempfile.TemporaryDirectory() as td:
            conn = store.connect(Path(td) / "run.db"); store.init(conn)
            with mock.patch.object(pipeline, "ingest", side_effect=RuntimeError("boom")):
                with self.assertRaises(RuntimeError):
                    pipeline.run(conn, {"sources": []}, {})
            row = conn.execute("SELECT * FROM runs").fetchone()
            self.assertEqual(row["status"], "failed")
            self.assertIsNotNone(row["ended_ts"])
            self.assertIn("boom", row["error"])
            conn.close()

    def test_source_probe_history_is_recorded(self):
        import tempfile
        from pathlib import Path
        from newsdesk import store
        with tempfile.TemporaryDirectory() as td:
            conn = store.connect(Path(td) / "probe.db"); store.init(conn)
            store.record_health(conn, {"source_id":"s","name":"S","tier":1,
                "enabled":True,"last_try_ts":2_000_000_000,"last_ok_ts":2_000_000_000,
                "verdict":"healthy","ms":42,"n_items":3,"n_new":2})
            row = conn.execute("SELECT * FROM source_probes").fetchone()
            self.assertEqual((row["source_id"],row["ms"],row["n_new"]), ("s",42,2))
            conn.close()


if __name__ == "__main__":
    unittest.main()
