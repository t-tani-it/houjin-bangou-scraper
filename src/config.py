# -*- coding: utf-8 -*-
"""
# 目的 (Why)
ルートの config.py を読み込み、実行時に使う派生情報（元号コード変換・日付計算）を提供する。
コード本体はここ（src/config.py）を import して参照する。

# 実装する機能の仕様 (What)
- ルート設定値の再エクスポート
- 法人番号指定年月日検索に使う元号コード（H27/R8 等）への変換
- 日付分割再帰のための日付補助関数

# 前提条件・依存関係 (Assumption / Dependencies)
- ルートの config.py が同じディレクトリ階層に存在すること
- 標準ライブラリのみ（datetime）

# 入出力の定義 (I/O)
- input : なし
- output: 定数, era_code(year, month_no), split_ratio など

# 注意点 (Caution)
- 元号変換は2026-08-20現在の制度に基づく。サイトの選択肢が変わった場合は
  load_era_options() で動的取得するのでそちらを優先する。

# 今後の拡張ポイント (Future Work)
- 海外在在外法人の取得対応（reefsTxtf）
- Web-API 方式への切替

# 変更履歴 (Change Log)
- 2026-08-20 初版作成
"""

from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

# ルートの config.py を import するため sys.path を補正
_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from config import (  # noqa: E402  (import順警告を抑制)
    ACCESS_INTERVAL,
    AJAX_PATH,
    AJAX_URL,
    ALLOWED_PAGE_SIZES,
    BACKOFF_BASE,
    BASE_URL,
    DATA_DIR,
    DATE_RANGE_END,
    DATE_RANGE_START,
    DB_PATH,
    INCLUDE_CLOSED,
    JITTER,
    LOG_DIR,
    MAX_CONSECUTIVE_ERRORS,
    MAX_DAYS_RECURSION_DEPTH,
    MAX_DISPLAY_LIMIT,
    MAX_PAGES_PER_QUERY,
    MAX_RETRIES,
    PAGE_SIZE,
    PROJECT_ROOT,
    REQUEST_TIMEOUT,
    RETRY_HTTP_STATUS,
    SEARCH_PATH,
    SEARCH_URL,
    SELECTORS,
    SLEEP_ON_FAILURE,
    STATE_DIR,
    STATE_PATH,
    TARGET_HOUZIN_KIND,
    TARGET_PREFECTURES,
    USER_AGENT,
    ensure_dirs,
)

__all__ = [
    "ACCESS_INTERVAL",
    "AJAX_PATH",
    "AJAX_URL",
    "ALLOWED_PAGE_SIZES",
    "BACKOFF_BASE",
    "BASE_URL",
    "DATA_DIR",
    "DATE_RANGE_END",
    "DATE_RANGE_START",
    "DB_PATH",
    "INCLUDE_CLOSED",
    "JITTER",
    "LOG_DIR",
    "MAX_CONSECUTIVE_ERRORS",
    "MAX_DAYS_RECURSION_DEPTH",
    "MAX_DISPLAY_LIMIT",
    "MAX_PAGES_PER_QUERY",
    "MAX_RETRIES",
    "PAGE_SIZE",
    "PROJECT_ROOT",
    "REQUEST_TIMEOUT",
    "RETRY_HTTP_STATUS",
    "SEARCH_PATH",
    "SEARCH_URL",
    "SELECTORS",
    "SLEEP_ON_FAILURE",
    "STATE_DIR",
    "STATE_PATH",
    "TARGET_HOUZIN_KIND",
    "TARGET_PREFECTURES",
    "USER_AGENT",
    "DateRange",
    "ensure_dirs",
    "era_code",
    "parse_era_options",
]


class DateRange:
    """取得対象期間（法人番号指定年月日）を表す不変値オブジェクト。"""

    __slots__ = ("end", "start")

    def __init__(self, start: _dt.date, end: _dt.date) -> None:
        if start > end:
            raise ValueError(f"start({start}) must be <= end({end})")
        self.start = start
        self.end = end

    def mid(self) -> _dt.date:
        """範囲を2分割する中間日（開始寄り）。"""
        delta = (self.end - self.start).days
        return self.start + _dt.timedelta(days=delta // 2)

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, DateRange)
            and self.start == other.start
            and self.end == other.end
        )

    def __hash__(self) -> int:
        return hash((self.start, self.end))

    def __repr__(self) -> str:  # ログ出力に使う
        return f"{self.start.isoformat()}..{self.end.isoformat()}"


def parse_era_options(options: list[tuple[str, str]]) -> dict[str, tuple[int, int]]:
    """サイトの元号セレクト選択肢(値, 表示名)から {値: (西暦年, 開始月)} を組む。

    例: ("R8","令和8年") -> {"R8": (2026, 1)} ("H27","平成27年") -> {"H27": (2015, 1)}

    Args:
        options: [("R8", "令和8年"), ...] の形式の選択肢リスト

    Returns:
        {値: (西暦年, その元号での年初の月)} 形式の辞書

    Raises:
        ValueError: 選択肢が空の場合
    """
    import re

    table: dict[str, tuple[int, int]] = {}
    for value, label in options:
        if not label:
            continue
        # 「平成27年」「令和元年」「令和8年」→ 元号と年数（2桁対応）
        m = re.match(r"(.+?)(?:(\d+)|元)$", label.replace("年", ""))
        if not m:
            continue
        era, num_str = m.group(1), m.group(2)
        if era == "令和":
            start_year = 2019
            start_month = 5
        elif era == "平成":
            start_year = 1989
            start_month = 1
        else:
            continue
        num = 1 if not num_str else int(num_str)
        year = start_year + num - 1
        # 令和元年のみ5月始まり（2019-05-01）。2年以降は1月始まり。
        start_month = 5 if (era == "令和" and num == 1) else 1
        table[value] = (year, start_month)
    if not table:
        raise ValueError("era options are empty")
    return table


def era_code(year: int, month: int = 1, table: dict[str, tuple[int, int]] | None = None) -> str:
    """西暦年月→サイトの元号コード（例: 2015->"H27", 2026->"R8"）を返す。

    Args:
        year: 西暦年 (2015-2026 を想定)
        month: 月（2019年の5月以降は令和と判定する）
        table: サイトの選択肢から作った変換表 {値: (年, 開始月)}

    Returns:
        元号コード文字列

    Raises:
        ValueError: 変換できない年の場合
    """
    if table:
        # テーブルから対象年月を包括する元号コードを探す（year一致 + start_month <= month の内で最新）
        best: str | None = None
        best_sm = -1
        for value, (y, sm) in table.items():
            if y == year and sm <= month and sm > best_sm:
                best, best_sm = value, sm
        if best is not None:
            return best
    if year >= 2019 and not (year == 2019 and month < 5):
        # 令和 n 年 = 2019 + n - 1  ※2019年5月以降
        return f"R{year - 2019 + 1}"
    if 1989 <= year <= 2019:
        # 平成 n 年 = 1989 + n - 1
        return f"H{year - 1989 + 1}"
    raise ValueError(f"cannot convert year to era code: {year}")


def default_end_date(today: _dt.date) -> _dt.date:
    """終端日付の既定値（設定が空なら今日、設定があればそれを解析）。"""
    if DATE_RANGE_END:
        return _dt.date.fromisoformat(DATE_RANGE_END)
    return today