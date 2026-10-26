# relevance v2のscratch観測

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「現行ガイド・仕様」です。手順・契約の再利用前に、現在の状態は[current-status.md](current-status.md)、入力・権利・版の前提は本文で確認してください。

> 本体への採用状況（2026-10-12）：この実装は本体へ適用し、本体の全テストで655 passedを確認した。以下のscratch限定・未適用・fixture限定の記述は隔離開発時点の記録である。実データのキャッシュ移行と実行条件の保存については、[採用記録](relevance-v2-development-verification.md#本体への採用と実データ移行)を参照。

この文書と実装は`data/development/relevance-v2`内の開発コピーだけを対象とする。本体・ライブrun・本番dataは変更も読取りもしていない。通信、Web、追加LLM、native Codexの起動は行っていない。scratchから本体への採用は別の統合作業である。

`scripts/summarize_classification_run.py`のread-only観測に、存在判定coverageと関連度availabilityの分離、およびvalidation policyのprovenanceを追加した。schema/input/config/job/durable結果等の既存の機械的照合は維持する。

入力のfreeze manifest照合は`work_inputs_sha256`、`pilot_inputs_sha256`、`ordered_inputs_sha256`のいずれかと、読み込んだ入力ファイルの正確なbytes SHAが一致することを要求する。ordered production入力にも対応し、明示したfreeze manifestまたは入力のsibling `manifest.json`から照合する。入力値を変えない空行追加でも固定SHAが変われば拒否する。一致したfield名は`frozen_manifest.matched_hash_fields`に記録する。この入力provenance照合は実行policyの承認・人手品質検証を表さず、元configと現在のvalidation version/hashの検証は別に保持する。

## 存在判定と関連度

present/absentはラベルの存在判定、relevanceはその関連度の数値である。relevanceがnullでも、present/absentの判定coverageは失われない。nullを0に置換して平均を下げたり、unknown判定へ変換したりしない。数値0は数値ありとして数える。

各集計の`relevance`に次を保持する。全体、軸、原言語、許可資料種類、許可資料深度で同じ定義を使う。

| 項目 | 定義 |
| --- | --- |
| `n_known` | 出力されたpresent/absentラベル数。両decisionを含む |
| `n_numeric` | knownのうちrelevanceが数値の数。0を含む |
| `n_null` | knownのうちrelevanceがnullの数 |
| `numeric_rate_among_known` | n_numeric / n_known |
| `null_rate_among_known` | n_null / n_known |
| `numeric_rate_all_taxonomy_slots` | n_numeric /（固定入力数×当該集計のtaxonomyラベル数） |
| `null_rate_all_taxonomy_slots` | n_null /（固定入力数×当該集計のtaxonomyラベル数） |
| `mean` / `min` / `max` / `p50` / `p95` | relevanceが数値のknownだけから計算 |

`n_known = n_numeric + n_null`。n_knownが0ならknown内の率はnull、数値が0件なら数値の統計はnullにする。taxonomy枠が非空なら全枠の率は0でもよい。全taxonomy枠の分母にはexplicit unknownとunreportedを残すが、そのrelevance nullを`n_null`へ加えない。`n_null`はあくまでknown-nullである。軸別の全枠分母はその軸のラベル数を用い、全体の72ラベル分母と混同しない。

例えば2入力×72ラベルの144枠で、present-nullが1枠、absent-0.25が1枠、explicit unknownが1枠、残りが未報告なら、存在判定は2/144、関連度数値ありは1/2、known-nullは1/2、数値あり全枠率は1/144である。関連度平均は0.25であり、nullを0にした0.125ではない。この例は構造確認用の合成fixtureで、人手goldではない。

HTMLは従来の存在判定・unknown・未報告の表と、関連度数値/nullの表を分ける。関連度の表にはknown/数値/nullの件数、known内の率、全taxonomy枠数と全枠率、数値のみの平均・中央値を表示する。JSONは数値統計の全項目を保持する。

## 元runのpolicyと今回の再検証

scratch validatorのpublic定数は`VALIDATION_VERSION='2.0'`。実際にimportした`recommend_book.classification.validation`のファイルbytes SHAを、その呼出し時に計算する。

新configの`validation_version`と`validation_hash`が両方ある場合は、定数と実ファイルSHAへの一致を要求する。不一致、片方だけの記録は拒否する。既存のtaxonomy/schema/prompt hashの照合も必要である。元configの値を今回の値で書き換えたり、manifest/job keyを再作成したりしない。

| provenance | 意味 |
| --- | --- |
| `config` / `config_sha256` | 元runに記録された分類設定とそのcanonical SHA。変更しない |
| `original_run_validation.status = recorded_artifacts_match_current` | 元configに2項目が記録され、現在の実artifactに一致した |
| `original_run_validation.status = unrecorded_legacy_policy` | 元configに2項目がない。元実行のvalidation policyは記録不足 |
| `observation_revalidation` | 今回のread-only observerが実際に使ったversion/hash/pathと検証結果数 |

`observation_revalidation.status`は`validated_now_read_only`。今回の観測で現在のvalidatorを使ったことを表し、旧分類時に新policyが実行されたことを意味しない。`original_execution_policy_independently_verified`はfalseのままとする。記録が一致する新configでも、過去の実行経路をこのobserverだけで独立証明したことにはしない。validatorファイルが観測中に変わった場合も拒否する。

旧configのrunを新validatorでread-only再検証することと、terminal cacheを新namespaceへmigrationすることは別の処理である。本observerはcacheを移行しない。migration担当が持つ`migration-provenance.jsonl`や候補台帳を複製・自動探索しない。元runのmanifest/index/durable SHAは既存provenanceに保持する。migrationの元ソース・attempt・usage・台帳整合性は、migrationコマンド側の検証対象である。

## scratchからの実行

scratch projectをcwdにして、importがscratch自身を向くことを先に確認する。

```powershell
Set-Location C:\Users\yuta\Desktop\Private\recommend-book\data\development\relevance-v2
uv run --offline python -c "import recommend_book.classification.validation as v; print(v.__file__); print(v.ROOT)"
uv run --offline pytest tests/test_classification_summary.py tests/test_classification_stability.py -q
```

確認したimportは`data/development/relevance-v2/src/recommend_book/classification/validation.py`、ROOTもscratch自身だった。テストに同じ経路制約を持たせている。本体のeditable installを使った誤ったGREENを避けるため、別cwdから本体pytestだけを実行した結果をscratchの検証に数えない。

将来、隔離した固定入力と完了済みfixture/runを明示して観測する場合は次の形で実行する。本開発では実productiondataを使っていない。

```powershell
uv run --offline python scripts/summarize_classification_run.py `
  --inputs <isolated-fixed-inputs.jsonl> `
  --run <isolated-completed-run-directory> `
  --output <new-observation-directory>
```

既存出力の上書きは拒否し、JSON/HTMLを新規ディレクトリに生成する。HTMLのタイトル・quote・summary等のescapeも維持する。

## TDDと未検証事項

最初に関連度集計がないための`KeyError: relevance`を確認した。その後、known-null/数値0を受理したJSON観測に対してHTMLに該当指標がないREDを確認し、表を実装した。テストはknown present-nullとnumeric0で存在判定coverageが変わらないこと、null除外平均、explicit unknown/unreportedを残した全枠分母、軸別分母、knownなしのnull率、旧configと今回policyの分離、新policy不一致・片方記録の拒否、元ファイル不変、HTML表示を確認する。

人手goldは0件、`human_quality_verified=false`。precision/recall/F1、semantic correctness、relevance accuracy、calibration、ECE、Brierはnullのまま。関連度を数値として出力できる割合は観測できるが、その関連度が作品に対して正しいかは未評価である。この変更に対する本番runの再観測・cache migration・人手評価は実行していない。
