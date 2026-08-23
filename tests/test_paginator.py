# -*- coding: utf-8 -*-
"""ページ遷移・日付分割（PageFetcher）のテスト。ネットワークなしで動作検証する。"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from bs4 import BeautifulSoup

from src import config
from src.config import DateRange
from src.database import Database
from src.logger import RecordLogger
from src.paginator import PageFetcher
from src.state import ProgressState


def row_html(num: str, name: str) -> str:
    return (
        "<tr><th scope='row'>" + num + "</th>"
        "<td><div class='furigana'>カナ</div>" + name + "</td>"
        "<td>東京都千代田区丸の内1-1-1</td><td></td></tr>"
    )


def page_html(rows: str, total: int | None = None) -> str:
    total_txt = f"<p>検索結果：全{total}件（1〜100件目を表示）</p>" if total is not None else ""
    return (
        "<html><body>"
        + total_txt
        + "<table class='fixed normal'>" + rows + "</table>"
        + "</body></html>"
    )


class FakeSite:
    """post_search を HTML で応答するモック。呼び出し元はページ番号を指定する。"""

    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.calls: list[dict[str, str]] = []

    def post_search(self, overrides: dict[str, str], *, base_form: dict[str, str] | None = None,
                    referer: str | None = None) -> tuple[BeautifulSoup, dict[str, str]]:
        self.calls.append(overrides)
        key = overrides.get("viewPageNo", "1")
        html = self.pages.get(key, self.pages["1"])
        return BeautifulSoup(html, "lxml"), {"token": "abc"}


AREA: dict[str, str] = {
    "pref_code": "13",
    "pref_name": "東京都",
    "city_code": "101",
    "city_name": "千代田区",
}


def make_fetcher(tmp_path: Path, site: FakeSite, monkeypatch) -> PageFetcher:
    monkeypatch.setattr(config, "PAGE_SIZE", 2)
    db = Database(tmp_path / "db.sqlite")
    db.connect()
    logger = RecordLogger(log_dir=tmp_path / "logs", level=__import__("logging").ERROR)
    state = ProgressState(tmp_path / "state" / "progress.json")
    return PageFetcher(site, db, logger, state)  # type: ignore[arg-type]


def test_make_query_key() -> None:
    assert PageFetcher.make_query_key(AREA) == "P13/C101"
    assert PageFetcher.make_query_key(AREA, DateRange(_dt.date(2015, 10, 1), _dt.date(2015, 10, 2))) == (
        "P13/C101/2015-10-01..2015-10-02"
    )
    assert PageFetcher.make_query_key({**AREA, "city_code": ""}) == "P13/C-"


def test_fetch_all_pages_two_pages(tmp_path: Path, monkeypatch) -> None:
    site = FakeSite({
        "1": page_html(row_html("100", "株式会社A") + row_html("200", "株式会社B"), total=4),
        "2": page_html(row_html("300", "株式会社C") + row_html("400", "株式会社D")),
    })
    fetcher = make_fetcher(tmp_path, site, monkeypatch)
    fetcher.fetch_all_pages(AREA, total=4)

    db = fetcher.db
    assert db.count_companies() == 4
    assert fetcher.total_pages == 2
    assert fetcher.success_records == 4
    key = "P13/C101"
    assert fetcher.state.is_query_done(key)
    assert fetcher.state.is_completed(key, 1)
    assert fetcher.state.is_completed(key, 2)
    # 2回目は重複登録にならない（ページ番号が完了済みのためスキップ）
    site.pages = {}
    fetcher2 = make_fetcher(tmp_path, site, monkeypatch)
    fetcher2.fetch_all_pages(AREA, total=4)
    assert fetcher2.db.count_companies() == 4
    fetcher.db.close()


def test_collect_range_split(tmp_path: Path, monkeypatch) -> None:
    over_page = (
        "<html><body><p>検索結果が60,000件でした。表示できる件数は50,000件を超えているため、結果を表示できません。</p>"
        "</body></html>"
    )
    left_rows = row_html("111", "株式会社L") + row_html("222", "株式会社L2") + row_html("333", "株式会社L3")
    right_page = "<html><body><p>検索結果が0件でした。</p></body></html>"

    def dispatch(overrides: dict[str, str]) -> str:
        if overrides.get("chgYmdDFromLst") == "1" and overrides.get("chgYmdDToLst") == "2":
            return over_page
        if overrides.get("chgYmdDToLst") == "1":
            return page_html(left_rows, total=3)
        return right_page  # 2日目は0件

    class FakeSplitSite(FakeSite):
        def post_search(self, overrides, *, base_form=None, referer=None):
            self.calls.append(overrides)
            return BeautifulSoup(dispatch(overrides), "lxml"), {"token": "abc"}

    site = FakeSplitSite({})
    fetcher = make_fetcher(tmp_path, site, monkeypatch)
    rng = DateRange(_dt.date(2015, 10, 1), _dt.date(2015, 10, 2))
    fetcher.collect_range(AREA, rng)

    assert fetcher.db.count_companies() == 3
    assert fetcher.state.is_query_done("P13/C101/2015-10-01..2015-10-01")
    assert fetcher.state.is_query_done("P13/C101/2015-10-02..2015-10-02")
    fetcher.db.close()


def test_collect_range_single_day_over_skips(tmp_path: Path, monkeypatch) -> None:
    over_page = (
        "<html><body><p>検索結果が60,000件でした。表示できる件数は50,000件を超えているため、結果を表示できません。</p>"
        "</body></html>"
    )

    class AlwaysOverSite(FakeSite):
        def post_search(self, overrides, *, base_form=None, referer=None):
            self.calls.append(overrides)
            return BeautifulSoup(over_page, "lxml"), {"token": "abc"}

    site = AlwaysOverSite({})
    fetcher = make_fetcher(tmp_path, site, monkeypatch)
    rng = DateRange(_dt.date(2015, 10, 1), _dt.date(2015, 10, 1))  # 単日
    fetcher.collect_range(AREA, rng)

    key = "P13/C101/2015-10-01..2015-10-01"
    assert fetcher.state.is_skipped(key)
    assert key in fetcher.over_limit_skipped
    fetcher.db.close()


def test_process_area_date_start_is_honored(tmp_path: Path, monkeypatch) -> None:
    """--date-start（date_start）が日付分割の開始日に反映されることの検証。

    初期クエリは超过を返し、日付付きクエリは上限内（1件）を返す。
    DATE_RANGE_END を2026-08-20に固定し、start=2016-02-01 が使われることを確認する。
    """
    from src import config as sc

    monkeypatch.setattr(sc, "DATE_RANGE_END", "2026-08-20")
    over_page = (
        "<html><body><p>検索結果が60,000件でした。表示できる件数は50,000件を超えているため、結果を表示できません。</p>"
        "</body></html>"
    )
    small_page = page_html(row_html("111", "株式会社L"), total=1)

    def dispatch(overrides: dict[str, str]) -> str:
        # 日付フィルタ付きクエリは上限内（1件）を返し、それ以外（初期）は超過を返す
        if "chgYmdDFromLst" in overrides:
            return small_page
        return over_page

    class DateStartSite(FakeSite):
        def post_search(self, overrides, *, base_form=None, referer=None):
            self.calls.append(overrides)
            return BeautifulSoup(dispatch(overrides), "lxml"), {"token": "abc"}

    site = DateStartSite({})
    fetcher = make_fetcher(tmp_path, site, monkeypatch)
    fetcher.date_start = _dt.date(2016, 2, 1)  # type: ignore[attr-defined]
    fetcher.process_area(AREA)

    # 开始日 2016-02-01 が反映された範囲キーが完了扱いになっている
    assert fetcher.state.is_query_done("P13/C101/2016-02-01..2026-08-20")
    fetcher.db.close()