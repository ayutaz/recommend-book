# 世界作品 DB の推薦用読み取り adapter

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「現行ガイド・仕様」です。手順・契約の再利用前に、現在の状態は[current-status.md](current-status.md)、入力・権利・版の前提は本文で確認してください。

`recommend_book.recommendation.world.load_world_database(path)` は派生分類 SQLite を読み、`{'rows': [...], 'audit': {...}}` を返します。SQLite URI の `mode=ro&immutable=1` と `query_only=ON` を使用し、`DatasetDB` の constructor・migration・書込みを呼びません。非空 WAL を拒否し、開始終了の DB SHA256 が異なれば結果を返しません。LLM、外部通信、CLI 起動、評価・既読情報の推定はありません。

## 行契約と特徴方針

一行は一つの `work_id` です。選択入力に含まれる作品を、分類なし・held・空特徴も含め全件保持します。

| field | 方針 |
| --- | --- |
| `work_id`, `title` | 保存された作品 ID と表示用 primary input の題名 |
| `features` | `label_id -> float >= 0`。present かつ work scope のみ |
| `feature_provenance` | 採用した feature の全観測に job/run/input hash、scope、根拠 ID・locator・URL・権利・language を保持 |
| `rating`, `popularity` | `None`。分類数値から推定しない |
| `read_status` | `unknown`。既読除外は後段の明示データで行う |
| `author_ids` | DB contributions の author が agents に存在し、agent-scope external identifier が明示された DB agent ID のみ |
| `series_ids` | DB work_relations の series_member が series に明示接続される ID のみ |
| `external_ids` | work-scope の `{source, value}` の重複を除いた一覧。版 ISBN や agent ID は含めない |
| `authors` | WorkInput.authors の表示用文字列。author_ids と独立で、identity を生成しない |
| `original_language`, `first_publication_year`, `region`, `series` | primary input の表示用値。未知値を補作しない |
| `source_urls` | 全同作品 input の evidence URL と immutable source record URL の distinct 一覧 |

numeric relevance はその値を強度として保持します。present で relevance が null の場合は、`world-work-features-v1` の**明示的な推薦用方針**として強度 `1.0` を使用し、元 null と policy を provenance/audit に記録します。これは中心性の実測や確率校正ではありません。raw_confidence は元の値を provenance に保存するだけで、重みに使用しません。calibrated confidence を生成しません。

unknown・absent・未報告 axis は特徴を生成せず、negative や zero の観測へ変換しません。明示的な numeric relevance=0 の present は、値0の特徴として保持します。同じ作品の複数 input/expression/run が同一 work-scope feature を報告した場合は最大強度を一度だけ使用し、全採用観測の provenance を残します。複数入力を独立投票や加算へ変換しません。

既定モードは work scope のみです。original/expression/translation は、文体も含め採用せず audit に件数を残します。原文指定 API は今回の一引数関数にはなく、原文・翻訳の文体を作品一般の特徴へ転記しません。除外する label も先に既存の validator で検証します。

表示用 primary は、original input（expression_id null）の最初の selected_position、なければ expression input の最初です。同順位は job key で固定します。題名・著者表示名・原言語・初出年・地域・シリーズの複数 input 間の矛盾は primary を保持し、`audit.metadata_conflicts` に元の候補値を記録します。表示名から同一人物判定を行いません。source-local な名前 hash だけの DB agent ID は authority として出力しません。シリーズ不明と著者不明は独立です。

## 検証と制約

保存 input JSON を WorkInput で検証し、identity/input hash、immutable source version refs と payload/raw SHA、根拠の原 DB evidence key・work/expression scope・権利を照合します。completed result は保存 result hash と既存 `validate_result` で検証し、分類 label と evidence-reference の JSON/各列が result と同値であることを確認します。分類値や原 JSON/hash は書き換えません。JSON duplicate property、NaN/Infinity、float overflow は拒否します。これは構造・参照・保存整合の確認であり、人手による分類正解の確認ではありません。

adapter はファイルの変更検出を行いますが、呼出し側が信頼する公開 manifest の SHA による snapshot 選定は後段の責任です。author・series の DB authority 解決、入力・分類 schema、taxonomy/prompt/validator の既存契約は変更しません。既読・rating・source link の適用も後段で行います。

## 指定 snapshot の読み取り確認

2026-10-13、`data/poc-combined-classified-v2-v1/dataset.sqlite` を読み取りのみで確認しました。

| 確認項目 | 件数 |
| --- | ---: |
| 選択入力 / distinct works / completed | 2020 / 2020 / 2020 |
| labels / label evidence refs | 3194 / 3510 |
| work-scope 特徴あり / 空特徴 | 962 / 1058 |
| scope 保留 | original の文体31 |
| present + relevance null を強度1.0へ | 49 |
| DB author authority IDs あり / series IDs あり | 1910 / 304 |
| 重複 feature 観測 / title 矛盾 | 0 / 0 |

初回ロードは3.818秒でした。DB SHA256 は開始終了とも `dce291b9ede240a26347ab7a1308e30425cccdd240a72a78956006318b35b0a0` です。snapshot・入力・分類・版・式の値は変更していません。空特徴の作品も2020行の中に保持します。この件数は推薦品質の人手評価ではありません。
