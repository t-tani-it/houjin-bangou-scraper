# handover.md - 法人番号公表サイト スクレイパ(webScraping)

## 方針（計画）
- 国税庁法人番号公表サイトから公開基本3情報（法人番号・名称・所在地）を都道府県単位で取得
- コードは「取得・編集・保存」の3工程に分離（fetch → parse → store）
- 表示上限5万件超過の自治体は「法人番号指定年月日」による再帰的日付2分割で完全取得
- サイトは POSTフォーム + CSRFトークン方式（Selenium不要）を実測で確認済み
- 外部郵便番号データは全て利用不可（日本郵便404 / GitHubミラー破損 / zipcloud404）→ 不採用

## 実装（実行）
- 設定集約: `config.py`（ルート）+ `src/config.py`（再エクスポート・元号変換）
- HTTPSession: `src/http_client.py`（SiteSession: トークン・フォーム・リトライ・市区町村JSON）
- 抽出: `src/parser.py`（parse_results・extract_total・is_over_limit・詳細URL組立）
- 取得制御: `src/paginator.py`（ページ送り・日付分割・重複防止・連続エラー安全停止）
- 保存: `src/database.py`（SQLite UPSERT / fetch_logs / failed_records / run_logs）
- 再開: `src/state.py`（JSON ページ粒度resume）
- ログ: `src/logger.py`（ファイル+コンソール・開始/ページ/失敗/サマリー）
- エントリ: `src/main.py`（main / validate_input / execute_logic / format_output）
- テスト: `tests/`（parser / config / state / database / paginator の28件）
- 規約ファイル: `AGENTS.md` / `requirements.txt` / `.gitignore` / `pyproject.toml`(lint)
- 学習用ドキュメント: `docs/beginner_overview.md`（初心者・中級者向け解説。丸付き数字不使用，行番号参照は現行コードに照合済み）
- 図: `diagrams/`（stockChecker と同形式の 6図 × .md/.mmd/.pdf）
  - 01_flowchart: 全体フロー / 02_sequence: モジュール間通信 / 03a: 主要クラス詳細(横A4)
  - 03b: モジュール構造 / 04_mindmap: 概念階層 / 05_state: クエリ状態遷移
  - 変換: `convert_to_pdf.ps1`（npx @mermaid-js/mermaid-cli）

## 結果
- **単体テスト: 27件すべて成功**（`python -m pytest tests -q`）
- **Lint: ruff エラー0**（`python -m ruff check src tests config.py`）
- **実サイト検証**: 千代田区 + 2016-02-01..02-07（法人番号指定年月日）で 56件を取得・DB登録し、
  再実行時に重複/再取得なし（resume 動作確認）。フリガナ・所在地・詳細URLの保存も確認
- 途中で発見・修正した不具合:
  - `parse_results` が soup を破壊（`kana_el.extract()`）→ 同一soupの再パースでフリガナ消失。
    非破壊の `_text_without()` へ変更し再解析テストを追加
  - `parse_era_options` の元号2桁年未対応（平成27→7）→ 正規表現で修正
  - `upsert_company` の detail_url 引数を渡さないと rec['detail_url'] が無視される点を修正
  - 2015-10に一括付番（2015-10-05）の集中があるため日付分割はそれが起きる範囲に要注意
  - **市区町村Ajaxの `?event=select` 欠落**（config.py の AJAX_URL が素の /index.html だった）→
    実サイトで「素はHTML・?event=select はJSON」を検証し、AJAX_PATH を `/index.html?event=select` に修正
  - **`--date-start` が実効していなかった**（validate_input で検証するのみで execute_logic が未参照）→
    PageFetcher に date_start を追加し、main.py から受け渡して日付分割の開始日に反映。回帰テスト追加

## 残タスク
- 東京全市区町村の本番実行（目安: 時間とネットワークを要するため分割して実施）
- 単日超過・スキップした検索区間の人手での再調査（`fetch_logs.status=over_limit_skipped`）
- （任意）市区町村指定コマンド / 期間限定コマンド（--date-end 追加）
- （任意）Web-API（アプリID取得後）への切替・主要3情報CSVとの結合

## 実行コマンド
```powershell
# 依存
pip install -r requirements.txt
# テスト / lint
python -m pytest tests -q
python -m ruff check src tests config.py
# 実行（東京都・全期間）
python -m src.main --prefectures 東京都
# 実行（開始日指定・resume）
python -m src.main --prefectures 東京都 --date-start 2015-10-01
```
実行成果物: `data/houjin.sqlite` / `logs/scraping_*.log` / `state/progress.json`

## 公開禁止情報
公開禁止情報は含まれていません

---

## 2026-10-26 紹介バナー反映

### 方針（計画）
- アーカイブ側にのみ存在する紹介バナーを正規プロジェクトへコピーし、READMEから表示する
- アーカイブは削除せず、正規プロジェクトへの反映を確認する

### 実装（実行）
- `houjin-bangou-scraper-main/charts/地域法人情報スクレイパーの全体像.png`を`charts/`へコピー
- README先頭に相対パスでバナーを追加
- ソースコード、DB、進捗データには変更なし

### 結果
- 正規プロジェクトのREADMEとGitHubへ画像を追加
- アーカイブフォルダは保持
- ドキュメント／画像のみの変更のため、pytest／ruffは未実行
- 公開禁止情報は含まれていません

### 残タスク
- なし

### 実行コマンド
```powershell
Copy-Item ..\houjin-bangou-scraper-main\charts\地域法人情報スクレイパーの全体像.png charts\
git add README.md docs/handover.md charts/地域法人情報スクレイパーの全体像.png
git commit -m "docs: add project banner"
git push origin main
```
