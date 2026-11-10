# 銘柄データ（EDINET → アプリ）

EDINETの有価証券報告書から、条件検索用の銘柄データを作り、GitHub Pages で公開する仕組みです。
毎日 20:17（日本時間）に GitHub Actions が新しい書類を取り込み、アプリ用データを更新します。
費用はかかりません（公開リポジトリの GitHub Actions と GitHub Pages は無料）。

## ファイル構成

| ファイル | 役割 |
|---|---|
| `edinet_stock_builder.py` | EDINET APIから有価証券報告書を取得し、売上・純利益・配当などを取り出して `data/stocks.json` に保存 |
| `build_app_data.py` | `data/stocks.json` からアプリ用の `site/v1/stocks.json`・`site/v1/meta.json` を作る。不備があれば公開しない |
| `data/stocks.json` | 作業用の詳しいデータ（全銘柄）。毎日の更新で差分だけ書き換わる |
| `.github/workflows/update-data.yml` | 毎日の自動更新と公開 |
| `test_*.py` | テスト（ネットワーク不要）。自動更新の前に毎回実行される |
| `run_edinet_test.sh` / `dump_elements.py` | 手元（Mac）での試験実行と調査用 |

## 公開されるデータ

`https://<GitHubのユーザー名>.github.io/<リポジトリ名>/` の下に置かれます。

- `v1/meta.json` … 業種一覧（東証33業種の順）、売上規模の区分、業種×規模の社数、出典、更新日時、各項目の説明
- `v1/stocks.json` … 銘柄ごとの数値（銘柄コード順。並べ替えや評価はしない）

`stocks.json` の1銘柄の例:

```json
{"code":"1301","name":"極洋","legal_name":"株式会社　極洋","industry":0,"fy_end":"2026-03-31",
 "basis":"c","rev":334612000000,"rev_prior":302681000000,"rev_label":"売上高",
 "ni":6841000000,"ni_prior":6740000000,"div":150,"emp":2542,"scale":2,
 "doc":"S100YE8K","filed":"2026-06-22"}
```

- `industry` と `scale` は `meta.json` の `industries`・`scales` の id
- `rev_label` の名称で売上を表示する（銀行・保険は「経常収益」など）
- `null` は「開示から取得できなかった」。アプリでは「—」と表示する
- `div` が 0 は無配

アプリで表示する際は、`meta.json` の `attribution`（出典）を必ず表示してください（公共データ利用規約 PDL1.0 の条件）。

## 初回の準備

1. **APIキーを再発行する**：以前チャットに貼ったキーは使わず、EDINETのAPIキー発行画面で新しいキーを発行します。
2. **GitHubで公開リポジトリを作る**（例: `kabu-data`）。無料で Pages と Actions を使うため「Public」にします。APIキーはコードに含めず、次の手順の Secrets に保存するので公開しても漏れません。
3. **このフォルダの中身を登録する**（`.github` という隠しフォルダも含めるため、ターミナルで行います）。

   ```bash
   cd このフォルダ
   git init -b main
   git add .
   git commit -m "初回登録"
   git remote add origin https://github.com/<ユーザー名>/<リポジトリ名>.git
   git push -u origin main
   ```

4. **APIキーを登録する**：リポジトリの Settings → Secrets and variables → Actions → New repository secret
   - Name: `EDINET_API_KEY`
   - Secret: 再発行したAPIキー
5. **Pagesを有効にする**：Settings → Pages → Build and deployment → Source を「GitHub Actions」にする。
6. **初回を手動で実行する**：Actions タブ → 「Update stock data」→ Run workflow（days は 7 のまま）。
   緑のチェックになったら `https://<ユーザー名>.github.io/<リポジトリ名>/` を開き、銘柄数と更新日時が表示されることを確認します。

以降は毎日自動で更新されます。

## 運用メモ

- **失敗したとき**：GitHubから失敗のお知らせメールが届きます。データに不備がある場合（銘柄数が3,000未満、売上の空欄が3%超など）は公開されず、前日のデータがそのまま残ります。
- **しばらく止まったとき**：公開リポジトリでは、60日間まったく更新がないと定期実行が自動で止まります。書類の提出が続く限り毎日コミットされるので通常は止まりませんが、止まった場合は Actions タブから再開できます。
- **長く止めていたあと**：Run workflow の days に止めていた日数を入れて実行すると、その分を取り込みます（最大366日）。
- **上場廃止**：EDINETのコードリストで「非上場」になった会社は自動で除かれます。
- **6月下旬**：有価証券報告書の提出が集中するため、1回の実行に1〜2時間かかることがあります（取得間隔を3秒空けているため）。
