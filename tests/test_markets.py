import json
import unittest
from unittest import mock

from newsdesk import markets


class MarketSnapshotTests(unittest.TestCase):
    def test_equity_parser(self):
        raw = 'v_s_sh000001="1~上证指数~000001~3930.12~-11.97~-0.30~1~2";'
        with mock.patch.object(markets, "_get", return_value=raw):
            row = markets._equities()[0]
        self.assertEqual(row["symbol"], "sh000001")
        self.assertEqual(row["price"], 3930.12)
        self.assertEqual(row["change_pct"], -0.30)

    def test_fx_parser_and_disclaimer(self):
        raw = json.dumps({"rates": {"CNY": 6.7, "EUR": 0.86}})
        with mock.patch.object(markets, "_get", return_value=raw):
            rows = markets._fx()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(x["asset"] == "fx" for x in rows))

    def test_treasury_curve_change_in_basis_points(self):
        raw = 'Date,"2 Yr","10 Yr","30 Yr"\n09/04/2026,4.37,4.78,5.24\n09/03/2026,4.34,4.77,5.25\n'
        with mock.patch.object(markets, "_get", return_value=raw), \
                mock.patch.object(markets.time, "gmtime") as clock:
            clock.return_value.tm_year = 2026
            rows = markets._rates()
        self.assertEqual(rows[0]["change_bps"], 3.0)
        self.assertEqual(rows[1]["price"], 4.78)

    def test_metals(self):
        with mock.patch.object(markets, "_get",
                               side_effect=['{"price":4431.1}', '{"price":66.3}']):
            rows = markets._metals()
        self.assertEqual([x["symbol"] for x in rows], ["XAUUSD", "XAGUSD"])

    def test_energy(self):
        raw = 'v_hf_CL="91.31,0.02,91.22,91.24,92.17,88.72,04:59:58";'
        with mock.patch.object(markets, "_get", return_value=raw):
            row = markets._energy()[0]
        self.assertEqual(row["symbol"], "WTI")
        self.assertEqual(row["change_pct"], 0.02)


if __name__ == "__main__":
    unittest.main()
