# CLAUDE.md

現行状態（2026-10-20）: 世界小説の収集・名寄せ・根拠付き分類・暫定推薦を実装済み。最新表示2,038候補、特徴1,040、暫定得点219、履歴内容接続2/335。名寄せ修正後の2作品を保存済み資料から分類・審査し、2候補・7特徴を追加した。モデル2呼出・再試行0、追加取得0。計画1〜4は実行済みで、次の適合入力がないため最大20作品バッチと大量処理は未開始。現在の作業順序と処理上限は[計画 v16](docs/autonomous-next-phase-plan-v16.md)、最新結果は[限定処理と再判定](docs/phase15-bounded-poc-and-delivery-v1.md)を参照する。以下のAPI候補の説明は初期調査の履歴であり、現時点の取得・AI解析・商用利用の許可を意味しない。追加費用0・外注なし・追加本人作業0を維持し、新規取得時に用途ごとの条件を確認する。

このファイルは、Claude Code (claude.ai/code) がこのリポジトリで作業する際のガイドです。

## プロジェクト概要

日本語エンタメ系書籍（推理小説、ライトノベル、なろう系など）のタイトル・あらすじ・タグ情報を取得・閲覧するためのレコメンドシステム。

## データソース

各APIの詳細仕様（エンドポイント、パラメータ、レスポンス、リクエスト例など）は `docs/` を参照。

### Tier 1: メインデータソース

- **小説家になろう API** ([docs/narou-api.md](docs/narou-api.md)): Web小説データを無料取得。認証不要。タイトル、あらすじ、キーワード（タグ）、ジャンル、文字数、評価ポイントなどを返す。なろう系タグデータ（異世界転生、悪役令嬢、チートなど）の情報源として最強。プログラムから安定してデータ取得できるWeb小説APIはこれが唯一。
- **AniList API** ([docs/anime-manga-apis.md](docs/anime-manga-apis.md)): GraphQL。認証不要・無料。ラノベ・マンガの詳細タグが数百種類（Isekai, Reincarnation, Overpowered MC等）。メディアリレーション（原作→アニメ化等）も取得可能。レコメンドのフィーチャーエンジニアリングに最適。
- **openBD API** ([docs/openbd-api.md](docs/openbd-api.md)): 認証不要・無料。約190万タイトル収録。ISBNをキーにタイトル、著者、出版社、価格を取得。**実測ではあらすじ充実率17%、書影6.6%、lanoveフラグ0%と低い**。書誌基本情報の補助ソースとして使用。

### Tier 2: データ補完

- **楽天ブックス 書籍検索 API** ([docs/rakuten-books-api.md](docs/rakuten-books-api.md)): 商業出版されたラノベや推理小説向け。タイトル、著者、あらすじ（商品説明）、ジャンルID、表紙画像URLを取得可能。楽天アプリIDが必要。レートリミット: 1秒1リクエスト、最大3,000件。
- **Jikan API** ([docs/anime-manga-apis.md](docs/anime-manga-apis.md)): MyAnimeList（MAL）の非公式REST API。認証不要・無料。MALスコア、人気度ランキング、ジャンル・テーマ情報を取得可能。セルフホスト可能。
- **Google Books API** ([docs/google-books-api.md](docs/google-books-api.md)): **APIキー必須**（キーなしでは429エラー頻発を確認済み）。ISBN検索・表紙画像の補完用途。日本語書籍のタイトル・著者は正しく返るが、あらすじやカテゴリの充実度は低い。1日1,000リクエスト制限。
- **国立国会図書館サーチ API** ([docs/ndl-search-api.md](docs/ndl-search-api.md)): 認証不要・無料。日本国内出版物をほぼ全て網羅（納本制度）。タイトル・著者の読み仮名付き。ISBN収集のハブとして有用。SRU APIで`mediatype="books"`指定により図書限定可能。あらすじ・タグ・評価データはなし。

### Tier 3: 特定用途

- **メディア芸術データベース (文化庁)**: SPARQL対応。メディアミックス（アニメ化・コミカライズ）のリレーション情報に強い。
- **MangaDex API**: マンガ特化。タグフィルタリングが充実（Isekai, Villainess等）。ライトノベルは非対応。

### Web小説プラットフォーム調査結果 ([docs/web-novel-platforms.md](docs/web-novel-platforms.md))

カクヨム、アルファポリス、エブリスタ等の主要プラットフォームはいずれも**公式APIなし・スクレイピング禁止**。pixiv小説は非公式ライブラリ（pixivpy）経由で取得可能だがAPI変更リスクあり。

### 参考サイト（公開APIなし）

- **読書メーター / ブクログ**: ユーザー生成タグが豊富（どんでん返し、叙述トリック、俺TUEEEなど）
- **キミラノ (KADOKAWA)**: ラノベ特化のレコメンドサイト。タグ検索が充実

## アーキテクチャ

Pythonプロジェクトは`uv`で依存・lockfileを管理する。`src/recommend_book/`に収集・DB・分類・名寄せ・用途判定・推薦、`scripts/`に実行と監査、`tests/`にfixture試験を配置する。実データはGit対象外の`data/`へ保存する。概要と開発コマンドは[README](README.md)、検証範囲は[CIガイド](docs/ci-validation-guide.md)を参照する。
