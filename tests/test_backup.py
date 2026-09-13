import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from newsdesk import config, server, store


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

    def test_record_recovery_drill_writes_status(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db = root / "source.db"
            with mock.patch.object(config, "DB_PATH", db), \
                 mock.patch.object(config, "DATA_DIR", root):
                conn = store.connect(); store.init(conn); conn.close()
                backup = store.backup_database(root / "copy.db")
                result = server._record_recovery_drill(backup)
            self.assertTrue(result["ok"])
            status = json.loads((root / "recovery-status.json").read_text())
            self.assertTrue(status["ok"])
            self.assertGreater(status["verified_ts"], 0)

    def test_seed_recovery_drill_creates_then_skips_when_fresh(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            backups = root / "backups"; backups.mkdir()
            db = root / "source.db"
            with mock.patch.object(config, "DB_PATH", db), \
                 mock.patch.object(config, "DATA_DIR", root), \
                 mock.patch.object(config, "BACKUP_DIR", backups):
                conn = store.connect(); store.init(conn); conn.close()
                store.backup_database(backups / "newsdesk-x.db")
                status_path = root / "recovery-status.json"
                # no prior record -> drills and writes a status
                server._seed_recovery_drill()
                self.assertTrue(status_path.exists())
                first = json.loads(status_path.read_text())["verified_ts"]
                # fresh record present -> second seed must not re-run/rewrite
                time.sleep(1)
                server._seed_recovery_drill()
                self.assertEqual(json.loads(status_path.read_text())["verified_ts"], first)

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

    def test_foreign_keys_and_claim_replacement_remove_stale_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            conn = store.connect(Path(td) / "claims.db"); store.init(conn)
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            conn.execute("INSERT INTO items(id,source_id,title) VALUES('i','s','claim')")
            conn.execute("INSERT INTO clusters(id,headline) VALUES('c','claim')")
            payload = {"id": "v1", "text": "claim", "status": "single_report",
                       "independent_groups": 1, "evidence": [{
                           "item_id": "i", "quote": "claim", "quote_field": "title",
                           "quote_start": 0, "quote_end": 5, "quote_hash": "x",
                           "relation": "support"}]}
            store.replace_cluster_claims(conn, "c", [payload])
            payload["id"] = "v2"
            store.replace_cluster_claims(conn, "c", [payload])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM claim_evidence").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT relation FROM claim_evidence").fetchone()[0],
                             "support")
            conn.close()


if __name__ == "__main__":
    unittest.main()
