# Understanding Guide — webScraping（法人番号公表サイト スクレイパ）理解・説明資料

この資料は、`webScraping`（国税庁法人番号公表サイトのスクレイパ）を**他人に説明できるレベルで理解する**ためのものです。
「どう調べたか → どう判断したか → どう作ったか」の流れを、非技術者にも技術者にも読める構成にしています。

- **非技術者向け**: 0章、各章の要点のまとめを読めば全体像をつかめます。
- **技術者向け**: 3章以降の判断根拠・設計意図が詳細です。

参照する行番号は、この資料を作成した時点のコードに照合済みです（`AGENTS.md` 記載の行番号とは同一ではありません）。
なお、本資料の作成過程で**2つのコード不具合**を実サイト検証により発見・修正しています（3.4章）。

---

## 0章 エグゼクティブサマリー

### 0.1 これは何をするシステムか

国税庁の法人番号公表サイト（`https://www.houjin-bangou.nta.go.jp/`）から、**指定した都道府県の全市区町村**に所在する法人の**基本3情報**（法人番号・名称・所在地）を自動で収集し、SQLite データベースに保存するプログラムです。

```
python -m src.main --prefectures 東京都
```

これを実行すると、東京都の市区町村を順に巡回し、その法人情報を `data/houjin.sqlite` にため込みます。

### 0.2 なぜ作られたか

- 特定エリアの企業リストを一括で取得したい
- 社名・所在地のデータベースを構築したい
- 市区町村ごとの法人分布を分析したい

サイトは誰でも検索できる公開情報なので、法律上・倫理上の壁は薄いですが、**大量アクセスはサーバに負荷をかけます**。そのため、常に1秒以上の間隔を挟み、破壊的な操作はしない設計になっています。

### 0.3 このシステムの難しいポイント（3つ）

1. **普通の「URLを見るだけ」の方法では動かない**
   検索が POST フォーム + CSRF トークン方式で、ブラウザが裏でやっている複雑な通信をコードで再現する必要があります。
2. **1回の検索で最大50,000件までしか表示されない**
   それを超える自治体（例：千代田区）は、取得期間を**自動で細かく分割**して完全取得します。
3. **失敗しても止まらない・途中から再開できる**
   1件・1ページの失敗で全体を止めず、失敗を記録して続行。途中でPCが落ちても `state/progress.json` から再開できます。

---

## 1章 スクレイピングの一般知識

### 1.1 スクレイピングとは

プログラムでWebページからデータを抜き出す技術です。人間がブラウザで見ている画面を、コードで同じように読み取ります。

**Web API との違い（重要）**:
- **Web API**: サーバが「機械が読みやすい形式（例：JSON）」でデータを返す。設計済みで安定。
- **スクレイピング**: HTMLという「人間の閲覧用」の文書を読み解く。サーバ都合でHTMLが変わると、そのまま壊れる。

このサイトは**公式API（アプリケーションID制）が未取得**だったため、スクレイピング方式を採用しました（`AGENTS.md` に「Web-APIへの切替」を残タスクとして記載）。

### 1.2 HTTP の基礎（このシステムに必要な範囲）

- **GET**: ページを「見る」リクエスト。URLに情報が付きます。このサイトではトップページ取得に使用。
- **POST**: フォームを「送る」リクエスト。検索条件をボディに載せます。このサイトの検索はすべて POST。
- **ヘッダ**: リクエストに付ける付加情報。代表的なのが `User-Agent`（身分を名乗る文字列）。ブラウザと同じUAを送ることで、サイト側を通常アクセスと認識させます。
- **Cookie / セッション**: 複数回の通信を「同じ利用者」として紐づける仕組み。`requests.Session` が Cookie を自動保存します。
- **ステータスコード**: `200`=成功、`404`=存在しない、`429`=アクセス過多、`500`系=サーバ内部エラー。

このシステムでは `requests.Session`（`src/http_client.py:69`）を1つ使い、ヘッダとCookieを維持しながら連続アクセスします。

### 1.3 HTML とパース、CSSセレクタ

HTMLは「タグ」で囲まれた入れ子構造（DOMツリー）です。特定の部分を指し示すのが**CSSセレクタ**です。

```html
<table class="fixed normal">   ← class="fixed" を持つ table
  <tr>                          ← 行
    <th scope="row">番号</th>    ← th（見出しセル）
    <td>名称</td>                ← td（データセル）
  </tr>
</table>
```

```python
table = soup.find("table", {"class": "fixed"})   # 結果テーブルを探す
rows = table.find_all("tr")                      # 行を列挙
```

このプロジェクトでは「セレクタを1箇所の設定ファイル（`config.py` の `SELECTORS`）に集める」ことで、サイトのHTML構造が変わったときに**その1箇所だけ直せば良い**ようにしています（設計意図は4章）。

### 1.4 動的ページと POST + CSRF トークン

- **静的ページ**: GET するだけで HTML が返る。スクレイピングが簡単。
- **動的ページ**: 検索ボタンを押すと現在の状態をPOSTし、新しい画面が返る。このサイトがこれに該当。
- **CSRFトークン**: フォームを偽装送信させないための「合い言葉」。検索画面にアクセスしたときにサーバから発行され、検索のたびに一緒に送り返す必要があります。トークンが無い・古いと結果が返りません。

このサイトのトークンは、フォーム内の `jp.go.nta.houjin_bangou.framework.web.common.CNSFWTokenProcessor.request.token` というフィールドに入っています（`config.py` の `SELECTORS["token_name"]`）。

### 1.5 合法性・倫理・負荷への配慮

スクレイピングは法律で全面禁止されているわけではありませんが、以下は守るべきです（このシステムも守っています）。

- **robots.txt の確認**: サイトがクロールを禁止していないか。形式に沿ったアクセスに留める。
- **User-Agent を偽らない**: ブラウザUAを送るのは、サイトが人間向け操作を前提としているため（なりすましではなく標準挙動）。
- **過剰アクセスをしない**: `ACCESS_INTERVAL=1.0` 秒 + 最大0.3秒のゆらぎ（`config.py:70-71`）を必ず挟みます（`sleep()` `src/http_client.py:158`）。
- **サーバから拒否されたら待つ/諦める**: 429・500系はリトライ＋指数バックオフ（`src/http_client.py:163`）。連続5回失敗したら安全停止（`src/paginator.py:239`）。

### 1.6 リトライ・冪等性・resume の概念

- **リトライ / 指数バックオフ**: 一時的なエラーは待ってやり直し、待ち時間を倍々にする（2^0, 2^1, ...）ことで圧迫を避ける。
- **冪等性（UPSERT）**: 何度実行しても結果が増えないこと。同じ法人番号を2回登録しても上書きのみ（`ON CONFLICT ... DO UPDATE`）。
- **resume（再開）**: 途中で失敗した場所を記録し、次回そこから続行する。このシステムは `state/progress.json` に「どこまで終えたか」を残します。

---

## 2章 対象サイトの調査手順

「コードを書く前に、まずサイトを観察する」ことが重要です。ここでは調査の順序を示します。ブラウザの開発者ツール（F12）を使うのが基本です。

### 2.1 ステップ1: サイト構造の把握

トップページ（`https://www.houjin-bangou.nta.go.jp/`）を開き、検索フォームを確認します。

- 都道府県の選択欄がある
- 都道府県を選ぶと、市区町村の選択欄が更新される（Ajax）
- 「検索」ボタンを押すと検索結果画面に遷移する

ここで「**トップページは GET で開けるが、検索は POST らしい**」と見当をつけます。

### 2.2 ステップ2: F12 の Network タブで「人の操作」を記録する

F12 → Network タブを開き、以下を順に行って通信内容を記録します。

1. トップページ（index）を GET
2. 都道府県を「東京都」に選択 → 市区町村一覧がAjaxで更新（ここで `/index.html?event=select` への POST が発生）
3. 市区町村を選んで「検索」 → 検索結果が返る（`/kensaku-kekka.html` への POST）
4. 「2ページ目」をクリック → ページ送り
5. （必要時）「法人番号指定年月日」で日付絞込

### 2.3 ステップ3: 各リクエストの差分を観察する

Network タブで各リクエストを開き、**リクエスト内容（形式・ボディ・ヘッダ）** と **レスポンス（形式・内容）** を比べます。得られた観察結果（実測）を表に示します。

| 通信 | 種別 | ボディ | レスポンス |
|------|------|--------|-----------|
| トップページ | GET | なし | HTML（検索フォーム兼 CSRF トークン入り） |
| 市区町村一覧 | POST `/index.html?event=select` | JSON（フォーム値 + `prefectureLst`） | JSON（`cityInfLst` に市区町村リスト） |
| 検索 | POST `/kensaku-kekka.html` | form-data（トークン + 検索条件） | HTML（結果テーブル + 次回用フォーム） |
| ページ送り | POST `/kensaku-kekka.html` | form-data（`viewPageNo`） | HTML |
| 日付絞込 | POST `/kensaku-kekka.html` | form-data（年月日の元号コード） | HTML |

ポイント: **検索結果ページにもフォームが含まれており、そのフォームが次回送信のベースになります**（選択中の都道府県・トークンを保持）。この「毎回応答からフォームを再構築する」仕様が、このシステムの重要な設計判断になります。

### 2.4 ステップ4: レスポンスから抽出対象を特定する

検索結果HTMLを詳しく見て、どこに何が入っているか確認します。

- **総件数**: ページ冒頭の「全N件」という文言（`src/parser.py:57` `extract_total`）。超過時は「検索結果がN件でした」。
- **1行の構造**: `<table class="fixed">` 内の `<tr>`。法人番号は `th[scope=row]`、名称は `td[0]`（`div.furigana` にカナ）、所在地は `td[1]`（`src/parser.py:103` `parse_results`）。
- **表示上限超過**: 本文に「表示できる件数は50,000件を超えているため、結果を表示できません」という文言（`src/parser.py:79` `is_over_limit`）。
- **フリガナ**: 名称セル内の `div.furigana`。名称はフリガナを除いた部分。

### 2.5 ステップ5: エッジケースを実測する

実際に試して「サイトの癖」を確認します。これが**このシステムの特殊な設計の根拠**になります。

- **存在しないページ番号を送る**: 全4件の検索に `viewPageNo=999` を送ると、**1ページ目がそのまま返る**（フォールバック）。→ `while` ループで「次ページがあるか」を判定すると無限ループするため、総件数から終了ページを先に確定し `for` で回す設計にした。
- **0件の検索**: 「検索結果が0件でした。」が返る。
- **単日でも上限超過**: 分割の最小単位（1日）でも50,000件を超える自治体がある。
- **2015-10-05 の集中**: 法人番号が一括付番された2015-10-05を含む期間は、異常に件数が多い。

### 2.6 ステップ6: 外部データ（郵便番号）の検証と不採用判断

住所の正規化に使えそうな外部の郵便番号データ（日本郵便のzipファイル / GitHubミラー / zipcloud API）を試しましたが、**全て利用不可**（404または破損）でした。そのため外部データは使わず、サイトが返す「所在地」をそのまま保存する方針にしました（`AGENTS.md` に明記）。

### 2.7 調査のテクニック

- **`requests` で再現実験**: ブラウザ操作をコードに置き換え、`Response.text` や `status_code` を確認する。
- **プレーンHTMLを保存**: 取得したHTMLをファイルに保存し、セレクタの検証を何度でも行う。
- **`curl` で単発確認**: リクエスト内容だけ確認するなら `curl` が手軽（Cookie保存は `curl -c cookie.txt`）。
- **セレクタ単体テスト**: 抽出ロジックを `pytest` で固定する（6章）。

---

## 3章 調査結果から、どのように判断したか

「何を見て → なぜそう決めたか」を1つずつ表で示します。ここが**このシステムの設計の核**です。

### 3.1 判断の一覧

| # | 観察結果 | 判断 | システムへの反映 |
|---|---------|------|----------------|
| 1 | 検索がPOSTフォーム方式で、トップページにCSRFトークンが入っている | Selenium不要。`requests` + BeautifulSoup で十分（実測確認） | `SiteSession`（`src/http_client.py:66`）でトークン取得→POST |
| 2 | トークンや選択状態が検索結果ページにも残留する | フォームは毎回「応答ページから再構築」して送る（古いフォームを再利用すると失敗する） | `build_form()`（`src/http_client.py:87`）→ `post_search()` が `base_form` を更新 |
| 3 | 市区町村一覧がAjaxでJSONを返す | 都道府県選択のたびにJSONを取得して選択肢を組み立てる | `fetch_municipalities()`（`src/http_client.py:245`）で `cityInfLst` を取得 |
| 4 | 1問合せ最大50,000件、超過は専用メッセージ | 超過した自治体は「法人番号指定年月日」で期間分割して完全取得する | `collect_range()`（`src/paginator.py:335`）の再帰2分割 |
| 5 | 存在しないページ番号で1ページ目が返る | ページ送りは `while` で判定せず、総件数から終了ページを確定して `for` で回す | `total_pages = ceil(total / PAGE_SIZE)`（`src/paginator.py:184`） |
| 6 | 単日でも超過の自治体がある | それ以上分割できないのでスキップし、その事実を記録（人手での再調査に備える） | `_skip_oversize()`（`src/paginator.py:384`） |
| 7 | 年のセレクトが元号コード（H27/R8） | 西暦→元号コードへ変換する必要がある | `era_code()`（`src/config.py:194`）／`parse_era_options()`（`src/config.py:151`） |
| 8 | 2019年は4月まで平成・5月から令和 | 「2019年」だけでは元号が一意に決まらない | `era_code()` が月を参照（2019年は `H31`/`R1` を使い分け） |
| 9 | 名称セル内に `div.furigana`（カナ）がある | 名称からフリガナを除去して保存する | `_text_without()`（`src/parser.py:85`）を使った非破壊抽出 |
| 10 | 名前や所在地が改行・空白を含む | 空白を正規化してから抽出する | `_clean()`（`src/parser.py:47`） |
| 11 | 1ページ・1レコードの失敗で全部やり直したくない | 失敗は記録して継続。ただし連続で一定回数失敗したら安全停止 | `_store_page()`（`src/paginator.py:249`）／`MAX_CONSECUTIVE_ERRORS=5` |
| 12 | 途中で落ちたら最初からやり直したくない | ページ単位の進捗をJSONに残し、再開できるようにする | `ProgressState`（`src/state.py:41`）の `mark_completed`/`save` |

### 3.2 重要な判断の詳細

#### (1) Selenium 不要をどう確認したか

- Chromeなどを自動操作する Selenium は、動的なJSを実行できる反面、導入が重くて遅い。
- このサイトは JavaScript が動きますが、**検索結果のHTMLはPOSTのレスポンスとして直接返ります**（JSで描画するタイプではない）。この点を Network タブで確認済み。
- よって「POSTを再現すれば十分」と判断し、`requests` + `BeautifulSoup(lxml)` にしました。軽量でテストもしやすい（`AGENTS.md:14` にも「実測確認済み」と記載）。

#### (2) 「フォームは毎回再構築」が必須な理由

- CSRFトークンはページ遷移で更新され得ます。先頭のトークンだけを再利用すると失効し、0件やリダイレクトになる恐れがあります。
- 検索結果ページには、選択中の都道府県・市区町村・日付などが**既に入ったフォーム**が含まれています。
- そこで「常に直近の応答ページから作ったフォーム」をベースに、ページ番号などだけ差し替える方式にしました（`src/http_client.py:283` `post_search`）。

#### (3) なぜ日付2分割の「再帰」なのか

- 50,000件超の自治体（例：千代田区）は、`開始日..今日` を一括では取得できません。
- 「法人番号指定年月日」で期間を半分に割り、**まだ50,000件超ならさらに割る**（再帰）。
- 0件の期間は分割をやめる（枝刈り）ことで高速化します。
- 分割し続けて期間が1日になっても超過なら、取得不能と判定してスキップ・記録します。
- 再帰の深さは `MAX_DAYS_RECURSION_DEPTH=60`（`config.py:82`）で制限し、無限再帰を防ぐ安全弁とします。

#### (4) なぜ郵便番号データを使わないのか

- 調査の結果、候補となった外部データ（日本郵便zip / GitHubミラー / zipcloud）は**全て利用不可**（404または破損）。
- 本システムの目的は「法人の基本3情報の取得」であり、住所の正規化は必須ではありません。
- 壊れ得る外部依存に左右されるより、**サイトが返す値をそのまま保存**するほうが安定・正確と判断しました。

### 3.3 開発中に発見・修正した不具合（教訓）

- `parse_results` がフリガナ要素を `extract()`（破壊操作）で除去していたため、**同一HTMLで2回パースするとフリガナが消える**問題があった。→ 非破壊の `_text_without()` に変更し、`tests/test_parser.py` に再解析テストを追加。
- `parse_era_options` が元号の2桁年（平成27→「27」）を解釈できず0件になる問題があった。→ 正規表現で修正。
- `upsert_company` に `detail_url` を渡さないと `rec['detail_url']` が無視される問題を修正。

### 3.4 本資料作成時に発見した不整合と修正（コードを修正済み）

コードを実サイトと照合した結果、**2つの不整合**を発見しました。いずれも実サイトへのアクセスで確認済みです。

#### 不整合A: 市区町村Ajaxの `?event=select`

- `AGENTS.md` は「`POST /index.html?event=select` にJSON」と**正しく記載**。
- しかし `config.py` の `AJAX_URL` は素の `/index.html` で、`fetch_municipalities()` はそこへPOSTしていた。
- 実サイトで検証した結果、**素の `/index.html` はHTMLを返しJSONにならない**（`cityInfLst` が無い）。`?event=select` を付けるとJSON（`cityInfLst` を63件・東京都）が返ることを確認。
- → `config.py` の `AJAX_PATH` を `/index.html?event=select` に修正。

#### 不整合B: `--date-start` が実際の取得範囲に効いていなかった

- `validate_input()`（`src/main.py:52`）は `--date-start` を検証するだけで使い捨てていた。
- `execute_logic()`（`src/main.py:76`）は引数を受け取るが、`args.date_start` を参照していなかった。
- そのため `--date-start 2016-02-01` を指定しても、実際の範囲は既定 `config.DATE_RANGE_START="2015-10-01"` のままだった（`src/paginator.py` の `process_area` が `DEFAULT_DATE_START` を固定使用）。
- → `PageFetcher` に `date_start` を追加し、`main.py` から受け渡して日付分割の開始日に反映する修正を実施。回帰テスト `test_process_area_date_start_is_honored`（`tests/test_paginator.py`）も追加。

---

## 4章 判断内容から、どのようにシステムを構築したか（設計意図）

### 4.1 全体像

```
config.py (設定集約)
   │
main.py (エントリポイント) ─── logger / database / state (横断的に使用)
   │
   └─ PageFetcher (paginator.py) ── SiteSession (http_client.py) ── サイト
                                          │
                                          └─ parse_results / extract_total (parser.py)
                                          └─ Database upsert (database.py)
```

- **取得（fetch）**: `http_client` が通信、`paginator` が何を取得するか制御
- **編集（transform）**: `parser` がHTMLからレコードへ変換
- **保存（store）**: `database` がSQLiteへ、`state` が進捗へ

### 4.2 3工程をファイル分割した理由

- 各工程の「変更理由」が異なります。通信の失敗・HTML構造の変化・保存スキーマ変更は、それぞれ独立して起きるため。
- 1ファイルに集約すると、関係ない部分を触るたびに巨大なファイルを読み直すことになります。
- 「たらい回し的」な巨大化を避け、**役割を追える構造**にしています（`beginner_overview.md` にも同旨の記述あり）。

### 4.3 各モジュールの責務

| ファイル | クラス/関数 | 責務 |
|---------|-----------|------|
| `src/http_client.py` | `SiteSession` | 通信本体。トークン・フォーム管理、リトライ/バックオフ、市区町村JSON |
| `src/parser.py` | `parse_results` ほか | HTMLから法人レコード抽出。総件数・上限超過の判定、詳細URL構築 |
| `src/paginator.py` | `PageFetcher` | 何ページ・何期間を取るかの制御。日付分割・重複防止・安全停止 |
| `src/database.py` | `Database` | SQLiteへのUPSERT・失敗記録・実行ログ |
| `src/state.py` | `ProgressState` | 進捗の永続化（resume）。ページ粒度で保存 |
| `src/logger.py` | `RecordLogger` | ファイル+コンソールへのログ・サマリー出力 |
| `src/main.py` | `main` ほか | 起動・検証・実行・整形の司令塔 |
| `config.py` | 定数群 | 全設定値・CSSセレクタの集約 |

### 4.4 config への設定・セレクタ集約（なぜ重要か）

- サイトの仕様（URL、フォーム名、セレクタ）は変更され得ます。
- ロジック内へ直書きせず、`config.py`（ルート）の `SELECTORS` に集めることで、変更時に**その1箇所を直すだけ**で済みます（`AGENTS.md` の規約2）。
- ルート `config.py` は定数、`src/config.py` は**変換ロジック**（`era_code`、`DateRange`、`parse_era_options`）を担当します。この分離を守ると迷いません。

### 4.5 ページ粒度 resume（state）の設計

- `ProgressState`（`src/state.py:41`）は「検索キー × ページ番号」で完了を管理します。
- 検索キーは `P13/C101`、期間があるなら `P13/C101/2016-02-01..2016-02-07` の形式（`make_query_key()` `src/paginator.py:85`）。
- `save()` を**ページごと**に呼ぶため、どこまで成功したかを1ページ単位で残せます（`src/paginator.py:223-224`）。
- これにより、途中停止しても「完了済みページはスキップ」して再開できます。

### 4.6 DB スキーマが4テーブルに分かれている理由

| テーブル | 目的 | 書き込み場所 |
|---------|------|------------|
| `companies` | 法人情報（法人番号を主キーにUPSERT） | `_upsert_one()`（`src/paginator.py:271`） |
| `fetch_logs` | ページ単位の成功/失敗/分割記録 | paginator 随所 |
| `failed_records` | レコード単位の失敗詳細 | `_store_page()`（`src/paginator.py:249`） |
| `run_logs` | 実行ごとのサマリー | `main()` の finally（`src/main.py:195`） |

- すべて1テーブルに混ぜると、後から「どのページが失敗したか」「どのレコードが失敗したか」「実行の総まとめ」を個別に集計しにくくなります。
- 分けておくと `SELECT * FROM fetch_logs WHERE status != 'success'` のようにSQLで即座に調査できます。

### 4.7 エラー継続方針（止めない・でも止める）

- **1ページの失敗では止めない**: `fetch_logs` にエラーを記録し、`SLEEP_ON_FAILURE` だけ待って次へ進む（`src/paginator.py:225-244`）。
- **1レコードの失敗では止めない**: `failed_records` に記録して続行（`src/paginator.py:249-265`）。
- **ただし連続で5回失敗したら安全停止**: サイト側の障害やアクセス制限の可能性があるため `RuntimeError` を raise（`src/paginator.py:239`）。
- この「1件エラーでも継続」と「異常時は止める」の両立が、運用で重要な設計ポイントです。

### 4.8 コーディング規約（AGENTS.md）への準拠

- ファイル先頭に目的（Why）/仕様（What）/前提/入出力/注意/拡張/変更履歴をコメントで記載。
- 設定値はコード冒頭にまとめ `config.py` へ集約。
- 関数は `main()/validate_input()/execute_logic()/format_output()` に分離（`src/main.py:52,76,131,153`）。
- 関数は単一責務・副作用回避・型ヒント必須。

---

## 5章 このサイトの特殊性（他サイトとの相違点・罠）

一般的なスクレイピング（URLをGETしてHTMLを読む）と比べた、このサイト固有の特徴です。

### 5.1 POSTフォーム + CSRFトークン方式

- 検索結果は単純なGETでは得られず、フォームPOSTが必要。
- トークンは `fetch_index()`（`src/http_client.py:213`）で取得し、`post_search()`（`src/http_client.py:283`）で送る。
- フォームは毎回応答ページから再構築（`build_form()` `src/http_client.py:87`）。

### 5.2 市区町村一覧が Ajax で JSON を返す

- 都道府県を選ぶと `POST /index.html?event=select` がJSON（`cityInfLst`）を返す（`fetch_municipalities()` `src/http_client.py:245`）。
- **`?event=select` は必須**（本資料で修正。実測による確認済み）。

### 5.3 1問合せ上限 50,000件と「ページ1フォールバック」

- 超過すると「検索結果がN件でした。…50,000件を超えているため、結果を表示できません」が返る。
- **存在しないページ番号を送ると1ページ目が返る**ため、`while`で「次ページ有無」を判断すると無限ループし得る。
- 対策として、総件数から `total_pages = ceil(total / PAGE_SIZE)` を先に確定し `for` で回す（`src/paginator.py:184`）。

### 5.4 元号コード変換（2015→H27、2026→R8）

- 年のセレクトは西暦ではなく元号コード（例：2015→`H27`、2026→`R8`）。
- 2019年は特異で、1〜4月は「平成31年=H31」、5月以降は「令和元年=R1」。
- `era_code()`（`src/config.py:194`）は月を参照し、2019年を場合分けして正しいコードを返します。正規表現で2桁年も解釈（`parse_era_options()` `src/config.py:151`）。

### 5.5 2015-10-05 の一括付番

- 法人番号の一括付番日は 2015-10-05 に集中しており、この日を含む期間は一気に50,000件を超えやすい。
- 想定外に遅い/多い場合は、まずこの日をまたぐ分割を疑います（`beginner_overview.md` 5章(4)）。

### 5.6 結果行の構造（th / td / div.furigana）

```html
<tr>
  <th scope="row">法人番号</th>          <!-- th -->
  <td><div class="furigana">カナ</div> 名称</td>  <!-- td[0] 名称 -->
  <td>所在地</td>                        <!-- td[1] -->
  <td>変更履歴情報等(不要)</td>
</tr>
```

- 法人番号は `th[scope=row]`、名称・所在地は `td`。`td` は複数あるため `td[0]`・`td[1]` で取る。
- フリガナは名称セル内の `div.furigana`。名称はフリガナを除いた部分（非破壊抽出で取得）。

### 5.7 詳細ページURLの形式

- 詳細URLは法人番号から `/henkorireki-johoto.html?selHouzinNo=<法人番号>` の形式に組み立てます（`src/parser.py:156` `_anchor_of`）。
- 推測で作ったURLは使わず、実ページのhrefを確認して決めています。

### 5.8 外部郵便番号データは不採用

- 候補（日本郵便zip / GitHubミラー / zipcloud）は全て利用不可。
- 住所はサイトが返す値をそのまま保存（正規化しない）。

---

## 6章 デバッグ・検証の方法

### 6.1 ネットワークなしで仕組みを検証する（pytest）

`tests/` は実際のサイトにアクセスしません。HTML文字列や偽の通信クラス（FakeSite）を差し込んで、ロジックだけを検証します。

- `test_parser.py`: 結果HTMLを文字列として作り、`parse_results` / `extract_total` / `is_over_limit` を検証。
- `test_paginator.py`: `FakeSite`（偽の通信クラス）でページ送り・日付分割・resume を検証。
- `test_state.py`: 一時フォルダにJSONを書き、save/load/resumeを検証。
- `test_database.py`: 一時SQLiteでUPSERTと失敗記録を検証。
- `test_config.py`: 元号変換・日付分割のDateRangeを検証。

これは「外部サイトに依存しない」テストなので、好きなだけ回せます。サイト仕様の再現テストになり、リファクタ時の安全網になります。

### 6.2 実行時の調査手段

実行が想定通りでないときは、以下の記録を確認します。

- **実行ログ**: `logs/scraping_*.log`（`RecordLogger` がファイル+コンソールへ出力）
- **ページ単位の成否**: DB の `fetch_logs` テーブル（`SELECT * FROM fetch_logs WHERE status != 'success'`）
- **レコード単位の失敗**: `failed_records` テーブル
- **再開位置**: `state/progress.json`（完了ページ・スキップ済みキー）

### 6.3 サイト仕様変更時の対処

- HTML構造やフォーム名が変わったら、まず `config.py` の `SELECTORS` と `AJAX_URL` を確認。
- 実測HTMLサンプルを保存してセレクタを再検証する。
- `pytest` を回して、抽出条件の再現テストが通ることを確認する。

### 6.4 `--date-start` の検証方法

`--date-start` は日付分割の開始日に反映されます。挙動を確認するには:

```
python -m pytest tests -q
```

回帰テスト `test_process_area_date_start_is_honored`（`tests/test_paginator.py`）で、指定開始日が範囲キーに反映されることを検証しています。

---

## 付録A 用語集

| 用語 | 意味 |
|------|------|
| スクレイピング | Webページからデータを自動抽出する技術 |
| CSRFトークン | フォーム偽装送信を防ぐ仕組みの合い言葉。このサイトでは必須 |
| 元号コード | 年のセレクトが使う `H27`（平成27年）や `R8`（令和8年）形式 |
| UPSERT | 「あれば更新、なければ挿入」。重複を防ぐSQLの書き方 |
| resume | 途中から再開する仕組み |
| ページ1フォールバック | 存在しないページ番号で1ページ目が返るサイトの挙動 |
| 表示上限 | 1問合せで返せる最大件数（このサイトは50,000件） |

## 付録B ブラウザ操作 ↔ コードメソッド対応表

| ブラウザ操作 | コード |
|------------|--------|
| トップページを開く | `SiteSession.fetch_index()`（`src/http_client.py:213`） |
| 都道府県を選ぶ | `fetch_municipalities()`（`src/http_client.py:245`）→ `resolve_prefecture_code()` |
| 検索ボタン | `SiteSession.post_search()`（`src/http_client.py:283`） |
| 2ページ目へ | `PageFetcher.fetch_all_pages()`（`src/paginator.py:145`）→ `viewPageNo` |
| 日付絞込 | `PageFetcher._date_overrides()`（`src/paginator.py:95`） |
| 結果を読む | `parser.parse_results()`（`src/parser.py:103`） |
| 保存 | `database.upsert_company()`（`src/database.py:137`） |
| 途中から再開 | `state.ProgressState`（`src/state.py:41`） |

## 付録C 主な実行コマンド

```powershell
# 依存パッケージ
pip install -r requirements.txt

# テスト / lint
python -m pytest tests -q
python -m ruff check src tests config.py

# 実行（東京都・全期間）
python -m src.main --prefectures 東京都

# 実行（日付分割の開始日を指定）
python -m src.main --prefectures 東京都 --date-start 2015-10-01
```

> 実行作業ディレクトリに注意。`python -m src.main` のように**モジュール指定**で起動してください（`src/config.py` がルート `config.py` を `sys.path` 補正で読むため。単に `python src/main.py` とするとルート config が見つからない場合があります）。

## 付録D 参考資料（相互参照）

- `AGENTS.md`: プロジェクトの概要・実測仕様・規約
- `docs/handover.md`: 開発の経緯・結果・残タスク
- `docs/beginner_overview.md`: 初心者・中級者向けの解説（本資料と補完関係）
- `config.py` / `src/*.py`: 実装本体

---

> 本資料は `AGENTS.md`, `config.py`, `src/*.py`, `docs/handover.md`, `docs/beginner_overview.md`, `tests/*.py` を参照して作成しています。
> 行番号は 2026-08-23 時点のコードに照合済みです。コード修正（本資料では `?event=select` と `--date-start` の2点）を行った場合は、該当する行番号・記載の再確認が必要です。



