# なろう個人推薦参照 adapter v1

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「現行ガイド・仕様」です。手順・契約の再利用前に、現在の状態は[current-status.md](current-status.md)、次の作業順序は[現行計画 v25](autonomous-next-phase-plan-v25.md)を参照し、入力・権利・版の前提は本文で確認してください。本文のCLI例・数値・測定値は記載した版・namespaceの再現用です。

`src/recommend_book/recommendation/narou.py` の `load_narou_reference(database, annotations, *, bridge=None)` は、保存済み SQLite とあらすじ annotation を read-only で読み、共通推薦 row と aggregate audit を返す。ネットワーク、分類 CLI、元 repository の実行・変更、commit は行わない。

## 固定参照と取得

private repository `ayutaz/narou-personal-recommender` の HEAD は `193f07c41b04709c2cdb4fa135c6f30ca9469732`。参照は `.gitignore` の `data/` 配下、`data/reference-code/narou-personal-recommender` に保存した。

blobless no-checkout clone の一括 `git restore` で報告された missing object / data directory の失敗は、個別取得では再現しなかった。原因の断定はしていない。`git show <pinned HEAD>:<必要 path>` で source 9 本、SQLite、annotation、推薦 config、既存 evaluation、pyproject の計 14 ファイルを取得し、各ファイルの Git blob SHA1 を pinned tree の object ID と一致させた。SQLite 等は subprocess の bytes をそのまま保存し、PowerShell の文字列 redirect による binary 変換を避けた。認証済み `gh api .../git/blobs/<oid>` の fallback を取得 script に用意したが、今回使う必要はなかった。

private な `acquisition-manifest.json` に file path / HEAD / blob SHA1 / raw SHA256 / byte 数を保存。個人の作品名、作者名、N code、引用、個別評価をログや commit に出さない。個人記録は公開用 fixture や document に転記しない。private repository への access は本文や書誌の再配布許諾を追加しない。

固定された入力の raw SHA256:

| 入力 | SHA256 |
| --- | --- |
| `data/narou.sqlite` | `2f13e7dce3ae53825576b017e3a2171e31002099134a0fdb6274acb00794c595` |
| `data/processed/content-llm/annotations.json` | `81359317df6ec7a71eeb4d8da1a32a5744873f5363346d338333e9ed474c83be` |
| SQLite から再構築した annotation source | `fb0d19093dc6d3bebfa3e6614c43ca5b80fa3852f739075d659fe2f82f99e57c` |

## 実測した形式と不足

SQLite `user_version=1`。`imports`, `novels`, `rating_observations`, `reading_status`, `novel_metadata` を持つ。現在の import は 1、rating observations / unique latest は 335 / 335。rating は全 335 件で 1～5 の整数を保持する。metadata は success 331 / not_found 4。annotation は `novels` 配列 331、重複 N code なし、保存 source 331 と集合・source SHA が一致した。

rating の first date 欠損は 0、last date 欠損は 320。全 335 件の native read status は unknown、completed_reading は null。評価記録から全編読了・離脱話数を推定しない。first/last rating date があっても、その時点の metadata / annotation が保存されているとはいえないため、この参照だけで temporal evaluation を主張しない。

API raw metadata の fields に `userid` がなく、明示 author identity は 0。writer の文字列は 331 件で利用可能だが canonical author ID としない。series の安定 ID も記録されていない。同作者漏洩を保守的に抑えるため writer の `NFKC → strip → casefold` を評価用 blocking key として使い、同名の別人を同群へ入れる可能性を不足として保持する。

annotation の prompt version は `content-v1`、review version は `content-review-v1`、rating_blinded は true。参照 source の `content_review.py` は LLM 再レビューを行う。review という名称を人手 taxonomy gold と読み替えない。全編本文、文体・結末の検証、raw confidence、calibration はこの annotation にない。

## 共通 row と読み取り契約

返却値は `{"rows": [...], "audit": {...}}`。annotation 不足の 4 件も含む全 335 件を保持する。実測では共有 feature が空の row は 7 件。source SHA 検証後に、各 annotation の native field / enum / identity coverage / 重複 / 根拠 quote 完全一致を検証する。quote 完全一致は semantic support や人手正解の保証ではない。

| field | adapter の扱い |
| --- | --- |
| `work_id` | `narou:` + lowercase N code。タイトル一致で別作品へ寄せない |
| `title` | 最新 rating observation の title を保持。metadata title へ置換しない |
| `rating` | 保存個人評価 1～5 または null。推薦 score を評価へ代入しない |
| `external_ids` | `[{"source":"narou","value":lowercase_ncode}]` |
| `author_ids` | raw API `userid` が bool でない正整数の場合だけ `narou_user:<id>`。この実参照では全空 |
| `author_group_keys` | `narou_writer_name:<normalized writer>`。評価用 blocking のみ、canonical identity ではない |
| `series_ids` | 安定 ID 未記録のため空。source/audit で unknown と明示 |
| `source_read_status` | native 状態をそのまま保存 |
| `read_status` | unknown/unread は維持。reading/completed/dropped は共通 `read` へ変換し、encountered の意味だけを持たせる |
| `popularity` | 成功 metadata の保存 `global_point` が 0 以上の整数なら保持、それ以外 null。評価・confidence から作らない |
| `features` | 相談済み partial bridge の共通 label ID → 推薦用 feature strength |
| `native_features` | `narou.theme.<native ID>` → central 1.0 / secondary 0.3。within-source 比較の別 field |
| `native_feature_provenance` | 全検証済 native theme の元根拠。feature ごとに SHA / id / prominence / 元 source と完全一致 quote / prompt・review version を list で保持 |
| `feature_provenance` | 各 target label の複数寄与を list で保持。SHA / source / mapping version / native evidence と重みを区別 |
| `provenance` | 全 row に SQLite / annotation / source / bridge SHA、rating snapshot と日付、元読書状態、欠損を保持 |

rating がある row は推薦 engine が既評価履歴として除外する。元 unknown の 335 件を `read` に推定変更しない。共通 `read` と明示 native completed を区別して保存し、`has_read_whole_work` は推定せず null を保持する。

最新 observation は原参照 `latest_ratings` と同じ `observed_at DESC, imported_at DESC, snapshot_id DESC` で ncode ごとに一件選び、出力は work ID 順。観測日と本人が評価した日を混同しない。case を正規化した ID が複数の latest row に衝突する場合は曖昧な統合を拒否する。orphaned import、未対応 schema、metadata raw N code の不一致も拒否する。

SQLite は `mode=ro&immutable=1` と query_only で開く。nonempty WAL は読み始める前と終了時に拒否する。入力 SQLite / annotation / bridge の SHA を前後で検証する。JSON の duplicate key、NaN / Infinity / `1e999` 等も拒否する。元 reference package を import せず、取得した source の CLI や学習を動かさない。

## Partial bridge

root 管理の [recommendation-feature-bridge.json](../config/recommendation-feature-bridge.json) が既定入力。version は `narou-world-feature-bridge-v1`、状態は `partial_manual_mapping_unvalidated_cross_domain`。現在相談済みの以下だけを受理する。拡張は別の検討と version 更新が必要。

| native field | target feature |
| --- | --- |
| metadata genre 201 / 202 | `genre.fantasy` |
| 401 / 402 / 403 | `genre.science_fiction` |
| 304 | `genre.mystery` |
| 101 / 102 | `genre.romance` |
| 305 | `genre.horror` |
| 303 | `genre.historical` |
| growth central / secondary | `theme.growth`（努力・訓練という狭い hint） |
| revenge central | `theme.revenge` |
| romance central | `genre.romance` |

metadata genre は宣言された推薦 feature、theme は LLM があらすじから得た hint。どちらも新 taxonomy の人手 gold ではない。central / secondary の 1.0 / 0.3 は推薦 weight であり、relevance または confidence として扱わない。raw confidence / calibrated confidence は null を保持する。

同じ target feature に複数寄与する場合は最大 weight を使い、provenance は全寄与を残す。未対応 native theme は audit と row の `unmapped_native_theme_ids` へ保存。power（強さの発揮・無双）を社会的権力の `theme.power` にしない。cozy を本文未取得の `atmosphere.tranquil` にしない。parenting / belonging を家族の事実なしに変換せず、gender / tone も world label にしない。feature の省略は unknown であり、absent を意味しない。

native-theme 診断は `native_features` と **`native_feature_provenance`** を組にして使う。共有 label の `feature_provenance` へ置き換えると、未対応テーマの元根拠が診断から欠落する。native 側は検証済み 14 themes すべてについて、実際の annotation の quote と元 field（title / keyword / story）、native id / prominence、prompt / review version、既存 base の SQLite / annotation / source / bridge SHA を保持する。annotation のない row は空 dict を返す。新たな quote、confidence、人手判定、target label への写像は生成しない。native provenance の追加によって既存の feature value、mapping、推薦 weight は変更しない。

## 検証と実行範囲

`uv run pytest tests/test_narou_adapter.py -q` は人工 SQLite / annotation のみを使用する。readonly bytes、stale source、identity / duplicate / quote / enum、unsupported bridge、userid の型、date tie、nonfinite JSON、WAL、source mutation、全 native read state の共通 engine への受け渡しを検証する。

native provenance の追加は TDD で全 14 themes × central / secondary、mapped / unmapped の元 quote、confidence null、人手 gold false、入力 bytes 不変を検証した。ここでの個別テーマ fixture は provenance の形式・保持を検証し、文学的な意味分類の正確さを検証するものではない。元 private data / 分類 core artifact / mapping config / 順位計算は編集しない。

実 private 入力も adapter を一度読み、source SHA 一致・全 335 row 保持・未 annotation 4・共有 feature 空 7・author identity 0 / writer blocking 331 / series identity 0・原 bytes 不変を確認した。aggregate audit は ignored private `adapter-audit.json` に保存した。実 rows をテストや公開 document に書き出していない。

この成果は推薦入力の形式・provenance と機械的整合性の確認であり、個人推薦の精度、他 source への転移品質、taxonomy 品質承認を意味しない。全後観測 feature に対する評価は grouped CV を別途行い、同作者 blocking・実 rating・空 feature の分母を保つ。
