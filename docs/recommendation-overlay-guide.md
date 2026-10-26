# 分類差分の推薦用 overlay

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「現行ガイド・仕様」です。手順・契約の再利用前に、現在の状態は[current-status.md](current-status.md)、入力・権利・版の前提は本文で確認してください。

`apply_classification_overlay` は、旧分類済み SQLite の推薦行へ、更新した書誌 SQLite に対応する新分類結果を読み取り専用で適用する。返却値は `{'rows': [...], 'audit': {...}}`。SQLite は `mode=ro&immutable=1` で読み、DB・入力・分類結果を書き換えず、新 DB に分類テーブルを作らない。LLM の追加起動も行わない。

初版作成時は実差分分類 session `25985` が進行中で、実 overlay 適用は0回だった。2026-10-13に268件の実分類終了（exit0）とactual overlay独立照合、推薦v3生成を確認した。[実行記録](enrichment-classification-run-record.md)に実測件数・終了proof・CLI表記の事後訂正を記録する。以下は処理の契約であり、人手品質確認を示さない。

## API と CLI

```python
from recommend_book.recommendation.world import load_world_database
from recommend_book.recommendation.overlay import apply_classification_overlay

base_rows = load_world_database(base_database)['rows']
snapshot = apply_classification_overlay(
    base_rows,
    base_database=base_database,
    enriched_database=enriched_database,
    delta_inputs=delta_inputs,
    delta_manifest=delta_manifest,
    results=delta_results,
    run_manifest=delta_run_manifest,
    terminal_proof=delta_terminal_proof,
)
```

`recommend` CLI の既存 `--world-database` が `base_database` を指定する。通常の推薦入力・出力オプションに、次の **6 オプションをまとめて**指定すると overlay を使用する。一部だけの指定は拒否する。

| CLI オプション | API 引数・内容 |
| --- | --- |
| `--enriched-database` | `enriched_database`: 更新後の書誌専用 SQLite |
| `--delta-inputs` | `delta_inputs`: 変更作品だけの ordered JSONL |
| `--delta-manifest` | `delta_manifest`: 固定された差分準備 manifest |
| `--delta-results` | `results`: 終了した差分 run の results JSONL |
| `--delta-run-manifest` | `run_manifest`: 同 run の manifest |
| `--delta-terminal-proof` | `terminal_proof`: 実プロセス終了の観測記録 |

API 自体はメモリ内の snapshot を返すだけで、保存は呼び出し側が担当する。推薦 pipeline は既存出力を拒否し、新 namespace の staging へ JSON 等を保存する。公開直前にも元ファイルとコードの SHA を再確認し、成功時だけ rename する。

## 新旧 DB と差分の境界

旧分類済み DB と新書誌 DB のファイル SHA は意図的に異なる。旧 DB は分類テーブルを含み、新 DB は書誌追加を含むため、ファイル SHA の同一性を要求しない。それぞれを独立した raw SHA に結び、旧 `source_records` の全 immutable version・raw JSON・列値が新 DB にそのまま保持されることを SQL で確認する。

今回の準備対象は全 **2020 作品**、変更 **268**、不変 **1752**。変更内訳は同じ canonical ID の入力変更 171 と、代表 canonical ID の変更 97。件数をコードへ固定するのではなく、manifest・全入力・SQL 再構成から一致を確認する。

`canonical-crosswalk.source.json` の全 old canonical ID について、旧 work node 集合が完全に一致し、それらが新 DB 内に残り、同じ単一 new canonical ID へ対応することを確認する。全 2020 の対応は一対一で、分割・多対一結合・欠落を拒否する。97 の代表 ID 変更は機械的な source union の結果で、人手による同定品質承認を意味しない。

全 `input-comparison.json` は旧 ordered 順に、old/new ID・input hash・source refs・evidence 差分・変更判定を SQL 再構成と照合する。delta は変更 268 の **集合と順序が完全一致**する必要があり、漏れや不変作品の再分類を拒否する。各 WorkInput の immutable source version refs、権利許可、evidence、work/expression scope も DB 再構成の hash と照合する。

manifest の `old_inputs_sha256`、`new_all_inputs_sha256`、`new_database_sha256`、crosswalk/comparison SHA、delta の `ordered_inputs_sha256` と `work_inputs_sha256` を raw bytes で検証する。namespace の固定 `all-inputs.jsonl` は、旧順序で不変行の旧 bytes と変更行の新 producer bytes を組み合わせたものと完全一致する必要がある。その `all_inputs_sha256` は、新 producer 全入力の SHA とは別に保持する。

## 分類結果と terminal proof

現行 v2 validator と純粋な `build_run_config` の config が差分準備・run で一致することを要求する。10 artifact は manifest の SHA、現行コードの raw bytes、準備 namespace の `artifacts/<relative path>` に保存した raw copy の 3 者を照合する。

1. `config/sources.json`
2. `config/taxonomy.json`
3. `schemas/classification.schema.json`
4. `src/recommend_book/classification/prompt.py`
5. `src/recommend_book/classification/validation.py`
6. `src/recommend_book/classification/runner.py`
7. `src/recommend_book/models.py`
8. `src/recommend_book/jobs.py`
9. `pyproject.toml`
10. `uv.lock`

run は `selected == completed == delta 件数`、`failed == pending == 0`、`stopped == false` を要求する。整数項目に bool は受理しない。全 job key は入力 hash と config から再計算し、results の件数・順序・work ID・expression ID・model・CLI version を照合する。各 completed job の durable JSON と results index は同じ値でなければならず、引用・scope・権利を含む current validator の検証を通す。held が残る場合も適用全体を拒否し、旧分類へ fallback しない。

terminal proof は `schema_version='classification-terminal-proof-v1'`、UTC `observed_at`、非空 `session_id`、対応する `run_id`、`process_exit_observed=true`、非 bool 整数 **`exit_code=0`** が必須。実 handle の終了観測後に作る証拠であり、分類の人手正解を表さない。

`manifest_sha256`、`results_sha256`、`ordered_inputs_sha256` は対応する raw artifact と一致する。`execution_context_manifest_sha256` と `cli_profile_sha256_before/after` はいずれも固定 delta manifest bytes の SHA と一致する。ここでいう CLI profile はユーザー設定や認証情報ではない。今回の実proofは `post_run_cli_observation_correction` と明記し、before/afterは訂正contextの終了監査前後を表す。v2を実起動前に記録した証拠ではなく、元v1とactual run configのCLI表記差を隠さない。

proof の `artifact_sha256` に次の **4 source keys** が必須で、追加 keys は許容する。

| key | 対象 |
| --- | --- |
| `base_database` | 旧分類済み DB の raw SHA |
| `enriched_database` | 新書誌 DB の raw SHA |
| `canonical_crosswalk` | コピー済み crosswalk の raw SHA |
| `input_comparison` | 全入力比較の raw SHA |

読み取り開始・終了と公開前で protected artifact の SHA と run ファイル集合を照合する。途中変更、新規 run ファイル、WAL、namespace 外へ出る result path を拒否する。正当な相対 result path の `/` と `\` は意味的に同じファイルとして照合する。

## 行の置換と feature scope

変更行は新分類の features/provenance と新入力の表示 metadata、DB 内の明示 author/series authority IDs で **REPLACE** する。旧 labels を union せず、旧結果を新 ID や新 hash へ rebind しない。新結果が unknown または feature 空でも旧 positive を残さない。不変 1752 行は元の値と順序を保持する。変更行の私的 `rating`、`read_status`、`popularity` は保持する。

入力は原作品の `expression_id=None` に限定する。デフォルト work 推薦の feature は `decision='present'` かつ `scope='work'` のみ。`original`、`expression`、`translated` scope の文体などはこの work snapshot の feature から除外し、audit に数える。unknown/unreported は negative に変換しない。

数値 relevance は feature 強度に使用し、null relevance は明示 policy **1.0** にする。raw confidence は未校正のモデル値として provenance に保持するが、確率や重みとして使用しない。人手 gold や校正済み確率を補作しない。著者表示名と authority ID は分け、名前から ID を推定しない。

推薦 pipeline の新 namespace には有効な全 2020 行の `world-works.json`、`classification-overlay-audit.json`、`read-history-audit.json` と通常の推薦 artifact を保存する。overlay audit は source SHA、検証済み `old_to_new_canonical`、置換数・不変数・scope 除外・empty features、旧ラベル再利用なし、人手未検証、DB tables 書込みなしを記録する。

## 私的履歴の canonical ID 対応

root pipeline は overlay が SQL 検証した `old_to_new_canonical` だけを使って履歴 work ID を対応付ける。タイトルの類似による対応は行わない。元 ID、新 ID、source index と対応根拠 SHA を `read-history-audit.json` に保存し、元履歴 records を変更しない。この audit は私的履歴を含むローカル artifact として扱う。

異なる old/new alias の履歴が同一 canonical ID へ到達した場合、読書状態は `read` を最優先し、次に `unknown`、最後に全件 `unread` の場合だけ `unread` とする。unknown から未読を推定しない。rating は入力順の最初の既知値を既読除外用に保持し、平均・合成・推薦対象の観測 rating への転記を行わない。元 records と統合時の選択理由は private audit に残る。

## 検証状況

synthetic fixture で overlay 29 件、world adapter 21 件、派生 DB importer 47 件の合計 **97 件が成功**した。exit code 1・17・-1 の拒否は RED を確認してから修正した。全入力差分、SQL crosswalk、immutable source 保持、raw copy、durable result、末尾変更、unknown による旧 feature 除去、world projection との一致を確認済み。これは実 268 分類の完了確認や推薦品質評価ではない。

root の SQL overlay＋実 ranker の結合 fixture を含む pipeline 関連15件も成功した。旧IDだけの既読・未読・不明の履歴、旧新aliasの競合、異なる実評価の非合成、HTML出力中のsource変更検出と生成stagingだけの削除を検証した。native provenance保持とCLI表記差fixtureを含む2026-10-13のローカル本体テストは **973 passed / 48.56秒**。GitHub hosted CIは未実行。

2026-10-13の実適用では、root がlive runの終了コード0を観測し、事後訂正scopeを明記したterminal proofを作成した後、実overlay独立照合と推薦v3生成を行った。実SQLの全97変更IDを使うsynthetic履歴対応、評価の順位・指標不変、全source/output SHAを確認した。旧DB・prepared namespace・旧推薦出力は保持する。実本人履歴と人手品質は未検証であり、[実行記録](enrichment-classification-run-record.md)へ引き継ぐ。
