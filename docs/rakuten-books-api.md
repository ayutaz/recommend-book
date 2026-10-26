# 楽天ブックス書籍検索 API

> **記録の位置づけ（2026-10-26）**: これは初回調査時点（2026-03-01）のAPI仕様メモです。エンドポイント、認証、パラメータ、ページング、レート制限、アフィリエイト条件は変更され得るため、実装・公開前に [Rakuten Developers の一次資料](https://webservice.rakuten.co.jp/) を再確認してください。現在のプロジェクト状況は [docs/current-status.md](current-status.md)、現行文書とアーカイブの区別は [docs/documentation-index.md](documentation-index.md) を参照してください。以下の上限値・例は当時の記録であり、現在の利用許諾やクォータを保証しません。

## 概要

楽天ブックスの商業出版書籍を検索するREST API。初回調査では、ライトノベルや推理小説などの商業出版物について、タイトル、著者、商品説明、表紙画像を取得する候補として整理した。

- エンドポイント: `https://openapi.rakuten.co.jp/services/api/BooksBook/Search/20170404`
- 認証: **Application ID（アプリID）が必須**
- レスポンス形式: JSON（JSONP対応）

## 認証（アプリIDの取得）

1. [Rakuten Developers](https://webservice.rakuten.co.jp/) にアクセス
2. 楽天会員としてログイン
3. 「アプリID発行」からアプリ名・URLを入力
4. **Application ID** と **Application Secret** が発行される

リクエスト時に `applicationId` パラメータとして付与する。

## リクエストパラメータ

### 検索条件（いずれか1つ以上が必須）

| パラメータ | 型 | 説明 |
|-----------|-----|------|
| `title` | String | 書籍タイトル（部分一致） |
| `author` | String | 著者名 |
| `publisherName` | String | 出版社名 |
| `isbn` | String | ISBNコード |
| `booksGenreId` | String | ジャンルID（デフォルト: `001`） |
| `size` | int | 書籍サイズ（0:全て, 1:単行本, 2:文庫, 3:新書, 9:コミック） |

### ページング・ソート

| パラメータ | デフォルト | 説明 |
|-----------|-----------|------|
| `hits` | 30 | 1回のレスポンス件数（1〜30） |
| `page` | 1 | ページ番号（1〜100） |
| `sort` | `standard` | ソート順（下表） |

**ソート順:**

| 値 | 意味 |
|----|------|
| `standard` | 楽天おすすめ順 |
| `sales` | 売上順 |
| `-releaseDate` | 発売日が新しい順 |
| `+releaseDate` | 発売日が古い順 |
| `-itemPrice` | 価格が高い順 |
| `+itemPrice` | 価格が安い順 |
| `reviewCount` | レビュー件数が多い順 |
| `reviewAverage` | レビュー平均評価が高い順 |

### その他

| パラメータ | 説明 |
|-----------|------|
| `applicationId` | **必須** アプリケーションID |
| `affiliateId` | アフィリエイトID（任意） |
| `elements` | 取得フィールドをカンマ区切りで指定（軽量化） |
| `formatVersion` | `1`: ネスト構造（デフォルト）, `2`: フラット構造 |
| `outOfStockFlag` | 品切れ商品を含めるか（0:含めない, 1:含める） |
| `genreInformationFlag` | ジャンル階層情報を含めるか（0:含めない, 1:含める） |

## レスポンスフィールド

### メタ情報

| フィールド | 説明 |
|-----------|------|
| `count` | 検索結果の総件数 |
| `page` | 現在のページ番号 |
| `pageCount` | 総ページ数 |
| `hits` | 現在ページの件数 |

### 書籍情報

| フィールド | 説明 |
|-----------|------|
| `title` | 書籍タイトル |
| `titleKana` | タイトルのカナ表記 |
| `subTitle` | サブタイトル |
| `seriesName` | シリーズ名 |
| `author` | 著者名 |
| `authorKana` | 著者名のカナ |
| `publisherName` | 出版社名 |
| `isbn` | ISBNコード |
| `itemCaption` | **商品説明（あらすじ）** |
| `salesDate` | 発売日 |
| `itemPrice` | 税込価格 |
| `size` | サイズ |
| `contents` | 収録内容 |

### URL・画像

| フィールド | 説明 |
|-----------|------|
| `itemUrl` | 商品ページURL |
| `affiliateUrl` | アフィリエイトURL |
| `smallImageUrl` | 表紙画像（64x64px） |
| `mediumImageUrl` | 表紙画像（128x128px） |
| `largeImageUrl` | 表紙画像（200x200px） |

### 評価

| フィールド | 説明 |
|-----------|------|
| `reviewCount` | レビュー件数 |
| `reviewAverage` | レビュー平均評価 |
| `booksGenreId` | ジャンルID |

## ジャンルID（本プロジェクト向け）

### 推理小説・ミステリー

| ジャンルID | ジャンル名 |
|-----------|-----------|
| `001004001` | **ミステリー・サスペンス** |
| `001004002` | SF・ホラー |
| `001004008` | 日本の小説 |
| `001004009` | 外国の小説 |

### ライトノベル

| ジャンルID | ジャンル名 |
|-----------|-----------|
| `001021` | ライトノベル（親） |
| `001021001` | **ライトノベル > 小説** |
| `001021002` | ライトノベル > コミック |

### ジャンル階層の探索

ジャンル検索APIで子ジャンルを探索可能:

```bash
curl "https://openapi.rakuten.co.jp/services/api/BooksGenre/Search/20121128?applicationId=YOUR_APP_ID&booksGenreId=001021"
```

## 利用制限（2026-03-01調査時の記録。現行値ではない）

| 項目 | 制限値 |
|------|--------|
| レートリミット | **1アプリIDあたり 1秒に1リクエスト** |
| 1回の取得件数上限 | 最大30件 |
| ページング上限 | 最大100ページ |
| 取得可能な最大件数 | **最大3,000件**（30件 x 100ページ） |
| 制限超過時 | HTTP 429。継続超過でアプリID利用停止の可能性 |

大量取得時は当時記録した制限を前提にせず、現行のレート制限と利用条件を確認して待機処理を設計する。

## リクエスト例

```bash
# 「推理」をタイトルに含む書籍を発売日新しい順に5件取得
curl "https://openapi.rakuten.co.jp/services/api/BooksBook/Search/20170404?applicationId=YOUR_APP_ID&title=%E6%8E%A8%E7%90%86&hits=5&sort=-releaseDate"

# ミステリー・サスペンスジャンルで売上順
curl "https://openapi.rakuten.co.jp/services/api/BooksBook/Search/20170404?applicationId=YOUR_APP_ID&booksGenreId=001004001&sort=sales&hits=30"

# ライトノベルジャンルで著者「川原礫」を検索
curl "https://openapi.rakuten.co.jp/services/api/BooksBook/Search/20170404?applicationId=YOUR_APP_ID&booksGenreId=001021001&author=%E5%B7%9D%E5%8E%9F%E7%A4%AB&sort=-releaseDate"

# ISBNで特定書籍を取得
curl "https://openapi.rakuten.co.jp/services/api/BooksBook/Search/20170404?applicationId=YOUR_APP_ID&isbn=9784041012345"

# フィールド限定・フラット構造で軽量取得
curl "https://openapi.rakuten.co.jp/services/api/BooksBook/Search/20170404?applicationId=YOUR_APP_ID&title=%E7%95%B0%E4%B8%96%E7%95%8C&booksGenreId=001021001&elements=title,author,itemCaption,isbn,largeImageUrl&formatVersion=2&hits=10"
```

## 活用上の注意

- `itemCaption` がレコメンドシステムで使う「あらすじ」に相当するが、空の場合もある
- **タグ情報（異世界転生、悪役令嬢等）は取得不可**。ジャンルID分類のみ
- **表紙画像URL**が取得できるためUI表示に有用
- レートリミットとページング上限は当時の記録である。実装前に現行値を確認する
