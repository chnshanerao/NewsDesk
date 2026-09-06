import tempfile
import unittest
from pathlib import Path

from newsdesk import markets, store


class MarketStoreTests(unittest.TestCase):
    def test_snapshot_history_and_watchlist(self):
        with tempfile.TemporaryDirectory() as td:
            conn = store.connect(Path(td) / "m.db"); store.init(conn)
            snap = {"asof": 100, "instruments": [
                {"symbol": "XAUUSD", "price": 2000, "asset": "commodity",
                 "currency": "USD", "source": "test"}]}
            self.assertEqual(store.record_market_snapshot(conn, snap), 1)
            self.assertEqual(len(store.market_history(conn, "XAUUSD", 0)), 1)
            store.add_watch(conn, "XAUUSD")
            self.assertEqual(store.watchlist(conn)[0]["symbol"], "XAUUSD")
            self.assertTrue(store.remove_watch(conn, "XAUUSD"))
            conn.close()

    def test_prune_and_latest_market_history(self):
        with tempfile.TemporaryDirectory() as td:
            conn = store.connect(Path(td) / "m.db"); store.init(conn)
            conn.executemany("INSERT INTO market_ticks(symbol,ts,price) VALUES(?,?,?)",
                             [("TEST", ts, float(ts)) for ts in range(1, 6)])
            conn.commit()
            self.assertEqual(store.prune_market_history(conn, 2), 1)
            rows = store.market_history(conn, "TEST", 0, limit=2)
            self.assertEqual([r["ts"] for r in rows], [4, 5])
            conn.close()

    def test_asset_linkage_is_explainable(self):
        links = markets.related_symbols("美联储政策推动美债收益率与黄金上涨")
        symbols = {x["symbol"] for x in links}
        self.assertIn("UST10Yr", symbols)
        self.assertIn("XAUUSD", symbols)
        self.assertTrue(all(x["matched_terms"] for x in links))


if __name__ == "__main__":
    unittest.main()
