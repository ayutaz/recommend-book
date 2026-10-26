# 派生資料の作品対応とOpen Library部分標本の選択

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「履歴・結果・計画・スナップショット」です。本文の件数・計画・実行状態と、本文内の「現在」「最新」「実行中」は記録日時のものとして扱い、現在の状態は[current-status.md](current-status.md)を参照してください。

2026年10月12日。今回の対応は、コピーされた書誌の独立性を主張せず、保存済み資料の由来と作品範囲を検証するためのもの。

## WikipediaからWikidata作品へのsource_mapping

`compare_source_mapping()` は、Wikidataの作品レコードとWikipediaの派生作品レコードの組だけを対象にする。通常の名寄せ規則にある「同じ外部機関の作品IDが複数ある場合は保留」を一般的には変更しない。

次の条件をすべて確認する。

1. 両レコードのWikidata作品IDが単一かつ同一である。
2. Wikipediaレコードの `extra.imported_bibliography_from.source_id`、`raw_sha256`、`raw_path` が対応するWikidata原レコードに一致する。
3. 両方の原データファイルの実際のSHA-256が、保存した原レコードのSHAと一致する。
4. Wikidataの原応答に対象QIDのentityが存在し、その `id` が一致する。
5. Wikipediaの原応答から `page_id` と `revision_id` が一致するページ・版を特定できる。レコードの取得IDに含まれる版IDも一致する。
6. 特定した原ページの `pageprops.wikibase_item` が同じQIDに完全一致する。記事名を記録している場合は原ページの題とも一致する。
7. 元書誌と派生書誌の全著者、原言語、作品種別、巻次、同じ言語の題が通常の整合性確認を通る。

この限定的な対応で確認済みの場合だけ、照合用のコピーからOpen Libraryの作品別名IDを外して判定する。原レコードや外部ID対応表は変更しない。コピー元のSHAを確認できない、ページ範囲が違う、書誌に矛盾がある場合は保留する。

`resolution_decisions.method` は `source_mapping`、`reviewer` はNULLとなる。履歴の `verified_source_mapping` には双方の原SHAと版IDを記録し、`human_verified=false`、`independent_identity_confirmation=false` とする。名寄せ信頼度に校正済み確率を作らない。

`DatasetDB.resolve_sources()` は、原レコードのheadやimmutable versionを変更せずに対応を再構築する。`undo(decision_id)` で取り消した対応は再構築でも復活させず、別経路の推移的な統合でも取り消した組を結ばない。

## 2004作品として数えられていた4候補

以下は新しい4作品の独立した確認ではなく、元の2000作品と対応づけられなかったWikipediaソースのノードだった。

| 作品 | Wikidata | 元の保留理由 |
|---|---|---|
| The Children Star | Q1031337 | 同じWikidata項目にOpen Library作品IDが2個 |
| Coasting / Navegação de Cabotagem | Q10335839 | 同上 |
| A Man Rides Through | Q104841212 | 同上 |
| Persian Letters / Lettres persanes | Q1050479 | 同上 |

ライブDBの読み取り専用backupで条件を確認し、コピー上の `resolve_sources()` で2004から2000作品へ戻ることを確認した。595件のWikipedia対応を `source_mapping` として保存できた。immutable source versionとpayload SHAは全件変わらなかった。処理時間は約10.97秒。この検証は実DB更新を意味しない。

## Open Library部分標本の固定選択

`data/openlibrary-prefix-v2/records.jsonl` の27作品・9著者を、`data/poc-v1/selected-sources.jsonl` の2000作品に限定して照合した。対象が分からないレコードを追加作品として採用しない。

- 21作品に元2000作品への明示的な外部作品ID対応があり、6作品は対象外。
- 14作品は全著者の外部authority ID集合と通常の作品整合性確認を通る。
- 7作品は保留。共同著者の集合不一致3、著者authority IDの未一致3、著者欠損1。
- 著者のpreferred labelが `1930-` だけのレコード、および出版社などの書誌文字列になっているレコードの計2件は隔離した。対応する作品は元2000作品の範囲外だった。
- 採用作品に直接関係する安全な著者レコードは4件。統合用入力は14作品と4著者の計18レコード。

全36レコードのRawSHAと元5列行を確認し、対応先のWikidata原SHAも確認した。作者の表示名を元Wikidataの一致するauthority IDから補完した箇所は、元の値、照合したID、コピー元RawSHAなどを `extra.selection_audit` に記録する。この補完とOpen Library属性の輸入を独立の裏付けには数えない。

統合用入力は `data/openlibrary-poc-selected/records.jsonl`。保留・不適切な著者は `quarantined-records.jsonl`、対象外・不要な著者は `outside-scope-records.jsonl` に分けた。判断一覧、固定入力ハッシュ、RawSHA検証件数は同じディレクトリのmanifestにある。

これは `partial_sample` の固定スナップショットの選択であり、未発見IDを完全ダンプからの欠落とは判定しない。freeze後に得たダンプ範囲は次のスナップショットで扱う。

## WorkInputの代表書誌

作品レコードを優先し、同じ作品ではWikidata、Open Library、その他、Wikipediaの順に選ぶ。原言語、原作品の初出年、地域、シリーズは、この順の作品レコードから最初の既知値を取る。版の言語、再刊年、出版地域、imprintは原作品のメタデータへ移さない。

著者は最初の著者を持つ作品レコードの完全な集合を採用する。authority IDによる別名対応を適用するが、別名を追加の共著者として数えない。題は作品レコードの順に、空値と取得IDそのものの不明な題を除いた最初の値を採用する。すべて不明なら代表レコードの値を残す。出典の異なる値と矛盾はfield assertionsとimmutable原レコードに保持する。

コピーDBに18レコードを投入しても2000作品を維持した。原言語、初出年、地域、シリーズの欠落は生じなかった。題の変更は、元の不明な `Q105956079` をOpen Libraryの `Crin-Blanc` で補完した1件だけだった。投入は約20.19秒。コピー検証の詳細は `data/openlibrary-poc-selected/copy-validation.json` に保存した。

## 実DBへの適用手順

書き込み担当が、ほかのDB書き込み処理の終了を確認したうえで実行する。

```python
from recommend_book.database import DatasetDB
from recommend_book.ingestion.core import read_jsonl

with DatasetDB("data/poc-v1/dataset.sqlite") as db:
    print(db.resolve_sources())
    print(db.ingest(read_jsonl("data/openlibrary-poc-selected/records.jsonl")))
```

その後、DBからWorkInputを再生成してからfreezeする。入力を再生成した後のハッシュを使い、以前の入力ハッシュを固定結果へ混ぜない。

対象テストは以下。初期の失敗、ページ・版・SHA・著者・言語・種別・巻次の保留、一般規則の維持、source_mappingの履歴・取消、作品書誌を版書誌で上書きしない条件を確認した。

```text
uv run pytest tests/test_database.py tests/test_resolution.py tests/test_normalization.py tests/test_cli.py -q
58 passed
```
