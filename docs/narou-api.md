# 小説家になろう API（なろうデベロッパー）

> **記録の位置づけ（2026-10-26）**: これは初回調査時点（2026-03-01）のAPI仕様メモです。エンドポイント、認証、レート制限、応答項目、禁止事項・利用条件は変更され得るため、実装・公開前に [なろうデベロッパーの一次資料](https://dev.syosetu.com/) を目的別に再確認してください。現在のプロジェクト状況は [docs/current-status.md](current-status.md)、現行文書とアーカイブの区別は [docs/documentation-index.md](documentation-index.md) を参照してください。以下の例と分類は当時の調査記録であり、利用許諾を保証しません。

## 概要

「小説家になろう」が提供すると当時確認した、認証不要のAPI。Web小説のメタデータ（タイトル、あらすじ、タグ、評価情報など）を取得できると記録した。小説本文の取得はこの調査の対象外とした。

- 公式サイト: https://dev.syosetu.com/
- 認証: **不要**
- メソッド: **GETのみ**
- 文字コード: UTF-8

## エンドポイント一覧

| API名 | エンドポイント | ドキュメント |
|--------|---------------|-------------|
| なろう小説API | `https://api.syosetu.com/novelapi/api/` | `/man/api/` |
| ランキングAPI | `https://api.syosetu.com/rank/rankget/` | `/man/rankapi/` |
| 殿堂入りAPI | `https://api.syosetu.com/rank/rankin/` | `/man/rankinapi/` |
| ユーザ検索API | `https://api.syosetu.com/userapi/api/` | `/man/userapi/` |
| R18小説API | `https://api.syosetu.com/novel18api/api/` | `/xman/api/` |

## リクエストパラメータ（なろう小説API）

### 出力制御

| パラメータ | 型 | 説明 |
|-----------|-----|------|
| `out` | string | 出力形式: `yaml`(デフォルト), `json`, `jsonp`, `php`, `atom` |
| `gzip` | int(1-5) | gzip圧縮レベル。転送量削減のため推奨 |
| `lim` | int(1-500) | 最大出力件数（デフォルト: 20） |
| `st` | int | 取得開始位置（ページング用） |
| `of` | string | 出力項目をハイフン区切りの短縮コードで指定 |

### ソート順（`order`）

| 値 | 説明 |
|----|------|
| `new` | 新着更新順 |
| `favnovelcnt` | ブックマーク数の多い順 |
| `hyoka` | 総合ポイントの高い順 |
| `dailypoint` | 日間ポイントの高い順 |
| `weeklypoint` | 週間ポイントの高い順 |
| `monthlypoint` | 月間ポイントの高い順 |
| `quarterpoint` | 四半期ポイントの高い順 |
| `yearlypoint` | 年間ポイントの高い順 |
| `impressioncnt` | 感想の多い順 |
| `hyokacnt` | 評価者数の多い順 |
| `lengthdesc` | 文字数が多い順 |
| `lengthasc` | 文字数が少ない順 |
| `ncodedesc` | Nコード降順 |
| `old` | 更新が古い順 |

### 検索・抽出

| パラメータ | 説明 |
|-----------|------|
| `word` | 検索語（スペース区切りでAND検索）。対象: タイトル・あらすじ・キーワード |
| `notword` | 除外単語 |
| `biggenre` | 大ジャンルコード（ハイフン区切りで複数指定可） |
| `genre` | ジャンルコード（ハイフン区切りで複数指定可） |
| `notgenre` | 除外するジャンル |
| `ncode` | Nコード指定（特定作品の取得） |
| `userid` | 作者のユーザID |
| `type` | 作品形式: `t`(短編), `r`(連載中), `er`(完結済み) |
| `length` / `minlen` / `maxlen` | 文字数指定・範囲指定 |

### コンテンツフラグ

| パラメータ | 説明 |
|-----------|------|
| `istensei` | 異世界転生 |
| `istenni` | 異世界転移 |
| `istt` | 異世界転生/転移（両方） |
| `isr15` | R15作品 |
| `isbl` / `isgl` | BL / GL要素 |
| `iszankoku` | 残酷な描写 |
| `notr15` / `notbl` / `notgl` / `notzankoku` / `nottensei` / `nottenni` | 各フラグの除外 |

### 日付指定

| パラメータ | 説明 |
|-----------|------|
| `lastup` | 最終掲載日: `thisweek`, `lastweek`, `sevenday`, `thismonth`, `lastmonth`, またはUNIXタイムスタンプ範囲 |

## レスポンスフィールド

先頭要素は `allcount`（該当作品の総数）。以降、各作品データ。

| フィールド | ofコード | 説明 |
|-----------|---------|------|
| `title` | `t` | 作品名 |
| `ncode` | `n` | Nコード（作品ID） |
| `userid` | `u` | 作者ユーザID |
| `writer` | `w` | 作者名 |
| `story` | `s` | あらすじ |
| `biggenre` | `bg` | 大ジャンルコード |
| `genre` | `g` | ジャンルコード |
| `keyword` | `k` | キーワード（スペース区切り） |
| `general_firstup` | `gf` | 初回掲載日 |
| `general_lastup` | `gl` | 最終掲載日 |
| `novel_type` | `nt` | 1=連載、2=短編 |
| `end` | `e` | 0=完結/短編、1=連載中 |
| `general_all_no` | `ga` | エピソード数 |
| `length` | `l` | 文字数 |
| `global_point` | `gp` | 総合ポイント（ブックマーク x2 + 評価） |
| `daily_point` | `dp` | 日間ポイント |
| `weekly_point` | `wp` | 週間ポイント |
| `monthly_point` | `mp` | 月間ポイント |
| `fav_novel_cnt` | `f` | ブックマーク数 |
| `impression_cnt` | `imp` | 感想数 |
| `review_cnt` | `r` | レビュー数 |
| `all_point` | `a` | 評価ポイント |
| `all_hyoka_cnt` | `ah` | 評価者数 |
| `istensei` | `its` | 異世界転生フラグ |
| `istenni` | `iti` | 異世界転移フラグ |

`of` パラメータで出力項目を制限可能。例: `of=t-n-w-s` でタイトル・Nコード・著者・あらすじのみ。

## ジャンルコード

### 大ジャンル (`biggenre`)

| コード | 名称 |
|--------|------|
| 1 | 恋愛 |
| 2 | ファンタジー |
| 3 | 文芸 |
| 4 | SF |
| 98 | ノンジャンル |
| 99 | その他 |

### ジャンル (`genre`)

| コード | 名称 |
|--------|------|
| 101 | 異世界〔恋愛〕 |
| 102 | 現実世界〔恋愛〕 |
| 201 | ハイファンタジー |
| 202 | ローファンタジー |
| 301 | 純文学 |
| 302 | ヒューマンドラマ |
| 303 | 歴史 |
| 304 | 推理 |
| 305 | ホラー |
| 306 | アクション |
| 307 | コメディー |
| 401 | VRゲーム〔SF〕 |
| 402 | 宇宙〔SF〕 |
| 403 | 空想科学〔SF〕 |
| 404 | パニック〔SF〕 |
| 9901 | 童話 |
| 9902 | 詩 |
| 9903 | エッセイ |
| 9904 | リプレイ |
| 9999 | その他 |

複数ジャンル同時指定: ハイフン区切り（例: `genre=201-202`）

## 利用制限（2026-03-01調査時の記録。現行値ではない）

| 項目 | 値 |
|------|-----|
| リクエスト上限 | 1日 80,000リクエスト（IPアドレス単位） |
| 転送量上限 | 1日 400MB |
| データ反映遅延 | 平均5分（最大2時間） |

※調査当時は利用制限が休止中との記載を確認したが、現在の状態は未確認。実装時は上記値を前提にせず、一次資料の現行条件で設計する。

## リクエスト例

```bash
# 総合ポイント上位5件をJSON取得
curl "https://api.syosetu.com/novelapi/api/?out=json&lim=5&order=hyoka"

# タイトル・Nコード・著者・あらすじのみ
curl "https://api.syosetu.com/novelapi/api/?out=json&lim=5&order=hyoka&of=t-n-w-s"

# 推理ジャンル(genre=304)の人気作品
curl "https://api.syosetu.com/novelapi/api/?out=json&lim=10&genre=304&order=hyoka&of=t-n-w-s-k-gp"

# 異世界転生フラグ付き作品を日間ポイント順に
curl "https://api.syosetu.com/novelapi/api/?out=json&lim=10&istensei=1&order=dailypoint&of=t-n-w-k-gp-dp"

# 特定Nコードで作品情報取得
curl "https://api.syosetu.com/novelapi/api/?out=json&ncode=N9636X&of=t-n-w-s-k-bg-g-gp-f"

# gzip圧縮有効（転送量削減）
curl --compressed "https://api.syosetu.com/novelapi/api/?out=json&lim=100&order=hyoka&gzip=5&of=t-n-k-gp"

# ランキングAPI（日間ランキング）
curl "https://api.syosetu.com/rank/rankget/?out=json&rtype=20260228-d"
```

## 禁止事項

- 小説本文の機械的取得・表示・ダウンロード
- 目次ページからのエピソード情報の機械的取得
- ブックマーク情報の無断利用

メタデータ利用の可否は、目的、表示方法、保存方法、提供元の現行規約・利用条件を確認して判断する。この文書の当時の確認結果は、本文等の機械的取得に注意が必要という範囲の記録であり、一般的な利用許諾を意味しない。
