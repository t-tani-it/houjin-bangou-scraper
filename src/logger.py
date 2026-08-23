# -*- coding: utf-8 -*-
"""
# 目的 (Why)
処理結果・エラーを後から調査できるよう、発生日時・URL・エラー内容・集計サマリーを
ファイルとコンソールへ出力するためのロギング設定。

# 実装する機能の仕様 (What)
- コンソール + ログファイル（logs/scraping_YYYYMMDD_HHMMSS.log）へ出力
- ログレコードに実行開始時刻を保持し、サマリー整形を提供

# 前提条件・依存関係 (Assumption / Dependencies)
- 標準ライブラリ logging のみ

# 入出力の定義 (I/O)
- input : LOG_DIR
- output: RecordLogger インスタンス / ログファイル

# 注意点 (Caution)
- 公開禁止情報（ローカルパス・APIキー）をログへ出力しないこと
- ログファイルは日付・時刻をファイル名に含めるため同時実行は別ファイルになる

# 今後の拡張ポイント (Future Work)
- ローテーション（RotatingFileHandler）対応

# 変更履歴 (Change Log)
- 2026-08-20 初版作成
"""

from __future__ import annotations

import datetime as _dt
import logging
import sys
from pathlib import Path

from src import config


class RecordLogger:
    """実行ごとのロガー。開始時刻と経過計測、結果サマリー出力を担う。"""

    def __init__(self, log_dir: Path | None = None, level: int = logging.INFO) -> None:
        self.started_at = _dt.datetime.now()
        log_dir = Path(log_dir) if log_dir else config.LOG_DIR
        log_dir.mkdir(parents=True, exist_ok=True)
        self._file = log_dir / f"scraping_{self.started_at:%Y%m%d_%H%M%S}.log"

        self.logger = logging.getLogger("houjin_scraper")
        self.logger.setLevel(level)
        self.logger.handlers.clear()
        self.logger.propagate = False

        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )

        fh = logging.FileHandler(self._file, encoding="utf-8")
        fh.setFormatter(fmt)
        fh.setLevel(level)
        self.logger.addHandler(fh)

        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        sh.setLevel(level)
        self.logger.addHandler(sh)

    # -- 基本ログメソッド -------------------------------------------------
    def debug(self, msg: str) -> None:
        self.logger.debug(msg)

    def info(self, msg: str) -> None:
        self.logger.info(msg)

    def warning(self, msg: str) -> None:
        self.logger.warning(msg)

    def error(self, msg: str) -> None:
        self.logger.error(msg, exc_info=False)

    def exception(self, msg: str) -> None:
        """例外情報つきで記録する（recursion 対策として exc_info=True を明示）。"""
        self.logger.exception(msg)

    # -- 運用メッセージ ---------------------------------------------------
    def log_start(self, description: str) -> None:
        """処理開始のサマリーを記録する。"""
        self.info(f"処理開始: 対象={description} 開始時刻={self.started_at:%Y-%m-%d %H:%M:%S}")

    def log_page_success(self, query_key: str, page: int, count: int) -> None:
        self.info(f"ページ取得成功 query={query_key} page={page} 件数={count}")

    def log_page_failure(self, query_key: str, page: int, url: str, err: str) -> None:
        self.error(
            f"ページ取得失敗 query={query_key} page={page} url={url} 発生日時={self.now()} エラー={err}"
        )

    def log_query_over_limit(self, query_key: str, count: int) -> None:
        self.warning(
            f"表示上限超過 query={query_key} 総件数={count}件 を日付分割して再取得します"
        )

    def log_summary(self, total_pages: int, success_records: int, failed_records: int,
                    failed_urls: list[str], over_limited: list[str]) -> None:
        """終了時サマリー（上ユーザー指定のログ形式）を記録する。"""
        ended = _dt.datetime.now()
        elapsed = (ended - self.started_at).total_seconds()
        self.info(
            f"処理完了: 開始={self.started_at:%Y-%m-%d %H:%M:%S} 終了={ended:%Y-%m-%d %H:%M:%S} "
            f"所要時間={elapsed:.1f}秒 総ページ={total_pages} "
            f"成功={success_records}件 失敗={failed_records}件"
        )
        self.info(f"失敗URL一覧({len(failed_urls)}件): {failed_urls}")
        if over_limited:
            self.warning(f"日付分割しても取得できずスキップした市区町村: {over_limited}")
        self.info(f"ログファイル: {self._file}")

    def log_run(self, level_name: str, msg: str) -> None:
        self.logger.log(getattr(logging, level_name, logging.INFO), msg)

    @staticmethod
    def now() -> str:
        """現在時刻の文字列（発生日時記録用）。"""
        return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")