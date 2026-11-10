#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
edinet_stock_builder.py のテスト（ネットワーク不要）。
EDINET と同じ形式のダミーデータ（UTF-16タブ区切りCSVのZIP、cp932のコードリスト）を作って検証する。

実行: python3 -m unittest test_edinet_stock_builder.py -v
"""

import io
import json
import sys
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import edinet_stock_builder as esb  # noqa: E402

HEADER = ["要素ID", "項目名", "コンテキストID", "相対年度", "連結・個別", "期間・時点", "ユニットID", "単位", "値"]


def make_csv_zip(rows, fname="XBRL_TO_CSV/jpcrp030000-asr-001_E00001-000_2026-03-31_01_2026-06-20.csv"):
    lines = ["\t".join(f'"{h}"' for h in HEADER)]
    for elem, ctx, val in rows:
        lines.append("\t".join(f'"{x}"' for x in [elem, "項目", ctx, "当期", "連結", "期間", "JPY", "円", val]))
    data = ("\r\n".join(lines) + "\r\n").encode("utf-16")  # BOM付きUTF-16
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(fname, data)
        # 監査報告書など本文以外のCSVは無視されることを確認するため追加
        zf.writestr("XBRL_TO_CSV/jpaud-aar-cn-001_E00001-000.csv",
                    ("\t".join(HEADER) + "\r\n" +
                     "jpcrp_cor:NetSalesSummaryOfBusinessResults\tx\tCurrentYearDuration\tx\tx\tx\tJPY\t円\t999\r\n"
                     ).encode("utf-16"))
    return buf.getvalue()


def make_codelist_zip(entries):
    lines = ["ダウンロード実行日,2026年11月07日現在,件数,3件",
             "ＥＤＩＮＥＴコード,提出者種別,上場区分,連結の有無,資本金,決算日,提出者名,提出者名（英字）,提出者名（ヨミ）,所在地,提出者業種,証券コード,提出者法人番号"]
    for code, name, industry, sec in entries:
        lines.append(f"{code},内国法人・組合,上場,有,100,3月31日,{name},X,X,東京都,{industry},{sec},1234567890123")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("EdinetcodeDlInfo.csv", "\r\n".join(lines).encode("cp932"))
    return buf.getvalue()


CONSOLIDATED_ROWS = [
    ("jpcrp_cor:NetSalesSummaryOfBusinessResults", "CurrentYearDuration", "120000000000"),
    ("jpcrp_cor:NetSalesSummaryOfBusinessResults", "Prior1YearDuration", "100000000000"),
    ("jpcrp_cor:ProfitLossAttributableToOwnersOfParentSummaryOfBusinessResults", "CurrentYearDuration", "5000000000"),
    ("jpcrp_cor:ProfitLossAttributableToOwnersOfParentSummaryOfBusinessResults", "Prior1YearDuration", "6000000000"),
    ("jpcrp_cor:DividendPaidPerShareSummaryOfBusinessResults", "CurrentYearDuration_NonConsolidatedMember", "45.00"),
    ("jpcrp_cor:NumberOfEmployees", "CurrentYearInstant", "3,210"),
    # 単体の数値もあるが、連結がある場合は使われないこと
    ("jpcrp_cor:NetSalesSummaryOfBusinessResults", "CurrentYearDuration_NonConsolidatedMember", "1"),
]

IFRS_ROWS = [
    ("jpcrp_cor:RevenueIFRSSummaryOfBusinessResults", "CurrentYearDuration", "2500000000000"),
    ("jpcrp_cor:RevenueIFRSSummaryOfBusinessResults", "Prior1YearDuration", "2400000000000"),
    ("jpcrp_cor:ProfitLossAttributableToOwnersOfParentIFRSSummaryOfBusinessResults", "CurrentYearDuration", "150000000000"),
    ("jpcrp_cor:ProfitLossAttributableToOwnersOfParentIFRSSummaryOfBusinessResults", "Prior1YearDuration", "120000000000"),
    ("jpcrp_cor:DividendPaidPerShareSummaryOfBusinessResults", "CurrentYearDuration_NonConsolidatedMember", "－"),
]

NONCONSOLIDATED_ROWS = [
    ("jpcrp_cor:NetSalesSummaryOfBusinessResults", "CurrentYearDuration_NonConsolidatedMember", "4928920000"),
    ("jpcrp_cor:NetSalesSummaryOfBusinessResults", "Prior1YearDuration_NonConsolidatedMember", "4928920000"),
    ("jpcrp_cor:NetIncomeLossSummaryOfBusinessResults", "CurrentYearDuration_NonConsolidatedMember", "-100436000"),
    ("jpcrp_cor:NetIncomeLossSummaryOfBusinessResults", "Prior1YearDuration_NonConsolidatedMember", "-200000000"),
    ("jpcrp_cor:DividendPaidPerShareSummaryOfBusinessResults", "CurrentYearDuration_NonConsolidatedMember", "0"),
    ("jpcrp_cor:NumberOfEmployees", "CurrentYearInstant_NonConsolidatedMember", "120"),
]


def doc(doc_id, edinet, sec, name, submit, doc_type="120", form="030000", csv_flag="1", withdrawn="0"):
    return {"docID": doc_id, "edinetCode": edinet, "secCode": sec, "filerName": name,
            "docTypeCode": doc_type, "ordinanceCode": "010", "formCode": form,
            "csvFlag": csv_flag, "withdrawalStatus": withdrawn,
            "periodEnd": "2026-03-31", "submitDateTime": submit}


CONSOLIDATED_ROWS_NO_DIV = [r for r in CONSOLIDATED_ROWS if "Dividend" not in r[0]]


class ParseTests(unittest.TestCase):
    def test_consolidated(self):
        idx = esb.build_fact_index(esb.read_main_csv_rows(make_csv_zip(CONSOLIDATED_ROWS)))
        fin = esb.extract_financials(idx)
        self.assertEqual(fin["basis"], "consolidated")
        self.assertEqual(fin["revenue"], 120_000_000_000)
        self.assertEqual(fin["revenue_prior"], 100_000_000_000)
        self.assertEqual(fin["net_income"], 5_000_000_000)
        self.assertEqual(fin["dividend_per_share"], 45.0)
        self.assertEqual(fin["employees"], 3210)

    def test_ifrs_and_dash_value(self):
        fin = esb.extract_financials(esb.build_fact_index(esb.read_main_csv_rows(make_csv_zip(IFRS_ROWS))))
        self.assertEqual(fin["revenue"], 2_500_000_000_000)
        self.assertEqual(fin["source_elements"]["revenue"], "jpcrp_cor:RevenueIFRSSummaryOfBusinessResults")
        self.assertEqual(fin["dividend_per_share"], 0.0)  # 配当欄の「－」は無配
        self.assertIn("無配", fin["source_elements"]["dividend"])

    def test_revenue_labels(self):
        fin = esb.extract_financials(esb.build_fact_index(esb.read_main_csv_rows(make_csv_zip(CONSOLIDATED_ROWS))))
        self.assertEqual(fin["revenue_label"], "売上高")
        fin = esb.extract_financials(esb.build_fact_index(esb.read_main_csv_rows(make_csv_zip(IFRS_ROWS))))
        self.assertEqual(fin["revenue_label"], "売上収益")
        self.assertEqual(esb.revenue_label_for("jppfs_cor:OperatingRevenue1"), "営業収益")
        self.assertEqual(esb.revenue_label_for("jpigp_cor:Revenue2IFRS"), "収益")
        self.assertIsNone(esb.revenue_label_for(None))

    def test_bank_ordinary_income(self):
        rows = [
            ("jpcrp_cor:OrdinaryIncomeSummaryOfBusinessResults", "CurrentYearDuration", "900000000000"),
            ("jpcrp_cor:OrdinaryIncomeSummaryOfBusinessResults", "Prior1YearDuration", "800000000000"),
            # 経常利益（別の要素）を経常収益と取り違えないこと
            ("jpcrp_cor:OrdinaryIncomeLossSummaryOfBusinessResults", "CurrentYearDuration", "300000000000"),
            ("jpcrp_cor:ProfitLossAttributableToOwnersOfParentSummaryOfBusinessResults", "CurrentYearDuration", "200000000000"),
        ]
        fin = esb.extract_financials(esb.build_fact_index(esb.read_main_csv_rows(make_csv_zip(rows))))
        self.assertEqual(fin["revenue"], 900_000_000_000)
        self.assertEqual(fin["revenue_prior"], 800_000_000_000)
        self.assertEqual(fin["revenue_label"], "経常収益")

    def test_revenue_dash_is_zero(self):
        rows = [
            ("jpcrp_cor:NetSalesSummaryOfBusinessResults", "CurrentYearDuration_NonConsolidatedMember", "－"),
            ("jpcrp_cor:NetSalesSummaryOfBusinessResults", "Prior1YearDuration_NonConsolidatedMember", "－"),
            ("jpcrp_cor:NetIncomeLossSummaryOfBusinessResults", "CurrentYearDuration_NonConsolidatedMember", "-1500000000"),
        ]
        fin = esb.extract_financials(esb.build_fact_index(esb.read_main_csv_rows(make_csv_zip(rows))))
        self.assertEqual(fin["basis"], "non_consolidated")
        self.assertEqual(fin["revenue"], 0.0)
        self.assertEqual(fin["revenue_prior"], 0.0)
        self.assertEqual(fin["net_income"], -1_500_000_000)
        self.assertIn("→0", fin["source_elements"]["revenue"])
        self.assertEqual(fin["revenue_label"], "売上高")

    def test_hoya_ifrs_profit_without_owners_line(self):
        # HOYA（S100Y90T）の実データの形：純利益は「当期利益（IFRS）」のみ
        rows = [
            ("jpcrp_cor:RevenueIFRSSummaryOfBusinessResults", "CurrentYearDuration", "947749000000"),
            ("jpcrp_cor:ProfitLossIFRSSummaryOfBusinessResults", "CurrentYearDuration", "251451000000"),
            ("jpcrp_cor:ProfitLossIFRSSummaryOfBusinessResults", "Prior1YearDuration", "201750000000"),
            ("jpcrp_cor:ProfitLossBeforeTaxIFRSSummaryOfBusinessResults", "CurrentYearDuration", "327668000000"),
            ("jpcrp_cor:NetIncomeLossSummaryOfBusinessResults", "CurrentYearDuration_NonConsolidatedMember", "266019000000"),
            ("jpcrp_cor:DividendPaidPerShareSummaryOfBusinessResults", "CurrentYearDuration_NonConsolidatedMember", "295.00"),
        ]
        fin = esb.extract_financials(esb.build_fact_index(esb.read_main_csv_rows(make_csv_zip(rows))))
        self.assertEqual(fin["basis"], "consolidated")
        self.assertEqual(fin["net_income"], 251_451_000_000)  # 単体の数値や税引前利益は使わない
        self.assertEqual(fin["net_income_prior"], 201_750_000_000)
        self.assertEqual(fin["dividend_per_share"], 295.0)

    def test_tokyo_steel_extension_profit(self):
        # 東京製鐵（S100YDWW）の実データの形：単体のみ、純利益・経常利益が企業独自の項目
        ext = "jpcrp030000-asr_E01261-000"
        rows = [
            ("jpcrp_cor:NetSalesSummaryOfBusinessResults", "CurrentYearDuration_NonConsolidatedMember", "268095000000"),
            (f"{ext}:OrdinaryProfitSummaryOfBusinessResults", "CurrentYearDuration_NonConsolidatedMember", "8632000000"),
            (f"{ext}:ProfitSummaryOfBusinessResults", "CurrentYearDuration_NonConsolidatedMember", "11557000000"),
            (f"{ext}:ProfitSummaryOfBusinessResults", "Prior1YearDuration_NonConsolidatedMember", "21203000000"),
        ]
        fin = esb.extract_financials(esb.build_fact_index(esb.read_main_csv_rows(make_csv_zip(rows))))
        self.assertEqual(fin["basis"], "non_consolidated")
        self.assertEqual(fin["net_income"], 11_557_000_000)
        self.assertEqual(fin["net_income_prior"], 21_203_000_000)
        self.assertFalse(esb.compare(fin["net_income"], fin["net_income_prior"]))

    def test_extension_common_dividend_ignores_preferred(self):
        # S100Y7CP / S100YK9A の形：普通株式の配当が企業独自の項目、優先株式の配当は使わない
        rows = CONSOLIDATED_ROWS_NO_DIV + [
            ("x_E1:DividendPaidPerShareClassAStockSummaryOfBusinessResults", "CurrentYearDuration_NonConsolidatedMember", "50000.00"),
            ("x_E1:InterimDividendPaidPerShareCommonStockSummaryOfBusinessResults", "CurrentYearDuration_NonConsolidatedMember", "2.0"),
            ("x_E1:DividendPaidPerShareCommonStockSummaryOfBusinessResults", "CurrentYearDuration_NonConsolidatedMember", "4.0"),
        ]
        fin = esb.extract_financials(esb.build_fact_index(esb.read_main_csv_rows(make_csv_zip(rows))))
        self.assertEqual(fin["dividend_per_share"], 4.0)
        rows2 = CONSOLIDATED_ROWS_NO_DIV + [
            ("x_E2:CommonStockDividendPaidPerShareSummaryOfBusinessResults", "CurrentYearDuration_NonConsolidatedMember", "10.00"),
            ("x_E2:ClassAPreferredSharesDividendPaidPerShareSummaryOfBusinessResults", "CurrentYearDuration_NonConsolidatedMember", "7400.00"),
        ]
        fin = esb.extract_financials(esb.build_fact_index(esb.read_main_csv_rows(make_csv_zip(rows2))))
        self.assertEqual(fin["dividend_per_share"], 10.0)

    def test_extension_common_dividend_dash_is_zero(self):
        # S100YIGD の形：普通株式は「－」（無配）、B種優先株式だけ配当あり
        rows = CONSOLIDATED_ROWS_NO_DIV + [
            ("x_E3:DividendPaidPerShareCommonStockSummaryOfBusinessResults", "CurrentYearDuration_NonConsolidatedMember", "－"),
            ("x_E3:ClassBPreferredStockDividendPaidPerShareSummaryOfBusinessResults", "CurrentYearDuration_NonConsolidatedMember", "40118.53"),
        ]
        fin = esb.extract_financials(esb.build_fact_index(esb.read_main_csv_rows(make_csv_zip(rows))))
        self.assertEqual(fin["dividend_per_share"], 0.0)
        self.assertIn("無配", fin["source_elements"]["dividend"])

    def test_extension_revenue_dash_is_zero(self):
        rows = [
            ("x_E4:BusinessRevenueSummaryOfBusinessResults", "CurrentYearDuration", "－"),
            ("jpcrp_cor:ProfitLossAttributableToOwnersOfParentSummaryOfBusinessResults", "CurrentYearDuration", "-3842612000"),
        ]
        fin = esb.extract_financials(esb.build_fact_index(esb.read_main_csv_rows(make_csv_zip(rows))))
        self.assertEqual(fin["revenue"], 0.0)
        self.assertEqual(fin["revenue_label"], "事業収益")

    def test_life_insurer_segment_total(self):
        # 第一ライフ（S100YC7A）の形：要約に経常収益の合計がなく、セグメントの外部顧客合計に入っている
        ext = "jpcrp030000-asr_E06141-000"
        rows = [
            (f"{ext}:PremiumAndOtherIncomeSummaryOfBusinessResults", "CurrentYearDuration", "6944066000000"),
            (f"{ext}:InvestmentIncomeSummaryOfBusinessResults", "CurrentYearDuration", "3735313000000"),
            ("jpcrp_cor:OrdinaryIncomeLossSummaryOfBusinessResults", "CurrentYearDuration", "753688000000"),
            ("jpcrp_cor:ProfitLossAttributableToOwnersOfParentSummaryOfBusinessResults", "CurrentYearDuration", "436597000000"),
            ("jpcrp_cor:RevenuesFromExternalCustomers", "CurrentYearDuration", "11308275000000"),
            ("jpcrp_cor:RevenuesFromExternalCustomers", "Prior1YearDuration", "9876615000000"),
            ("jpcrp_cor:OperatingRevenue1SummaryOfBusinessResults", "CurrentYearDuration_NonConsolidatedMember", "334518000000"),
        ]
        z = make_csv_zip(rows)
        fin = esb.extract_financials(esb.build_fact_index(esb.read_main_csv_rows(z)))
        self.assertEqual(fin["basis"], "consolidated")
        self.assertEqual(fin["revenue"], 11_308_275_000_000)  # 単体の営業収益（持株会社）は使わない
        self.assertEqual(fin["revenue_prior"], 9_876_615_000_000)
        doc_ = doc("S1", "E06141", "87500", "第一ライフ", "2026-06-16 09:00")
        st = esb.build_stock(doc_, z, {"E06141": {"industry": "保険業"}})
        self.assertEqual(st.revenue_label, "経常収益")
        st = esb.build_stock(doc_, z, {"E06141": {"industry": "サービス業"}})
        self.assertEqual(st.revenue_label, "収益")

    def test_gross_sales_company_uses_formal_revenue(self):
        # セーラー広告（S100YEAK）の形：要約は「総売上高」（会計基準外）と「収益」の独自項目のみ
        ext = "jpcrp030000-asr_E05701-000"
        rows = [
            (f"{ext}:GrossSalesKeyFinancialData", "CurrentYearDuration", "7858502000"),
            (f"{ext}:ProceedsKeyFinancialData", "CurrentYearDuration", "2224849000"),
            ("jpcrp_cor:ProfitLossAttributableToOwnersOfParentSummaryOfBusinessResults", "CurrentYearDuration", "-4762000"),
            ("jpcrp_cor:RevenuesFromExternalCustomers", "CurrentYearDuration", "2224849000"),
        ]
        fin = esb.extract_financials(esb.build_fact_index(esb.read_main_csv_rows(make_csv_zip(rows))))
        self.assertEqual(fin["revenue"], 2_224_849_000)  # 総売上高 78.6億円ではなく正式な収益 22.2億円
        self.assertEqual(fin["revenue_label"], "収益")

    def test_biotech_key_financial_data_dash(self):
        # Chordia（S100X63V）の形：単体のみ、事業収益が「－」
        rows = [
            ("x_E38905:BusinessRevenueKeyFinancialData", "CurrentYearDuration_NonConsolidatedMember", "－"),
            ("x_E38905:BusinessRevenueKeyFinancialData", "Prior1YearDuration_NonConsolidatedMember", "－"),
            ("jpcrp_cor:NetIncomeLossSummaryOfBusinessResults", "CurrentYearDuration_NonConsolidatedMember", "-1785867000"),
        ]
        fin = esb.extract_financials(esb.build_fact_index(esb.read_main_csv_rows(make_csv_zip(rows))))
        self.assertEqual(fin["basis"], "non_consolidated")
        self.assertEqual(fin["revenue"], 0.0)
        self.assertEqual(fin["revenue_prior"], 0.0)
        self.assertEqual(fin["revenue_label"], "事業収益")

    def test_dividend_element_absent_stays_none(self):
        rows = [r for r in CONSOLIDATED_ROWS if "Dividend" not in r[0]]
        fin = esb.extract_financials(esb.build_fact_index(esb.read_main_csv_rows(make_csv_zip(rows))))
        self.assertIsNone(fin["dividend_per_share"])  # 項目自体がない場合は「不明」のまま

    def test_yukiguni_real_structure(self):
        # 実データ（S100YK0W）の項目構成を再現:
        #   要約には独自項目 IncomeIFRSKeyFinancialData（収益 534億円）しかなく、
        #   損益計算書に 売上収益（378億円）と 収益（534億円）がある。単体の売上高（354億円）もある。
        ext = "jpcrp030000-asr_E00007-000"
        rows = [
            (f"{ext}:IncomeIFRSKeyFinancialData", "Prior1YearDuration", "53139000000"),
            (f"{ext}:IncomeIFRSKeyFinancialData", "CurrentYearDuration", "53449000000"),
            ("jpcrp_cor:ProfitLossAttributableToOwnersOfParentIFRSSummaryOfBusinessResults", "Prior1YearDuration", "1502000000"),
            ("jpcrp_cor:ProfitLossAttributableToOwnersOfParentIFRSSummaryOfBusinessResults", "CurrentYearDuration", "2958000000"),
            ("jpcrp_cor:NetSalesSummaryOfBusinessResults", "CurrentYearDuration_NonConsolidatedMember", "35372000000"),
            ("jpcrp_cor:DividendPaidPerShareSummaryOfBusinessResults", "CurrentYearDuration_NonConsolidatedMember", "23.00"),
            ("jpigp_cor:RevenueIFRS", "Prior1YearDuration", "37102000000"),
            ("jpigp_cor:RevenueIFRS", "CurrentYearDuration", "37845000000"),
            ("jpigp_cor:Revenue2IFRS", "Prior1YearDuration", "53139000000"),
            ("jpigp_cor:Revenue2IFRS", "CurrentYearDuration", "53449000000"),
            ("jpigp_cor:IntersegmentRevenueIFRS", "CurrentYearDuration", "－"),
        ]
        fin = esb.extract_financials(esb.build_fact_index(esb.read_main_csv_rows(make_csv_zip(rows))))
        self.assertEqual(fin["basis"], "consolidated")
        self.assertEqual(fin["revenue"], 37_845_000_000)        # 売上収益を採用（連結）
        self.assertEqual(fin["revenue_prior"], 37_102_000_000)
        self.assertEqual(fin["source_elements"]["revenue"], "jpigp_cor:RevenueIFRS")
        self.assertEqual(fin["dividend_per_share"], 23.0)

    def test_company_specific_revenue_element(self):
        # ユキグニファクトリーのように、IFRSの売上を企業独自の拡張項目で開示しているケース
        ext = "jpcrp030000-asr_E00007-000"
        rows = [
            (f"{ext}:RevenueIFRSSummaryOfBusinessResults", "CurrentYearDuration", "53449000000"),
            (f"{ext}:RevenueIFRSSummaryOfBusinessResults", "Prior1YearDuration", "53139000000"),
            (f"{ext}:CostOfSalesIFRSSummaryOfBusinessResults", "CurrentYearDuration", "39400000000"),
            (f"{ext}:IntersegmentRevenueSummaryOfBusinessResults", "CurrentYearDuration", "99999999999"),
            ("jpcrp_cor:ProfitLossAttributableToOwnersOfParentIFRSSummaryOfBusinessResults", "CurrentYearDuration", "2958000000"),
            ("jpcrp_cor:ProfitLossAttributableToOwnersOfParentIFRSSummaryOfBusinessResults", "Prior1YearDuration", "1502000000"),
        ]
        fin = esb.extract_financials(esb.build_fact_index(esb.read_main_csv_rows(make_csv_zip(rows))))
        self.assertEqual(fin["basis"], "consolidated")
        self.assertEqual(fin["revenue"], 53_449_000_000)
        self.assertEqual(fin["revenue_prior"], 53_139_000_000)
        self.assertEqual(fin["source_elements"]["revenue"], f"{ext}:RevenueIFRSSummaryOfBusinessResults")

    def test_nonconsolidated_only(self):
        fin = esb.extract_financials(esb.build_fact_index(esb.read_main_csv_rows(make_csv_zip(NONCONSOLIDATED_ROWS))))
        self.assertEqual(fin["basis"], "non_consolidated")
        self.assertEqual(fin["net_income"], -100_436_000)
        self.assertEqual(fin["employees"], 120)

    def test_non_main_csv_ignored(self):
        rows = esb.read_main_csv_rows(make_csv_zip([]))
        self.assertEqual(rows, [])

    def test_helpers(self):
        self.assertEqual(esb.normalize_sec_code("72030"), "7203")
        self.assertEqual(esb.normalize_sec_code("130A0"), "130A")
        self.assertEqual(esb.scale_bucket(5e9), "100億円未満")
        self.assertEqual(esb.scale_bucket(1.2e11), "1,000億〜1兆円")
        self.assertEqual(esb.scale_bucket(2.5e12), "1兆円以上")
        self.assertIsNone(esb.compare(None, 1))
        self.assertTrue(esb.compare(2, 1))

    def test_codelist(self):
        m = esb.parse_codelist_zip(make_codelist_zip([("E00001", "テスト工業株式会社", "機械", "60010")]))
        self.assertEqual(m["E00001"]["industry"], "機械")
        self.assertTrue(m["E00001"]["listed"])

    def test_filter(self):
        self.assertTrue(esb.is_target_document(doc("S1", "E1", "10010", "A", "2026-06-20 09:00")))
        self.assertFalse(esb.is_target_document(doc("S1", "E1", "10010", "A", "x", doc_type="140")))  # 四半期報告書
        self.assertFalse(esb.is_target_document(doc("S1", "E1", None, "A", "x")))                      # 非上場
        self.assertFalse(esb.is_target_document(doc("S1", "E1", "10010", "A", "x", csv_flag="0")))
        self.assertFalse(esb.is_target_document(doc("S1", "E1", "10010", "A", "x", withdrawn="1")))


class EndToEndTest(unittest.TestCase):
    def test_main(self):
        lists = {
            date(2026, 6, 20): [
                doc("S100AAA1", "E00001", "60010", "テスト工業株式会社", "2026-06-20 09:00"),
                doc("S100BBB2", "E00002", "72030", "サンプル自動車株式会社", "2026-06-20 10:00"),
                doc("S100XXX9", "E00009", "99990", "四半期の会社", "2026-06-20 11:00", doc_type="140"),
            ],
            date(2026, 6, 21): [
                # 同じ銘柄の訂正後の新しい提出 → こちらが採用される
                doc("S100AAA3", "E00001", "60010", "テスト工業株式会社", "2026-06-21 09:00"),
                doc("S100CCC4", "E00003", "30010", "小さな商店株式会社", "2026-06-21 09:30"),
            ],
        }
        zips = {"S100AAA3": make_csv_zip(CONSOLIDATED_ROWS),
                "S100BBB2": make_csv_zip(IFRS_ROWS),
                "S100CCC4": make_csv_zip(NONCONSOLIDATED_ROWS)}
        codelist = esb.parse_codelist_zip(make_codelist_zip([
            ("E00001", "テスト工業株式会社", "機械", "60010"),
            ("E00002", "サンプル自動車株式会社", "輸送用機器", "72030"),
            ("E00003", "小さな商店株式会社", "小売業", "30010"),
        ]))

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(esb.EdinetClient, "list_documents", lambda self, d: lists.get(d, [])), \
             mock.patch.object(esb.EdinetClient, "get_csv_zip", lambda self, i: zips[i]), \
             mock.patch.object(esb, "load_industry_map", lambda cache_dir: codelist):
            out = Path(tmp) / "stocks.json"
            rc = esb.main(["--from", "2026-06-20", "--to", "2026-06-22", "--out", str(out),
                           "--cache-dir", str(Path(tmp) / "cache"), "--api-key", "dummy", "--sleep", "0"])
            self.assertEqual(rc, 0)
            data = json.loads(out.read_text(encoding="utf-8"))

        self.assertEqual(data["count"], 3)
        self.assertIn("EDINET", data["attribution"])
        codes = [s["sec_code"] for s in data["stocks"]]
        self.assertEqual(codes, ["3001", "6001", "7203"])  # 銘柄コード順
        by = {s["sec_code"]: s for s in data["stocks"]}
        self.assertEqual(by["6001"]["doc_id"], "S100AAA3")      # 新しい提出を採用
        self.assertEqual(by["6001"]["industry"], "機械")
        self.assertTrue(by["6001"]["revenue_increased"])
        self.assertFalse(by["6001"]["profit_increased"])
        self.assertTrue(by["6001"]["has_dividend"])
        self.assertEqual(by["7203"]["revenue_scale"], "1兆円以上")
        self.assertFalse(by["7203"]["has_dividend"])          # 「－」→無配
        self.assertEqual(by["3001"]["basis"], "non_consolidated")
        self.assertFalse(by["3001"]["has_dividend"])
        self.assertFalse(by["3001"]["revenue_increased"])        # 前期と同額 → 増収ではない


class FailureTest(unittest.TestCase):
    def test_all_days_failed_returns_error(self):
        def boom(self, d):
            raise esb.EdinetError("取得に失敗しました: test")
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(esb.EdinetClient, "list_documents", boom), \
             mock.patch.object(esb, "load_industry_map", lambda cache_dir: {}):
            out = Path(tmp) / "stocks.json"
            rc = esb.main(["--from", "2026-06-20", "--to", "2026-06-21", "--out", str(out),
                           "--cache-dir", str(Path(tmp) / "cache"), "--api-key", "dummy", "--sleep", "0"])
            self.assertEqual(rc, 1)
            self.assertFalse(out.exists())

    def test_auth_error_stops_immediately(self):
        calls = []
        def unauthorized(self, d):
            calls.append(d)
            raise esb.EdinetError("認証エラー（HTTP 401）。APIキーを確認してください。")
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(esb.EdinetClient, "list_documents", unauthorized), \
             mock.patch.object(esb, "load_industry_map", lambda cache_dir: {}):
            rc = esb.main(["--from", "2026-06-01", "--to", "2026-06-30", "--out", str(Path(tmp) / "s.json"),
                           "--cache-dir", str(Path(tmp) / "cache"), "--api-key", "dummy", "--sleep", "0"])
            self.assertEqual(rc, 1)
            self.assertEqual(len(calls), 1)  # 30日分を叩かずに1回で止まる


if __name__ == "__main__":
    unittest.main()
