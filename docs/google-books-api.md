# Google Books API

## 概要

Googleが提供する書籍検索API。ISBNベースの書誌情報取得や、日本語タイトル・著者での検索が可能。

- エンドポイント: `https://www.googleapis.com/books/v1/volumes`
- 認証: **APIキー推奨**（なしでも動作するが共有クォータを消費）
- 費用: **無料**

## APIキー取得手順

1. [Google Cloud Console](https://console.cloud.google.com/) にログイン
2. プロジェクト作成 → 「認証情報」→「APIキー」作成
3. 「Books API」を有効化
4. クエリパラメータに `key=YOUR_API_KEY` を付与

## リクエストパラメータ

| パラメータ | 説明 | 例 |
|-----------|------|-----|
| `q` | **必須**。検索キーワード | `q=推理小説` |
| `langRestrict` | 言語フィルタ（ISO 639-1） | `langRestrict=ja` |
| `maxResults` | 返却件数（デフォルト10、最大40） | `maxResults=40` |
| `startIndex` | ページネーション開始位置（0始まり） | `startIndex=40` |
| `orderBy` | `relevance` / `newest` | `orderBy=newest` |
| `printType` | `books` / `magazines` | `printType=books` |
| `country` | 国コード | `country=JP` |

### 特殊検索プレフィックス（`q` 内で使用）

| プレフィックス | 用途 | 例 |
|--------------|------|-----|
| `intitle:` | タイトル内検索 | `intitle:名探偵コナン` |
| `inauthor:` | 著者名検索 | `inauthor:東野圭吾` |
| `inpublisher:` | 出版社検索 | `inpublisher:講談社` |
| `subject:` | カテゴリ検索 | `subject:Fiction` |
| `isbn:` | ISBN検索 | `isbn:9784062748681` |

## レスポンスフィールド（`volumeInfo` 内）

| フィールド | 説明 | 日本語書籍での状況 |
|-----------|------|-------------------|
| `title` | タイトル | 日本語で正しく返る |
| `authors` | 著者名（配列） | 日本語で正しく返る |
| `publisher` | 出版社 | 取得できない場合あり |
| `publishedDate` | 出版日 | `YYYY-MM` 形式 |
| `description` | あらすじ/内容紹介 | **未登録の書籍が多い** |
| `industryIdentifiers` | ISBN-10/ISBN-13 | 商業出版物は取得可能 |
| `pageCount` | ページ数 | |
| `categories` | カテゴリ | **英語のみ**（"Fiction"等）、粒度が粗い |
| `averageRating` | 平均評価 | 日本の書籍ではほぼデータなし |
| `ratingsCount` | 評価数 | 同上 |
| `imageLinks.thumbnail` | 表紙画像URL | 取得できる書籍が多い |
| `language` | 言語コード | `ja` |

## 利用制限

| 項目 | 内容 |
|------|------|
| デイリークォータ | **1,000リクエスト/日**（APIキー使用時） |
| 1リクエスト最大件数 | 40件 |
| クォータ引き上げ | Google Cloud Consoleから申請可能 |

## リクエスト例

```bash
# 日本語の推理小説を検索
curl "https://www.googleapis.com/books/v1/volumes?q=推理小説&langRestrict=ja&maxResults=10&printType=books&key=YOUR_API_KEY"

# 著者名で検索
curl "https://www.googleapis.com/books/v1/volumes?q=inauthor:東野圭吾&langRestrict=ja&maxResults=40&key=YOUR_API_KEY"

# ISBN検索
curl "https://www.googleapis.com/books/v1/volumes?q=isbn:9784048935005&key=YOUR_API_KEY"

# ライトノベル + 異世界を新しい順に
curl "https://www.googleapis.com/books/v1/volumes?q=ライトノベル+異世界&langRestrict=ja&orderBy=newest&maxResults=40&key=YOUR_API_KEY"
```

## レコメンドシステムへの活用

### 強み
- APIキー取得が簡単で無料
- 商業出版書籍のタイトル・著者・ISBN情報は比較的網羅
- 表紙画像（thumbnail）が取得できる書籍が多い

### 弱み
- `categories` が英語のみ、粒度が粗い（"Fiction" 程度）
- `description` が空の書籍が多い（特にライトノベル・新刊）
- エンタメ特有のタグ（異世界転生等）は取得不可
- 評価データは日本語書籍ではほぼ期待できない
- Web小説（未書籍化作品）は対象外
- 1日1,000リクエストで大量データ収集には不向き

### 位置づけ

メインデータソースとしては不十分。ISBN検索や表紙画像の補完用途として他APIと組み合わせて使う。
