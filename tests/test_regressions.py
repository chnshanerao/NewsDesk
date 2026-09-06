import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from newsdesk import cluster, config, credibility, fetch, pipeline, server, store
from newsdesk.crosslingual import bridge_score, features
from newsdesk.normalize import gram_set, parse_time, simhash, tokens


def _item(item_id, title):
    toks = tokens(title)
    return {
        "id": item_id,
        "title": title,
        "grams": set(toks),
        "simhash": simhash(toks),
    }


class NormalizeTimeTests(unittest.TestCase):
    def test_rfc822_timezone_is_respected(self):
        actual = parse_time("Wed, 04 Jun 2025 10:22:00 +0800")
        expected = int(datetime(2025, 6, 4, 2, 22, tzinfo=timezone.utc).timestamp())
        self.assertEqual(actual, expected)

    def test_iso_zulu_and_epoch_milliseconds(self):
        expected = int(datetime(2025, 6, 4, 2, 22, tzinfo=timezone.utc).timestamp())
        self.assertEqual(parse_time("2025-06-04T02:22:00Z"), expected)
        self.assertEqual(parse_time(expected * 1000), expected)

    def test_invalid_time_uses_fallback(self):
        self.assertEqual(parse_time("not a date", fallback=123), 123)

    def test_future_feed_time_is_clamped(self):
        self.assertEqual(fetch._safe_published(2000, 1000), (1000, True))
        self.assertEqual(fetch._safe_published(1200, 1000), (1200, False))


class ClusterConflictTests(unittest.TestCase):
    def test_different_dates_are_not_merged(self):
        a = _item("a", "纽约股市三大股指12日上涨")
        b = _item("b", "纽约股市三大股指13日上涨")
        self.assertEqual(cluster.conflicting(a, b, {"a": set(), "b": set()}),
                         "数字/日期互斥")
        groups = cluster.build([a, b])
        self.assertEqual(len(groups), 2)

    def test_mutually_exclusive_rare_entities_are_rejected(self):
        a = _item("a", "贵州省委主要负责同志职务调整")
        b = _item("b", "天津市委主要负责同志职务调整")
        rare = {"a": {"贵州"}, "b": {"天津"}}
        self.assertEqual(cluster.conflicting(a, b, rare), "关键实体词互斥")


class CrossLingualClusterTests(unittest.TestCase):
    def test_fed_rate_cut_with_matching_number_is_merged(self):
        zh = _item("zh", "美联储宣布降息25个基点")
        en = _item("en", "Federal Reserve cuts interest rates by 25 basis points")
        self.assertGreaterEqual(bridge_score(features(zh["title"]), features(en["title"])),
                                0.5)
        self.assertEqual(len(cluster.build([zh, en])), 1)

    def test_company_layoff_without_number_can_bridge(self):
        zh = _item("zh", "微软宣布新一轮裁员")
        en = _item("en", "Microsoft announces new round of layoffs")
        self.assertEqual(len(cluster.build([zh, en])), 1)

    def test_same_company_action_different_object_does_not_merge(self):
        for left, right in [
            ("苹果公司发布新产品iPhone", "Apple launches new Mac product"),
            ("微软宣布收购游戏公司", "Microsoft acquires a cybersecurity company"),
            ("苹果公司因隐私违规被罚款", "Apple fined for antitrust violations"),
        ]:
            with self.subTest(left=left):
                self.assertEqual(len(cluster.build([_item("a", left), _item("b", right)])), 2)

    def test_conflicting_numbers_do_not_merge(self):
        zh = _item("zh", "美联储宣布降息25个基点")
        en = _item("en", "Federal Reserve cuts interest rates by 50 basis points")
        self.assertEqual(bridge_score(features(zh["title"]), features(en["title"])), 0.0)
        self.assertEqual(len(cluster.build([zh, en])), 2)

    def test_equivalent_cross_language_money_units_merge(self):
        zh = _item("zh", "苹果公司因违规被罚款5亿美元")
        en = _item("en", "Apple fined $500 million for violations")
        self.assertEqual(len(cluster.build([zh, en])), 1)

    def test_different_currencies_do_not_merge(self):
        zh = _item("zh", "苹果公司因违规被罚款5亿元人民币")
        en = _item("en", "Apple fined $500 million for violations")
        self.assertEqual(len(cluster.build([zh, en])), 2)

    def test_generic_country_words_do_not_bridge(self):
        zh = _item("zh", "美国宣布新的经济政策")
        en = _item("en", "US announces a new economic policy")
        self.assertFalse(features(zh["title"]).bridge_keys)
        self.assertFalse(features(en["title"]).bridge_keys)
        self.assertEqual(len(cluster.build([zh, en])), 2)

    def test_opposite_rate_actions_do_not_merge(self):
        zh = _item("zh", "欧洲央行宣布降息")
        en = _item("en", "European Central Bank raises interest rates")
        self.assertEqual(len(cluster.build([zh, en])), 2)

    def test_crosslingual_evaluation_set(self):
        """Representative pairs make bridge coverage and precision regressions visible."""
        positives = [
            ("日本央行宣布加息25个基点",
             "Bank of Japan raises interest rates by 25 basis points"),
            ("台积电上调全年业绩预期", "TSMC raises full-year earnings forecast"),
            ("世界卫生组织批准新疫苗", "WHO approves a new vaccine"),
            ("亚马逊宣布新一轮裁员", "Amazon announces another round of layoffs"),
            ("特斯拉在美国召回汽车", "Tesla recalls vehicles in the United States"),
        ]
        negatives = [
            # Same institution, materially different event.
            ("日本央行宣布加息", "Bank of Japan cuts interest rates"),
            # Same action, different named entity.
            ("亚马逊宣布裁员", "Microsoft announces layoffs"),
            # Generic geography/action remains insufficient.
            ("欧洲宣布新制裁", "Europe announces new sanctions"),
            # Conflicting structured values must block a merge.
            ("特斯拉召回2万辆汽车", "Tesla recalls 30,000 vehicles"),
        ]
        for zh, en in positives:
            with self.subTest(zh=zh):
                self.assertGreaterEqual(bridge_score(features(zh), features(en)), 0.5)
        for zh, en in negatives:
            with self.subTest(zh=zh):
                self.assertEqual(bridge_score(features(zh), features(en)), 0.0)


class RelevanceTests(unittest.TestCase):
    def test_english_keyword_matching_is_case_insensitive(self):
        profile = {
            "topics": {
                "macro": {"weight": 1.0, "keywords": ["inflation"]},
            },
            "muted_keywords": [],
            "noise_keywords": [],
            "boost_keywords": [],
        }
        lower = credibility.relevance("inflation slows", [], set(), profile)
        mixed = credibility.relevance("Inflation Slows", [], set(), profile)
        upper = credibility.relevance("INFLATION SLOWS", [], set(), profile)
        self.assertEqual(lower[0], mixed[0])
        self.assertEqual(lower[0], upper[0])
        self.assertEqual(mixed[1], ["macro"])


class EvidenceSemanticsTests(unittest.TestCase):
    def test_official_statement_is_not_independent_confirmation(self):
        score, note = credibility.corroboration(1, 0, {"official"})
        self.assertLess(score, 0.5)
        self.assertIn("尚无独立来源核实", note)

    def test_two_groups_are_independently_supported(self):
        score, note = credibility.corroboration(2, 1, {"reporting"})
        self.assertGreater(score, 0.5)
        self.assertIn("2 个独立信源集团", note)

    def test_aggregator_does_not_promote_cluster_evidence_status(self):
        def item(item_id, group, role):
            return {
                "id": item_id, "grp": group, "src_role": role,
                "source_name": group, "tier": 1, "title": "Company reports earnings",
                "summary": "", "published_ts": 1000, "fetched_ts": 1000,
                "src_topics": [],
            }

        profile = {"topics": {}, "muted_keywords": [], "noise_keywords": [],
                   "boost_keywords": []}
        result = credibility.score_cluster(
            [item("original", "publisher", "reporting"),
             item("copy", "portal", "aggregator")],
            profile, {1: 0.8}, now=1000)
        self.assertEqual(result["n_groups"], 1)
        self.assertEqual(result["breakdown"]["evidence"]["status"], "SINGLE_REPORT")
        self.assertEqual(result["breakdown"]["corroboration"]["excluded_groups"],
                         ["portal"])


class FeedFreshnessTests(unittest.TestCase):
    def test_empty_and_frozen_feeds_are_unhealthy(self):
        now = 2_000_000_000
        source = {"freshness_hours": 72}
        empty = pipeline._health_layers({"ok": True, "n_items": 0}, source, now)
        frozen = pipeline._health_layers(
            {"ok": True, "n_items": 3, "newest_ts": now - 10 * 86400}, source, now)
        healthy = pipeline._health_layers(
            {"ok": True, "n_items": 3, "newest_ts": now - 3600}, source, now)
        self.assertEqual(empty["verdict"], "unhealthy")
        self.assertEqual(empty["parse_status"], "empty")
        self.assertEqual(frozen["verdict"], "degraded")
        self.assertEqual(frozen["freshness_status"], "stale")
        self.assertEqual(healthy["verdict"], "healthy")


class ApiLanguageFilterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.db_patch = mock.patch.object(config, "DB_PATH", self.db_path)
        self.db_patch.start()
        conn = store.connect()
        store.init(conn)
        now = 2_000_000_000
        for cid, lang in (("zh-event", "zh"), ("en-event", "en")):
            conn.execute(
                "INSERT INTO clusters "
                "(id,headline,last_ts,first_ts,cred,relevance,rank,topics,breakdown) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (cid, cid, now, now, 80, 0.8, 0.8, "[]", "{}"),
            )
            conn.execute(
                "INSERT INTO items "
                "(id,source_id,title,lang,published_ts,fetched_ts,cluster_id) "
                "VALUES (?,?,?,?,?,?,?)",
                (cid + "-item", cid + "-source", cid, lang, now, now, cid),
            )
        conn.commit()
        conn.close()

        profile = {"min_relevance": 0.12, "min_credibility": 40}
        handler = server.make_handler({"sources": [], "tiers": {}}, profile, False)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        self.db_patch.stop()
        self.tmp.cleanup()

    def _feed(self, lang):
        port = self.httpd.server_address[1]
        url = (f"http://127.0.0.1:{port}/api/feed?view=all&hours=999999"
               f"&lang={lang}")
        with urllib.request.urlopen(url, timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_security_headers_and_quality_endpoint(self):
        port = self.httpd.server_address[1]
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/quality", timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
            self.assertEqual(response.headers["X-Frame-Options"], "DENY")
            self.assertIn("frame-ancestors 'none'",
                          response.headers["Content-Security-Policy"])
        self.assertIn(payload["status"], ("healthy", "healthy_with_warnings", "degraded"))
        self.assertTrue(payload["boundaries"])

    def test_benchmark_report_is_served(self):
        port = self.httpd.server_address[1]
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/report.html", timeout=3) as response:
            body = response.read().decode("utf-8")
        self.assertIn("NEWSDESK 横向评估", body)
        self.assertIn("Bloomberg Terminal", body)

    def test_cross_origin_write_is_rejected(self):
        port = self.httpd.server_address[1]
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/alert-events/read", data=b"{}",
            method="POST", headers={"Content-Type": "application/json",
                                    "Origin": "https://attacker.example"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(req, timeout=3)
        self.assertEqual(caught.exception.code, 403)

    def test_forwarded_public_origin_is_recognized(self):
        port = self.httpd.server_address[1]
        with mock.patch.object(config, "WRITE_TOKEN", "test-secret"):
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/alert-events/read", data=b"{}",
                method="POST", headers={"Content-Type": "application/json",
                                        "Origin": "https://news.example",
                                        "X-Forwarded-Host": "news.example",
                                        "X-Newsdesk-Token": "test-secret"})
            with urllib.request.urlopen(req, timeout=3) as response:
                self.assertEqual(response.status, 200)

    def test_loopback_write_still_requires_token(self):
        port = self.httpd.server_address[1]
        with mock.patch.object(config, "WRITE_TOKEN", "test-secret"):
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/alert-events/read", data=b"{}",
                method="POST", headers={"Content-Type": "application/json",
                                        "Origin": f"http://127.0.0.1:{port}"})
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(req, timeout=3)
            self.assertEqual(caught.exception.code, 401)

    def test_rejected_body_closes_connection(self):
        port = self.httpd.server_address[1]
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/alerts", data=b'{"name":"probe"}',
            method="POST", headers={"Content-Type": "application/json",
                                    "Origin": "https://attacker.example"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(req, timeout=3)
        self.assertEqual(caught.exception.code, 403)
        self.assertEqual(caught.exception.headers["Connection"], "close")

    def test_entity_catalog_endpoint_exists(self):
        port = self.httpd.server_address[1]
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/entities?q=Microsoft", timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertIn("entities", payload)

    def test_ai_radar_endpoint_exists(self):
        port = self.httpd.server_address[1]
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/ai-radar?hours=72", timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertIn("topic_counts", payload)
        self.assertIn("top_entities", payload)
        self.assertIn("sources", payload)

    def test_invalid_numeric_query_returns_400(self):
        port = self.httpd.server_address[1]
        for query in ("limit=nope", "limit=-1", "hours=0", "min_cred=nan"):
            with self.subTest(query=query), self.assertRaises(
                    urllib.error.HTTPError) as caught:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/feed?{query}", timeout=3)
            self.assertEqual(caught.exception.code, 400)

    def test_json_write_requires_an_object(self):
        port = self.httpd.server_address[1]
        with mock.patch.object(config, "WRITE_TOKEN", "test-secret"):
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/alerts", data=b"[]", method="POST",
                headers={"Content-Type": "application/json",
                         "Origin": f"http://127.0.0.1:{port}",
                         "X-Newsdesk-Token": "test-secret"})
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(req, timeout=3)
            self.assertEqual(caught.exception.code, 400)

    def test_feed_filters_english(self):
        payload = self._feed("en")
        self.assertEqual([item["id"] for item in payload["items"]], ["en-event"])
        self.assertEqual(payload["items"][0]["languages"], ["en"])

    def test_feed_filters_chinese(self):
        payload = self._feed("zh")
        self.assertEqual([item["id"] for item in payload["items"]], ["zh-event"])
        self.assertEqual(payload["items"][0]["languages"], ["zh"])


if __name__ == "__main__":
    unittest.main()
