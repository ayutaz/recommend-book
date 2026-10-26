# CoNSSAとmain2kのタイトル一致2候補：オフライン書誌照合

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「履歴・結果・計画・スナップショット」です。本文の件数・計画・実行状態と、本文内の「現在」「最新」「実行中」は記録日時のものとして扱い、現在の状態は[current-status.md](current-status.md)を参照してください。

2026-10-12。保存済みCoNSSA TSV、native fixture、curated reference、main2k書誌入力、Wikidataの作品・著者rawだけを照合した。追加Web/LLM取得は0、モデル結果の参照も0。元source/raw/frozen/curatedは変更していない。

**結論は2件ともhold、evaluation_readyは0件。** 作者・作品authorityの一致は同一作品への支持であり、独立した人手同定、注釈された版・表現の一致、taxonomyラベルの意味確認を済ませたことにはしない。

| native ID / 作品 | main work / Wikidata | 一致する支持 | 保留 |
| --- | --- | --- | --- |
| ne0157 / Pequeñeces | work_53f4538fb92d727d0242c53c / Q112262940 | 作者Luis Coloma / Q4228309、作者VIAF 59211037・BNE XX843538、作品VIAF 176389595・BNE XX1932239が一致。 | CoNSSA年1891、WD P577/main年1890。注釈対象版・expression不明。 |
| ne0175 / El niño de la Bola | work_7afe3c1d47f1a6b247a8cc3e / Q106963817 | 大文字小文字を除く作品名、作者Pedro Antonio de Alarcón / Q313553、作者VIAF 44294383・BNE XX920308が一致。 | CoNSSA年1880、WD P577/main年1884。CoNSSA作品VIAF 312335351に対応するWD P214なし。CoNSSA作品BNE空欄、WD側はXX5897369。共通作品authorityと版・expression未確認。 |

CoNSSAの作者VIAFはTSVで`.0`付きの数値文字列なので、その末尾だけを除去して比較した。作者名、出生・死亡年も保存済みWD著者rawと整合する。年の差を初版と再版、連載と単行本の差と説明できる可能性はあるが、保存資料ではその理由を確認できない。元の年を補正しない。WD P577のreferencesにはBNE識別子やURL、Open Library IDがあるが、その書誌参照先の内容は本作業では取得・検証していない。

main側は2件とも作品型（P31 literary work / P7937 novel）、P407はSpanish=Q1321、P1476はスペイン語の題名。CoNSSA READMEはスペイン語のコーパスと説明する一方、当該TSV行に明示的な言語・翻訳欄はない。mainのexpression_idはnullで、入力は書誌情報とdescriptionだけ、原著本文を含まない。CoNSSA側のdigital.sourceはne0157=`#bvmc`/HTML、ne0175=`#archive`/PDF、どちらもdigital.source.idnoは空欄。この情報では注釈対象の版・翻訳・本文をmain入力に対応させられない。作品authorityのID自体のwork/edition scopeも独立に検証していない。

## 180作品の参照セットとの関係

先のタイトル一致2件は、**269件のnative候補**を基準に数えたもの。180作品・200positive-only labelsのcurated referenceに実際に入るのは、ne0157の`protagonist.adult` **1作品・1ラベルだけ**である。ne0175はnativeの`young`/`worker`等が厳密に対応せず、mapping-auditのmapped_labelsは空で、180作品goldに含まれない。`young`をchildへ置換するなど、新しいラベルは付けない。

ne0157も既存の人手由来metadataを直接対応した`curated_reference`であり、新taxonomyの独立人手goldではない。関連度・信頼度はnullのまま。今回ラベルを評価せず、LLM成果・人手taxonomy goldへ転用していない。nativeの年齢判定をmainの書誌情報からコピーしたものではないが、完全なsource-chain漏洩検査も完了していない。

## 保存物と次に必要な確認

- [candidate-crosswalk.jsonl](../data/reference/conssa-main-crosswalk-v1/candidate-crosswalk.jsonl)：2候補のauthor/work ID、言語、年、scope、hold理由、原物SHA。
- [review-note.json](../data/reference/conssa-main-crosswalk-v1/review-note.json)：参照対象の実数とhold結論。
- [manifest.json](../data/reference/conssa-main-crosswalk-v1/manifest.json)：読んだ入力のSHA、WD原物4件のSHA、出力SHA。
- [prepare.py](../data/reference/conssa-main-crosswalk-v1/prepare.py)：保存資料のみの再現コード。完成snapshotは上書きしない。

必要なのは、CoNSSA year欄の意味と注釈対象版の特定、年の差を解く書誌資料、明示的な原文・翻訳scope、独立した人手による同定である。ne0175は共通作品IDまたは書誌タイトルレコードの確認と、未対応nativeカテゴリの別途検討も必要。現段階では対応確定0件、評価投入0件、追加人手gold0件。

CoNSSA metadataのCC-BY-4.0 attribution/changesをcrosswalkに保持した。WD API metadataはCC0。本文利用・世界全体でのpublic domainを本照合によって承認していない。
