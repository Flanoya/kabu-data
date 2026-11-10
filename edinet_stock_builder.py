#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
edinet_stock_builder.py

金融庁 EDINET API v2 から有価証券報告書（docTypeCode=120）を集め、
アプリの「条件検索」に使う銘柄データ（stocks.json）を作る試作スクリプト。

取り出す項目（すべて開示書類に書かれた事実のみ。評価や推奨は付けない）
  - 証券コード、会社名、業種（EDINETコードリストの「提出者業種」）
  - 決算期末
  - 売上高（当期・前期）       … 増収かどうかの判定用
  - 親会社株主に帰属する当期純利益（当期・前期） … 増益かどうかの判定用
  - 1株当たり配当額（当期）
  - 従業員数
  - 売上規模の区分（100億円未満 / 100億〜1,000億円 / 1,000億〜1兆円 / 1兆円以上）

使い方
  1. EDINET の API キーを取得する（無料、EDINET 公式サイトでメール登録）
  2. 環境変数に設定する
       export EDINET_API_KEY="xxxxxxxx"
  3. 期間を指定して実行する（3月決算企業の有報は6月中旬〜下旬に集中）
       python3 edinet_stock_builder.py --from 2026-06-15 --to 2026-06-30 --out stocks.json
     まず少数で試すなら --limit 20 を付ける

出力
  stocks.json : アプリに配信する銘柄データ（銘柄コード順）
  cache/      : ダウンロードした一覧JSON・CSVのZIP（再実行時はここから読む）

データの出典表示（公共データ利用規約 第1.0版 PDL1.0）
  出力JSONの "attribution" をアプリ内に必ず表示すること。

依存ライブラリ: なし（Python 3.9 以上の標準ライブラリのみ）
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

API_BASE = "https://api.edinet-fsa.go.jp/api/v2"
CODELIST_URL = "https://disclosure2dl.edinet-fsa.go.jp/searchdocument/codelist/Edinetcode.zip"

DOC_TYPE_ANNUAL_REPORT = "120"   # 有価証券報告書
ORDINANCE_CORP = "010"           # 企業内容等の開示に関する内閣府令
FORM_DOMESTIC_ANNUAL = "030000"  # 第三号様式（国内会社の有価証券報告書）

ATTRIBUTION = "出典：金融庁 EDINET（公共データ利用規約 第1.0版 PDL1.0）を加工して作成"
USER_AGENT = "investment-learning-app-prototype/0.1"

# 連結・個別のコンテキストID
CTX_CUR = "CurrentYearDuration"
CTX_PRI = "Prior1YearDuration"
CTX_CUR_NC = "CurrentYearDuration_NonConsolidatedMember"
CTX_PRI_NC = "Prior1YearDuration_NonConsolidatedMember"
CTX_CUR_INSTANT = "CurrentYearInstant"
CTX_CUR_INSTANT_NC = "CurrentYearInstant_NonConsolidatedMember"

# 要素IDの候補（上から順に探す）。会計基準ごとにIDが異なるため複数持つ。
REVENUE_ELEMENTS = [
    "jpcrp_cor:NetSalesSummaryOfBusinessResults",              # 日本基準 売上高
    "jpcrp_cor:RevenueIFRSSummaryOfBusinessResults",            # IFRS 売上収益
    "jpcrp_cor:NetSalesIFRSSummaryOfBusinessResults",           # IFRS 売上高
    "jpcrp_cor:OperatingRevenue1SummaryOfBusinessResults",      # 営業収益
    "jpcrp_cor:OperatingRevenue2SummaryOfBusinessResults",      # 営業収益
    "jpcrp_cor:RevenuesUSGAAPSummaryOfBusinessResults",         # 米国基準 売上高
    "jpcrp_cor:NetSalesUSGAAPSummaryOfBusinessResults",
    "jpcrp_cor:OperatingRevenuesUSGAAPSummaryOfBusinessResults",
]

# 銀行・保険・日本郵政などは「売上高」の代わりに「経常収益」を開示する
# （「経常利益」は OrdinaryIncomeLoss… という別の要素なので取り違えない）
REVENUE_FINANCIAL_ELEMENTS = [
    "jpcrp_cor:OrdinaryIncomeSummaryOfBusinessResults",          # 経常収益
]

NET_INCOME_ELEMENTS = [
    "jpcrp_cor:ProfitLossAttributableToOwnersOfParentSummaryOfBusinessResults",       # 日本基準（連結）
    "jpcrp_cor:ProfitLossAttributableToOwnersOfParentIFRSSummaryOfBusinessResults",   # IFRS
    "jpcrp_cor:NetIncomeLossAttributableToOwnersOfParentUSGAAPSummaryOfBusinessResults",  # 米国基準
    "jpcrp_cor:NetIncomeLossSummaryOfBusinessResults",                                # 単体のみの会社
    "jpcrp_cor:ProfitLossSummaryOfBusinessResults",
    "jpcrp_cor:ProfitLossIFRSSummaryOfBusinessResults",   # IFRSで「親会社の所有者に帰属」の行がない会社（HOYAなど）
]

# 企業独自の拡張項目で純利益を開示している会社向け（東京製鐵の ProfitSummaryOfBusinessResults など）。
# コロンの後ろの名前が完全一致するものだけを使う（「経常利益」などと取り違えないため）。
NET_INCOME_EXTENSION_LOCAL_NAMES = [
    "ProfitLossAttributableToOwnersOfParentSummaryOfBusinessResults",
    "ProfitAttributableToOwnersOfParentSummaryOfBusinessResults",
    "NetIncomeLossSummaryOfBusinessResults",
    "NetIncomeSummaryOfBusinessResults",
    "ProfitLossSummaryOfBusinessResults",
    "ProfitSummaryOfBusinessResults",
]

DIVIDEND_ELEMENTS = [
    "jpcrp_cor:DividendPaidPerShareSummaryOfBusinessResults",
]

EMPLOYEE_ELEMENTS = [
    "jpcrp_cor:NumberOfEmployees",
]

# 売上規模の区分（円）
SCALE_BUCKETS = [
    (10_000_000_000, "100億円未満"),
    (100_000_000_000, "100億〜1,000億円"),
    (1_000_000_000_000, "1,000億〜1兆円"),
    (float("inf"), "1兆円以上"),
]


# ---------------------------------------------------------------------------
# データ構造
# ---------------------------------------------------------------------------

@dataclass
class Stock:
    sec_code: str
    name: str
    edinet_code: str
    industry: Optional[str]
    fiscal_period_end: Optional[str]
    basis: Optional[str]                 # "consolidated" / "non_consolidated"
    revenue: Optional[float]
    revenue_prior: Optional[float]
    revenue_label: Optional[str]         # 画面表示用の名称（売上高／売上収益／営業収益／経常収益など）
    net_income: Optional[float]
    net_income_prior: Optional[float]
    dividend_per_share: Optional[float]
    employees: Optional[int]
    revenue_scale: Optional[str]
    revenue_increased: Optional[bool]
    profit_increased: Optional[bool]
    has_dividend: Optional[bool]
    doc_id: str
    submitted_at: Optional[str]
    source_elements: dict = field(default_factory=dict)  # どの要素IDから取ったか（検証用）


# ---------------------------------------------------------------------------
# HTTP（標準ライブラリのみ）
# ---------------------------------------------------------------------------

class EdinetError(RuntimeError):
    pass


class EdinetClient:
    def __init__(self, api_key: str, cache_dir: Path, sleep_sec: float = 3.0,
                 timeout: float = 60.0, max_retries: int = 3) -> None:
        if not api_key:
            raise EdinetError("APIキーがありません。環境変数 EDINET_API_KEY か --api-key で指定してください。")
        self.api_key = api_key
        self.cache_dir = cache_dir
        self.sleep_sec = sleep_sec
        self.timeout = timeout
        self.max_retries = max_retries
        self._last_request = 0.0
        (cache_dir / "lists").mkdir(parents=True, exist_ok=True)
        (cache_dir / "csv").mkdir(parents=True, exist_ok=True)

    # EDINET は短時間の連続アクセスで応答しなくなるため、必ず間隔を空ける
    def _wait(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.sleep_sec:
            time.sleep(self.sleep_sec - elapsed)
        self._last_request = time.monotonic()

    def _get(self, url: str) -> bytes:
        last_err: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            self._wait()
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return resp.read()
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    raise EdinetError(f"認証エラー（HTTP {e.code}）。APIキーを確認してください。") from e
                last_err = e
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last_err = e
            # 再試行の前に間隔を広げる
            time.sleep(self.sleep_sec * attempt * 2)
        raise EdinetError(f"取得に失敗しました: {url.split('Subscription-Key')[0]} ({last_err})")

    def list_documents(self, day: date) -> list[dict]:
        cache_file = self.cache_dir / "lists" / f"{day.isoformat()}.json"
        if cache_file.exists():
            data = json.loads(cache_file.read_text(encoding="utf-8"))
        else:
            q = urllib.parse.urlencode({"date": day.isoformat(), "type": 2,
                                        "Subscription-Key": self.api_key})
            raw = self._get(f"{API_BASE}/documents.json?{q}")
            data = json.loads(raw.decode("utf-8"))
            status = str(data.get("metadata", {}).get("status", data.get("StatusCode", "")))
            if status != "200":
                msg = data.get("metadata", {}).get("message") or data.get("message")
                raise EdinetError(f"書類一覧APIエラー {day}: status={status} {msg}")
            # 当日分は後から書類が増えるのでキャッシュしない
            if day < date.today():
                cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data.get("results") or []

    def get_csv_zip(self, doc_id: str) -> bytes:
        cache_file = self.cache_dir / "csv" / f"{doc_id}.zip"
        if cache_file.exists():
            return cache_file.read_bytes()
        q = urllib.parse.urlencode({"type": 5, "Subscription-Key": self.api_key})
        raw = self._get(f"{API_BASE}/documents/{doc_id}?{q}")
        if raw[:2] != b"PK":
            # ZIP でなければエラーJSONが返っている
            try:
                detail = json.loads(raw.decode("utf-8"))
            except Exception:
                detail = raw[:200]
            raise EdinetError(f"CSV取得エラー {doc_id}: {detail}")
        cache_file.write_bytes(raw)
        return raw


# ---------------------------------------------------------------------------
# EDINET コードリスト（業種）
# ---------------------------------------------------------------------------

def load_industry_map(cache_dir: Path, timeout: float = 60.0,
                      max_age_hours: float = 20.0) -> dict[str, dict]:
    """EDINETコード → {industry, sec_code, name, listed} の辞書を返す。
    コードリストは上場・廃止で日々変わるので、保存から max_age_hours 時間を過ぎたら取り直す。"""
    zpath = cache_dir / "Edinetcode.zip"
    stale = zpath.exists() and (time.time() - zpath.stat().st_mtime) > max_age_hours * 3600
    if not zpath.exists() or stale:
        req = urllib.request.Request(CODELIST_URL, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            zpath.write_bytes(resp.read())
    return parse_codelist_zip(zpath.read_bytes())


def parse_codelist_zip(raw: bytes) -> dict[str, dict]:
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        csv_name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        text = zf.read(csv_name).decode("cp932", errors="replace")
    lines = text.splitlines()
    # 1行目はダウンロード日などのメタ情報、2行目がヘッダー
    reader = csv.DictReader(lines[1:])
    result: dict[str, dict] = {}
    for row in reader:
        code = (row.get("ＥＤＩＮＥＴコード") or "").strip()
        if not code:
            continue
        result[code] = {
            "industry": (row.get("提出者業種") or "").strip() or None,
            "sec_code": (row.get("証券コード") or "").strip() or None,
            "name": (row.get("提出者名") or "").strip() or None,
            "listed": (row.get("上場区分") or "").strip() == "上場",
        }
    return result


# ---------------------------------------------------------------------------
# CSV（XBRL→CSV変換データ）の解析
# ---------------------------------------------------------------------------

def read_main_csv_rows(zip_bytes: bytes) -> list[dict]:
    """type=5 のZIPから本文（jpcrp...）のCSVを読み、行のリストを返す。"""
    rows: list[dict] = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = [n for n in zf.namelist()
                 if n.lower().endswith(".csv") and Path(n).name.startswith("jpcrp")]
        for n in names:
            text = zf.read(n).decode("utf-16", errors="replace")
            reader = csv.DictReader(io.StringIO(text), delimiter="\t")
            for r in reader:
                rows.append({(k or "").strip().strip('"'): (v or "").strip() for k, v in r.items()})
    return rows


DASH_VALUES = ("－", "-", "―", "—", "‐", "−")


class _Dash:
    """開示書類で「－」と記載されている値（該当なし）を表す目印。"""
    def __repr__(self) -> str:
        return "DASH"


DASH = _Dash()


def to_number(value: str) -> Optional[float]:
    v = (value or "").replace(",", "").strip()
    if v in ("", "NaN") or v in DASH_VALUES:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def build_fact_index(rows: Iterable[dict]) -> dict:
    """(要素ID, コンテキストID) → 数値 の索引。「－」の項目は DASH として記録する。"""
    index: dict = {}
    for r in rows:
        elem = r.get("要素ID", "")
        ctx = r.get("コンテキストID", "")
        if not elem or not ctx:
            continue
        raw = (r.get("値", "") or "").strip()
        num = to_number(raw)
        key = (elem, ctx)
        if num is not None:
            # 数値を優先（同じキーに「－」が先に入っていても上書きする）
            if key not in index or index[key] is DASH:
                index[key] = num
        elif raw in DASH_VALUES and key not in index:
            index[key] = DASH
    return index


def pick(index: dict, elements: list[str], context: str) -> tuple[Optional[float], Optional[str]]:
    for e in elements:
        v = index.get((e, context))
        if v is not None and v is not DASH:
            return v, e
    return None, None


# 業績の要約（SummaryOfBusinessResults）に標準の売上項目がない場合に見る、損益計算書本体の売上項目。
# IFRSでは「売上収益」（顧客への販売）を「収益」（公正価値の変動益などを含む）より優先する。
REVENUE_STATEMENT_ELEMENTS = [
    "jpigp_cor:RevenueIFRS",           # 売上収益（IFRS）
    "jpigp_cor:NetSalesIFRS",          # 売上高（IFRS）
    "jpigp_cor:Revenue2IFRS",          # 収益（IFRS）
    "jppfs_cor:NetSales",              # 売上高（日本基準）
    "jppfs_cor:OperatingRevenue1",     # 営業収益（日本基準）
    "jppfs_cor:OperatingRevenue2",
    "jppfs_cor:Revenue",
]

# それでも見つからない場合に使う、項目名のパターン（企業独自の拡張項目への対応）
REVENUE_FALLBACK_KEYWORDS = ["RevenueIFRS", "NetSalesIFRS", "Revenue", "NetSales", "OperatingRevenue"]
REVENUE_FALLBACK_EXCLUDE = ["Cost", "Intersegment", "Segment", "PerShare", "Ratio", "Rate",
                            "Growth", "Increase", "Decrease", "Expense", "Other", "Deferred"]


def pick_revenue_fallback(index: dict, context: str) -> tuple[Optional[float], Optional[str]]:
    """要素IDの名前（コロンの後ろ）が「…SummaryOfBusinessResults」または「…KeyFinancialData」で、
    売上を表す語を含むものを探す。"""
    candidates: list[tuple[int, str, float]] = []
    for (elem, ctx), v in index.items():
        if ctx != context or v is DASH or v is None or v <= 0:
            continue
        local = elem.split(":", 1)[-1]
        if not (local.endswith("SummaryOfBusinessResults") or local.endswith("KeyFinancialData")):
            continue
        if any(x in local for x in REVENUE_FALLBACK_EXCLUDE):
            continue
        for rank, kw in enumerate(REVENUE_FALLBACK_KEYWORDS):
            if kw in local:
                candidates.append((rank, elem, v))
                break
    if not candidates:
        return None, None
    candidates.sort(key=lambda c: (c[0], c[1]))
    _, elem, v = candidates[0]
    return v, elem


def scale_bucket(revenue: Optional[float]) -> Optional[str]:
    if revenue is None:
        return None
    for upper, label in SCALE_BUCKETS:
        if revenue < upper:
            return label
    return None


def compare(cur: Optional[float], prior: Optional[float]) -> Optional[bool]:
    if cur is None or prior is None:
        return None
    return cur > prior


DASH_NOTE = " (記載「－」→0)"


def revenue_label_for(element: Optional[str]) -> Optional[str]:
    """売上の要素名から、画面に出す名称を決める。"""
    if not element:
        return None
    local = element.split(" ", 1)[0].split(":", 1)[-1]
    if "OrdinaryIncome" in local:
        return "経常収益"
    if local == "RevenuesFromExternalCustomers":
        return "収益"
    if "BusinessRevenue" in local:
        return "事業収益"
    if "OperatingRevenue" in local:
        return "営業収益"
    if "RevenueIFRS" in local and "Revenue2IFRS" not in local:
        return "売上収益"
    if "NetSales" in local:
        return "売上高"
    if "Revenue" in local or "Income" in local:
        return "収益"
    return "売上高"


def pick_by_local_name(index: dict, local_names: list[str], context: str) -> tuple[Optional[float], Optional[str]]:
    """名前空間（コロンの前）を問わず、名前が一致する要素を探す。"""
    for name in local_names:
        hits = sorted(e for (e, ctx), v in index.items()
                      if ctx == context and v is not None and v is not DASH
                      and e.split(":", 1)[-1] == name)
        if hits:
            return index[(hits[0], context)], hits[0]
    return None, None


def pick_net_income(index: dict, context: str) -> tuple[Optional[float], Optional[str]]:
    v, e = pick(index, NET_INCOME_ELEMENTS, context)
    if v is None:
        v, e = pick_by_local_name(index, NET_INCOME_EXTENSION_LOCAL_NAMES, context)
    return v, e


def is_common_dividend_element(elem: str) -> bool:
    """企業独自の「普通株式の1株当たり配当額」の要素か（中間配当・優先株式は除く）。"""
    local = elem.split(":", 1)[-1]
    return ("DividendPaidPerShare" in local and "Common" in local and "Interim" not in local
            and local.endswith("SummaryOfBusinessResults"))


def pick_dividend(index: dict) -> tuple[Optional[float], Optional[str]]:
    """配当は個別（単体）ベースで開示される。「－」は無配として 0 円にする。
    標準の項目がない場合は、普通株式の配当を表す企業独自の項目を使う（優先株式の配当は使わない）。"""
    for ctx in (CTX_CUR_NC, CTX_CUR):
        v, e = pick(index, DIVIDEND_ELEMENTS, ctx)
        if v is not None:
            return v, e
    for ctx in (CTX_CUR_NC, CTX_CUR):
        for e in DIVIDEND_ELEMENTS:
            if index.get((e, ctx)) is DASH:
                return 0.0, f"{e} (記載「－」→無配)"
    for ctx in (CTX_CUR_NC, CTX_CUR):
        ext = sorted(e for (e, c) in index if c == ctx and is_common_dividend_element(e))
        for e in ext:
            v = index[(e, ctx)]
            if v is DASH:
                return 0.0, f"{e} (記載「－」→無配)"
            if v is not None:
                return v, e
    return None, None


# 最後の手段：セグメント情報の「外部顧客への売上高」の合計。
# 生命保険（第一ライフ、T&Dなど）は業績の要約に経常収益の合計がなく、ここに経常収益の合計が入る。
# セーラー広告のように業績の要約が「総売上高」（会計基準外の指標）のみの会社でも、正式な収益が入る。
REVENUE_SEGMENT_TOTAL_ELEMENTS = [
    "jpcrp_cor:RevenuesFromExternalCustomers",
]

FINANCIAL_INDUSTRIES = ("銀行業", "保険業", "証券、商品先物取引業", "その他金融業")


def pick_revenue_chain(index: dict, context: str) -> tuple[Optional[float], Optional[str]]:
    """業績の要約 → 金融業の経常収益 → 損益計算書本体 → 名前のパターン → セグメント合計、の順に探す。"""
    for elements in (REVENUE_ELEMENTS, REVENUE_FINANCIAL_ELEMENTS, REVENUE_STATEMENT_ELEMENTS):
        v, e = pick(index, elements, context)
        if v is not None:
            return v, e
    v, e = pick_revenue_fallback(index, context)
    if v is not None:
        return v, e
    v, e = pick(index, REVENUE_SEGMENT_TOTAL_ELEMENTS, context)
    if v is not None and v > 0:
        return v, e
    return None, None


def revenue_dash(index: dict, context: str) -> tuple[Optional[float], Optional[str]]:
    """売上の欄が「－」（売上なし。創薬ベンチャーなど）なら 0 円として扱う。"""
    for e in REVENUE_ELEMENTS + REVENUE_FINANCIAL_ELEMENTS + REVENUE_STATEMENT_ELEMENTS:
        if index.get((e, context)) is DASH:
            return 0.0, e + DASH_NOTE
    # 企業独自の売上項目（事業収益など）が「－」の場合
    for (e, ctx), v in sorted(index.items(), key=lambda kv: kv[0]):
        if ctx != context or v is not DASH:
            continue
        local = e.split(":", 1)[-1]
        if not (local.endswith("SummaryOfBusinessResults") or local.endswith("KeyFinancialData")):
            continue
        if any(x in local for x in REVENUE_FALLBACK_EXCLUDE):
            continue
        if any(kw in local for kw in REVENUE_FALLBACK_KEYWORDS):
            return 0.0, e + DASH_NOTE
    return None, None


def extract_financials(index: dict) -> dict:
    """連結の数値を優先し、なければ単体の数値を使う（同じ銘柄の中で混在させない）。"""
    rev, rev_e = pick(index, REVENUE_ELEMENTS, CTX_CUR)
    ni, ni_e = pick_net_income(index, CTX_CUR)
    if rev is None and ni is not None:
        rev, rev_e = pick_revenue_chain(index, CTX_CUR)
    if rev is None and ni is not None:
        rev, rev_e = revenue_dash(index, CTX_CUR)
    if rev is not None or ni is not None:
        basis = "consolidated"
        cur, pri, inst = CTX_CUR, CTX_PRI, CTX_CUR_INSTANT
    else:
        basis = "non_consolidated"
        cur, pri, inst = CTX_CUR_NC, CTX_PRI_NC, CTX_CUR_INSTANT_NC
        rev, rev_e = pick_revenue_chain(index, cur)
        ni, ni_e = pick_net_income(index, cur)
        if rev is None and ni is not None:
            rev, rev_e = revenue_dash(index, cur)
        if rev is None and ni is None:
            basis = None
    rev_prior_elem = rev_e.replace(DASH_NOTE, "") if rev_e else None
    rev_p, _ = pick(index, [rev_prior_elem] if rev_prior_elem else REVENUE_ELEMENTS, pri)
    if rev_p is None and rev_prior_elem and index.get((rev_prior_elem, pri)) is DASH:
        rev_p = 0.0
    ni_p, _ = pick(index, [ni_e] if ni_e else NET_INCOME_ELEMENTS, pri)
    emp, _ = pick(index, EMPLOYEE_ELEMENTS, inst)
    if emp is None:
        emp, _ = pick(index, EMPLOYEE_ELEMENTS, CTX_CUR_INSTANT_NC)

    div, div_e = pick_dividend(index)

    return {
        "basis": basis,
        "revenue": rev, "revenue_prior": rev_p,
        "revenue_label": revenue_label_for(rev_e),
        "net_income": ni, "net_income_prior": ni_p,
        "dividend_per_share": div,
        "employees": int(emp) if emp is not None else None,
        "source_elements": {k: v for k, v in
                            {"revenue": rev_e, "net_income": ni_e, "dividend": div_e}.items() if v},
    }


# ---------------------------------------------------------------------------
# 書類一覧の絞り込み
# ---------------------------------------------------------------------------

def is_target_document(doc: dict) -> bool:
    return (
        doc.get("docTypeCode") == DOC_TYPE_ANNUAL_REPORT
        and doc.get("ordinanceCode") == ORDINANCE_CORP
        and doc.get("formCode") == FORM_DOMESTIC_ANNUAL
        and bool(doc.get("secCode"))
        and str(doc.get("csvFlag")) == "1"
        and str(doc.get("withdrawalStatus", "0")) == "0"
    )


def normalize_sec_code(sec_code: str) -> str:
    """EDINET の証券コードは5桁（末尾0）なので、4桁表記にそろえる。"""
    s = (sec_code or "").strip()
    return s[:4] if len(s) == 5 and s.endswith("0") else s


def daterange(start: date, end: date) -> Iterable[date]:
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


class AllDaysFailed(EdinetError):
    pass


def collect_latest_filings(client: EdinetClient, start: date, end: date, log) -> dict[str, dict]:
    """期間内の有報を集め、銘柄ごとに最新の1件だけを残す。"""
    latest: dict[str, dict] = {}
    days = 0
    failed = 0
    for day in daterange(start, end):
        days += 1
        try:
            docs = client.list_documents(day)
        except AllDaysFailed:
            raise
        except EdinetError as e:
            failed += 1
            log(f"  ! {e}")
            # 認証エラーはどの日でも同じなので、すぐに打ち切る
            if "認証エラー" in str(e):
                raise AllDaysFailed(str(e)) from e
            continue
        targets = [d for d in docs if is_target_document(d)]
        log(f"  {day}: 書類 {len(docs)} 件中、有報 {len(targets)} 件")
        for d in targets:
            code = normalize_sec_code(d["secCode"])
            prev = latest.get(code)
            if prev is None or (d.get("submitDateTime") or "") > (prev.get("submitDateTime") or ""):
                latest[code] = d
    if days > 0 and failed == days:
        raise AllDaysFailed("すべての日付で書類一覧の取得に失敗しました。通信環境とAPIキーを確認してください。")
    return latest


# ---------------------------------------------------------------------------
# メイン処理
# ---------------------------------------------------------------------------

def build_stock(doc: dict, zip_bytes: bytes, industries: dict[str, dict]) -> Stock:
    fin = extract_financials(build_fact_index(read_main_csv_rows(zip_bytes)))
    info = industries.get(doc.get("edinetCode", ""), {})
    div = fin["dividend_per_share"]
    return Stock(
        sec_code=normalize_sec_code(doc["secCode"]),
        name=doc.get("filerName") or info.get("name") or "",
        edinet_code=doc.get("edinetCode", ""),
        industry=info.get("industry"),
        fiscal_period_end=doc.get("periodEnd"),
        basis=fin["basis"],
        revenue=fin["revenue"],
        revenue_prior=fin["revenue_prior"],
        revenue_label=("経常収益" if fin["revenue_label"] == "収益"
                       and info.get("industry") in FINANCIAL_INDUSTRIES
                       and "RevenuesFromExternalCustomers" in (fin["source_elements"].get("revenue") or "")
                       else fin["revenue_label"]),
        net_income=fin["net_income"],
        net_income_prior=fin["net_income_prior"],
        dividend_per_share=div,
        employees=fin["employees"],
        revenue_scale=scale_bucket(fin["revenue"]),
        revenue_increased=compare(fin["revenue"], fin["revenue_prior"]),
        profit_increased=compare(fin["net_income"], fin["net_income_prior"]),
        has_dividend=(div > 0) if div is not None else None,
        doc_id=doc.get("docID", ""),
        submitted_at=doc.get("submitDateTime"),
        source_elements=fin["source_elements"],
    )


def merge_with_existing(out_path: Path, new_stocks: dict[str, Stock]) -> list[dict]:
    """既存の stocks.json がある場合、今回取得した銘柄だけを差し替える。"""
    merged: dict[str, dict] = {}
    if out_path.exists():
        try:
            old = json.loads(out_path.read_text(encoding="utf-8"))
            for s in old.get("stocks", []):
                merged[s["sec_code"]] = s
        except Exception:
            pass
    for code, s in new_stocks.items():
        merged[code] = asdict(s)
    # 並び順は銘柄コード順（中立な順序。評価による並べ替えはしない）
    return [merged[k] for k in sorted(merged)]


def prune_delisted(stock_list: list[dict], industries: dict[str, dict]) -> tuple[list[dict], list[str]]:
    """コードリストで「非上場」となった会社を除く。コードリストが取れなかった場合は何も除かない。"""
    if not industries:
        return stock_list, []
    kept, pruned = [], []
    for s in stock_list:
        info = industries.get(s.get("edinet_code") or "")
        if info is not None and not info.get("listed"):
            pruned.append(s["sec_code"])
        else:
            kept.append(s)
    return kept, pruned


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="EDINETの有価証券報告書から条件検索用の銘柄データを作る")
    p.add_argument("--from", dest="date_from", required=True, help="開始日 YYYY-MM-DD（提出日）")
    p.add_argument("--to", dest="date_to", required=True, help="終了日 YYYY-MM-DD（提出日）")
    p.add_argument("--out", default="stocks.json", help="出力ファイル（既定: stocks.json）")
    p.add_argument("--cache-dir", default="cache", help="キャッシュ用フォルダ（既定: cache）")
    p.add_argument("--api-key", default=os.environ.get("EDINET_API_KEY", ""), help="EDINET APIキー")
    p.add_argument("--sleep", type=float, default=3.0, help="リクエスト間隔（秒、既定: 3）")
    p.add_argument("--limit", type=int, default=0, help="処理する銘柄数の上限（試験用、0=無制限）")
    p.add_argument("--no-merge", action="store_true", help="既存の出力と統合せずに上書きする")
    p.add_argument("--prune-delisted", action="store_true",
                   help="EDINETコードリストで上場していない会社を出力から除く（毎日の更新用）")
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    log = lambda m: print(m, file=sys.stderr, flush=True)

    try:
        start = date.fromisoformat(args.date_from)
        end = date.fromisoformat(args.date_to)
    except ValueError:
        log("日付は YYYY-MM-DD 形式で指定してください。")
        return 2
    if end < start:
        log("終了日が開始日より前です。")
        return 2
    if (end - start).days > 366:
        log("期間は366日以内にしてください。")
        return 2

    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    try:
        client = EdinetClient(args.api_key, cache_dir, sleep_sec=args.sleep)
    except EdinetError as e:
        log(str(e))
        return 2

    log("EDINETコードリスト（業種）を読み込み中…")
    try:
        industries = load_industry_map(cache_dir)
    except Exception as e:
        log(f"  ! コードリストを取得できませんでした（業種は空欄になります）: {e}")
        industries = {}

    log(f"書類一覧を取得中: {start} 〜 {end}")
    try:
        filings = collect_latest_filings(client, start, end, log)
    except AllDaysFailed as e:
        log(f"中止: {e}")
        return 1
    codes = sorted(filings)
    if args.limit > 0:
        codes = codes[: args.limit]
    log(f"対象銘柄: {len(codes)} 件")

    stocks: dict[str, Stock] = {}
    failures: list[str] = []
    for i, code in enumerate(codes, 1):
        doc = filings[code]
        try:
            zip_bytes = client.get_csv_zip(doc["docID"])
            s = build_stock(doc, zip_bytes, industries)
            stocks[code] = s
            log(f"  [{i}/{len(codes)}] {code} {s.name}  売上={s.revenue}  純利益={s.net_income}  配当={s.dividend_per_share}")
        except Exception as e:
            failures.append(code)
            log(f"  [{i}/{len(codes)}] {code} ! {e}")

    out_path = Path(args.out)
    stock_list = ([asdict(stocks[k]) for k in sorted(stocks)] if args.no_merge
                  else merge_with_existing(out_path, stocks))
    if args.prune_delisted:
        stock_list, pruned = prune_delisted(stock_list, industries)
        if pruned:
            log(f"上場廃止などで除外: {', '.join(pruned)}")
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "attribution": ATTRIBUTION,
        "note": "開示書類の数値をそのまま整理したもので、銘柄の評価や推奨ではありません。",
        "count": len(stock_list),
        "stocks": stock_list,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"完了: {out_path}（{len(stock_list)} 銘柄、今回取得 {len(stocks)}、失敗 {len(failures)}）")
    if failures:
        log("失敗した銘柄: " + ", ".join(failures))
    return 0


if __name__ == "__main__":
    sys.exit(main())
