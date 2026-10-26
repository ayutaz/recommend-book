# 国立国会図書館サーチ（NDLサーチ）API

> **記録の位置づけ（2026-10-26）**: これは初回調査時点（2026-03-01）の仕様メモと例です。エンドポイント、収録範囲、認証、件数上限、レート制限、書影・二次利用条件は変更され得るため、実装・公開前に [NDLサーチAPIの一次資料](https://ndlsearch.ndl.go.jp/help/api/specifications) を再確認してください。現在のプロジェクト状況は [docs/current-status.md](current-status.md)、現行文書とアーカイブの区別は [docs/documentation-index.md](documentation-index.md) を参照してください。

## 概要

国立国会図書館が提供する書誌情報検索API。日本国内で出版された資料を広い範囲で収録するが、資料種別・収録条件・更新状況による差がある。

- 公式サイト: https://ndlsearch.ndl.go.jp/help/api/specifications
- 認証: 初回調査では **基本不要** と記録（営利目的・継続利用の申請条件は現在の資料を確認）
- 費用: 初回調査では無料と記録（現在の条件は要確認）

## エンドポイント

| API | エンドポイント | 出力形式 |
|-----|---------------|---------|
| OpenSearch | `https://ndlsearch.ndl.go.jp/api/opensearch` | XML (RSS 2.0) |
| SRU | `https://ndlsearch.ndl.go.jp/api/sru` | XML (RDF) |
| OAI-PMH | `https://ndlsearch.ndl.go.jp/api/oaipmh` | XML |
| 書影 | `https://ndlsearch.ndl.go.jp/thumbnail/{ISBN}.jpg` | JPEG |

※旧URL（`https://iss.ndl.go.jp/api/...`）は2024年1月に廃止済み。

## OpenSearch API パラメータ

| パラメータ | 説明 | 例 |
|-----------|------|-----|
| `any` | 全項目フリーワード検索 | `any=異世界` |
| `title` | タイトル（部分一致） | `title=ソードアート` |
| `creator` | 著者（部分一致） | `creator=東野圭吾` |
| `publisher` | 出版社 | `publisher=講談社` |
| `ndc` | NDC分類（前方一致） | `ndc=913`（日本小説） |
| `isbn` | ISBN | `isbn=9784062135269` |
| `from` / `until` | 出版年範囲 | `from=2020` |
| `cnt` | 取得件数（デフォルト200、最大500） | `cnt=10` |
| `idx` | 取得開始位置 | `idx=1` |

## SRU API パラメータ

| パラメータ | 必須 | 説明 |
|-----------|-----|------|
| `operation` | 必須 | `searchRetrieve` |
| `query` | 必須 | CQL形式の検索条件（`title="推理小説"` など） |
| `recordSchema` | 任意 | `dc`（簡易）/ `dcndl`（詳細） |
| `maximumRecords` | 任意 | 最大取得件数（デフォルト200、最大500） |
| `startRecord` | 任意 | 取得開始位置 |

CQL検索では `AND` / `OR` で条件連結可能。

### CQL検索インデックス

| インデックス | 説明 |
|-------------|------|
| `title` | タイトル |
| `creator` | 著者 |
| `publisher` | 出版社 |
| `subject` | 件名・主題 |
| `ndc` | NDC分類番号 |
| `isbn` | ISBN |
| `from` / `until` | 出版年 |

## レスポンスフィールド（OpenSearch）

| フィールド | 説明 |
|-----------|------|
| `dc:title` | タイトル |
| `dc:creator` | 著者 |
| `dc:publisher` | 出版社 |
| `dc:date` / `dcterms:issued` | 出版年月 |
| `dc:extent` | ページ数 |
| `dc:identifier` (ISBN) | ISBN |
| `dc:subject` (NDC9) | NDC分類（例: `913.6`） |
| `dcndl:titleTranscription` | タイトル読み（カタカナ） |
| `dcndl:creatorTranscription` | 著者読み（カタカナ） |
| `dcndl:seriesTitle` | シリーズ名 |
| `dcndl:volume` | 巻号 |
| `dcndl:price` | 価格 |

※初回調査の観測では `dc:description` に「文献あり」等の注記が多く、あらすじを含まないレコードが多かった。フィールドの有無はレコードごとに確認する。

## 利用制限（2026-03-01調査時の記録。現行値・許諾ではない）

| 項目 | 内容 |
|------|------|
| 1回の最大取得件数 | 500件 |
| 501件目以降 | 取得不可（検索条件で分割が必要） |
| レートリミット | 具体的数値は非公開。大量連続アクセスはブロックの可能性 |
| 二次利用 | 当時の確認では出典表示が必要と整理。現在のライセンス・出典条件を一次資料で確認 |

## リクエスト例

```bash
# 東野圭吾の日本小説（NDC 913）を10件取得
curl "https://ndlsearch.ndl.go.jp/api/opensearch?creator=%E6%9D%B1%E9%87%8E%E5%9C%AD%E5%90%BE&ndc=913&cnt=10"

# タイトル検索
curl "https://ndlsearch.ndl.go.jp/api/opensearch?title=%E3%82%BD%E3%83%BC%E3%83%89%E3%82%A2%E3%83%BC%E3%83%88%E3%83%BB%E3%82%AA%E3%83%B3%E3%83%A9%E3%82%A4%E3%83%B3&cnt=5"

# ISBN検索
curl "https://ndlsearch.ndl.go.jp/api/opensearch?isbn=9784062135269"

# SRU: 2020年以降の推理小説を詳細スキーマで取得
curl "https://ndlsearch.ndl.go.jp/api/sru?operation=searchRetrieve&query=title%3D%22%E6%8E%A8%E7%90%86%E5%B0%8F%E8%AA%AC%22%20AND%20from%3D%222020%22&recordSchema=dcndl&maximumRecords=5"

# 書影画像取得
curl -o cover.jpg "https://ndlsearch.ndl.go.jp/thumbnail/9784062135269.jpg"
```

## レコメンドシステムへの活用

### 強み
- **収録範囲**: 日本国内出版物を広い範囲で収録（対象資料と収録条件の確認が必要）
- **正規化データ**: タイトル・著者の読み仮名（カタカナ）付き
- **ISBN取得**: 他API（openBD、楽天）との連携ハブとして有用
- **書影API**: ISBNから表紙画像を取得可能

### 弱み
- **あらすじ**: 初回調査では `description` にあらすじがないレコードが多かった
- **タグ情報なし**: エンタメ特有のキーワード（異世界転生等）は取得不可
- **評価データなし**: ランキング・レビュー・ポイント情報なし
- **500件上限**: ページング上限あり

### 推奨活用方法

書誌マスターデータベースとして位置づけ、ISBNをキーに他APIでデータ補完する。

```
NDLサーチ → 正式書誌情報（タイトル、著者、ISBN、出版社、NDC）
    ↓ ISBN連携
openBD → あらすじ・内容紹介
楽天ブックス → 商品説明、ジャンル、レビュー
なろうAPI → タグ、評価ポイント
```
