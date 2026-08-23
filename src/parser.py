# -*- coding: utf-8 -*-
"""
# 目的 (Why)
検索結果ページの HTML から安定して法人情報（法人番号・名称・フリガナ・所在地）を抽出する。
CSS セレクタは config.SELECTORS に集約し、サイト変更に備える。

# 実装する機能の仕様 (What)
- parse_results(): 結果テーブル → レコードリスト
- extract_total(): 総件数「全N件」「検索結果がN件でした」を抽出
- is_over_limit(): 表示上限(5万件)超過の判定
- results_table(): 結果テーブル存在の判定

# 前提条件・依存関係 (Assumption / Dependencies)
- beautifulsoup4 / lxml
- config.SELECTORS

# 入出力の定義 (I/O)
- input : BeautifulSoup (検索結果ページ)
- output: dict レコード / int / bool

# 注意点 (Caution)
- 行のget_textは空白1つに正規化する
- フリガナは div.furigana、法人番号は th[scope=row]

# 今後の拡張ポイント (Future Work)
- 外字・英語表記の別カラム対応

# 変更履歴 (Change Log)
- 2026-08-20 初版作成
"""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup, Tag

from src import config

# ---------------------------------------------------------------------------
# 設定値ブロック
# ---------------------------------------------------------------------------
_WS = re.compile(r"\s+")


def _clean(text: str) -> str:
    """連続空白を1つに正規化する。"""
    return _WS.sub(" ", text).strip()


def results_table(soup: BeautifulSoup) -> Tag | None:
    """結果テーブル（table.fixed[.normal]）があれば返す。"""
    return soup.find("table", {"class": re.compile(r"fixed")})


def extract_total(soup: BeautifulSoup) -> int | None:
    """検索結果の総件数を返す。見つからなければ None。

    「全100件」「検索結果が100件でした」の形式に加え、
    「全100件」のように数値にカンマや空白が含まれる場合も処理する。

    Args:
        soup: 検索結果ページ

    Returns:
        総件数 or None
    """
    text = _WS.sub("", soup.get_text())
    m = re.search(r"全([0-9,]+)件", text)
    if m:
        return int(m.group(1).replace(",", ""))
    m = re.search(r"検索結果が([0-9,]+)件", text)
    if m:
        return int(m.group(1).replace(",", ""))
    return None


def is_over_limit(soup: BeautifulSoup) -> bool:
    """1問合せの表示上限（50000件）を超過したことを判定する。"""
    text = _WS.sub("", soup.get_text())
    return "件を超えている" in text


def _text_without(node: Tag, excluded: Tag) -> str:
    """node のテキストから excluded サブツリーを除いて抽出する。

    extract() は呼ばないため、同じ soup を複数回パースしても結果が変わらない
    （ページ送りでページ1の soup を再利用する場合に必須）。
    """
    parts: list[str] = []
    for s in node.find_all(string=True):
        parent = s.parent
        while parent is not None and parent is not node:
            if parent is excluded:
                break
            parent = parent.parent
        else:
            parts.append(str(s))
    return _clean("".join(parts))


def parse_results(soup: BeautifulSoup) -> list[dict[str, str]]:
    """結果テーブルから法人レコードのリストを抽出する。

    行構造（実測確認済み）:
        <tr>
          <th scope="row">法人番号</th>
          <td><div class="furigana">...</div> 商号又は名称</td>
          <td>所在地</td>
          <td>変更履歴情報等(不要)</td>
        </tr>

    Args:
        soup: 検索結果ページ

    Returns:
        [{'corporate_number','name','name_kana','address'}, ...]

    Raises:
        ParseError: 結果テーブルが見つからない場合（呼び出し側でレコード0件と解釈）
    """
    table = results_table(soup)
    if table is None:
        # 結果なしまたはエラーページ。None テーブルは空リストで返す。
        return []
    records: list[dict[str, str]] = []
    for tr in table.find_all("tr"):
        th = tr.find("th", {"scope": "row"})
        if th is None:
            continue
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
        number = _clean(th.get_text(" ", strip=True))
        name_cell = tds[0]
        kana_el = name_cell.find("div", class_="furigana")
        kana = ""
        if kana_el is not None:
            kana = _clean(kana_el.get_text(" ", strip=True))
        name = _text_without(name_cell, kana_el) if kana_el is not None else _clean(name_cell.get_text(" ", strip=True))
        address = _clean(tds[1].get_text(" ", strip=True))
        if not name and not address and not number:
            continue
        records.append(
            {
                "corporate_number": number,
                "name": name,
                "name_kana": kana,
                "address": address,
            }
        )
    return records


def _anchor_of(corporate_number: str) -> str:
    """法人番号から詳細ページURLを組み立てる（サイトの詳細ページURL形式）。"""
    return f"{config.BASE_URL}/henkorireki-johoto.html?selHouzinNo={corporate_number}"


def enrich_with_detail(record: dict[str, Any]) -> dict[str, Any]:
    """レコードに詳細URLと取得日時を付与する。"""
    record = dict(record)
    record["detail_url"] = _anchor_of(record.get("corporate_number", ""))
    import datetime

    record["fetched_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    return record