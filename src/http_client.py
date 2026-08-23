# -*- coding: utf-8 -*-
"""
# 目的 (Why)
国税庁法人番号公表サイトへの HTTP アクセスを一元化する。
CSRF トークンの取得・保持、POST 検索、市区町村一覧JSON、リトライ/指数バックオフ、
アクセス間隔の制御を担う。

# 実装する機能の仕様 (What)
- Session 作成（UA / ヘッダ）
- index.html からトークンとフォーム初期値を取得
- 検索結果ページPOST（トークン等を自動で引き継ぐ）
- 市区町村一覧 JSON AJAX
- リトライ + 指数バックオフ + トークン失効時の再取得
- アクセス間隔（ACCESS_INTERVAL + ジッター）

# 前提条件・依存関係 (Assumption / Dependencies)
- requests, beautifulsoup4, lxml
- config / src.config

# 入出力の定義 (I/O)
- input : 検索条件（overrides）
- output: BeautifulSoup（検索結果）/ フォーム辞書 / メタデータ

# 注意点 (Caution)
- 過剰アクセスを避けるため必ず sleep() を挟む
- 公開禁止情報をヘッダ・ログに含めない

# 今後の拡張ポイント (Future Work)
- セッション切断時の再接続
- プロキシ利用

# 変更履歴 (Change Log)
- 2026-08-20 初版作成
"""

from __future__ import annotations

import logging
import random
import sys
import time
from typing import Any

import requests
from bs4 import BeautifulSoup

from src import config

# ルートの config を利用するための sys.path は src/config.py が保証済み

_logger = logging.getLogger("houjin_scraper.http")
_logger.setLevel(logging.INFO)
if not _logger.handlers:
    _logger.addHandler(logging.StreamHandler(sys.stdout))


# BeautifulSoup で扱うときの parse エラー名（ログ・failed_records 用）
class SiteFetchError(RuntimeError):
    """サイト取得失敗（HTTPエラー/タイムアウト）を表す例外。"""


class ParseError(RuntimeError):
    """HTML パース（抽出）失敗を表す例外。"""


class SiteSession:
    """法人番号公表サイトへのアクセスを担うクラス。"""

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": config.USER_AGENT,
                "Accept-Language": "ja,en-US;q=0.8,en;q=0.5",
                "Origin": config.BASE_URL,
            }
        )
        self.index_soup: BeautifulSoup | None = None   # 最新のindex/結果ページsoup
        self.form: dict[str, str] = {}                 # 最新の送信フォーム
        self.token: str = ""
        self._now = time.monotonic  # 正確な間隔計測用

    # ------------------------------------------------------------------
    # フォーム構築
    # ------------------------------------------------------------------
    @staticmethod
    def build_form(soup: BeautifulSoup) -> dict[str, str]:
        """ページ内の appForm をブラウザ相当にシリアライズする。

        - disabled な入力は除外（ブラウザが送らないため）
        - radio / checkbox は checked のみを含める
        - select は selected の option 値

        Args:
            soup: 対象ページ

        Returns:
            フォーム辞書
        """
        form = soup.find("form", {"id": config.SELECTORS["form_id"]})
        if form is None:
            raise ParseError("appForm not found in page")
        data: dict[str, str] = {}
        for el in form.find_all(["input", "select"]):
            if el.has_attr("disabled") or not el.get("name"):
                continue
            name = str(el.get("name"))
            if el.name == "select":
                val = ""
                for opt in el.find_all("option"):
                    if opt.has_attr("selected"):
                        val = str(opt.get("value") or "")
                        break
                data[name] = val
                continue
            typ = str(el.get("type") or "text").lower()
            if typ in ("radio", "checkbox"):
                if el.has_attr("checked"):
                    data[name] = str(el.get("value") or "on")
            else:
                data[name] = str(el.get("value") or "")
        return data

    @staticmethod
    def build_json_payload(soup: BeautifulSoup) -> dict[str, Any]:
        """市区町村一覧 AJAX 用の JSON ペイロード（houzinKdCkbx を配列化）。"""
        form = soup.find("form", {"id": config.SELECTORS["form_id"]})
        param: dict[str, Any] = {}
        houzin_kd: list[str] = []
        for el in form.find_all(["input", "select"]):
            if el.has_attr("disabled") or not el.get("name"):
                continue
            name = str(el.get("name"))
            if name == "houzinKdCkbx":
                if el.name == "input" and el.has_attr("checked"):
                    houzin_kd.append(str(el.get("value") or ""))
                continue
            if el.name == "select":
                val = ""
                for opt in el.find_all("option"):
                    if opt.has_attr("selected"):
                        val = str(opt.get("value") or "")
                        break
                param[name] = val
                continue
            typ = str(el.get("type") or "text").lower()
            if typ in ("radio", "checkbox"):
                if el.has_attr("checked"):
                    param[name] = str(el.get("value") or "on")
            else:
                param[name] = str(el.get("value") or "")
        param["houzinKdCkbx"] = houzin_kd
        return param

    # ------------------------------------------------------------------
    # 取得メソッド（リトライ・バックオフつき）
    # ------------------------------------------------------------------
    def sleep(self) -> None:
        """アクセス間隔を挟む（ACCESS_INTERVAL + 乱数ジッター）。"""
        span = config.ACCESS_INTERVAL + random.uniform(0, config.JITTER)
        time.sleep(max(0.0, span))

    def _get(self, url: str, *, timeout: int | None = None, referer: str | None = None) -> requests.Response:
        headers = {}
        if referer:
            headers["Referer"] = referer
        last_err: Exception | None = None
        for attempt in range(config.MAX_RETRIES + 1):
            try:
                resp = self.session.get(url, headers=headers, timeout=timeout or config.REQUEST_TIMEOUT)
                if resp.status_code in config.RETRY_HTTP_STATUS:
                    raise SiteFetchError(f"HTTP {resp.status_code} on GET {url}")
                resp.raise_for_status()
                return resp
            except (requests.ConnectionError, requests.Timeout, requests.HTTPError,
                    requests.TooManyRedirects, SiteFetchError) as exc:
                last_err = exc
                wait = config.ACCESS_INTERVAL + config.BACKOFF_BASE ** attempt
                _logger.warning("GET retry %d for %s after %s: %s", attempt + 1, url, exc, wait)
                time.sleep(wait)
        raise SiteFetchError(f"GET failed for {url}: {last_err}")

    def _post(self, url: str, data: dict[str, str] | None = None, *,
              json_: Any = None, timeout: int | None = None,
              referer: str | None = None) -> requests.Response:
        headers = {}
        if referer:
            headers["Referer"] = referer
        last_err: Exception | None = None
        for attempt in range(config.MAX_RETRIES + 1):
            try:
                if json_ is not None:
                    resp = self.session.post(url, json=json_, headers=headers,
                                             timeout=timeout or config.REQUEST_TIMEOUT)
                else:
                    resp = self.session.post(url, data=data, headers=headers,
                                             timeout=timeout or config.REQUEST_TIMEOUT)
                if resp.status_code in config.RETRY_HTTP_STATUS:
                    raise SiteFetchError(f"HTTP {resp.status_code} on POST {url}")
                resp.raise_for_status()
                return resp
            except (requests.ConnectionError, requests.Timeout, requests.HTTPError,
                    requests.TooManyRedirects, SiteFetchError) as exc:
                last_err = exc
                wait = config.ACCESS_INTERVAL + config.BACKOFF_BASE ** attempt
                _logger.warning("POST retry %d for %s after %s: %s", attempt + 1, url, exc, wait)
                time.sleep(wait)
        raise SiteFetchError(f"POST failed for {url}: {last_err}")

    # ------------------------------------------------------------------
    # サイト操作
    # ------------------------------------------------------------------
    def fetch_index(self) -> BeautifulSoup:
        """トップページを取得し、トークンとフォーム初期値を保持する。"""
        resp = self._get(config.BASE_URL + "/", referer=config.BASE_URL + "/")
        soup = BeautifulSoup(resp.content, "lxml")
        self.index_soup = soup
        self.form = self.build_form(soup)
        self.token = self.form.get(config.SELECTORS["token_name"], "")
        if not self.token:
            raise ParseError("CSRF token not found on index page")
        return soup

    def get_prefecture_options(self) -> list[tuple[str, str]]:
        """都道府県セレクトの [(コード, 名称)] を返す（日本語タブ）。"""
        soup = self.index_soup or self.fetch_index()
        sel = soup.find("select", {"id": "addr_pref"})
        if sel is None:
            sel = soup.find("select", {"name": "prefectureLst"})
        if sel is None:
            raise ParseError("prefectureLst select not found")
        return [
            (str(o.get("value") or ""), str(o.get_text(" ", strip=True)))
            for o in sel.find_all("option")
            if o.get("value")
        ]

    def resolve_prefecture_code(self, name: str) -> str:
        """都道府県名からコードを引く。見つからなければ ValueError。"""
        for code, label in self.get_prefecture_options():
            if label == name:
                return code
        raise ValueError(f"未知の都道府県名: {name}")

    def fetch_municipalities(self, prefecture_code: str) -> list[tuple[str, str]]:
        """指定都道府県内の市区町村一覧を JSON AJAX で取得し [(コード, 名称)] を返す。"""
        soup = self.index_soup or self.fetch_index()
        payload = self.build_json_payload(soup)
        payload["prefectureLst"] = prefecture_code
        self.sleep()
        resp = self._post(config.AJAX_URL, json_=payload, referer=config.BASE_URL + "/")
        try:
            body = resp.json()
        except ValueError as exc:
            raise ParseError("city list endpoint did not return JSON") from exc
        items = body.get("cityInfLst") or []
        result: list[tuple[str, str]] = []
        for it in items:
            code = (it or {}).get("code")
            name = (it or {}).get("name")
            if code and name:
                result.append((str(code), str(name)))
        return result

    def get_era_options(self) -> dict[str, tuple[int, int]]:
        """法人番号指定年月日の年セレクト選択肢から元号変換テーブルを返す。"""
        soup = self.index_soup or self.fetch_index()
        sel = soup.find("select", {"name": "chgYmdEyFromLst"})
        if sel is None:
            raise ParseError("chgYmdEyFromLst select not found")
        options = [
            (str(o.get("value") or ""), str(o.get_text(" ", strip=True)))
            for o in sel.find_all("option")
            if o.get("value")
        ]
        from src.config import parse_era_options

        return parse_era_options(options)

    # ------------------------------------------------------------------
    # 検索実行
    # ------------------------------------------------------------------
    def post_search(self, overrides: dict[str, str], *, base_form: dict[str, str] | None = None,
                    referer: str | None = None) -> tuple[BeautifulSoup, dict[str, str]]:
        """検索/ページングPOSTを実行し (soup, 新しいフォーム) を返す。

        Args:
            overrides: フォームに合成する検索条件（例: cityLst=105, viewPageNo=2）
            base_form: ベースとするフォーム（Noneなら保持中のもの）
            referer: Referer（未指定なら検索URL）

        Returns:
            (結果BeautifulSoup, 次回送信用の新しいフォーム)

        Raises:
            SiteFetchError: HTTP系失敗
            ParseError: フォーム抽出失敗
        """
        form = dict(base_form if base_form is not None else self.form)
        form.update(overrides)
        # ページ遷移時も常に表示件数を統一
        form[config.SELECTORS["page_size_name"]] = str(config.PAGE_SIZE)
        if "viewPageNo" in overrides:
            form["searchFlg"] = "0"
        data = {k: v for k, v in form.items() if v != ""}

        self.sleep()
        resp = self._post(config.SEARCH_URL, data=data, referer=referer or config.SEARCH_URL)
        soup = BeautifulSoup(resp.content, "lxml")
        new_form = self.build_form(soup)
        # 応答フォームのトークンを保持（次のPOSTで使用）
        new_token = new_form.get(config.SELECTORS["token_name"], "")
        if new_token:
            self.form = new_form
            self.token = new_token
        else:
            # フォームが再表示でない場合は前回の状態を維持
            self.form = form
        return soup, self.form