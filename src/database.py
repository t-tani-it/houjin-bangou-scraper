# -*- coding: utf-8 -*-
"""
# 目的 (Why)
取得した法人情報・ページ取得結果・失敗レコードを SQLite に登録する。
法人番号を主キーに UPSERT して重複を防ぎ、後から原因を調査できるよう
失敗情報（発生日時・URL・エラー内容）をテーブルに残す。

# 実装する機能の仕様 (What)
- companies(法人情報) / fetch_logs(ページ単位結果) / failed_records(失敗詳細) / run_logs(実行サマリー)
- upsert_company(): 法人番号で重複更新
- add_fetch_log() / add_failed_record(): 失敗・成功の記録
- begin/rollback/commit: ページ単位のトランザクション制御

# 前提条件・依存関係 (Assumption / Dependencies)
- 標準ライブラリ sqlite3
- config.DB_PATH

# 入出力の定義 (I/O)
- input : 各種登録データ
- output: SQLite DB ファイル / 登録レコード数

# 注意点 (Caution)
- 同時書き込みは想定しない（ロックを避けるため WAL を利用しない）
- couples と failed_records は分離して保存する

# 今後の拡張ポイント (Future Work)
- PostgreSQL への移行（SQLが直書きのため移植しやすい）
- 索引付与チューニング

# 変更履歴 (Change Log)
- 2026-08-20 初版作成
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from src import config

# ---------------------------------------------------------------------------
# 設定値ブロック
# ---------------------------------------------------------------------------
DB_SCHEMA_SQL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS companies (
        corporate_number TEXT PRIMARY KEY, -- 法人番号（13桁）
        name TEXT NOT NULL,                -- 商号又は名称
        name_kana TEXT,                    -- フリガナ（あれば）
        address TEXT,                      -- 本店又は主たる事務所の所在地
        prefecture TEXT,                   -- 都道府県名
        city TEXT,                         -- 市区町村名
        detail_url TEXT,                   -- 法人情報詳細ページURL
        fetched_at TEXT                    -- 取得日時
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fetch_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        query_key TEXT,                    -- 検索単位（都道府県/市区町村/日付範囲）
        page_no INTEGER,                   -- ページ番号
        url TEXT,                          -- アクセスURL
        status TEXT,                       -- success/empty/error/over_limit_split...
        error_type TEXT,                   -- エラー種別
        error_message TEXT,                -- エラー内容
        record_count INTEGER,              -- 成功件数
        occurred_at TEXT                   -- 発生日時
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS failed_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        corporate_number TEXT,             -- 法人番号（無ければNULL）
        url TEXT,                          -- 失敗したURL
        error_type TEXT,                   -- エラー種別
        error_message TEXT,                -- エラー内容
        occurred_at TEXT                   -- 発生日時
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at TEXT,                   -- 処理開始時刻
        ended_at TEXT,                     -- 処理終了時刻
        description TEXT,                  -- 対象説明
        total_pages INTEGER,               -- 総ページ数
        success_records INTEGER,           -- 成功レコード数
        failed_records INTEGER,            -- 失敗レコード数
        status TEXT                        -- ok/with_errors/aborted
    )
    """,
)


class Database:
    """SQLite への登録を担うクラス。"""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path: Path = Path(db_path) if db_path else config.DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn: sqlite3.Connection | None = None

    # -- 接続・スキーマ ---------------------------------------------------
    def connect(self) -> None:
        """DBを開き、スキーマを作成する。既存があれば再利用。"""
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.execute("PRAGMA busy_timeout = 30000")
        self._init_schema()

    def _init_schema(self) -> None:
        for stmt in DB_SCHEMA_SQL:
            assert self.conn is not None
            self.conn.execute(stmt)
        self.conn.commit()

    def close(self) -> None:
        if self.conn is not None:
            self.conn.commit()
            self.conn.close()
            self.conn = None

    # -- トランザクション -------------------------------------------------
    def begin(self) -> None:
        assert self.conn is not None
        self.conn.execute("BEGIN")

    def commit(self) -> None:
        assert self.conn is not None
        self.conn.commit()

    def rollback(self) -> None:
        assert self.conn is not None
        self.conn.rollback()

    # -- 法人情報の登録 ----------------------------------------------------
    def upsert_company(self, rec: dict[str, Any], detail_url: str = "") -> None:
        """法人1件を upsert する。法人番号が主キーのため重複は更新になる。

        Args:
            rec: {'corporate_number','name','name_kana','address','prefecture','city'}
            detail_url: 詳細ページURL（任意）
        """
        assert self.conn is not None
        self.conn.execute(
            """
            INSERT INTO companies
                (corporate_number, name, name_kana, address, prefecture, city, detail_url, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(corporate_number) DO UPDATE SET
                name=excluded.name,
                name_kana=excluded.name_kana,
                address=excluded.address,
                prefecture=excluded.prefecture,
                city=excluded.city,
                detail_url=excluded.detail_url,
                fetched_at=excluded.fetched_at
            """,
            (
                rec.get("corporate_number", ""),
                rec.get("name", ""),
                rec.get("name_kana"),
                rec.get("address"),
                rec.get("prefecture"),
                rec.get("city"),
                detail_url or rec.get("detail_url") or "",
                rec.get("fetched_at", ""),
            ),
        )

    # -- ログ系 ------------------------------------------------------------
    def add_fetch_log(self, *, query_key: str, page_no: int, url: str, status: str,
                      record_count: int | None = None, error_type: str = "",
                      error_message: str = "", occurred_at: str = "") -> None:
        """ページ単位の取得結果を記録する。"""
        assert self.conn is not None
        self.conn.execute(
            """
            INSERT INTO fetch_logs
                (query_key, page_no, url, status, error_type, error_message, record_count, occurred_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (query_key, page_no, url, status, error_type, error_message, record_count, occurred_at),
        )

    def add_failed_record(self, *, corporate_number: str = "", url: str = "",
                          error_type: str = "", error_message: str = "",
                          occurred_at: str = "") -> None:
        """レコード単位の失敗を記録し、処理を継続するための材料にする。"""
        assert self.conn is not None
        self.conn.execute(
            """
            INSERT INTO failed_records (corporate_number, url, error_type, error_message, occurred_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (corporate_number, url, error_type, error_message, occurred_at),
        )

    def add_run_log(self, *, started_at: str, ended_at: str, description: str,
                    total_pages: int, success_records: int, failed_records: int,
                    status: str) -> None:
        """実行サマリーを記録する。"""
        assert self.conn is not None
        self.conn.execute(
            """
            INSERT INTO run_logs
                (started_at, ended_at, description, total_pages, success_records, failed_records, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (started_at, ended_at, description, total_pages, success_records, failed_records, status),
        )

    # -- 集計 ----------------------------------------------------------------
    def count_companies(self) -> int:
        assert self.conn is not None
        cur = self.conn.execute("SELECT COUNT(*) FROM companies")
        return int(cur.fetchone()[0])

    def count_failed(self, started_at: str | None = None) -> int:
        assert self.conn is not None
        if started_at:
            cur = self.conn.execute(
                "SELECT COUNT(*) FROM failed_records WHERE occurred_at >= ?", (started_at,)
            )
        else:
            cur = self.conn.execute("SELECT COUNT(*) FROM failed_records")
        return int(cur.fetchone()[0])

    def failed_urls(self, started_at: str | None = None) -> list[str]:
        assert self.conn is not None
        if started_at:
            cur = self.conn.execute(
                "SELECT url FROM failed_records WHERE occurred_at >= ? ORDER BY id", (started_at,)
            )
        else:
            cur = self.conn.execute("SELECT url FROM failed_records ORDER BY id")
        return [str(r[0]) for r in cur.fetchall()]