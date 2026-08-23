# -*- coding: utf-8 -*-
"""
# 目的 (Why)
途中で処理が失敗しても最初からやり直さず再開できるよう、処理済みページ・
スキップした検索キーなどを JSON で永続化する。

# 実装する機能の仕様 (What)
- 完了ページ集合（query_key, page_no）を保存
- スキップ（超過・エラー）した query_key を保存
- 再実行時は load() で復元し、保存済みのものはスキップする

# 前提条件・依存関係 (Assumption / Dependencies)
- 標準ライブラリ json / pathlib のみ

# 入出力の定義 (I/O)
- input : STATE_PATH の JSON ファイル
- output: save()/load() による read/write

# 注意点 (Caution)
- ページ完了のたびに保存する（1ページ粒度のresume）
- 公開禁止情報は含めない

# 今後の拡張ポイント (Future Work)
- 実行日ごとの履歴管理
- 失敗URLの再利用キュー

# 変更履歴 (Change Log)
- 2026-08-20 初版作成
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from src import config


class ProgressState:
    """処理進捗の永続化 + スレッドセーフな更新管理。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path: Path = Path(path) if path else config.STATE_PATH
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {
            "completed": {},   # {query_key: [page_no, ...]}
            "skipped": [],     # [query_key, ...]
            "last_updated": "",
        }
        self.load()

    # -- アクセス ---------------------------------------------------------
    @property
    def data(self) -> dict[str, Any]:
        return self._data

    def is_completed(self, query_key: str, page_no: int) -> bool:
        return page_no in self._data["completed"].get(query_key, [])

    def mark_completed(self, query_key: str, page_no: int) -> None:
        pages = self._data["completed"].setdefault(query_key, [])
        if page_no not in pages:
            pages.append(page_no)

    def mark_query_done(self, query_key: str) -> None:
        """クエリ全体を完了扱いにする（分割不要な0件など）。"""
        self._data["completed"].setdefault(query_key, [])

    def is_query_done(self, query_key: str) -> bool:
        """クエリ全体が完了扱いかどうか（空の完了セットも完了とみなす）。"""
        return query_key in self._data["completed"]

    def is_skipped(self, query_key: str) -> bool:
        return query_key in self._data["skipped"]

    def mark_skipped(self, query_key: str) -> None:
        if query_key not in self._data["skipped"]:
            self._data["skipped"].append(query_key)

    # -- 永続化 -----------------------------------------------------------
    def save(self) -> None:
        self._data["last_updated"] = _now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self.path.open("w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            with self._lock, self.path.open("r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                completed = loaded.get("completed", {})
                if isinstance(completed, list):  # 旧形式互換
                    completed = {str(k): True for k in completed}
                self._data["completed"] = {
                    str(k): (list(v) if isinstance(v, list) else [])
                    for k, v in completed.items()
                }
                self._data["skipped"] = list(loaded.get("skipped", []))
                self._data["last_updated"] = loaded.get("last_updated", "")
        except (json.JSONDecodeError, OSError) as exc:
            # 破損時は空状態で開始（ログが無いため無視せず、後続で上書きされる）
            print(f"Warning: fail to load progress state ({exc}), start from empty.")


def _now() -> str:
    import datetime

    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")