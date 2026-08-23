# AGENTS.md - webScraping（法人番号公表サイト スクレイパ）プロジェクト

## 概要
国税庁法人番号公表サイト（https://www.houjin-bangou.nta.go.jp/）から、
対象都道府県の全市区町村に所在する法人の**公開基本3情報**（法人番号・名称・所在地）を
取得・編集・保存するスクレイパである。
表示上限（5万件）超過の自治体は「法人番号指定年月日」による再帰的日付2分割で完全取得する。

OpenCode はこの AGENTS.md を参照して作業を行う。

## 開発環境
- OS: Windows 前提（PowerShell）
- Python 3.10+（実測確認: 3.11.15, conda env `backtest`）
- サイトとは requests + BeautifulSoup(lxml) で通信する。Selenium 不要（実測確認済み）

## 対象サイトの実測仕様（コード反映済み・変更時に要確認）
- **POSTフォーム方式**（CSRFトークン + `appForm`）。GET の index でトークン取得 → `POST /kensaku-kekka.html`
- 市区町村一覧は `POST /index.html?event=select` へ **JSONボディ** `{...}` で取得（`cityInfLst`）
- 表示件数は `viewNumAnc`（100固定）、ページ送りは `viewPageNo`（必ず `searchFlg=0`）
- 総件数は「全N件」、上限超過は「検索結果がN件でした。…件を超えているため、結果を表示できません」
- 超過ページへ飛ぶと **1ページ目にフォールバック** する → 終了ページは「総件数/PAGE_SIZE」から算出
- 日付絞込: `_chgYmdShTargetCkbx=on` + `chgYmdShTargetCkbx=2` + 年(`H27`/`R8`形式)/月/日の From/To select
- 法人番号指定年月日が 2015-10-05（一括付番）に集中 → この日を含む範囲は超過になりやすい
- 詳細/履歴ページURL: `/henkorireki-johoto.html?selHouzinNo=<法人番号>`
- 結果行: `table.fixed[.normal]` → `th[scope=row]`=法人番号 / `td[0]`=名称（`div.furigana`=カナ）/ `td[1]`=所在地
- 外部郵便番号データ（日本郵便zip / GitHubミラー / zipcloud）は全て利用不可（404 or 破損）→ 不採用

## セキュリティ・公開禁止情報
以下の情報はコード・設定ファイル・ログ・コミットに絶対に含めないこと：
- ローカルパス（例: C:\Users\...）
- APIキー・トークン（サイトのCSRFトークンは実行時に動的取得するもの）
- GitHub 操作に必要な個人IDやトークン
- 認証情報全般

## GitHub 運用ルール
- GitHub を使用してバージョン管理を行う（リモート HTTPS方式 / 認証 gh CLI / リポジトリ public）
- 生成・修正時は公開禁止情報を含まないか必ずチェックする
- push 前: 問題があれば該当箇所を一覧表示して中断、なければ「公開禁止情報は含まれていません」と表示
- ブランチ: `main` を使用
- **分割コミット**: 骨格/ロジック/API/テストなどの論理区切りごとにコミットする
  - 参考: config → logger/state/database → http_client/parser → paginator/main → tests（実績）

### ハンドオーバー運用（フェーズ完了時・必須）
- フェーズ完了時に `docs/handover.md` を必ず更新する（方針 → 実装 → 結果 → 残タスク → 実行コマンド）

## 技術スタック
- Python 3.10+ / requests / BeautifulSoup4 / lxml / pytest
- 保存: SQLite（データ・失敗記録・実行ログ）
- 進捗: JSON（resume）

## ディレクトリ構造
- `config.py`         → 全設定値とCSSセレクタ集約
- `src/config.py`     → ルート設定の再エクスポート + 元号コード変換・DateRange
- `src/http_client.py`→ SiteSession（トークン・フォーム管理、リトライ/バックオフ、市区町村JSON）
- `src/parser.py`     → parse_results / extract_total / is_over_limit（抽出条件の安定化）
- `src/paginator.py`  → ページ送り・日付分割・重複防止・連続エラー安全停止
- `src/main.py`       → main() / validate_input() / execute_logic() / format_output()
- `src/database.py`   → SQLite UPSERT・失敗記録
- `src/state.py`      → JSON進捗（resume）
- `src/logger.py`     → ファイル+コンソールログ・サマリー
- `tests/`            → pytest
- `data/ logs/ state/`→ 実行時生成物（.gitignore 対象）

## コーディング規約（マスタ AGENTS.md に準拠）
1. ファイル先頭に目的/仕様/前提/入出力/注意/拡張/変更履歴をコメント記載（読み手が5秒で理解できる密度）
2. 設定値はコード冒頭にまとめ、config.py へ集約（ロジック内への直書き禁止）
3. 関数構成: main() / validate_input() / execute_logic() / format_output() を分離。
   関数は単一責務・副作用回避・型ヒント必須
4. コメントは「なぜ」、docstring は「何を」。PEP8 準拠
5. 例外処理は明示的に。1件・1ページの失敗では止めず失敗URLを記録して継続
6. `# -*- coding: utf-8 -*-` 宣言を維持すること（lint の UP009 は pyproject で除外）

## ビルド・実行・テスト
- 依存導入: `pip install -r requirements.txt`
- テスト: `python -m pytest tests -q`
- Lint: `python -m ruff check src tests config.py`
- 実行: `python -m src.main [--prefectures 東京都] [--date-start YYYY-MM-DD]`
- resume: 中断しても `state/progress.json` から再開できる（ページ粒度）

## 注意点
- アクセス間隔（ACCESS_INTERVAL ≥0.5s + ジッター）を守り、対象サーバに過剰負荷をかけない
- 日付分割の再帰は `MAX_DAYS_RECURSION_DEPTH` で安全弁
- 単日でも上限超過の自治体はスキップしログに残す（人手確認が必要）
- サイト仕様変更時は SELECTORS と各パーサを更新し、実測HTMLサンプルで再検証すること

## OpenCodeへの指示
- コード生成・修正はこの規約に従うこと
- 新規ファイルは適切なディレクトリに配置すること
- 修正時は差分を明確に示すこと