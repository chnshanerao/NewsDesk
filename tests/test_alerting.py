import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from newsdesk import alerting, config, entities, store


class AlertEvaluationTests(unittest.TestCase):
    def test_webhook_rejects_http_and_private_targets(self):
        with self.assertRaises(ValueError):
            alerting._validate_webhook_url("http://example.com/hook")
        with mock.patch.object(alerting.socket, "getaddrinfo", return_value=[
                (2, 1, 6, "", ("127.0.0.1", 443))]):
            with self.assertRaises(ValueError):
                alerting._validate_webhook_url("https://hook.example/path")

    def test_webhook_refuses_redirect_without_leaking_bearer(self):
        seen = []

        class Sink(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *_args):
                pass

        sink = ThreadingHTTPServer(("127.0.0.1", 0), Sink)

        class Redirect(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(302)
                self.send_header(
                    "Location", f"http://127.0.0.1:{sink.server_address[1]}/sink")
                self.end_headers()

            def log_message(self, *_args):
                pass

        redirect = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
        threads = [threading.Thread(target=s.serve_forever, daemon=True)
                   for s in (sink, redirect)]
        for thread in threads:
            thread.start()
        try:
            with mock.patch.object(
                    config, "ALERT_WEBHOOK",
                    f"http://127.0.0.1:{redirect.server_address[1]}/start"), \
                    mock.patch.object(config, "ALERT_WEBHOOK_TOKEN", "secret"), \
                    mock.patch.object(alerting, "_validate_webhook_url"):
                with self.assertRaises(Exception):
                    alerting._post_webhook({"event": "test"})
            self.assertEqual(seen, [])
        finally:
            for service in (redirect, sink):
                service.shutdown()
                service.server_close()
            for thread in threads:
                thread.join(timeout=2)

    def test_structured_asset_alert_uses_persisted_relation(self):
        with tempfile.TemporaryDirectory() as td:
            conn = store.connect(Path(td) / "asset.db"); store.init(conn)
            entities.sync_catalog(conn)
            now = 2_000_000_000
            conn.execute("INSERT INTO clusters(id,headline,last_ts,cred,rank,topics) "
                         "VALUES('c','Microsoft update',?,80,1,'[]')", (now,))
            entities.link_cluster(conn, "c", "Microsoft update")
            aid = store.create_alert(conn, name="msft", q="asset:MSFT", min_cred=40)
            alert = dict(next(x for x in store.alerts(conn) if x["id"] == aid))
            self.assertEqual([x["id"] for x in alerting.matching_clusters(
                conn, alert, since_ts=0)], ["c"])
            conn.close()

    def test_new_matches_deliver_once(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "a.db"
            with mock.patch.object(config, "DB_PATH", db), \
                    mock.patch.object(config, "ALERT_WEBHOOK", "https://hook.invalid"), \
                    mock.patch.object(alerting, "_post_webhook") as post:
                conn = store.connect(); store.init(conn)
                now = 2_000_000_000
                conn.execute("INSERT INTO clusters(id,headline,url,last_ts,cred,relevance,rank,topics) "
                             "VALUES('c','rate cut','https://x',?,80,.8,.8,'[\"macro\"]')", (now,))
                store.create_alert(conn, name="rates", q="rate", topic="macro", min_cred=40)
                with mock.patch.object(alerting.time, "time", return_value=now):
                    first = alerting.evaluate(conn)
                    second = alerting.evaluate(conn)
                self.assertEqual(first[0]["new_count"], 1)
                self.assertEqual(second[0]["new_count"], 0)
                post.assert_called_once()
                conn.close()

    def test_failed_webhook_is_persisted_and_retried(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "retry.db"
            with mock.patch.object(config, "DB_PATH", db), \
                    mock.patch.object(config, "ALERT_WEBHOOK", "https://hook.invalid"):
                conn = store.connect(); store.init(conn)
                now = 2_000_000_000
                conn.execute("INSERT INTO clusters(id,headline,url,last_ts,cred,relevance,rank,topics) "
                             "VALUES('c','rate cut','https://x',?,80,.8,.8,'[\"macro\"]')", (now,))
                store.create_alert(conn, name="rates", q="rate", topic="macro", min_cred=40)
                with mock.patch.object(alerting.time, "time", return_value=now), \
                        mock.patch.object(alerting, "_post_webhook", side_effect=OSError("down")):
                    result = alerting.evaluate(conn)
                self.assertFalse(result[0]["delivered"])
                queued = conn.execute("SELECT * FROM alert_outbox").fetchone()
                self.assertEqual(queued["attempts"], 1)
                with mock.patch.object(alerting, "_post_webhook") as post:
                    retried = alerting.deliver_pending(conn, now=queued["next_attempt_ts"])
                self.assertTrue(retried[0]["delivered"])
                post.assert_called_once()
                self.assertIsNotNone(conn.execute(
                    "SELECT delivered_ts FROM alert_outbox").fetchone()[0])
                conn.close()


if __name__ == "__main__":
    unittest.main()
