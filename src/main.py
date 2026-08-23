# -*- coding: utf-8 -*-
"""
# 目的 (Why)
国税庁法人番号公表サイトから、対象都道府県の全市区町村の法人情報を
取得・編集・保存する3工程（fetch -> transform -> store）をつなぐエントリポイント。
表示上限5万件超過の自治体は「法人番号指定年月日」による日付分割で完全取得する。

# 実装する機能の仕様 (What)
- validate_input(): 設定値の検証（対象都道府県など）
- execute_logic():  市区町村列挙 → PageFetcher で全ページ取得 → DB保存
- format_output():  サマリー文字列の組み立て
- main():           全体の流れの管理（開始・実行・終了・異常終了）

# 前提条件・依存関係 (Assumption / Dependencies)
- src.config / src.http_client / src.database / src.parser / src.state / src.paginator
- config.TARGET_PREFECTURES に都道府県表示名を指定する

# 入出力の定義 (I/O)
- input : config の設定値 / コマンドライン引数（--prefectures, --date-start）
- output: DB（companies/fetch_logs/failed_records/run_logs）, ログ, 進捗state, サマリー印刷

# 注意点 (Caution)
- 公開禁止情報（ローカルパス・APIキー・個人ID）をコード・ログに含めないこと
- 途中終了も resume できるよう state は常に保存する

# 今後の拡張ポイント (Future Work)
- 市区町村の絞り込み取得（--cities）
- 期間指定（--date-start/--date-end）

# 変更履歴 (Change Log)
- 2026-08-20 初版作成
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from typing import Any

from src import config
from src.config import ensure_dirs as _ensure_dirs  # noqa: F401 再エクスポート用

# 実行する加工・保存ロジック
from src.database import Database
from src.http_client import SiteSession
from src.logger import RecordLogger
from src.paginator import PageFetcher
from src.state import ProgressState


def validate_input(args: argparse.Namespace) -> list[str]:
    """入力（設定値・コマンド引数）を検証し、対象都道府県名のリストを返す。

    Args:
        args: argparse.Namespace（--prefectures で上書き可）

    Returns:
        対象都道府県名リスト

    Raises:
        ValueError: 条件が不正な場合
    """
    prefs = args.prefectures or list(config.TARGET_PREFECTURES)
    if not prefs:
        raise ValueError("対象都道府県が指定されていません (config.TARGET_PREFECTURES か --prefectures を設定)")
    if args.date_start:
        from datetime import date

        parsed = date.fromisoformat(args.date_start)
        if parsed > date.today():
            raise ValueError(f"--date-start が未来の日付です: {args.date_start}")
    return prefs


def execute_logic(
    prefs: list[str],
    logger: RecordLogger,
    db: Database,
    state: ProgressState,
    args: argparse.Namespace,
) -> dict[str, Any]:
    """取得対象を列挙し、各区市町村を処理して DB へ保存する。

    Args:
        prefs: 対象都道府県名リスト
        logger: RecordLogger
        db: Database（接続済み）
        state: ProgressState
        args: argparse.Namespace

    Returns:
        サマリー用 dict（total_pages, success_records, failed_records, ...）
    """
    site = SiteSession()
    # トークン・初期フォーム取得
    site.fetch_index()
    era_table = site.get_era_options()
    # --date-start を日付分割（法人番号指定年月日）の開始日に反映する
    start_date: date | None = None
    if args.date_start:
        start_date = date.fromisoformat(args.date_start)
    fetcher = PageFetcher(site, db, logger, state, era_table=era_table, date_start=start_date)

    logger.info(f"都道府県ごとの市区町村一覧を取得します: {prefs}")
    for pref_name in prefs:
        pref_code = site.resolve_prefecture_code(pref_name)
        logger.info(f"都道府県: {pref_name} (コード {pref_code})")
        cities = site.fetch_municipalities(pref_code)
        logger.info(f"市区町村数: {len(cities)} 件")
        for city_code, city_name in cities:
            area: dict[str, str] = {
                "pref_code": pref_code,
                "pref_name": pref_name,
                "city_code": city_code,
                "city_name": city_name,
            }
            fetcher.process_area(area)

    summary: dict[str, Any] = {
        "prefectures": prefs,
        "total_pages": fetcher.total_pages,
        "success_records": fetcher.success_records,
        "failed_records": fetcher.failed_records_in_run,
        "failed_urls": list(dict.fromkeys(fetcher.failed_urls_in_run)),
        "over_limited": list(fetcher.over_limit_skipped),
    }
    return summary


def format_output(summary: dict[str, Any]) -> str:
    """実行結果のサマリー文字列を組み立てる。

    Args:
        summary: execute_logic() の戻り値

    Returns:
        整形済みサマリー文字列
    """
    lines = [
        "=== スクレイピング実行結果 ===",
        f"対象都道府県: {', '.join(summary['prefectures'])}",
        f"取得ページ: {summary['total_pages']} ページ",
        f"成功レコード: {summary['success_records']} 件",
        f"失敗レコード: {summary['failed_records']} 件",
        f"失敗URL: {summary['failed_urls']}",
    ]
    if summary["over_limited"]:
        lines.append(f"日付分割でも取得できなかった検索: {summary['over_limited']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """全体の流れを管理する（開始ログ → 実行 → 終了ログ → サマリー）。"""
    parser = argparse.ArgumentParser(description="法人番号公表サイト スクレイパ")
    parser.add_argument("--prefectures", action="append", help="対象都道府県名（繰り返し指定可）")
    parser.add_argument("--date-start", help="取得開始日（YYYY-MM-DD、既定はconfig.DATE_RANGE_START）")
    args = parser.parse_args(argv)

    started_at = RecordLogger.now()
    logger = RecordLogger()
    db = Database()
    state = ProgressState()
    status = "aborted"
    summary: dict[str, Any] = {}
    try:
        config.ensure_dirs()
        prefs = validate_input(args)
        logger.log_start(", ".join(prefs))
        db.connect()
        logger.info(f"DB: {db.db_path}")
        summary = execute_logic(prefs, logger, db, state, args)
        status = "with_errors" if summary["failed_records"] else "ok"
        out = format_output(summary)
        print("\n" + out)
        logger.log_summary(
            total_pages=summary["total_pages"],
            success_records=summary["success_records"],
            failed_records=summary["failed_records"],
            failed_urls=summary["failed_urls"],
            over_limited=summary["over_limited"],
        )
        return 0
    except Exception as exc:  # noqa: BLE001 - 入口で必ずサマリーを出す
        try:
            logger.exception(f"スクレイピング異常終了: {exc.__class__.__name__}: {exc}")
            print(f"異常終了: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        except Exception:  # noqa: BLE001 ログ作成失敗でもcontinue
            pass
        return 1
    finally:
        ended_at = RecordLogger.now()
        try:
            if status == "aborted":
                db.add_run_log(
                    started_at=started_at, ended_at=ended_at,
                    description="aborted", total_pages=0,
                    success_records=0, failed_records=0, status=status,
                )
            else:
                db.add_run_log(
                    started_at=started_at, ended_at=ended_at,
                    description=", ".join(summary.get("prefectures", ["?"])),
                    total_pages=summary.get("total_pages", 0),
                    success_records=summary.get("success_records", 0),
                    failed_records=summary.get("failed_records", 0),
                    status=status,
                )
            db.commit()
        except Exception:  # noqa: BLE001 DB未接続時は無視
            pass
        finally:
            try:
                db.close()
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    sys.exit(main())