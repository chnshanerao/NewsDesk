import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from newsdesk import config, store


class BackupTests(unittest.TestCase):
    def test_online_backup_is_readable(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db = root / "source.db"
            with mock.patch.object(config, "DB_PATH", db):
                conn = store.connect(); store.init(conn)
                conn.execute("INSERT INTO alerts(name,created_ts) VALUES('x',1)")
                conn.commit(); conn.close()
                out = store.backup_database(root / "copy.db")
            copied = sqlite3.connect(out)
            self.assertEqual(copied.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(copied.execute("SELECT COUNT(*) FROM alerts").fetchone()[0], 1)
            copied.close()

    def test_recovery_drill_restores_and_migrates_copy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db = root / "source.db"
            with mock.patch.object(config, "DB_PATH", db):
                conn = store.connect(); store.init(conn)
                conn.execute("INSERT INTO runs(started_ts,ended_ts) VALUES(1,2)")
                conn.commit(); conn.close()
                backup = store.backup_database(root / "copy.db")
            result = store.recovery_drill(backup)
            self.assertTrue(result["ok"])
            self.assertEqual(result["integrity"], "ok")
            self.assertEqual(result["schema_version"], store.SCHEMA_VERSION)
            self.assertEqual(result["counts"]["runs"], 1)
            self.assertIn("entities", result["counts"])
            self.assertIn("source_probes", result["counts"])

    def test_schema_migrations_are_idempotent_and_audited(self):
        with tempfile.TemporaryDirectory() as td:
            conn = store.connect(Path(td) / "schema.db")
            store.init(conn)
            store.init(conn)
            status = store.schema_status(conn)
            self.assertEqual(status["current"], store.SCHEMA_VERSION)
            self.assertEqual([m["version"] for m in status["migrations"]],
                             list(range(1, store.SCHEMA_VERSION + 1)))
            conn.close()


if __name__ == "__main__":
    unittest.main()
