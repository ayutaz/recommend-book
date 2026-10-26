# openBD API

> **記録の位置づけ（2026-10-26）**: これは初回調査時点（2026-03-01）のAPI仕様メモです。エンドポイント、収録件数、レスポンス項目、速度、利用条件は変更され得るため、実装・公開前に [openBDの一次資料](https://openbd.jp/spec/) を再確認してください。現在のプロジェクト状況は [docs/current-status.md](current-status.md)、現行文書とアーカイブの区別は [docs/documentation-index.md](documentation-index.md) を参照してください。

## 概要

版元ドットコムとカーリルが運営すると初回調査で確認した、無料の書誌情報API。ISBNをキーに書籍のメタデータを取得できると記録した。

- ベースURL: `https://api.openbd.jp/v1/`
- 認証: 初回調査では **不要** と記録（APIキー・アカウント登録なし。現行条件は要確認）
- 費用: 初回調査では無料と記録（現行条件は要確認）
- 収録タイトル数: 初回調査時に約 **190万件** と記録（現在の件数は要確認）

## エンドポイント

| エンドポイント | メソッド | 説明 |
|---------------|---------|------|
| `/v1/get?isbn={ISBN}` | GET | 書誌情報の取得 |
| `/v1/get` | POST | 書誌情報の取得（大量ISBN向け） |
| `/v1/coverage` | GET | 収録されている全ISBNの一覧（大容量注意） |
| `/v1/schema` | GET | レスポンスのJSONスキーマ定義 |

## リクエスト方法

- **ISBN形式**: ハイフンなし13桁（例: `9784101010014`）
- **複数ISBN**: カンマ区切り（GET/POST共通）
- **未登録ISBN**: 配列の該当位置に `null` が返る

## レスポンス構造

各要素は3つのブロックに分かれる。

### (A) `onix` -- JPRO-onix準拠の書誌情報

| フィールドパス | 説明 |
|---------------|------|
| `RecordReference` | ISBN（13桁） |
| `DescriptiveDetail.TitleDetail.TitleElement.TitleText.content` | **書名** |
| `DescriptiveDetail.TitleDetail.TitleElement.TitleText.collationkey` | 書名のヨミ |
| `DescriptiveDetail.Collection.TitleDetail` | **シリーズ名/レーベル名** |
| `DescriptiveDetail.Contributor[].PersonName.content` | **著者名** |
| `DescriptiveDetail.Contributor[].BiographicalNote` | 著者略歴 |
| `DescriptiveDetail.Contributor[].ContributorRole[]` | 著者区分コード（A01=著者） |
| `DescriptiveDetail.Extent[]` | ページ数 |
| `DescriptiveDetail.Subject[]` | Cコード/ジャンルコード/キーワード |
| `CollateralDetail.TextContent[].Text` | **内容紹介**（TextType=03） |
| `CollateralDetail.SupportingResource[].ResourceVersion[].ResourceLink` | **書影画像URL** |
| `PublishingDetail.Imprint.ImprintName` | 発行元出版社名 |
| `PublishingDetail.Publisher.PublisherName` | 発売元出版社名 |
| `PublishingDetail.PublishingDate[].Date` | 発売日（YYYYMMDD） |
| `ProductSupply.SupplyDetail.Price[].PriceAmount` | 価格（税抜） |

### (B) `hanmoto` -- 版元ドットコム独自項目

| フィールド | 説明 |
|-----------|------|
| `dateshuppan` | 出版年月日 |
| `lanove` | **ライトノベルフラグ** |
| `hasshohyo` | 書評データありフラグ |
| `reviews[]` | 書評情報（掲載日、書評者、掲載元） |
| `zaiko` | 在庫状況（11=在庫あり, 21=僅少, 33=品切れ, 34=絶版） |
| `ndccode` | NDC分類コード |
| `genrecodetrc` | ジャンルコード（TRC） |
| `jushoujouhou` | **受賞情報** |
| `obinaiyou` | **帯の内容** |
| `kaisetsu105w` | 内容紹介（TRC、105字） |
| `kanrenshoisbn` | 関連書ISBN |
| `jyuhan[]` | 重版情報（日付、刷り数） |

### (C) `summary` -- 簡易情報

| フィールド | 説明 |
|-----------|------|
| `isbn` | ISBN |
| `title` | 書名 |
| `volume` | 巻号 |
| `series` | シリーズ名/レーベル名 |
| `publisher` | 出版社名 |
| `pubdate` | 出版日 |
| `cover` | **書影画像URL**（`https://cover.openbd.jp/{ISBN}.jpg`） |
| `author` | 著者名 |

軽量なデータ取得には `summary` が便利。

## カバレッジ

- 初回調査時に約 **190万タイトル** と記録（現在の件数は要確認）
- データ提供元: 初回調査時に版元ドットコム参加の約24,747社と記録

### データ充実度のばらつき

- **版元ドットコム会員社**: 内容紹介、書影、著者略歴、在庫情報、重版情報、帯の内容、受賞情報など豊富
- **非会員社（大手出版社含む）**: ISBN、タイトル、著者、出版社、価格、発売日程度の基本情報のみ。内容紹介や書影が空のケースが多い

## 利用制限（2026-03-01調査時の記録。現行条件ではない）

- 明示的なレートリミットの記載なし
- 応答速度: 1件あたり1ミリ秒以下（当時の公称値。現在の性能を保証しない）
- 利用条件: 当時確認した記載では本の販促・紹介目的に限定、データ改変は禁止。現在のライセンスと用途適合性を一次資料で確認

## リクエスト例

```bash
# 単一ISBN取得
curl "https://api.openbd.jp/v1/get?isbn=9784101010014"

# 複数ISBN一括取得（カンマ区切り）
curl "https://api.openbd.jp/v1/get?isbn=9784101010014,9784049130034,9784062938426"

# POSTで大量ISBN送信
curl -X POST "https://api.openbd.jp/v1/get" -d "isbn=9784101010014,9784049130034"

# 収録ISBN一覧（大容量注意: 約190万件分）
curl "https://api.openbd.jp/v1/coverage"

# 書影画像の直接取得
curl -O "https://cover.openbd.jp/9784101010014.jpg"
```

## レコメンドシステムへの活用

- **強み（当時の観測）**: 認証不要、無料、高速、広い書誌範囲。`lanove`（ライトノベルフラグ）は存在する場合に利用でき、`summary`で基本情報を取得できる
- **弱み**: エンタメ特有のタグ情報なし（「異世界転生」「悪役令嬢」等）。大手出版社の書籍は内容紹介が空のことが多い
- タグベースのレコメンドでは小説家になろうAPIや楽天ブックスAPIなどを補完候補として検討する
