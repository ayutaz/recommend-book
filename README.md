# recommend-book

日本語のエンタメ系書籍（推理小説、ライトノベル、なろう系など）を対象にした書籍レコメンドシステムのための、**データソース調査とデータ収集基盤**です。各種 API の実測調査結果（`docs/`）と、書誌・あらすじ・タグ情報を収集する Python スクリプト（`scripts/`）を置いています。

> **現時点ではレコメンド本体（モデル・UI）は未実装です。** 収録しているのは API 調査とデータ収集までです。

## データソース

調査の詳細（エンドポイント、パラメータ、レスポンス例、実測結果）は `docs/` にあります。

| 区分 | データソース | 認証 | 主な用途 | 収集スクリプト | 資料 |
|------|-------------|------|---------|---------------|------|
| Tier 1 | 小説家になろう API | 不要 | Web小説のタイトル・あらすじ・キーワード（タグ）・ジャンル | `collect_narou*.py` | [docs/narou-api.md](docs/narou-api.md) |
| Tier 1 | AniList API（GraphQL） | 不要 | ラノベ・マンガの詳細タグ、メディアリレーション | `collect_anilist*.py` | [docs/anime-manga-apis.md](docs/anime-manga-apis.md) |
| Tier 1 | openBD API | 不要 | ISBN をキーにした書誌基本情報（あらすじ充実率は約17%） | `collect_openbd*.py` | [docs/openbd-api.md](docs/openbd-api.md) |
| Tier 2 | Jikan API（MyAnimeList 非公式） | 不要 | MAL スコア・人気度、ジャンル・テーマ | `collect_jikan*.py` | [docs/anime-manga-apis.md](docs/anime-manga-apis.md) |
| Tier 2 | 国立国会図書館サーチ API | 不要 | 国内出版物の網羅的な書誌・ISBN | `collect_ndl*.py` | [docs/ndl-search-api.md](docs/ndl-search-api.md) |
| Tier 2 | 楽天ブックス 書籍検索 API | アプリID | 商業出版物のあらすじ・表紙 | （スクリプトなし） | [docs/rakuten-books-api.md](docs/rakuten-books-api.md) |
| Tier 2 | Google Books API | APIキー | ISBN 検索・表紙の補完 | （スクリプトなし） | [docs/google-books-api.md](docs/google-books-api.md) |

カクヨム・アルファポリス・エブリスタ等の Web 小説プラットフォームは、いずれも公式 API がなくスクレイピングも禁止されています（[docs/web-novel-platforms.md](docs/web-novel-platforms.md)）。全体の比較は [docs/api-survey.md](docs/api-survey.md)、各 API に実際にリクエストした結果は [docs/api-test-results.md](docs/api-test-results.md) にまとめています。

## データ収集

スクリプトは Python 3 の標準ライブラリだけで動きます（追加の依存パッケージなし）。認証の要らない API だけを使います。出力はリポジトリ直下の `data/` に JSON で書き出されます（`data/` は `.gitignore` 済み）。

```bash
python3 scripts/collect_narou.py      # なろう: 4大ジャンル × 500件 → data/narou_novels.json
python3 scripts/collect_anilist.py    # AniList: ラノベ・マンガ・アニメ 各500件 → data/anilist_media.json
python3 scripts/collect_jikan.py      # Jikan: ラノベ・マンガ 各500件 → data/jikan_manga.json
python3 scripts/collect_ndl.py        # NDLサーチ → data/ndl_books.json
python3 scripts/collect_openbd.py     # openBD: 手動リスト30件のISBN → data/openbd_books.json
```

`*_full.py` / `*_mega.py` は大規模収集版です。各 API のレートリミットに合わせて待機しながら取得するため、完了まで長時間かかります。

| スクリプト | 出力 | 備考 |
|-----------|------|------|
| `collect_narou_full.py` | `data/narou_novels_full.json` | ジャンル・ソート・フラグを組み合わせて収集し、ncode で重複除去 |
| `collect_narou_mega.py` | `data/narou_novels_mega.json` | `narou_novels_full.json` があれば読み込んで追加収集。途中経過を checkpoint に保存 |
| `collect_anilist_full.py` | `data/anilist_media_full.json` | ラノベ全件 + マンガ上位 + 読切 |
| `collect_jikan_full.py` | `data/jikan_manga_full.json` | 中断時は `jikan_manga_full_interim.json` に保存 |
| `collect_ndl_full.py` | `data/ndl_books_full.json` | NDC分類・年代・キーワード・出版社で分割検索 |
| `collect_openbd_full.py` | `data/openbd_books_full.json` | **先に `collect_ndl.py` を実行**（`data/ndl_books.json` の ISBN を使う） |

## 収集済みデータセット

2026-03-01 時点で収集したデータは GitHub Releases の [v0.1.0 - 初期データセット](https://github.com/ayutaz/recommend-book/releases/tag/v0.1.0) に置いています（`narou_novels_mega.json` 107,101件、`narou_novels_full.json` 55,215件、`anilist_media_full.json` 15,950件、`jikan_manga_full.json` 9,450件、`ndl_books_full.json` 11,129件、`openbd_books_full.json` 3,518件）。

## ディレクトリ構成

```text
docs/      # データソースごとの API 調査・実測結果
scripts/   # データ収集スクリプト（Python 3 標準ライブラリのみ）
data/      # 収集結果の出力先（git 管理外）
CLAUDE.md  # データソースの優先度と要点
```

## ライセンス

未定。
