# -*- coding: utf-8 -*-
"""進捗state（JSON永続化・resume）のテスト。"""

from __future__ import annotations

from pathlib import Path

from src.state import ProgressState


def roundtrip(tmp_path: Path) -> ProgressState:
    path = tmp_path / "state" / "progress.json"
    return ProgressState(path)


def test_mark_and_check(tmp_path: Path) -> None:
    state = roundtrip(tmp_path)
    query = "P13/C101"
    assert not state.is_completed(query, 1)
    state.mark_completed(query, 1)
    assert state.is_completed(query, 1)
    assert not state.is_completed(query, 2)
    state.mark_query_done(query)
    assert state.is_query_done(query)


def test_save_load_resume(tmp_path: Path) -> None:
    path = tmp_path / "state" / "progress.json"
    s1 = ProgressState(path)
    s1.mark_completed("P13/C101", 1)
    s1.mark_completed("P13/C101", 2)
    s1.mark_skipped("P13/C102")
    s1.save()

    s2 = ProgressState(path)  # loadされる
    assert s2.is_completed("P13/C101", 1)
    assert s2.is_completed("P13/C101", 2)
    assert s2.is_query_done("P13/C101")
    assert s2.is_skipped("P13/C102")
    assert not s2.is_completed("P13/C101", 3)


def test_load_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "state" / "progress.json"
    path.parent.mkdir(parents=True)
    path.write_text("{broken json", encoding="utf-8")
    state = ProgressState(path)
    assert not state.is_completed("X", 1)  # 破損時は空から開始


def test_load_legacy_list_format(tmp_path: Path) -> None:
    path = tmp_path / "state" / "progress.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"completed": ["P13/C101"], "skipped": []}', encoding="utf-8")
    state = ProgressState(path)
    assert state.is_query_done("P13/C101")