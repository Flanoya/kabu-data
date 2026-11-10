#!/bin/bash
# =============================================================================
# run_edinet_test.sh
#
# EDINET 銘柄データ作成スクリプトの試験実行を、まとめて行うためのスクリプト（Mac用）。
#
#   1. 必要なファイルがそろっているかを確認
#   2. Python のバージョンを確認（3.9以上）
#   3. 通信なしのテストを実行
#   4. APIキーを入力（画面に表示されない。ファイルにも保存しない）
#   5. 20銘柄で試験取得（2026年6月15日〜30日に提出された有価証券報告書）
#   6. 結果の要約を表示し、送付用のZIPファイルを作成
#
# 使い方:
#   このファイルを edinet_stock_builder.py などと同じフォルダに置き、
#   ターミナルで次を実行する。
#       cd このフォルダ
#       bash run_edinet_test.sh
#
#   期間や銘柄数を変えたい場合:
#       FROM=2026-06-01 TO=2026-06-30 LIMIT=50 bash run_edinet_test.sh
# =============================================================================

set -u

FROM="${FROM:-2026-06-15}"
TO="${TO:-2026-06-30}"
LIMIT="${LIMIT:-20}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

STAMP="$(date +%Y%m%d_%H%M%S)"
OUT_JSON="stocks_${STAMP}.json"
LOG_FILE="log_${STAMP}.txt"
SUMMARY_FILE="summary_${STAMP}.txt"
ZIP_FILE="edinet_result_${STAMP}.zip"

echo "=============================================="
echo " EDINET 銘柄データ 試験実行"
echo " 期間: ${FROM} 〜 ${TO} / 上限: ${LIMIT} 銘柄"
echo "=============================================="

# --- 1. ファイル確認 ----------------------------------------------------------
echo ""
echo "[1/6] 必要なファイルを確認しています…"
for f in edinet_stock_builder.py test_edinet_stock_builder.py; do
  if [ ! -f "$f" ]; then
    echo "  ✗ ${f} が見つかりません。このスクリプトと同じフォルダに置いてください。"
    exit 1
  fi
  echo "  ✓ ${f}"
done

# --- 2. Python 確認 -----------------------------------------------------------
echo ""
echo "[2/6] Python のバージョンを確認しています…"
if ! command -v python3 >/dev/null 2>&1; then
  echo "  ✗ python3 が見つかりません。"
  echo "    https://www.python.org/downloads/ から Python 3 をインストールしてください。"
  exit 1
fi
PYVER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
  echo "  ✗ Python ${PYVER} です。3.9 以上が必要です。"
  exit 1
fi
echo "  ✓ Python ${PYVER}"

# --- 3. テスト ----------------------------------------------------------------
echo ""
echo "[3/6] 通信なしのテストを実行しています…"
if python3 -m unittest test_edinet_stock_builder.py > "test_${STAMP}.txt" 2>&1; then
  echo "  ✓ テストはすべて成功しました"
else
  echo "  ✗ テストが失敗しました。test_${STAMP}.txt の内容を送ってください。"
  exit 1
fi

# --- 4. APIキー入力 -----------------------------------------------------------
echo ""
echo "[4/6] EDINET の APIキーを入力してください（入力中の文字は表示されません）"
if [ -n "${EDINET_API_KEY:-}" ]; then
  echo "  ✓ 環境変数 EDINET_API_KEY に設定済みのキーを使います"
else
  read -r -s -p "  APIキー: " EDINET_API_KEY
  echo ""
  if [ -z "$EDINET_API_KEY" ]; then
    echo "  ✗ APIキーが入力されていません。"
    exit 1
  fi
fi
export EDINET_API_KEY

# --- 5. 試験取得 --------------------------------------------------------------
echo ""
echo "[5/6] EDINET からデータを取得しています（数分かかります。止まって見えても正常です）…"
echo "      進み具合は ${LOG_FILE} に記録されます"
python3 edinet_stock_builder.py \
  --from "$FROM" --to "$TO" --limit "$LIMIT" \
  --out "$OUT_JSON" --cache-dir cache --no-merge 2> "$LOG_FILE"
RC=$?

# ログにAPIキーが残らないよう、念のため伏せ字にする
if [ -f "$LOG_FILE" ]; then
  python3 - "$LOG_FILE" <<'PY'
import os, sys
p = sys.argv[1]
key = os.environ.get("EDINET_API_KEY", "")
text = open(p, encoding="utf-8", errors="replace").read()
if key:
    text = text.replace(key, "****")
open(p, "w", encoding="utf-8").write(text)
PY
fi

if [ $RC -ne 0 ] || [ ! -f "$OUT_JSON" ]; then
  echo "  ✗ 取得に失敗しました。${LOG_FILE} の最後の部分:"
  tail -n 15 "$LOG_FILE" | sed 's/^/    /'
  echo ""
  echo "  よくある原因:"
  echo "    - 認証エラー → APIキーの貼り付け間違い"
  echo "    - 取得に失敗 → 通信環境、またはEDINET側の混雑（時間をおいて再実行）"
  exit 1
fi
echo "  ✓ 取得が完了しました → ${OUT_JSON}"

# --- 6. 要約と送付用ZIP -------------------------------------------------------
echo ""
echo "[6/6] 結果を要約しています…"
python3 - "$OUT_JSON" > "$SUMMARY_FILE" <<'PY'
import json, sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
stocks = data.get("stocks", [])
n = len(stocks)
print(f"取得銘柄数: {n}")
if n == 0:
    print("銘柄が0件です。期間内に有価証券報告書の提出がなかった可能性があります。")
    sys.exit(0)

def missing(field):
    return [s for s in stocks if s.get(field) is None]

print("")
print("■ 項目ごとの空欄（取れなかった）件数")
for field, label in [("revenue", "売上高"), ("net_income", "純利益"),
                     ("dividend_per_share", "1株配当"), ("industry", "業種"),
                     ("employees", "従業員数")]:
    m = missing(field)
    print(f"  {label:<8} 空欄 {len(m):>3} / {n}")

print("")
print("■ 売上高が取れなかった銘柄（スクリプト改善の材料になります）")
for s in missing("revenue"):
    print(f"  {s['sec_code']} {s['name']}  業種={s.get('industry')}  基準={s.get('basis')}")
if not missing("revenue"):
    print("  なし")

print("")
print("■ 取得結果の一覧（銘柄コード順）")
def fmt_yen(v):
    if v is None:
        return "—"
    return f"{v/1e8:,.0f}億円"
for s in stocks:
    div = s.get("dividend_per_share")
    div_s = "—" if div is None else f"{div:g}円"
    print(f"  {s['sec_code']} {s['name'][:16]:<16} 業種={s.get('industry') or '—'}"
          f"  売上={fmt_yen(s.get('revenue'))}  純利益={fmt_yen(s.get('net_income'))}"
          f"  配当={div_s}  増収={s.get('revenue_increased')}  増益={s.get('profit_increased')}")

print("")
print("■ 照合のお願い")
print("  上の一覧から1〜2社を選び、EDINETでその会社の有価証券報告書（PDF）を開いて、")
print("  冒頭の「主要な経営指標等の推移」の売上高・純利益・配当と一致するか確認してください。")
PY

cat "$SUMMARY_FILE"

zip -q "$ZIP_FILE" "$OUT_JSON" "$LOG_FILE" "$SUMMARY_FILE"
echo ""
echo "=============================================="
echo " 完了しました。"
echo " 次のZIPファイルを送ってください: ${ZIP_FILE}"
echo " （APIキーは含まれていません）"
echo "=============================================="
