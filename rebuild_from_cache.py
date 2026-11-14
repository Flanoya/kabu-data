#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rebuild_from_cache.py

取り出し方（edinet_stock_builder.py の抽出ルール）を直したあと、
ダウンロード済みのCSV（cache/csv/*.zip）から data/stocks.json の全銘柄を作り直す。
EDINETには接続しない（APIキー不要）。CSVが手元にない銘柄は、元のデータをそのまま残す。

使い方:
  python3 rebuild_from_cache.py --data data/stocks.json --cache ../iOS-App-test/cache
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import edinet_stock_builder as esb  # noqa: E402

COMPARE_FIELDS = ["revenue", "revenue_prior", "revenue_label", "net_income", "net_income_prior",
                  "dividend_per_share", "has_dividend", "employees", "industry", "basis"]


def main() -> int:
    p = argparse.ArgumentParser(description="キャッシュ済みCSVから stocks.json を作り直す")
    p.add_argument("--data", default="data/stocks.json")
    p.add_argument("--cache", default="cache")
    args = p.parse_args()

    data_path = Path(args.data)
    cache = Path(args.cache)
    src = json.loads(data_path.read_text(encoding="utf-8"))

    industries = {}
    codelist = cache / "Edinetcode.zip"
    if codelist.exists():
        industries = esb.parse_codelist_zip(codelist.read_bytes())
    else:
        print("! コードリストがありません。業種は元のデータの値を使います。", file=sys.stderr)

    out, missing, failed = [], [], []
    changes: Counter = Counter()
    examples: dict[str, list[str]] = {}
    for old in src["stocks"]:
        zpath = cache / "csv" / f"{old.get('doc_id')}.zip"
        if not zpath.exists():
            missing.append(old["sec_code"])
            out.append(old)
            continue
        code = old["sec_code"]
        doc = {
            "secCode": code + "0" if len(code) == 4 else code,
            "edinetCode": old.get("edinet_code", ""),
            "filerName": old.get("name"),
            "periodEnd": old.get("fiscal_period_end"),
            "docID": old.get("doc_id"),
            "submitDateTime": old.get("submitted_at"),
        }
        try:
            new = asdict(esb.build_stock(doc, zpath.read_bytes(), industries))
        except Exception as e:  # 1銘柄の失敗で全体を止めない
            failed.append(f"{code}: {e}")
            out.append(old)
            continue
        if not industries:
            new["industry"] = old.get("industry")
        for f in COMPARE_FIELDS:
            if old.get(f) != new.get(f):
                changes[f] += 1
                ex = examples.setdefault(f, [])
                if len(ex) < 8:
                    ex.append(f"{code} {old.get(f)!r} → {new.get(f)!r}")
        out.append(new)

    payload = dict(src)
    payload["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload["count"] = len(out)
    payload["stocks"] = sorted(out, key=lambda s: s["sec_code"])
    data_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"完了: {data_path}（{len(out)} 銘柄、CSVなしで据え置き {len(missing)}、失敗 {len(failed)}）")
    for f in COMPARE_FIELDS:
        if changes[f]:
            print(f"  {f}: {changes[f]} 件変更")
            for e in examples[f]:
                print(f"    {e}")
    if missing:
        print("  CSVなし: " + ", ".join(missing[:30]) + (" …" if len(missing) > 30 else ""))
    for f in failed:
        print("  失敗 " + f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
