<p align="center">
  <img src="charts/地域法人情報スクレイパーの全体像.png" alt="法人番号スクレイパーの紹介バナー">
</p>

# houjin-bangou-scraper - 法人番号公表サイト スクレイパ

国税庁法人番号公表サイト（https://www.houjin-bangou.nta.go.jp/）から、
対象都道府県の全市区町村に所在する法人の**公開基本3情報**（法人番号・名称・所在地）を取得し、
SQLiteに保存するスクレイパです。

表示上限（5万件）超過の自治体は「法人番号指定年月日」による再帰的日付2分割で取得します。
単日でも上限を超える範囲は自動取得せず、`fetch_logs`にスキップとして記録します。
中断しても進捗ファイルから再開できます。

## 特徴

- requests + BeautifulSoup（lxml）のみで動作します。Seleniumは不要です。
- POSTフォーム + CSRFトークン方式に対応しています。
- 1回の検索で5万件を超える自治体は「法人番号指定年月日」で期間を再帰的に2分割して取得します。単日でも超過する範囲はスキップ記録します。
- 法人番号を主キーにしたUPSERTのため重複登録されません。
- ページ単位で進捗を保存するため途中停止しても再開できます。
- 取得結果・失敗・実行サマリーをSQLiteとログに残します。

## 必要環境

- OS: Windows（PowerShell前提）
- Python: 3.10以上（動作確認: 3.11.15）
- 依存パッケージ: requests / beautifulsoup4 / lxml / pytest（`requirements.txt`参照）

## 導入手順

初めて使う場合は上から順に実行します。

```powershell
# 1. プロジェクト直下へ移動
cd houjin-bangou-scraper

# 2. 仮想環境を使う場合（任意）
python -m venv .venv
.venv\Scripts\Activate.ps1

# 3. 依存パッケージの導入
pip install -r requirements.txt
```

`data/` `logs/` `state/`は実行時に自動生成されます。Git管理対象外です。

## 使用手順

### 1. 基本実行（東京都・全期間）

```powershell
python -m src.main --prefectures 東京都
```

### 2. 対象を変える

```powershell
# 複数都道府県
python -m src.main --prefectures 東京都 --prefectures 大阪府

# 開始日を指定（法人番号指定年月日のFrom側）
python -m src.main --prefectures 東京都 --date-start 2015-10-01

# 動作確認用の小規模取得（例: 特定期間のみ）
python -m src.main --prefectures 東京都 --date-start 2016-02-01
```

引数一覧：

| 引数 | 意味 | 既定値 |
|------|------|--------|
| `--prefectures` | 対象都道府県名。繰り返し指定可 | `config.py`の`TARGET_PREFECTURES` |
| `--date-start` | 取得開始日（YYYY-MM-DD形式）。未来日はエラー | `config.py`の`DATE_RANGE_START`（2015-10-01） |

注意：`--date-end`は未対応です。終端は実行日になります。

### 3. 中断と再開

- 進捗は`state/progress.json`にページ単位で保存されます。
- 同じコマンドを再実行すると完了済みページは飛ばされます。
- 最初から取り直したい場合は`state/progress.json`を削除します。

### 4. 成果物の確認

| 成果物 | パス | 内容 |
|--------|------|------|
| DB | `data/houjin.sqlite` | 法人データ本体 |
| ログ | `logs/scraping_YYYYMMDD_HHMMSS.log` | 実行ログ |
| 進捗 | `state/progress.json` | 再開用進捗 |

DB確認例：

```powershell
# sqlite3がある場合
sqlite3 data/houjin.sqlite "SELECT COUNT(*) FROM companies;"
sqlite3 data/houjin.sqlite "SELECT * FROM companies LIMIT 5;"
sqlite3 data/houjin.sqlite "SELECT * FROM fetch_logs WHERE status='over_limit_skipped';"
```

Python確認例：

```python
import sqlite3
con = sqlite3.connect("data/houjin.sqlite")
print(con.execute("SELECT COUNT(*) FROM companies").fetchone())
con.close()
```

## 設定変更

値はコードに直書きせず`config.py`（ルート）に集約されています。主な項目：

| 設定 | 意味 | 既定値 |
|------|------|--------|
| `TARGET_PREFECTURES` | 既定の対象都道府県 | `["東京都"]` |
| `PAGE_SIZE` | 1ページ表示件数 | 100 |
| `MAX_DISPLAY_LIMIT` | サイトの表示上限 | 50000 |
| `ACCESS_INTERVAL` / `JITTER` | アクセス間隔秒 + ゆらぎ | 1.0 / 0.3 |
| `MAX_RETRIES` / `BACKOFF_BASE` | リトライ回数 / バックオフ基数 | 3 / 2.0 |
| `MAX_CONSECUTIVE_ERRORS` | 連続エラー安全停止回数 | 5 |
| `DATE_RANGE_START` | 日付分割の開始日 | 2015-10-01 |
| `SELECTORS` | HTML抽出セレクタ集約 | 辞書参照 |

`src/config.py`はルート設定の再エクスポート＋元号変換（`era_code`）・期間計算（`DateRange`）を担います。
値はルート、変換ロジックは`src/config.py`と覚えると迷いません。

## 仕組み概要

処理は「取得・編集・保存」の3工程です。

```text
python -m src.main
  -> validate_input（対象検証）
  -> execute_logic（市区町村列挙 -> PageFetcher）
       -> SiteSession.fetch_index（CSRFトークン取得）
       -> SiteSession.fetch_municipalities（市区町村JSON取得）
       -> paginator.collect_range（上限判定・日付2分割）
       -> paginator.fetch_all_pages（viewPageNoでページ送り）
       -> parser.parse_results（法人番号・名称・フリガナ・所在地抽出）
       -> database.upsert_company（SQLite保存）
       -> state.save（ページ毎に進捗保存）
  -> format_output（サマリー表示）
```

重要な仕様（実測確認済み）：

- 検索は`POST /kensaku-kekka.html`、市区町村一覧は`POST /index.html?event=select`（JSON）です。
- 総件数は「全N件」、上限超過は「件を超えているため」で判定します。
- 存在しないページ番号を送ると1ページ目が返るため、終了ページは総件数から計算します。
- 日付絞込は法人番号指定年月日（`chgYmdShTargetCkbx=2`＋元号コード年/月/日）を使います。
- 2015-10-05の一括付番に件数が集中するため、この日を含む範囲は超過しやすいです。
- 単日でも超過する範囲は自動取得不能としてスキップし`fetch_logs`に記録します。人手確認が必要です。
- 詳細ページURL形式：`/henkorireki-johoto.html?selHouzinNo=<法人番号>`。

DBテーブル：

- `companies`: 法人本体（主キー`corporate_number`）
- `fetch_logs`: ページ単位の成功・失敗・分割記録
- `failed_records`: レコード単位の失敗
- `run_logs`: 実行毎サマリー

## テスト・Lint

```powershell
# テスト（28件、ネットワーク不使用）
python -m pytest tests -q

# Lint
python -m ruff check src tests config.py
```

テスト内容：`test_parser`（抽出）・`test_config`（元号・期間）・`test_paginator`（FakeSiteによる分割・resume）・`test_database`（UPSERT）・`test_state`（保存・復元）。

## フォルダ構成

```text
houjin-bangou-scraper/
├── README.md               ← このファイル
├── AGENTS.md               ← 開発者向けプロジェクト規約
├── config.py               ← 全設定値とセレクタ集約
├── requirements.txt
├── pyproject.toml          ← ruff/pytest設定
├── src/
│   ├── main.py             ← エントリ（main/validate_input/execute_logic/format_output）
│   ├── http_client.py      ← SiteSession（通信・リトライ・市区町村JSON）
│   ├── parser.py           ← HTML抽出
│   ├── paginator.py        ← ページ送り・日付分割・resume連携
│   ├── database.py         ← SQLite保存
│   ├── state.py            ← 進捗JSON
│   ├── logger.py           ← ログ
│   └── config.py           ← ルート再エクスポート＋DateRange/era_code
├── tests/                  ← pytest
├── docs/
│   ├── handover.md         ← 開発経緯・残タスク・実行コマンド
│   ├── beginner_overview.md← 初心者・中級者向け詳細解説
│   └── understanding_guide.md
├── diagrams/               ← 6種の構成図（.md/.mmd）
├── data/ logs/ state/      ← 実行時生成物（Git対象外）
```

学習順のおすすめ：`AGENTS.md` -> `config.py` -> `src/config.py` -> `src/http_client.py` -> `src/parser.py` -> `src/paginator.py` -> `src/database.py` `state.py` `logger.py` -> `src/main.py` -> `tests/` -> `docs/handover.md`。詳細は`docs/beginner_overview.md`参照。

## 注意・トラブル対処

- アクセス間隔（既定1.0秒＋ジッター0.3秒）を守ります。短くしすぎるとサーバ負荷・制限の原因になります。
- 東京全市区町村の本番取得は時間とネットワークを要するため分割実施を推奨します。
- `over_limit_skipped`が出た範囲は`fetch_logs`を確認し人手で再調査します。
- サイト仕様変更時は`config.py`の`SELECTORS`・元号表・行構造・総件数文言を見直し、実測HTMLで再検証します。
- 実行は必ず`python -m src.main`形式で行います（`src/config.py`のパス補正前提）。
- `data/` `logs/` `state/`を削除しても再生成されます。DBを消すと取得済みも取り直しになります。

## 関連資料

- `AGENTS.md`: 実測仕様・規約・実行コマンド
- `docs/beginner_overview.md`: 初心者・中級者向け解説（行番号付き）
- `docs/handover.md`: 方針・実装・結果・残タスク
- `diagrams/`: フロー・シーケンス・クラス・構造・マインドマップ・状態遷移図

## 公開禁止情報

公開禁止情報は含まれていません
