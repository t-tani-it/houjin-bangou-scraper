# -*- coding: utf-8 -*-
"""
# 目的 (Why)
検索クエリ単位の取得制御を担う。ページ送り、総件数からの終了ページ算出、
表示上限超過時の「法人番号指定年月日」による再帰的な日付分割と、
重複ページ・無限ループの防止を実装する。

# 実装する機能の仕様 (What)
- fetch_all_pages(): 1クエリ（市区町村 or 日付範囲）の全ページを取得・登録
- process_area(): 市区町村単位の取得。超過時は collect_range() で日付分割
- collect_range(): 日付範囲を二分し、各範囲 ≤ 表示上限なら全ページ取得
- ページごとの feat成功・失敗を fetch_logs に記録
- レコード単位の失敗は failed_records に記録して継続
- 連続エラー閾値を超えたら安全停止（raise）

# 前提条件・依存関係 (Assumption / Dependencies)
- src.http_client.SiteSession / src.database.Database / src.parser / src.state / src.config

# 入出力の定義 (I/O)
- input : 都道府県・市区町村・日付範囲
- output: companies / fetch_logs / failed_records への登録、進捗stateの保存

# 注意点 (Caution)
- 表示上限超過ページは「ページ1の複製を返す」ため、必ず総件数から終了ページを算出する
- 1日あたり超過が発生しないよう分割するが、安全弁として単日超過はスキップしてログに残す

# 今後の拡張ポイント (Future Work)
- 市区町村粒度での部分再取得
- 並列化（スレッドプール）による高速化

# 変更履歴 (Change Log)
- 2026-08-20 初版作成
"""

from __future__ import annotations

import datetime as _dt
import math
from typing import Any

from src import config
from src.config import DateRange
from src.database import Database
from src.http_client import SiteSession
from src.logger import RecordLogger
from src.parser import extract_total, is_over_limit, parse_results
from src.state import ProgressState

# ---------------------------------------------------------------------------
# 設定値ブロック
# ---------------------------------------------------------------------------
DEFAULT_DATE_START = config.DATE_RANGE_START


class PageFetcher:
    """検索クエリ単位のページ取得と、日付分割による完全取得を担う。"""

    def __init__(
        self,
        site: SiteSession,
        db: Database,
        logger: RecordLogger,
        state: ProgressState,
        era_table: dict[str, tuple[int, int]] | None = None,
        date_start: _dt.date | None = None,
    ) -> None:
        self.site = site
        self.db = db
        self.log = logger
        self.state = state
        self.era_table = era_table or {}
        # 日付分割（法人番号指定年月日）の開始日。None なら config.DATE_RANGE_START を使う。
        self.date_start = date_start
        self.consecutive_errors = 0
        self.total_pages = 0
        self.success_records = 0
        self.failed_records_in_run = 0
        self.failed_urls_in_run: list[str] = []
        self.over_limit_skipped: list[str] = []

    # ------------------------------------------------------------------
    # クエリキー
    # ------------------------------------------------------------------
    @staticmethod
    def make_query_key(area: dict[str, str], date_range: DateRange | None = None) -> str:
        """検索単位を特定するキーを返す（例: P13/C101 または P13/C101/2015-10-01..2015-10-31）。"""
        key = f"P{area.get('pref_code', '')}/C{area.get('city_code', '') or '-'}"
        if date_range is not None:
            key += f"/{date_range.start}..{date_range.end}"
        return key

    # ------------------------------------------------------------------
    # 日付→フォーム込みのクエリ変換
    # ------------------------------------------------------------------
    def _date_overrides(self, date_range: DateRange) -> dict[str, str]:
        """DateRange → 法人番号指定年月日のPOST条件。"""
        from src.config import era_code

        ov: dict[str, str] = {
            "chgYmdShTargetCkbx": "2",
            "_chgYmdShTargetCkbx": "on",
            "chgYmdEyFromLst": era_code(date_range.start.year, date_range.start.month, self.era_table),
            "chgYmdEyToLst": era_code(date_range.end.year, date_range.end.month, self.era_table),
            "chgYmdMFromLst": str(date_range.start.month),
            "chgYmdMToLst": str(date_range.end.month),
            "chgYmdDFromLst": str(date_range.start.day),
            "chgYmdDToLst": str(date_range.end.day),
        }
        # 月・日はゼロ埋め不要（サイトのoption値がそのまま送られる）
        return ov

    def _area_overrides(self, area: dict[str, str], extra: dict[str, str] | None = None) -> dict[str, str]:
        ov: dict[str, str] = {"prefectureLst": area.get("pref_code", "")}
        if area.get("city_code"):
            ov["cityLst"] = area["city_code"]
        if extra:
            ov.update(extra)
        return ov

    # ------------------------------------------------------------------
    # クエリ実行と解析
    # ------------------------------------------------------------------
    def _query(
        self,
        overrides: dict[str, str],
        *,
        base_form: dict[str, str] | None = None,
        referer: str | None = None,
    ) -> tuple[Any, dict[str, str], dict[str, Any]]:
        """1回の検索POSTを実行し (soup, new_form, stats) を返す。

        stats = {'total': int|None, 'over': bool, 'rows': [...]}
        """
        soup, new_form = self.site.post_search(overrides, base_form=base_form, referer=referer)
        stats: dict[str, Any] = {
            "total": extract_total(soup),
            "over": is_over_limit(soup),
            "rows": parse_results(soup),
        }
        return soup, new_form, stats

    # ------------------------------------------------------------------
    # ページ送り
    # ------------------------------------------------------------------
    def fetch_all_pages(
        self,
        area: dict[str, str],
        date_range: DateRange | None = None,
        *,
        page1_soup: Any = None,
        page1_form: dict[str, str] | None = None,
        total: int | None = None,
    ) -> None:
        """1クエリ内の全ページを取得して DB へ登録する。

        表示上限超過は想定しない（超過は呼び出し側で日付分割済み）。
        総件数から終了ページ（total_pages）を事前に算出し、終了条件を保証する。

        Args:
            area: {'pref_code','pref_name','city_code','city_name'}
            date_range: 絞込期間（None=期間指定なし）
            page1_soup: 初回ページの soup（既に取得済みの場合）
            page1_form: 初回ページの送信用フォーム
            total: 総件数（既知の場合）
        """
        query_key = self.make_query_key(area, date_range)
        if self.state.is_skipped(query_key) or self.state.is_query_done(query_key):
            return

        overrides0 = self._area_overrides(area, self._date_overrides(date_range) if date_range else None)
        overrides0["viewPageNo"] = "1"

        soup = page1_soup
        form = page1_form
        if soup is None:
            soup, form, stats = self._query(overrides0)
            total = stats["total"] if total is None else total
            rows = stats["rows"]
        else:
            rows = parse_results(soup)

        if total is None:
            total = 0
        total_pages = math.ceil(total / config.PAGE_SIZE) if total > 0 else 0
        if total_pages == 0:
            self.state.mark_query_done(query_key)
            self.state.save()
            return
        if config.MAX_PAGES_PER_QUERY and total_pages > config.MAX_PAGES_PER_QUERY:
            self.log.warning(
                f"ページ数が上限超過によりクエリ中断 key={query_key} pages={total_pages} "
                f"上限={config.MAX_PAGES_PER_QUERY}"
            )
            self.state.mark_skipped(query_key)
            self.state.save()
            return

        current_rows = rows
        for page in range(1, total_pages + 1):
            if self.state.is_completed(query_key, page):
                continue
            if page > 1:
                soup, form, stats = self._query(
                    {"viewPageNo": str(page)},
                    base_form=form,
                    referer=config.SEARCH_URL,
                )
                current_rows = stats["rows"]
            try:
                self._store_page(area, query_key, page, current_rows)
                self.db.add_fetch_log(
                    query_key=query_key,
                    page_no=page,
                    url=config.SEARCH_URL,
                    status="success",
                    record_count=len(current_rows),
                    occurred_at=self.log.now(),
                )
                self.db.commit()
                self.log.log_page_success(query_key, page, len(current_rows))
                self.consecutive_errors = 0
                self.total_pages += 1
                self.state.mark_completed(query_key, page)
                self.state.save()
            except Exception as exc:  # noqa: BLE001 - 1ページ失敗で止めない
                self._record_page_failure(query_key, page, exc)
                self.consecutive_errors += 1
                self.db.add_fetch_log(
                    query_key=query_key,
                    page_no=page,
                    url=config.SEARCH_URL,
                    status="error",
                    error_type=exc.__class__.__name__,
                    error_message=str(exc),
                    occurred_at=self.log.now(),
                )
                self.db.commit()
                if self.consecutive_errors >= config.MAX_CONSECUTIVE_ERRORS:
                    raise RuntimeError(
                        f"連続エラーが{self.consecutive_errors}回で安全停止(key={query_key})"
                    ) from exc
                import time  # 失敗後の待機

                time.sleep(config.SLEEP_ON_FAILURE)
        # 全ページ完了
        self.state.mark_query_done(query_key)
        self.state.save()

    def _store_page(self, area: dict[str, str], query_key: str, page: int,
                    rows: list[dict[str, str]]) -> None:
        """1ページ分のレコードを個別UPSERTする。レコード単位の失敗は継続する。"""
        for raw in rows:
            try:
                self._upsert_one(area, raw)
                self.success_records += 1
            except Exception as exc:  # noqa: BLE001 - レコード単位の失敗
                self.db.add_failed_record(
                    corporate_number=raw.get("corporate_number", ""),
                    url=config.SEARCH_URL,
                    error_type=exc.__class__.__name__,
                    error_message=str(exc),
                    occurred_at=self.log.now(),
                )
                self.failed_records_in_run += 1
                self.failed_urls_in_run.append(config.SEARCH_URL)
                self.log.warning(
                    f"レコード登録失敗 key={query_key} page={page} 番号={raw.get('corporate_number')} "
                    f"エラー={exc.__class__.__name__}: {exc} 発生日時={self.log.now()}"
                )

    def _upsert_one(self, area: dict[str, str], raw: dict[str, str]) -> None:
        """1レコードへ都道府県・市区町村名を付与して DB へ upsert する。"""
        from src.parser import enrich_with_detail

        rec = dict(raw)
        rec["prefecture"] = area.get("pref_name", "")
        rec["city"] = area.get("city_name", "")
        rec = enrich_with_detail(rec)
        self.db.upsert_company(rec)

    def _record_page_failure(self, query_key: str, page: int, exc: Exception) -> None:
        self.log.log_page_failure(query_key, page, config.SEARCH_URL, f"{exc.__class__.__name__}: {exc}")

    # ------------------------------------------------------------------
    # 市区町村単位の取得（超過時は日付分割）
    # ------------------------------------------------------------------
    def process_area(self, area: dict[str, str]) -> None:
        """市区町村1件分を処理する。表示上限超過なら日付範囲を再帰的に分割する。"""
        from datetime import date

        from src.config import default_end_date

        qkey = self.make_query_key(area, None)
        if self.state.is_skipped(qkey) or self.state.is_query_done(qkey):
            return

        self.log.info(f"===== 市区町村処理開始: {area.get('pref_name')} {area.get('city_name')} ({area.get('city_code')}) =====")

        overrides0 = self._area_overrides(area)
        overrides0["viewPageNo"] = "1"
        try:
            soup, form, stats = self._query(overrides0)
        except Exception as exc:
            # 初期クエリ自体が失敗した場合も止めずに記録して継続
            self.log.error(f"初期クエリ失敗 {area} : {exc}")
            self.db.add_fetch_log(
                query_key=qkey, page_no=0, url=config.SEARCH_URL, status="error",
                error_type=exc.__class__.__name__, error_message=str(exc), occurred_at=self.log.now(),
            )
            self.state.mark_skipped(qkey)
            self.state.save()
            return

        if stats["over"]:
            count = stats["total"] or 0
            self.log.log_query_over_limit(qkey, count)
            self.db.add_fetch_log(
                query_key=qkey, page_no=0, url=config.SEARCH_URL, status="over_limit_split",
                record_count=count, error_message="表示上限超過により日付分割を実行",
                occurred_at=self.log.now(),
            )
            self.db.commit()
            start = self.date_start or date.fromisoformat(DEFAULT_DATE_START)
            end = default_end_date(date.today())
            self.collect_range(area, DateRange(start, end))
        else:
            total = stats["total"] if stats["total"] is not None else len(stats["rows"])
            if total == 0:
                self.state.mark_query_done(qkey)
                self.state.save()
                self.log.info(f"市区町村0件: {area.get('city_name')}")
                return
            self.fetch_all_pages(area, page1_soup=soup, page1_form=form, total=total)

    def collect_range(self, area: dict[str, str], date_range: DateRange, depth: int = 0) -> None:
        """日付範囲を二分しながら、表示上限内なら全ページ取得する再帰処理。

        Args:
            area: 市区町村情報
            date_range: 対象期間
            depth: 再帰深さ（安全弁）
        """
        if depth > config.MAX_DAYS_RECURSION_DEPTH:
            self._skip_oversize(area, date_range, "再帰深さ上限超過")
            return
        qkey = self.make_query_key(area, date_range)
        if self.state.is_skipped(qkey) or self.state.is_query_done(qkey):
            return

        overrides = self._area_overrides(area, self._date_overrides(date_range))
        overrides["viewPageNo"] = "1"
        try:
            soup, form, stats = self._query(overrides)
        except Exception as exc:
            self.log.error(f"日付分割クエリ失敗 {qkey}: {exc}")
            self.db.add_fetch_log(
                query_key=qkey, page_no=0, url=config.SEARCH_URL, status="error",
                error_type=exc.__class__.__name__, error_message=str(exc), occurred_at=self.log.now(),
            )
            return

        total = stats["total"] if stats["total"] is not None else len(stats["rows"])
        if total == 0:
            # データが無い範囲 → 分割を枝刈り
            self.state.mark_query_done(qkey)
            self.state.save()
            return

        if stats["over"] or total > config.MAX_DISPLAY_LIMIT:
            if date_range.days <= 1:
                # 単日でも超過 → 取得不可能なのでスキップして記録（要人手確認）
                self._skip_oversize(area, date_range, f"単日でも{total}件(上限超過)")
                return
            mid = date_range.mid()
            left = DateRange(date_range.start, mid)
            right = DateRange(mid + _ONE_DAY, date_range.end)
            self.collect_range(area, left, depth + 1)
            self.collect_range(area, right, depth + 1)
            return

        # 表示上限内 → 全ページ取得
        self.fetch_all_pages(area, date_range, page1_soup=soup, page1_form=form, total=total)

    def _skip_oversize(self, area: dict[str, str], date_range: DateRange, reason: str) -> None:
        qkey = self.make_query_key(area, date_range)
        self.log.warning(f"取得不可としてスキップ {qkey}: {reason}")
        self.db.add_fetch_log(
            query_key=qkey, page_no=0, url=config.SEARCH_URL, status="over_limit_skipped",
            error_message=reason, occurred_at=self.log.now(),
        )
        self.db.commit()
        self.state.mark_skipped(qkey)
        self.state.save()
        self.over_limit_skipped.append(qkey)


_ONE_DAY = _dt.timedelta(days=1)