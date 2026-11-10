#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_app_data.py

edinet_stock_builder.py が作った stocks.json（作業用の詳しいデータ）から、
アプリが読み込む公開用データを作る。

出力（--out で指定したフォルダの下）:
  v1/stocks.json  … 銘柄ごとの数値（銘柄コード順。評価や並べ替えはしない）
  v1/meta.json    … 業種・売上規模の一覧と件数、出典、更新日時など
  index.html      … 出典と更新日時を示す簡単なページ

公開前の確認（どれかに当てはまると公開せずに終了コード1で終わる）:
  ・銘柄数が --min-count 未満
  ・売上が空欄の銘柄の割合が --max-missing-revenue を超える
  ・業種が空欄の銘柄の割合が --max-missing-industry を超える

使い方:
  python3 build_app_data.py --in data/stocks.json --out site

標準ライブラリのみで動く（Python 3.9 以上）。
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

SCHEMA_VERSION = 1

# 東証33業種の並び（証券コード協議会の業種コード順）。名称はEDINETコードリストの表記に合わせる。
INDUSTRY_ORDER = [
    "水産・農林業", "鉱業", "建設業", "食料品", "繊維製品", "パルプ・紙", "化学", "医薬品",
    "石油・石炭製品", "ゴム製品", "ガラス・土石製品", "鉄鋼", "非鉄金属", "金属製品", "機械",
    "電気機器", "輸送用機器", "精密機器", "その他製品", "電気・ガス業", "陸運業", "海運業",
    "空運業", "倉庫・運輸関連", "情報・通信業", "卸売業", "小売業", "銀行業",
    "証券、商品先物取引業", "保険業", "その他金融業", "不動産業", "サービス業",
]

# 売上規模の区分（edinet_stock_builder.py の SCALE_BUCKETS と同じ境界）。上限は含まない。
SCALES = [
    {"id": 0, "label": "100億円未満", "min": None, "max": 10_000_000_000},
    {"id": 1, "label": "100億〜1,000億円", "min": 10_000_000_000, "max": 100_000_000_000},
    {"id": 2, "label": "1,000億〜1兆円", "min": 100_000_000_000, "max": 1_000_000_000_000},
    {"id": 3, "label": "1兆円以上", "min": 1_000_000_000_000, "max": None},
]

REVENUE_LABELS = ["売上高", "売上収益", "営業収益", "経常収益", "事業収益", "収益"]

LEGAL_FORMS = ["株式会社", "（株）", "(株)", "有限会社", "合同会社"]


def display_name(legal_name: str) -> str:
    """「株式会社　極洋」→「極洋」、「Ｕｍｉｏｓ株式会社」→「Umios」のように画面表示用の短い名前にする。"""
    s = legal_name or ""
    for form in LEGAL_FORMS:
        s = s.replace(form, " ")
    s = unicodedata.normalize("NFKC", s)  # 全角英数字・全角スペースを半角に
    s = re.sub(r"\s+", " ", s).strip()
    return s or unicodedata.normalize("NFKC", legal_name or "").strip()


def scale_id(revenue: Optional[float]) -> Optional[int]:
    if revenue is None:
        return None
    for sc in SCALES:
        if sc["max"] is None or revenue < sc["max"]:
            return sc["id"]
    return None


def to_int(v) -> Optional[int]:
    if v is None:
        return None
    return int(round(float(v)))


def to_num(v) -> Optional[float]:
    """1株配当など小数がありうる値。整数ならintにしてJSONを短くする。"""
    if v is None:
        return None
    f = float(v)
    return int(f) if f.is_integer() else round(f, 2)


def build(src: dict) -> tuple[dict, dict]:
    stocks_in = src.get("stocks", [])

    # 業種一覧（33業種の順。一覧にない名称が来たら末尾に追加）
    names_present = {s.get("industry") for s in stocks_in if s.get("industry")}
    industries = [n for n in INDUSTRY_ORDER if n in names_present]
    industries += sorted(names_present - set(industries))
    industry_id = {n: i for i, n in enumerate(industries)}

    out_rows = []
    for s in sorted(stocks_in, key=lambda x: x["sec_code"]):
        rev = to_int(s.get("revenue"))
        out_rows.append({
            "code": s["sec_code"],
            "name": display_name(s.get("name") or ""),
            "legal_name": s.get("name"),
            "industry": industry_id.get(s.get("industry")),
            "fy_end": s.get("fiscal_period_end"),
            "basis": {"consolidated": "c", "non_consolidated": "n"}.get(s.get("basis")),
            "rev": rev,
            "rev_prior": to_int(s.get("revenue_prior")),
            "rev_label": s.get("revenue_label"),
            "ni": to_int(s.get("net_income")),
            "ni_prior": to_int(s.get("net_income_prior")),
            "div": to_num(s.get("dividend_per_share")),
            "emp": s.get("employees"),
            "scale": scale_id(rev),
            "doc": s.get("doc_id"),
            "filed": (s.get("submitted_at") or "")[:10] or None,
        })

    # 件数（条件を選ぶ画面で「この条件で○社」と出すため）
    ind_counts = Counter(r["industry"] for r in out_rows)
    scale_counts = Counter(r["scale"] for r in out_rows)
    matrix = [[0] * len(SCALES) for _ in industries]
    for r in out_rows:
        if r["industry"] is not None and r["scale"] is not None:
            matrix[r["industry"]][r["scale"]] += 1

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    filed_dates = [r["filed"] for r in out_rows if r["filed"]]
    common = {
        "schema": SCHEMA_VERSION,
        "generated_at": now,
        "source_generated_at": src.get("generated_at"),
        "attribution": src.get("attribution"),
        "note": src.get("note") or "開示書類の数値をそのまま整理したもので、銘柄の評価や推奨ではありません。",
        "count": len(out_rows),
    }
    meta = {
        **common,
        "latest_filing_date": max(filed_dates) if filed_dates else None,
        "units": {"rev": "円", "ni": "円", "div": "円/株", "emp": "人"},
        "industries": [{"id": i, "name": n, "count": ind_counts.get(i, 0)} for i, n in enumerate(industries)],
        "scales": [{**sc, "count": scale_counts.get(sc["id"], 0)} for sc in SCALES],
        "industry_scale_counts": matrix,
        "revenue_labels": REVENUE_LABELS,
        "fields": {
            "code": "銘柄コード（4桁。英字を含む場合あり）",
            "name": "表示用の会社名（法人格を除き、英数字を半角にしたもの）",
            "legal_name": "提出者名（EDINETの表記そのまま）",
            "industry": "業種（industries の id）",
            "fy_end": "決算期末日",
            "basis": "c=連結 / n=単体（連結財務諸表のない会社）",
            "rev": "売上（rev_label の名称で表示する）",
            "rev_prior": "前期の売上",
            "rev_label": "売上の名称（売上高／売上収益／営業収益／経常収益／事業収益／収益）",
            "ni": "純利益（親会社株主に帰属する当期純利益など）",
            "ni_prior": "前期の純利益",
            "div": "1株当たり配当額（普通株式。0は無配）",
            "emp": "従業員数",
            "scale": "売上規模（scales の id）",
            "doc": "EDINETの書類管理番号",
            "filed": "有価証券報告書の提出日",
        },
        "missing": {
            "rev": sum(1 for r in out_rows if r["rev"] is None),
            "ni": sum(1 for r in out_rows if r["ni"] is None),
            "div": sum(1 for r in out_rows if r["div"] is None),
            "emp": sum(1 for r in out_rows if r["emp"] is None),
            "industry": sum(1 for r in out_rows if r["industry"] is None),
        },
    }
    stocks = {**common, "stocks": out_rows}
    return stocks, meta


def check(meta: dict, min_count: int, max_missing_revenue: float, max_missing_industry: float) -> list[str]:
    problems = []
    n = meta["count"]
    if n < min_count:
        problems.append(f"銘柄数が少なすぎます（{n} 件 < {min_count} 件）")
    if n:
        r = meta["missing"]["rev"] / n
        if r > max_missing_revenue:
            problems.append(f"売上が空欄の割合が高すぎます（{r:.1%}）")
        r = meta["missing"]["industry"] / n
        if r > max_missing_industry:
            problems.append(f"業種が空欄の割合が高すぎます（{r:.1%}）")
    return problems


def index_html(meta: dict) -> str:
    e = html.escape
    return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>銘柄データ（EDINET）</title>
<style>body{{font-family:system-ui,sans-serif;max-width:40rem;margin:2rem auto;padding:0 1rem;line-height:1.7;color:#222}}code{{background:#f3f3f3;padding:0 .3em}}</style>
</head><body>
<h1>銘柄データ</h1>
<p>{e(meta.get("note") or "")}</p>
<ul>
<li>銘柄数: {meta["count"]}</li>
<li>最新の提出日: {e(meta.get("latest_filing_date") or "-")}</li>
<li>更新日時（UTC）: {e(meta["generated_at"])}</li>
</ul>
<p>データ: <a href="v1/meta.json"><code>v1/meta.json</code></a> / <a href="v1/stocks.json"><code>v1/stocks.json</code></a></p>
<p>出典: {e(meta.get("attribution") or "")}</p>
</body></html>
"""


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="アプリ用の公開データを作る")
    p.add_argument("--in", dest="src", default="data/stocks.json", help="edinet_stock_builder.py の出力")
    p.add_argument("--out", default="site", help="出力フォルダ（GitHub Pages に公開するフォルダ）")
    p.add_argument("--min-count", type=int, default=3000)
    p.add_argument("--max-missing-revenue", type=float, default=0.03)
    p.add_argument("--max-missing-industry", type=float, default=0.03)
    args = p.parse_args(argv)
    log = lambda m: print(m, file=sys.stderr, flush=True)

    src_path = Path(args.src)
    try:
        src = json.loads(src_path.read_text(encoding="utf-8"))
    except Exception as e:
        log(f"入力を読めませんでした: {src_path}: {e}")
        return 1

    stocks, meta = build(src)
    problems = check(meta, args.min_count, args.max_missing_revenue, args.max_missing_industry)
    if problems:
        for msg in problems:
            log(f"中止（公開しません）: {msg}")
        return 1

    out = Path(args.out)
    (out / "v1").mkdir(parents=True, exist_ok=True)
    (out / "v1" / "stocks.json").write_text(
        json.dumps(stocks, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (out / "v1" / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "index.html").write_text(index_html(meta), encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")

    size = (out / "v1" / "stocks.json").stat().st_size
    log(f"完了: {out}/v1/stocks.json（{meta['count']} 銘柄、{size / 1024:.0f} KB）")
    log(f"  業種 {len(meta['industries'])} 種、空欄: " +
        "、".join(f"{k}={v}" for k, v in meta["missing"].items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
