import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from newsdesk import edgar, movements, store


SOURCE_META = {
    "wire_a": {"owner": "wire-a", "source_role": "wire"},
    "paper_b": {"owner": "paper-b", "source_role": "reporting"},
}

EDGAR_META = {
    "sec_nvidia": {"owner": "sec_nvidia", "source_role": "regulatory_filing",
                   "edgar_form": "8-K", "filer_org": "NVIDIA",
                   "person_id": "person_jensen_huang",
                   "actor_kind_default": "controlled_institution"},
    "sec_scion_13f": {"owner": "sec_scion_13f", "source_role": "regulatory_filing",
                      "edgar_form": "13F-HR", "filer_org": "Scion Asset Management",
                      "person_id": "person_michael_burry",
                      "actor_kind_default": "controlled_institution"},
}


class MovementLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.connect(Path(self.tmp.name) / "movement.db")
        store.init(self.conn)
        movements.sync_catalog(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _cluster(self, cid, title, summary="", second=False):
        self.conn.execute(
            "INSERT INTO clusters(id,headline,headline_src,url,first_ts,last_ts,n_items,n_groups,"
            "best_tier,topics,cred,relevance,rank) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, title, "Wire A", "https://example.test/a", 1000, 1100,
             2 if second else 1, 2 if second else 1, 1, "[]", 80, .8, 70))
        rows = [(f"{cid}-a", "wire_a", "Wire A", title, summary, "https://example.test/a")]
        if second:
            rows.append((f"{cid}-b", "paper_b", "Paper B", title, summary,
                         "https://example.test/b"))
        self.conn.executemany(
            "INSERT INTO items(id,source_id,source_name,tier,grp,title,summary,url,lang,"
            "published_ts,fetched_ts,cluster_id) VALUES(?,?,?,1,?,?,?,?, 'en',1000,1100,?)",
            [(iid, sid, source, source, title, summary, url, cid)
             for iid, sid, source, title, summary, url in rows])
        self.conn.commit()

    def _edgar_cluster(self, cid, source_id, title, summary, url, published_ts=1000):
        self.conn.execute(
            "INSERT INTO clusters(id,headline,headline_src,url,first_ts,last_ts,n_items,n_groups,"
            "best_tier,topics,cred,relevance,rank) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, title, source_id, url, published_ts, published_ts + 100, 1, 1, 0, "[]", 80, .8, 70))
        self.conn.execute(
            "INSERT INTO items(id,source_id,source_name,tier,grp,title,summary,url,lang,"
            "published_ts,fetched_ts,cluster_id) VALUES(?,?,?,0,?,?,?,?, 'en',?,?,?)",
            (f"{cid}-a", source_id, source_id, source_id, title, summary, url,
             published_ts, published_ts + 100, cid))
        self.conn.commit()

    def test_edgar_8k_makes_draft_that_cannot_publish_without_object(self):
        self._edgar_cluster(
            "c-edgar-8k", "sec_nvidia", "NVIDIA 8-K · 2026-08-01 · 0001045810-26-000123",
            "Filed: 2026-08-01 AccNo: 0001045810-26-000123 - Item 2.01: Completion of "
            "Acquisition or Disposition of Assets",
            "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001045810")
        result = movements.extract_edgar_items(self.conn, EDGAR_META, 0)
        self.assertEqual(result["candidates"], 1)
        event = movements.list_events(self.conn, workflow="all", verification="all")["items"][0]
        self.assertEqual(event["extraction_method"], "edgar-v1")
        self.assertEqual(event["action_type"], "acquisition")
        self.assertEqual(event["workflow_status"], "draft")
        self.assertEqual(event["persons"][0]["id"], "person_jensen_huang")
        self.assertEqual(event["evidence"][0]["source_role"], "regulatory_filing")
        self.assertEqual(event["object_text"], "")
        # 缺 object_text，直接发布必被门禁拦下
        with self.assertRaisesRegex(ValueError, "object_text required"):
            movements.review_event(self.conn, event["id"], verification_status="verified",
                                   workflow_status="published")

    def test_edgar_complete_and_publish_passes_gate(self):
        self._edgar_cluster(
            "c-edgar-pub", "sec_nvidia", "NVIDIA 8-K · 2026-08-01 · 0001045810-26-000123",
            "Filed: 2026-08-01 AccNo: 0001045810-26-000123 - Item 2.01: Completion of "
            "Acquisition or Disposition of Assets",
            "https://www.sec.gov/filing/000123")
        movements.extract_edgar_items(self.conn, EDGAR_META, 0)
        mid = self.conn.execute("SELECT id FROM movement_events").fetchone()[0]
        event = movements.complete_and_publish(
            self.conn, mid, object_text="Run:ai (AI 编排软件公司)",
            amount_value_text="$700 million", reviewer="tester")
        self.assertEqual(event["workflow_status"], "published")
        self.assertEqual(event["verification_status"], "verified")
        self.assertEqual(event["object_text"], "Run:ai (AI 编排软件公司)")
        self.assertEqual(event["amount_currency"], "USD")
        self.assertIn("object", event["fact_citations"])
        self.assertIn("amount", event["fact_citations"])
        self.assertEqual(movements.list_events(self.conn)["total"], 1)

    def test_edgar_13f_attributes_to_fund_beneficiary(self):
        self._edgar_cluster(
            "c-edgar-13f", "sec_scion_13f", "Scion Asset Management 13F-HR · 2026-08-14 · 0001649339-26-000005",
            "Filed: 2026-08-14 AccNo: 0001649339-26-000005",
            "https://www.sec.gov/filing/13f-005")
        result = movements.extract_edgar_items(self.conn, EDGAR_META, 0)
        self.assertEqual(result["candidates"], 1)
        event = movements.list_events(self.conn, workflow="all", verification="all")["items"][0]
        self.assertEqual(event["action_type"], "capital_allocate")
        self.assertEqual(event["verb_code"], "portfolio_report")
        self.assertEqual(event["persons"][0]["id"], "person_michael_burry")
        # 补对象即可发布（金额可选，13F 无单一金额）
        published = movements.complete_and_publish(
            self.conn, event["id"], object_text="美股组合季度持仓（详见 13F 表）", reviewer="tester")
        self.assertEqual(published["workflow_status"], "published")
        self.assertNotIn("amount", published["fact_citations"])

    def test_13f_information_table_aggregates_repeated_issuer_rows(self):
        # 同一发行人常被拆成多行（不同管理人/投票权口径），必须合并后再排名
        xml = """<informationTable>
          <infoTable><nameOfIssuer>APPLE INC</nameOfIssuer><value>600</value>
            <shrsOrPrnAmt><sshPrnamt>6</sshPrnamt></shrsOrPrnAmt></infoTable>
          <infoTable><nameOfIssuer>APPLE INC</nameOfIssuer><value>400</value>
            <shrsOrPrnAmt><sshPrnamt>4</sshPrnamt></shrsOrPrnAmt></infoTable>
          <infoTable><nameOfIssuer>COCA COLA CO</nameOfIssuer><value>500</value>
            <shrsOrPrnAmt><sshPrnamt>5</sshPrnamt></shrsOrPrnAmt></infoTable>
        </informationTable>"""
        table = edgar.parse_information_table(xml)
        self.assertEqual(table["n_rows"], 3)
        self.assertEqual(table["n_issuers"], 2)
        self.assertEqual(table["total_value"], 1500)
        self.assertEqual(table["holdings"][0],
                         {"cusip": "", "issuer": "APPLE INC", "value": 1000, "shares": 10})

    def test_13f_aggregates_by_cusip_not_issuer_name(self):
        # 发行人名会漂移（"APPLE INC" / "APPLE INC COM"），CUSIP 稳定 —— 按 CUSIP 合并，
        # 否则相邻季 join 不上，会误判成清仓+新建仓
        xml = """<informationTable>
          <infoTable><nameOfIssuer>APPLE INC</nameOfIssuer><cusip>037833100</cusip>
            <value>600</value><shrsOrPrnAmt><sshPrnamt>6</sshPrnamt></shrsOrPrnAmt></infoTable>
          <infoTable><nameOfIssuer>APPLE INC COM</nameOfIssuer><cusip>037833100</cusip>
            <value>400</value><shrsOrPrnAmt><sshPrnamt>4</sshPrnamt></shrsOrPrnAmt></infoTable>
        </informationTable>"""
        table = edgar.parse_information_table(xml)
        self.assertEqual(table["n_issuers"], 1)
        self.assertEqual(table["holdings"][0]["cusip"], "037833100")
        self.assertEqual(table["holdings"][0]["shares"], 10)

    def test_13f_values_reported_in_thousands_are_rescaled(self):
        # 大量申报人沿用旧的千美元惯例，附表里没有单位字段，只能用每股单价反推
        def table(value, shares):
            return (f"<informationTable><infoTable><nameOfIssuer>APPLE INC</nameOfIssuer>"
                    f"<value>{value}</value><shrsOrPrnAmt><sshPrnamt>{shares}</sshPrnamt>"
                    f"<sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt></infoTable></informationTable>")
        # 每股 $0.29 不可能是股价 → 实为千美元
        thousands = edgar.parse_information_table(table(65950296, 227917808))
        self.assertEqual(thousands["unit_scale"], 1000)
        self.assertEqual(thousands["total_value"], 65950296000)
        # 每股 $289 是正常股价 → 已是整美元，不得再乘 1000
        dollars = edgar.parse_information_table(table(65950296923, 227917808))
        self.assertEqual(dollars["unit_scale"], 1)
        self.assertEqual(dollars["total_value"], 65950296923)
        # 没有股数就无从反推，一律不缩放（宁可少乘，不可凭空放大三个数量级）
        self.assertEqual(edgar.parse_information_table(table(1000, 0))["unit_scale"], 1)

    def _pending_13f(self, cid="c-13f-auto", url="https://www.sec.gov/filing/13f-auto"):
        self._edgar_cluster(
            cid, "sec_scion_13f",
            "Scion Asset Management 13F-HR · 2026-08-14 · 0001649339-26-000009",
            "Filed: 2026-08-14 AccNo: 0001649339-26-000009", url)
        movements.extract_edgar_items(self.conn, EDGAR_META, 0)
        return self.conn.execute("SELECT id FROM movement_events").fetchone()[0]

    def test_13f_autocomplete_publishes_object_derived_from_filing(self):
        movement_id = self._pending_13f()
        summary = {"object_text": "2 个头寸；前 2 大持仓：APPLE INC $1bn、COCA COLA CO $500m（占组合 100%）",
                   "amount_value_text": "$1.50 billion",
                   "table_url": "https://www.sec.gov/Archives/edgar/data/1/2/3.xml"}
        with mock.patch.object(edgar, "holdings_from_filing", return_value=summary):
            result = movements.autocomplete_13f(self.conn)
        self.assertEqual((result["scanned"], result["published"], result["failed"]), (1, 1, 0))
        event = movements.list_events(self.conn)["items"][0]
        self.assertEqual(event["id"], movement_id)
        self.assertEqual(event["workflow_status"], "published")
        # 自动补全必须与人工补全在审计上可区分
        self.assertEqual(event["extraction_method"], "edgar-13f-v1")
        self.assertEqual(event["object_text"], summary["object_text"])
        self.assertEqual(event["amount_currency"], "USD")
        self.assertIn("object", event["fact_citations"])
        self.assertIn("amount", event["fact_citations"])
        self.assertEqual(self.conn.execute(
            "SELECT amount_usd_text FROM movement_events WHERE id=?",
            (movement_id,)).fetchone()[0], "1500000000.0")
        # 已发布后不再被重复处理
        with mock.patch.object(edgar, "holdings_from_filing", return_value=summary) as fetch_again:
            self.assertEqual(movements.autocomplete_13f(self.conn)["scanned"], 0)
            fetch_again.assert_not_called()

    def test_13f_stays_draft_when_holdings_table_unavailable(self):
        movement_id = self._pending_13f()
        with mock.patch.object(edgar, "holdings_from_filing", return_value=None):
            result = movements.autocomplete_13f(self.conn)
        self.assertEqual((result["published"], result["failed"]), (0, 1))
        self.assertEqual(movements.list_events(self.conn)["total"], 0)
        row = self.conn.execute(
            "SELECT workflow_status,extraction_method,object_text FROM movement_events WHERE id=?",
            (movement_id,)).fetchone()
        # 取不到附表就留在分诊队列等人工，绝不编造对象
        self.assertEqual((row["workflow_status"], row["extraction_method"], row["object_text"]),
                         ("draft", "edgar-v1", ""))

    def _hold(self, period, cusip, issuer, value, shares, filer="person_michael_burry",
              disclosed_ts=1000):
        self.conn.execute(
            "INSERT INTO edgar_holdings(filer_key,period,cusip,issuer,value_usd,shares,"
            "filer_org,filing_url,table_url,disclosed_ts) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (filer, period, cusip, issuer, value, shares, "Scion Asset Management",
             f"https://www.sec.gov/filing/{filer}-{period}", None, disclosed_ts))
        self.conn.commit()

    def test_13f_deltas_classify_and_gate_position_changes(self):
        q1, q2 = "2026-03-31", "2026-06-30"
        # 新建仓：Q1 无、Q2 $3B（价$300）→ 建仓规模 ≥ $200M
        self._hold(q2, "AAA", "New Bet Corp", 3e9, 10_000_000)
        # 清仓：Q1 $1B（价$200）、Q2 无 → 清仓规模 ≥ $200M
        self._hold(q1, "BBB", "Exited Co", 1e9, 5_000_000)
        # 加仓：+19M 股 × $100 = $1.9B ≥ 绝对门槛 $1B
        self._hold(q1, "CCC", "Bigger Stake Inc", 1e8, 1_000_000)
        self._hold(q2, "CCC", "Bigger Stake Inc", 2e9, 20_000_000)
        # 小额减仓：-100k 股 × $100 = $10M，相对 10% —— 三道门槛全不过，不发布
        self._hold(q1, "DDD", "Tiny Trim Co", 1e8, 1_000_000)
        self._hold(q2, "DDD", "Tiny Trim Co", 9e7, 900_000)
        # 相对门槛减仓：-2M 股（50%）× $300 = $600M ≥ $200M 且 ≥25%
        self._hold(q1, "EEE", "Half Sold Ltd", 1.2e9, 4_000_000)
        self._hold(q2, "EEE", "Half Sold Ltd", 6e8, 2_000_000)

        result = movements.compute_13f_deltas(self.conn)
        self.assertEqual(result["published"], 4)  # AAA/BBB/CCC/EEE，DDD 被门槛拦下

        published = movements.list_events(self.conn)["items"]
        verbs = {e["object_text"].split("（")[0]: e["verb_code"] for e in published}
        self.assertEqual(verbs["New Bet Corp"], "position_open")
        self.assertEqual(verbs["Exited Co"], "position_close")
        self.assertEqual(verbs["Bigger Stake Inc"], "position_increase")
        self.assertEqual(verbs["Half Sold Ltd"], "position_decrease")
        self.assertNotIn("Tiny Trim Co", verbs)  # 小额调仓不刷屏

        sample = next(e for e in published if e["verb_code"] == "position_increase")
        self.assertEqual(sample["workflow_status"], "published")
        self.assertEqual(sample["verification_status"], "verified")
        self.assertEqual(sample["extraction_method"], "edgar-13f-delta-v1")
        self.assertEqual(sample["persons"][0]["id"], "person_michael_burry")
        # 两份 13F 都作一次源引用，且关键字段全有引用
        self.assertEqual(len([e for e in sample["evidence"]
                              if e["source_role"] == "regulatory_filing"]), 2)
        for field in ("actor", "action", "object", "disclosure_date", "amount"):
            self.assertIn(field, sample["fact_citations"])

        # 幂等：再算一次不产生重复
        self.assertEqual(movements.compute_13f_deltas(self.conn)["published"], 0)
        self.assertEqual(movements.list_events(self.conn)["total"], 4)

    def test_13f_delta_continuity_adds_materiality_bonus(self):
        # 同一标的连续两季同向加仓，第二段应拿到 +0.1 的 materiality 加分
        for i, period in enumerate(("2026-03-31", "2026-06-30", "2026-09-30")):
            self._hold(period, "FFF", "Steady Buyer Co", 1.5e9 * (i + 1), 15_000_000 * (i + 1))
        movements.compute_13f_deltas(self.conn)
        rows = {e["object_text"]: e["materiality_score"]
                for e in movements.list_events(self.conn, order="recent")["items"]}
        scores = sorted(rows.values())
        # 第一段 .9（trade $1.5B），第二段连续同向 → 1.0
        self.assertEqual(scores, [0.9, 1.0])

    def test_13f_backfill_stores_holdings_idempotently(self):
        movement_id = self._pending_13f(url="https://www.sec.gov/filing/13f-backfill")
        summary = {"period": "2026-06-30",
                   "table_url": "https://www.sec.gov/Archives/edgar/data/1/2/t.xml",
                   "holdings": [{"cusip": "037833100", "issuer": "APPLE INC",
                                 "value": 2.0e9, "shares": 10_000_000},
                                {"cusip": "191216100", "issuer": "COCA COLA CO",
                                 "value": 5.0e8, "shares": 8_000_000}]}
        with mock.patch.object(edgar, "holdings_from_filing", return_value=summary) as fetch:
            r1 = movements.backfill_13f_holdings(self.conn)
            self.assertEqual((r1["fetched"], r1["filings_stored"]), (1, 1))
            self.assertEqual(fetch.call_count, 1)
            # 同一份申报已入库 → 二次运行跳过、不再联网
            r2 = movements.backfill_13f_holdings(self.conn)
            self.assertEqual((r2["fetched"], r2["skipped"]), (0, 1))
            self.assertEqual(fetch.call_count, 1)
        rows = self.conn.execute(
            "SELECT filer_key,period,cusip,shares FROM edgar_holdings ORDER BY cusip").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["filer_key"], "person_michael_burry")
        self.assertEqual(rows[0]["period"], "2026-06-30")
        self.assertEqual(rows[0]["shares"], 10_000_000)
        self.assertTrue(movement_id)

    def test_edgar_8k_without_mapped_item_is_skipped(self):
        self._edgar_cluster(
            "c-edgar-skip", "sec_nvidia", "NVIDIA 8-K · 2026-08-02 · 0001045810-26-000200",
            "Filed: 2026-08-02 AccNo: 0001045810-26-000200 - Item 7.01: Regulation FD Disclosure",
            "https://www.sec.gov/filing/000200")
        result = movements.extract_edgar_items(self.conn, EDGAR_META, 0)
        self.assertEqual(result["candidates"], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM movement_events").fetchone()[0], 0)

    def test_schema_v12_is_idempotent_and_persons_are_separate(self):
        store.init(self.conn)
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], store.SCHEMA_VERSION)
        self.assertGreaterEqual(self.conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0], 53)
        self.assertIsNotNone(self.conn.execute(
            "SELECT 1 FROM persons WHERE id='person_sam_altman'").fetchone())
        policy = self.conn.execute(
            "SELECT category FROM persons WHERE id='person_kazuo_ueda'").fetchone()
        self.assertEqual(policy["category"], "policy")

    def test_execution_status_defaults_to_completed(self):
        self._cluster("c-status", "Sam Altman invested $375 million in Helion")
        movements.extract_recent(self.conn, SOURCE_META, 0)
        event = movements.list_events(
            self.conn, workflow="all", verification="all")["items"][0]
        self.assertEqual(event["execution_status"], "completed")

    def test_catalog_sync_retires_but_never_deletes_historical_people(self):
        self.conn.execute(
            "INSERT INTO persons(id,name,category,signal_prior,review_status,created_ts,updated_ts) "
            "VALUES('person_historical','Historical Person','investor',.5,'human_approved',1,1)")
        self.conn.commit()
        movements.sync_catalog(self.conn)
        row = self.conn.execute(
            "SELECT review_status FROM persons WHERE id='person_historical'").fetchone()
        self.assertEqual(row["review_status"], "retired")

    def test_completed_action_creates_auditable_candidate(self):
        self._cluster("c-action", "Sam Altman invested $375 million in Helion", second=True)
        result = movements.extract_recent(self.conn, SOURCE_META, 0)
        self.assertEqual(result["candidates"], 1)
        event = movements.list_events(
            self.conn, workflow="all", verification="all")["items"][0]
        self.assertEqual(event["actor_kind"], "associated_institution")
        self.assertEqual(event["amount_currency"], "USD")
        self.assertEqual(event["verification_status"], "verified")
        self.assertEqual(event["workflow_status"], "draft")
        self.assertIsNone(event["occurred_from_ts"])
        self.assertEqual(len(event["evidence"]), 2)
        self.assertIn("amount", event["fact_citations"])

    def test_explicit_personal_money_is_not_confused_with_company_money(self):
        self._cluster("c-personal", "Sam Altman personally invested $375 million in Helion")
        movements.extract_recent(self.conn, SOURCE_META, 0)
        event = movements.list_events(
            self.conn, workflow="all", verification="all")["items"][0]
        self.assertEqual(event["actor_kind"], "personal")
        self.assertEqual(event["persons"][0]["role"], "beneficial_owner")

    def test_speech_and_plans_do_not_become_movements(self):
        self._cluster("c-speech", "Elon Musk says AI will change every industry")
        self._cluster("c-plan", "Mark Zuckerberg plans to invest $60 billion in AI")
        result = movements.extract_recent(self.conn, SOURCE_META, 0)
        self.assertEqual(result["candidates"], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM movement_events").fetchone()[0], 0)

    def test_company_action_and_person_in_separate_fields_are_not_joined(self):
        self._cluster("c-misattribution", "Tesla invested $5 billion",
                      "Elon Musk attended a conference", second=True)
        result = movements.extract_recent(self.conn, SOURCE_META, 0)
        self.assertEqual(result["candidates"], 0)

    def test_generic_completed_phrase_is_not_misclassified_as_acquisition(self):
        self._cluster("c-role-change", "Tim Cook completed a leadership transition")
        result = movements.extract_recent(self.conn, SOURCE_META, 0)
        self.assertEqual(result["candidates"], 0)

    def test_sold_out_crowd_is_not_a_divestment(self):
        self._cluster("c-sold-out", "Elon Musk addressed a sold-out crowd")
        result = movements.extract_recent(self.conn, SOURCE_META, 0)
        self.assertEqual(result["candidates"], 0)

    def test_different_objects_and_amounts_never_corroborate_each_other(self):
        self._cluster("c-conflict", "placeholder", second=True)
        self.conn.execute(
            "UPDATE items SET title='Sam Altman invested $100 million in Helion',summary='' "
            "WHERE id='c-conflict-a'")
        self.conn.execute(
            "UPDATE items SET title='Sam Altman invested $200 million in Reddit',summary='' "
            "WHERE id='c-conflict-b'")
        self.conn.commit()
        movements.extract_recent(self.conn, SOURCE_META, 0)
        event = movements.list_events(
            self.conn, workflow="all", verification="all")["items"][0]
        self.assertEqual(event["verification_status"], "candidate")
        self.assertEqual(len(event["evidence"]), 1)
        self.assertEqual(len(event["fact_citations"]["amount"]), 1)

    def test_missing_amount_can_support_action_but_not_amount_field(self):
        self._cluster("c-compatible", "placeholder", second=True)
        self.conn.execute(
            "UPDATE items SET title='Sam Altman invested $100 million in Helion',summary='' "
            "WHERE id='c-compatible-a'")
        self.conn.execute(
            "UPDATE items SET title='Sam Altman invested in Helion',summary='' "
            "WHERE id='c-compatible-b'")
        self.conn.commit()
        movements.extract_recent(self.conn, SOURCE_META, 0)
        event = movements.list_events(
            self.conn, workflow="all", verification="all")["items"][0]
        self.assertEqual(event["verification_status"], "verified")
        self.assertEqual(len(event["evidence"]), 2)
        self.assertEqual(len(event["fact_citations"]["amount"]), 1)

    def test_item_deletion_preserves_long_term_evidence(self):
        self._cluster("c-delete", "Warren Buffett invested $1 billion in a company")
        movements.extract_recent(self.conn, SOURCE_META, 0)
        self.conn.execute("DELETE FROM items WHERE id='c-delete-a'")
        self.conn.commit()
        evidence = self.conn.execute("SELECT item_id,url,quote FROM movement_evidence").fetchone()
        self.assertIsNone(evidence["item_id"])
        self.assertTrue(evidence["url"])
        self.assertTrue(evidence["quote"])

    def test_public_feed_excludes_unreviewed_candidates(self):
        self._cluster("c-draft", "Elon Musk completed the acquisition for $44 billion")
        movements.extract_recent(self.conn, SOURCE_META, 0)
        self.assertEqual(movements.list_events(self.conn)["total"], 0)
        self.assertEqual(movements.list_events(
            self.conn, workflow="all", verification="all")["total"], 1)

    def test_publish_gate_requires_verification_and_independent_evidence(self):
        self._cluster("c-gate", "Sam Altman invested $375 million in Helion")
        movements.extract_recent(self.conn, SOURCE_META, 0)
        movement_id = self.conn.execute("SELECT id FROM movement_events").fetchone()[0]
        with self.assertRaisesRegex(ValueError, "must be verified"):
            movements.review_event(
                self.conn, movement_id, verification_status="candidate",
                workflow_status="published")
        with self.assertRaisesRegex(ValueError, "primary record or two independent"):
            movements.review_event(
                self.conn, movement_id, verification_status="verified",
                workflow_status="published")

    def test_publish_gate_accepts_two_independent_owners(self):
        self._cluster("c-publish", "Sam Altman invested $375 million in Helion", second=True)
        movements.extract_recent(self.conn, SOURCE_META, 0)
        movement_id = self.conn.execute("SELECT id FROM movement_events").fetchone()[0]
        item = movements.review_event(
            self.conn, movement_id, verification_status="verified",
            workflow_status="published")
        self.assertEqual(item["workflow_status"], "published")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM movement_reviews WHERE movement_id=?", (movement_id,)).fetchone()[0], 1)
        self.assertEqual(movements.list_events(self.conn)["total"], 1)
        self.conn.execute("UPDATE movement_events SET observed_fact='human reviewed fact' WHERE id=?",
                          (movement_id,))
        self.conn.commit()
        movements.extract_recent(self.conn, SOURCE_META, 0)
        self.assertEqual(self.conn.execute(
            "SELECT observed_fact FROM movement_events WHERE id=?", (movement_id,)).fetchone()[0],
            "human reviewed fact")

    def test_curated_seed_is_idempotent_and_passes_same_publish_gate(self):
        entry = {
            "id": "mov_curated_test", "action_type": "capital_allocate",
            "verb_code": "invested", "actor_kind": "personal", "title": "Curated action",
            "object_text": "Helion", "occurred_from_ts": 1000, "disclosed_ts": 1100,
            "amount_value_text": "$375 million", "amount_currency": "USD",
            "amount_basis": "personal investment", "confidence": .98,
            "materiality_score": .9, "observed_fact": "Sam Altman invested in Helion.",
            "execution_status": "contracted",
            "analytical_boundary": "This establishes the investment, not its future return.",
            "persons": [{"person_id": "person_sam_altman", "role": "beneficial_owner",
                         "attribution_confidence": 1.0, "control_basis": "personal investment"}],
            "themes": ["energy"],
            "evidence": [{"id": "mev_curated_test", "source_name": "Primary filing",
                          "source_owner": "primary", "source_role": "regulatory_filing",
                          "url": "https://example.test/filing", "published_ts": 1100,
                          "quote": "Sam Altman invested $375 million in Helion.",
                          "independence_group": "primary", "fields":
                          ["actor", "action", "object", "amount", "disclosure_date"]}]
        }
        self.assertEqual(movements.sync_curated(self.conn, [entry]), 1)
        self.assertEqual(movements.sync_curated(self.conn, [entry]), 0)
        event = movements.list_events(self.conn)["items"][0]
        self.assertEqual(event["id"], "mov_curated_test")
        self.assertEqual(event["workflow_status"], "published")
        self.assertEqual(event["execution_status"], "contracted")

        # A later curated schema revision may add a normalized amount without
        # reopening or rewriting the already-published evidence ledger.
        self.conn.execute(
            "UPDATE movement_events SET amount_usd_text=NULL WHERE id='mov_curated_test'")
        self.conn.commit()
        entry["amount_usd_text"] = "375000000"
        self.assertEqual(movements.sync_curated(self.conn, [entry]), 0)
        self.assertEqual(self.conn.execute(
            "SELECT amount_usd_text FROM movement_events WHERE id='mov_curated_test'"
        ).fetchone()[0], "375000000")


if __name__ == "__main__":
    unittest.main()
