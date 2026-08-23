# -*- coding: utf-8 -*-
"""SQLite 登録（UPSERT・失敗記録・集計）のテスト。"""

from __future__ import annotations

from pathlib import Path

from src.database import Database


def make_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "data" / "test.sqlite")
    db.connect()
    return db


def test_upsert_dedupe(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    rec1 = {
        "corporate_number": "5011101012345",
        "name": "株式会社A",
        "name_kana": "カブシキガイシャエー",
        "address": "東京都千代田区丸の内1-1-1",
        "prefecture": "東京都",
        "city": "千代田区",
        "detail_url": "https://example.com/1",
        "fetched_at": "2026-08-20T00:00:00",
    }
    db.upsert_company(rec1)
    db.commit()
    assert db.count_companies() == 1
    # 同じ法人番号で再登録 → 重複せず更新
    rec1["name"] = "株式会社A改"
    db.upsert_company(rec1)
    db.commit()
    assert db.count_companies() == 1
    cur = db.conn.execute("SELECT name FROM companies WHERE corporate_number='5011101012345'")
    assert cur.fetchone()[0] == "株式会社A改"
    db.close()


def test_fetch_logs_and_failed(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    db.add_fetch_log(query_key="P13/C101", page_no=1, url="https://x/", status="success",
                     record_count=100, occurred_at="2026-08-20 00:00:00")
    db.add_failed_record(corporate_number="999", url="https://x/",
                         error_type="ValueError", error_message="bad", occurred_at="2026-08-20 00:00:00")
    db.add_failed_record(corporate_number="999", url="https://x/",
                         error_type="ValueError", error_message="bad2", occurred_at="2026-08-20 00:00:00")
    db.commit()
    assert db.count_failed() == 2
    urls = db.failed_urls()
    assert urls == ["https://x/", "https://x/"]
    cur = db.conn.execute("SELECT COUNT(*) FROM fetch_logs")
    assert cur.fetchone()[0] == 1
    db.close()


def test_run_logs(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    db.add_run_log(started_at="t0", ended_at="t1", description="東京都",
                   total_pages=3, success_records=250, failed_records=0, status="ok")
    db.commit()
    cur = db.conn.execute("SELECT status FROM run_logs")
    assert cur.fetchone()[0] == "ok"
    db.close()