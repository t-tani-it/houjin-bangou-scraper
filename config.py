# -*- coding: utf-8 -*-
"""
# 目的 (Why)
都市庁法人番号公表サイトから法人情報を取得するスクレイパの
設定値を集約するファイル。コードから値を直接参照せず、このモジュールを経由する。

# 実装する機能の仕様 (What)
- 対象サイトの URL・パス
- 取得対象（都道府県・市区町村・法人種別）
- アクセス間隔・タイムアウト・リトライ・バックオフ
- ページ表示件数・最大表示上限（5万件）
- 安全のための上限値（連続エラー・ページ数）
- DB / ログ / 進捗ファイルの保存先

# 前提条件・依存関係 (Assumption / Dependencies)
- Python 3.10+ / requests / beautifulsoup4 / lxml
- 公開禁止情報（ローカルパス・APIキー・個人ID）は含めない

# 入出力の定義 (I/O)
- input : なし（定数定義のみ）
- output: 各定数・設定値

# 注意点 (Caution)
- 対象サイトの仕様は実測で確認済み（2026-08）。サイト変更時は SELECTORS を更新すること。
- アクセス間隔は対象サーバへ過剰な負荷をかけないよう設定すること。

# 今後の拡張ポイント (Future Work)
- Web-API（アプリケーションID取得後）への切替
- 基本3情報ダウンロード（全件CSV）との突合作業

# 変更履歴 (Change Log)
- 2026-08-20 初版作成
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# 設定値ブロック（コード冒頭に集約。意味をコメントで説明）
# ---------------------------------------------------------------------------

# --- 対象サイト ---
BASE_URL: str = "https://www.houjin-bangou.nta.go.jp"
SEARCH_PATH: str = "/kensaku-kekka.html"      # 検索結果ページのPOST先
AJAX_PATH: str = "/index.html?event=select"   # 市区町村一覧JSON取得用（?event=select 必須を実測確認）
SEARCH_URL: str = BASE_URL + SEARCH_PATH
AJAX_URL: str = BASE_URL + AJAX_PATH

# ユーザエージェント（サイトの仕様に合わせてブラウザ相当を設定）
USER_AGENT: str = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# --- 取得対象 ---
# 対象都道府県名（表示名）。複数指定可。例: ["東京都"] / ["東京都", "大阪府"]
TARGET_PREFECTURES: list[str] = ["東京都"]

# 法人種別で絞り込む場合のみ指定（サイトのチェックボックス値: 1..8）。空なら全種別。
TARGET_HOUZIN_KIND: list[str] = []

# 検索対象外等のデフォルト（サイト初期値に従う）:
#   変更履歴を含める(historyCkbx) / 検索対象除外法人を除く(hideCkbx) / 閉鎖法人を含める(closeCkbx)
INCLUDE_CLOSED: bool = True      # 登記記録の閉鎖等が生じた法人を対象に含める

# --- リクエスト設定 ---
REQUEST_TIMEOUT: int = 60        # 秒
ACCESS_INTERVAL: float = 1.0     # アクセス間隔(秒)　※過剰負荷防止のため最小値は0.5以上を推奨
JITTER: float = 0.3              # アクセス間隔に加算するゆらぎ(秒)
MAX_RETRIES: int = 3             # 1リクエスト当たりのリトライ回数
BACKOFF_BASE: float = 2.0        # リトライ間隔の指数バックオフ基数(秒)
SLEEP_ON_FAILURE: float = 5.0    # ページ失敗時に次へ進む前の待機秒
RETRY_HTTP_STATUS: tuple[int, ...] = (429, 500, 502, 503, 504)

# --- ページング・上限 ---
PAGE_SIZE: int = 100             # 1ページの表示件数（サイトのviewNumAncは10/50/100）
MAX_DISPLAY_LIMIT: int = 50000   # サイトの1問合せ表示上限（超過は分割が必要）
MAX_PAGES_PER_QUERY: int = 0     # 1クエリの取得ページ上限(0=無制限)
MAX_CONSECUTIVE_ERRORS: int = 5  # 連続エラーがこの回数を超えたら安全停止
MAX_DAYS_RECURSION_DEPTH: int = 60  # 日付分割の再帰深さ上限(安全弁)

# --- 保存先（運用時に可変） ---
PROJECT_ROOT: Path = Path(__file__).resolve().parent
DATA_DIR: Path = Path(os.getenv("HOUJIN_DATA_DIR", PROJECT_ROOT / "data"))
LOG_DIR: Path = Path(os.getenv("HOUJIN_LOG_DIR", PROJECT_ROOT / "logs"))
STATE_DIR: Path = Path(os.getenv("HOUJIN_STATE_DIR", PROJECT_ROOT / "state"))
DB_PATH: Path = Path(os.getenv("HOUJIN_DB_PATH", DATA_DIR / "houjin.sqlite"))
STATE_PATH: Path = STATE_DIR / "progress.json"

# --- 日付分割（法人番号指定年月日）のデフォルト範囲 ---
DATE_RANGE_START: str = "2015-10-01"  # 法人番号付番制度の開始日
DATE_RANGE_END: str = ""              # 空なら実行日（今日）を終端とする

# --- CSS セレクタ（抽出条件の集約。サイト変更時にここだけ修正） ---
SELECTORS: dict[str, str] = {
    "token_name": "jp.go.nta.houjin_bangou.framework.web.common.CNSFWTokenProcessor.request.token",
    "form_id": "appForm",
    "prefecture_select": 'select[name="prefectureLst"]',
    "city_select": 'select[name="cityLst"]',
    "results_table": "table.fixed.normal",
    "row": "tr",
    "number_cell": "th",          # 法人番号セル
    "name_cell": "td",            # 商号又は名称セル（div.furiganaはカナ）
    "furigana_node": "div.furigana",
    "house_num_cell": "td",       # 所在地セル
    "page_size_name": "viewNumAnc",
    "page_no_name": "viewPageNo",
}

# ページ表示件数チェック用
ALLOWED_PAGE_SIZES: tuple[int, ...] = (10, 50, 100)


def ensure_dirs() -> None:
    """必要なディレクトリを作成する。"""
    for d in (DATA_DIR, LOG_DIR, STATE_DIR):
        d.mkdir(parents=True, exist_ok=True)