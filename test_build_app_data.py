#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_app_data.py と、毎日の更新で使う edinet_stock_builder.prune_delisted のテスト（ネットワーク不要）。

実行: python3 -m unittest test_build_app_data.py -v
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_app_data as bad  # noqa: E402
import edinet_stock_builder as esb  # noqa: E402


def stock(code, name, industry, revenue, **kw):
    s = {
        "sec_code": code, "name": name, "edinet_code": "E" + code, "industry": industry,
        "fiscal_period_end": "2026-03-31", "basis": "consolidated",
        "revenue": revenue, "revenue_prior": None, "revenue_label": "売上高",
        "net_income": 1.0e9, "net_income_prior": None, "dividend_per_share": 10.0,
        "employees": 100, "doc_id": "S1" + code, "submitted_at": "2026-06-20 10:00",
    }
    s.update(kw)
    return s


SRC = {
    "generated_at": "2026-11-10T00:00:00+00:00",
    "attribution": "出典：EDINET",
    "note": "評価や推奨ではありません。",
    "stocks": [
        stock("9999", "株式会社　サンプル", "サービス業", 5.0e9),
        stock("1301", "株式会社　極洋", "水産・農林業", 3.346e11, dividend_per_share=54.5),
        stock("130A", "株式会社Ｖｅｒｉｔａｓ　Ｉｎ　Ｓｉｌｉｃｏ", "医薬品", 0.0, revenue_label="事業収益"),
        stock("7181", "株式会社かんぽ生命保険", "保険業", None, revenue_label=None),
        stock("7203", "トヨタ自動車株式会社", "輸送用機器", 5.07e13, basis="non_consolidated"),
    ],
}


class DisplayNameTest(unittest.TestCase):
    def test_names(self):
        self.assertEqual(bad.display_name("株式会社　極洋"), "極洋")
        self.assertEqual(bad.display_name("Ｕｍｉｏｓ株式会社"), "Umios")
        self.assertEqual(bad.display_name("株式会社ＶＲＡＩＮ　Ｓｏｌｕｔｉｏｎ"), "VRAIN Solution")
        self.assertEqual(bad.display_name("株式会社"), "株式会社")  # 空になる場合は元の名前


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.stocks, self.meta = bad.build(SRC)
        self.rows = {r["code"]: r for r in self.stocks["stocks"]}

    def test_order_is_by_code(self):
        self.assertEqual([r["code"] for r in self.stocks["stocks"]], ["1301", "130A", "7181", "7203", "9999"])

    def test_industry_order_follows_tse_33(self):
        names = [i["name"] for i in self.meta["industries"]]
        self.assertEqual(names, ["水産・農林業", "医薬品", "輸送用機器", "保険業", "サービス業"])
        self.assertEqual(self.meta["industries"][self.rows["9999"]["industry"]]["name"], "サービス業")

    def test_values(self):
        r = self.rows["1301"]
        self.assertEqual(r["name"], "極洋")
        self.assertEqual(r["rev"], 334_600_000_000)
        self.assertIsInstance(r["rev"], int)
        self.assertEqual(r["div"], 54.5)
        self.assertEqual(r["scale"], 2)
        self.assertEqual(r["filed"], "2026-06-20")
        self.assertEqual(self.rows["9999"]["div"], 10)
        self.assertEqual(self.rows["7203"]["scale"], 3)
        self.assertEqual(self.rows["7203"]["basis"], "n")
        self.assertEqual(self.rows["130A"]["scale"], 0)        # 売上0円は「100億円未満」
        self.assertIsNone(self.rows["7181"]["scale"])          # 売上不明は区分なし

    def test_counts(self):
        self.assertEqual(self.meta["count"], 5)
        self.assertEqual([s["count"] for s in self.meta["scales"]], [2, 0, 1, 1])
        self.assertEqual(self.meta["missing"]["rev"], 1)
        total = sum(sum(row) for row in self.meta["industry_scale_counts"])
        self.assertEqual(total, 4)  # 売上不明の1社は入らない
        self.assertEqual(self.meta["attribution"], "出典：EDINET")

    def test_check(self):
        self.assertEqual(bad.check(self.meta, 5, 0.25, 0.0), [])
        self.assertTrue(bad.check(self.meta, 3000, 0.25, 0.0))   # 銘柄数不足
        self.assertTrue(bad.check(self.meta, 5, 0.1, 0.0))       # 売上空欄20% > 10%


class MainTest(unittest.TestCase):
    def test_main_writes_files(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "stocks.json"
            src.write_text(json.dumps(SRC, ensure_ascii=False), encoding="utf-8")
            out = Path(td) / "site"
            rc = bad.main(["--in", str(src), "--out", str(out), "--min-count", "5",
                           "--max-missing-revenue", "0.25"])
            self.assertEqual(rc, 0)
            data = json.loads((out / "v1" / "stocks.json").read_text(encoding="utf-8"))
            self.assertEqual(data["count"], 5)
            self.assertTrue((out / "v1" / "meta.json").exists())
            self.assertIn("出典：EDINET", (out / "index.html").read_text(encoding="utf-8"))

    def test_main_refuses_to_publish_bad_data(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "stocks.json"
            src.write_text(json.dumps(SRC, ensure_ascii=False), encoding="utf-8")
            out = Path(td) / "site"
            rc = bad.main(["--in", str(src), "--out", str(out)])  # 既定は3000銘柄以上
            self.assertEqual(rc, 1)
            self.assertFalse(out.exists())


class PruneDelistedTest(unittest.TestCase):
    def test_prune(self):
        lst = [{"sec_code": "1111", "edinet_code": "E1"},
               {"sec_code": "2222", "edinet_code": "E2"},
               {"sec_code": "3333", "edinet_code": "E3"}]
        industries = {"E1": {"listed": True}, "E2": {"listed": False}}  # E3 はリストにない
        kept, pruned = esb.prune_delisted(lst, industries)
        self.assertEqual([s["sec_code"] for s in kept], ["1111", "3333"])
        self.assertEqual(pruned, ["2222"])

    def test_no_codelist_prunes_nothing(self):
        lst = [{"sec_code": "1111", "edinet_code": "E1"}]
        kept, pruned = esb.prune_delisted(lst, {})
        self.assertEqual(kept, lst)
        self.assertEqual(pruned, [])


if __name__ == "__main__":
    unittest.main()
