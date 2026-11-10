#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dump_elements.py

キャッシュ済みのEDINET CSV（cache/csv/<書類ID>.zip）から、
「主要な経営指標等の推移」などに使われている項目名（要素ID）と値を書き出す調査用スクリプト。
売上高などが取れない銘柄の原因調査に使う。通信は行わない。

使い方:
  python3 dump_elements.py S100YK0W
  （結果は elements_S100YK0W.txt に保存され、画面にも表示される）

複数指定も可:
  python3 dump_elements.py S100YK0W S100XXXX
"""

import csv
import io
import sys
import zipfile
from pathlib import Path

CACHE_DIR = Path("cache") / "csv"

# 業績の要約や売上に関係しそうな項目を拾うためのキーワード
KEYWORDS = ["SummaryOfBusinessResults", "KeyFinancialData", "Revenue", "NetSales", "Sales"]
CONTEXTS = ["CurrentYearDuration", "Prior1YearDuration",
            "CurrentYearDuration_NonConsolidatedMember", "Prior1YearDuration_NonConsolidatedMember"]


def dump(doc_id: str) -> str:
    zpath = CACHE_DIR / f"{doc_id}.zip"
    if not zpath.exists():
        return f"[{doc_id}] {zpath} が見つかりません。run_edinet_test.sh を実行したフォルダで実行してください。\n"

    lines = [f"===== {doc_id} ====="]
    with zipfile.ZipFile(zpath) as zf:
        csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        lines.append("ZIP内のCSV:")
        for n in csv_names:
            lines.append(f"  {n}")
        lines.append("")

        for n in csv_names:
            if not Path(n).name.startswith("jpcrp"):
                continue
            text = zf.read(n).decode("utf-16", errors="replace")
            reader = csv.DictReader(io.StringIO(text), delimiter="\t")
            header = reader.fieldnames
            lines.append(f"--- {Path(n).name} ---")
            lines.append(f"列名: {header}")
            seen = set()
            for r in reader:
                r = {(k or "").strip().strip('"'): (v or "").strip() for k, v in r.items()}
                elem = r.get("要素ID", "")
                ctx = r.get("コンテキストID", "")
                if ctx not in CONTEXTS:
                    continue
                if not any(k in elem for k in KEYWORDS):
                    continue
                key = (elem, ctx)
                if key in seen:
                    continue
                seen.add(key)
                name = r.get("項目名", "")
                val = r.get("値", "")
                if len(val) > 60:
                    val = val[:60] + "…"
                lines.append(f"{elem}\t{ctx}\t{name}\t{val}")
            lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    if len(sys.argv) < 2:
        print("使い方: python3 dump_elements.py <書類ID> [<書類ID> ...]")
        return 2
    for doc_id in sys.argv[1:]:
        out = dump(doc_id)
        print(out)
        Path(f"elements_{doc_id}.txt").write_text(out, encoding="utf-8")
        print(f"→ elements_{doc_id}.txt に保存しました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
