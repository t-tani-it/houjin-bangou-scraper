# -*- coding: utf-8 -*-
"""検索結果HTMLからの抽出ロジックのテスト。"""

from __future__ import annotations

from bs4 import BeautifulSoup

from src.parser import extract_total, is_over_limit, parse_results

RESULT_PAGE = """<!DOCTYPE html>
<html><body>
<form id="appForm" method="post">
  <input name="token" value="abc">
</form>
<p>検索結果：<span class="bold">全3,345件</span>　（1〜100件目を表示）</p>
<table class="fixed normal">
  <tr><th scope="row">5011101012345</th>
      <td><div class="furigana">ナンバーサンプル</div>ナンバーサンプル株式会社</td>
      <td>東京都千代田区丸の内1-1-1</td>
      <td>変更履歴情報等</td></tr>
  <tr><th scope="row">5011101012346</th>
      <td><div class="furigana">ギンコウ</div>サンプル銀行</td>
      <td>東京都中央区銀座2-2-2</td>
      <td></td></tr>
</table>
</body></html>
"""

OVER_LIMIT_PAGE = """<!DOCTYPE html>
<html><body>
<p>検索結果が103,251件でした。表示できる件数は50,000件を超えているため、結果を表示できません。</p>
</body></html>
"""

EMPTY_PAGE = """<!DOCTYPE html>
<html><body>
<p>検索結果が0件でした。</p>
</body></html>
"""


def test_extract_total_normal() -> None:
    soup = BeautifulSoup(RESULT_PAGE, "lxml")
    assert extract_total(soup) == 3345


def test_extract_total_over_limit_page() -> None:
    soup = BeautifulSoup(OVER_LIMIT_PAGE, "lxml")
    assert extract_total(soup) == 103251


def test_extract_total_none_when_missing() -> None:
    soup = BeautifulSoup("<html><body>nothing</body></html>", "lxml")
    assert extract_total(soup) is None


def test_is_over_limit_true() -> None:
    soup = BeautifulSoup(OVER_LIMIT_PAGE, "lxml")
    assert is_over_limit(soup) is True


def test_is_over_limit_false() -> None:
    soup = BeautifulSoup(EMPTY_PAGE, "lxml")
    assert is_over_limit(soup) is False


def test_parse_results_normal() -> None:
    soup = BeautifulSoup(RESULT_PAGE, "lxml")
    rows = parse_results(soup)
    assert len(rows) == 2
    first = rows[0]
    assert first["corporate_number"] == "5011101012345"
    assert first["name"] == "ナンバーサンプル株式会社"
    assert first["name_kana"] == "ナンバーサンプル"
    assert first["address"] == "東京都千代田区丸の内1-1-1"
    second = rows[1]
    # デフォルト化（フリガナを外して正式名称になること）
    assert second["name"] == "サンプル銀行"


def test_parse_results_empty_page() -> None:
    soup = BeautifulSoup(EMPTY_PAGE, "lxml")
    assert parse_results(soup) == []


def test_parse_results_reparse_idempotent() -> None:
    """同じ soup を再解析してもフリガナが欠落しないこと（ページ1の再利用用）。"""
    soup = BeautifulSoup(RESULT_PAGE, "lxml")
    first = parse_results(soup)
    second = parse_results(soup)
    assert second == first
    assert second[0]["name_kana"] == "ナンバーサンプル"
    assert second[0]["name"] == "ナンバーサンプル株式会社"