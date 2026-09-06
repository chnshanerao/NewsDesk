import tempfile
import unittest
from pathlib import Path

from unittest import mock

from newsdesk import alerting, store


class AlertStoreTests(unittest.TestCase):
    def test_create_list_delete(self):
        with tempfile.TemporaryDirectory() as td:
            conn = store.connect(Path(td) / "alerts.db")
            store.init(conn)
            aid = store.create_alert(conn, name="央行政策", q="央行", topic="macro",
                                     lang="zh", min_cred=60)
            row = store.alerts(conn)[0]
            self.assertEqual(row["id"], aid)
            self.assertEqual(row["q"], "央行")
            self.assertTrue(store.delete_alert(conn, aid))
            self.assertEqual(store.alerts(conn), [])
            conn.close()

    def test_evaluate_and_ack_event(self):
        with tempfile.TemporaryDirectory() as td:
            conn = store.connect(Path(td) / "events.db")
            store.init(conn)
            aid = store.create_alert(conn, name="AI", q="AI", min_cred=40)
            now = 2_000_000_000
            conn.execute(
                "INSERT INTO clusters(id,headline,last_ts,first_ts,cred,relevance,rank,topics,breakdown) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                ("c1", "AI policy changes", now, now, 70, .5, .5, '["policy"]', '{}'))
            conn.execute("UPDATE alerts SET created_ts=? WHERE id=?", (now - 1, aid))
            conn.commit()
            with mock.patch.object(alerting.time, "time", return_value=now):
                self.assertEqual(alerting.evaluate(conn, notify=False)[0]["new_count"], 1)
                self.assertEqual(alerting.evaluate(conn, notify=False)[0]["new_count"], 0)
            rows = store.alert_events(conn, unread_only=True)
            self.assertEqual(len(rows), 1)
            self.assertEqual(store.mark_alert_events_read(conn, rows[0]["id"]), 1)
            self.assertEqual(store.alert_events(conn, unread_only=True), [])
            conn.close()


if __name__ == "__main__":
    unittest.main()
