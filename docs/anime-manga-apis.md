# アニメ・マンガ・メディアミックス関連API

書籍レコメンドシステムに活用できるアニメ・マンガ系APIの調査結果。

## AniList API（推奨度: 高）

アニメ・マンガ・ライトノベルのデータベースAPI。**タグシステムが非常に充実**しており、レコメンドシステムのフィーチャーエンジニアリングに最適。

- **エンドポイント**: `https://graphql.anilist.co`（GraphQL）
- **認証**: 不要（公開データ）
- **費用**: 無料
- **レートリミット**: 90リクエスト/分
- **ドキュメント**: https://docs.anilist.co/

### 取得できるデータ

- タイトル（日本語・英語・ローマ字）
- あらすじ（description）
- **ジャンル**（Action, Fantasy, Mystery 等）
- **詳細タグ（数百種類）**: Isekai, Reincarnation, Overpowered MC, Villainess 等なろう系タグ多数
- 評価スコア（平均・分布）、人気度
- メディアリレーション（原作 → アニメ化、コミカライズ等）
- スタッフ、キャラクター情報

### ライトノベルの検索

ライトノベルは `type: MANGA`, `format: NOVEL` として収録。

### リクエスト例

```bash
# ライトノベルをタグ付きで検索
curl -X POST "https://graphql.anilist.co" \
  -H "Content-Type: application/json" \
  -d '{"query":"{ Page(page:1,perPage:5) { media(type:MANGA,format:NOVEL,sort:POPULARITY_DESC) { title{romaji native} description genres tags{name rank} averageScore } } }"}'
```

---

## Jikan API（推奨度: 高）

MyAnimeList（MAL）の非公式REST API。世界最大のアニメ・マンガデータベースのデータを取得可能。

- **エンドポイント**: `https://api.jikan.moe/v4/`
- **認証**: 不要
- **費用**: 無料・オープンソース（セルフホスト可能）
- **レートリミット**: 60リクエスト/分、2リクエスト/秒
- **ドキュメント**: https://docs.api.jikan.moe/

### 取得できるデータ

- タイトル（日本語・英語）
- あらすじ
- ジャンル・テーマ
- MALスコア、人気度ランキング
- 著者情報
- メディアリレーション

### リクエスト例

```bash
# ライトノベルを検索
curl "https://api.jikan.moe/v4/manga?q=ソードアート&type=lightnovel&order_by=score&sort=desc"

# マンガの詳細取得
curl "https://api.jikan.moe/v4/manga/21479"
```

### 備考

MAL公式API v2（`https://api.myanimelist.net/v2/`）も存在するがOAuth2認証が必要でレートリミットが厳しい。Jikan APIの方が手軽。

---

## メディア芸術データベース（MADB）（推奨度: 中）

文化庁が運営するマンガ・アニメ・ゲーム・メディアアートのデータベース。

- **エンドポイント**: `https://mediaarts-db.artmuseums.go.jp/sparql`（SPARQL）
- **認証**: 不要
- **費用**: 無料（Linked Open Data）
- **ドキュメント**: https://mediag.bunka.go.jp/madb_lab/lod/howto/

### 取得できるデータ

- 作品情報（マンガ・アニメ・ゲーム）
- 作品間リレーション（原作 → アニメ化等）
- 所蔵情報
- Wikidataとのフェデレーテッドクエリ対応
- 全件一括ダウンロード可能

### 弱み

- SPARQLの学習コスト
- あらすじ・評価情報・エンタメタグは取得不可

---

## MangaDex API（推奨度: 中）

マンガ特化のAPIで**タグフィルタリングが充実**。

- **エンドポイント**: `https://api.mangadex.org/`
- **認証**: 多くのGETは不要
- **費用**: 無料
- **レートリミット**: 約5リクエスト/秒/IP
- **ドキュメント**: https://api.mangadex.org/docs/

### 取得できるデータ

- タイトル（多言語）、あらすじ
- **詳細タグ**（ジャンル・テーマ・フォーマット）: Isekai, Reincarnation, Villainess等
- デモグラフィック、著者

### 制約

- マンガ専用（ライトノベル・書籍は非対応）
- スキャンレーション中心のため日本の出版情報は不完全
- 広告・有料サービスでの利用禁止

---

## Amazon PA-API（推奨度: 条件付き）

- **エンドポイント**: PA-API 5.0 → **2026年4月30日廃止予定**
- **後継**: Amazon Creators API
- **認証**: Amazonアソシエイトアカウント必須
- **利用条件**: **過去30日以内に10件以上の売上が必要**（2025年11月改定）
- 個人開発の初期段階では利用困難

---

## その他のAPI

### Kitsu API
- **エンドポイント**: `https://kitsu.io/api/edge/`
- 無料。JSON:API仕様。AniList/MALと類似だがデータ量はやや少ない

### Bangumi API
- **エンドポイント**: `https://api.bgm.tv/`
- 無料。アニメ・マンガ・**書籍**・ゲームをカバー。ユーザー生成タグが豊富
- 中国語ベースのため日本語との照合に工夫が必要

### ISBNdb API
- **エンドポイント**: `https://api2.isbndb.com/`
- **有料のみ**（$14.95/月〜）。英語圏中心で日本語書籍には不向き

---

## 推奨構成

本プロジェクトのレコメンドシステムに最適な組み合わせ:

### Tier 1（メインデータソース）
| API | 用途 |
|-----|------|
| **小説家になろうAPI** | Web小説のタグ・ジャンル・評価 |
| **AniList API** | ラノベ・マンガの詳細タグ・メディアミックス情報 |
| **openBD API** | 商業書籍のあらすじ・書影 |

### Tier 2（データ補完）
| API | 用途 |
|-----|------|
| **Jikan API** | MALユーザースコア補完 |
| **楽天ブックスAPI** | 商業書籍の追加検索・ジャンル |
| **Google Books API** | ISBN検索・表紙画像補完 |
| **NDLサーチAPI** | 正規書誌データ・ISBN収集 |

### Tier 3（特定用途）
| API | 用途 |
|-----|------|
| メディア芸術DB | メディアミックスのリレーション情報 |
| MangaDex | マンガ特化のタグ情報 |
