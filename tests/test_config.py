# -*- coding: utf-8 -*-
"""設定値・日付変換ロジックのテスト。"""

from __future__ import annotations

import datetime as _dt

import pytest

from src.config import DateRange, default_end_date, era_code, parse_era_options


def test_era_code_without_table() -> None:
    assert era_code(2015) == "H27"
    assert era_code(2019, 3) == "H31"
    assert era_code(2019, 8) == "R1"
    assert era_code(2026, 1) == "R8"


def test_era_code_with_table_month_aware() -> None:
    table = {
        "H31": (2019, 1),
        "R1": (2019, 5),
    }
    assert era_code(2019, 3, table) == "H31"
    assert era_code(2019, 6, table) == "R1"


def test_parse_era_options() -> None:
    options = [("H27", "平成27年"), ("H31", "平成31年"), ("R1", "令和元年"), ("R8", "令和8年"), ("", "")]
    table = parse_era_options(options)
    assert table["H27"] == (2015, 1)
    assert table["H31"] == (2019, 1)
    assert table["R1"] == (2019, 5)
    assert table["R8"] == (2026, 1)


def test_parse_era_options_empty_raises() -> None:
    with pytest.raises(ValueError):
        parse_era_options([("", "年")])


def test_date_range_mid_and_days() -> None:
    r = DateRange(_dt.date(2015, 10, 1), _dt.date(2015, 10, 31))
    assert r.days == 31
    assert r.mid() == _dt.date(2015, 10, 16)
    left = DateRange(_dt.date(2015, 10, 1), r.mid())
    right = DateRange(r.mid() + _dt.timedelta(days=1), _dt.date(2015, 10, 31))
    assert left.days == 16
    assert right.days == 15


def test_date_range_invalid_raises() -> None:
    with pytest.raises(ValueError):
        DateRange(_dt.date(2020, 1, 2), _dt.date(2020, 1, 1))


def test_date_range_repr() -> None:
    r = DateRange(_dt.date(2015, 10, 1), _dt.date(2015, 10, 2))
    assert r.__repr__() == "2015-10-01..2015-10-02"


def test_default_end_date(monkeypatch) -> None:
    from src import config as sc
    monkeypatch.setattr(sc, "DATE_RANGE_END", "")
    assert default_end_date(_dt.date(2026, 8, 20)) == _dt.date(2026, 8, 20)
    monkeypatch.setattr(sc, "DATE_RANGE_END", "2025-12-31")
    assert default_end_date(_dt.date(2026, 8, 20)) == _dt.date(2025, 12, 31)