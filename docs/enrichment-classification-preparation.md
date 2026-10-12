# Open Library 補強後の変更作品だけを分類する入力準備

2026-10-13。`scripts/prepare_enrichment_classification.py` は旧2020件と補強後2020件を比較し、変更した WorkInput だけを新 namespace に保存する。準備は完了したが、この作業では実 Sol・Codex CLI・Web を起動していない。分類実行と旧結果への overlay は root と database 担当の別工程である。

## API と変更判定

```python
prepare_enrichment_classification(
    old_inputs, enriched_dir, output_dir,
    expected_count=2020, model='gpt-6.1-sol', cli_version='0.162.1',
    batch_size=5, concurrency=8, timeout=900,
    max_run_seconds=None, max_observed_tokens=None, stop_on_rate_limit=True,
) -> dict
```

manifest の filenames を確認し、補強後入力は `bibliographic-work-inputs.jsonl`、対応資料は `canonical-crosswalk.json` を使用した。`work-inputs.jsonl` と両方が宣言されていれば入力の曖昧さとして拒否する。各 manifest の SHA、入力件数・work-level expression、ID 重複、durable outputs 全SHA、SQLite sidecar 不在、crosswalk の完全一対一対応を照合する。

変更判定は `classification.validation.input_hash` による **WorkInput 全体**の差。title/authors/year、work_id、根拠・ライセンス・利用条件、source-version refs 等を含む。本文の増加だけを変更としない。crosswalk は新旧 snapshot の比較専用であり、旧結果の work_id/input_hash を新しい値へ書き換えない。旧 review pack・旧 full2020 importer も変更しない。

旧順序に沿って一対一 crosswalk で新作品を探し、変更行には新producerの行 bytes をそのまま使用する。unchanged は旧 ordered 行 bytes を保持する。新producerの serialization・並び順が旧入力と異なるため、source新全入力SHAと、旧順序で作る `all-inputs.jsonl` SHA は別に記録する。旧 immutable node IDs をcrosswalkで保持していても、代表ID変更作品には新ID・新hashの実分類が必要である。

既存 output、入力 tree/core tree 内への output、未知・分割・重複・欠落 crosswalk、expression変更、SHA不一致は拒否する。元tree inventoryとSHA、core10 bytes を公開直前に再比較する。UUID staging siblingから成功時だけ renameし、失敗時は自分の staging だけを削除する。

## 実生成

```powershell
uv run python scripts/prepare_enrichment_classification.py `
  --old-inputs data/production-inputs/poc-combined-v1-v1/ordered-inputs.jsonl `
  --enriched data/poc-openlibrary-enriched-v1 `
  --output data/production-inputs/poc-openlibrary-enriched-v1-delta-v1 `
  --model gpt-6.1-sol --cli-version 0.162.1 `
  --batch-size 5 --concurrency 8 --timeout 900
```

exit0、`prepared_not_started`。全2020件は **変更268 / unchanged1752**、代表ID変更97。同一IDの変更171件と合わせて268件を固定した。accepted work264件という収集件数とdelta件数を同一視しない。

| 差分 | 件数 |
| --- | ---: |
| source refsの値・順序が変更 | 264作品 |
| evidenceが変更 | 213作品 |
| 許可済みevidenceが増加 | 207作品 |
| authorsが変更 | 9作品 |
| titleが変更 | 7作品 |
| first_publication_yearが変更 | 3作品 |
| source refsに除去がある | 13作品 |

これはactive入力の差であり、旧 immutable SourceRecord を削除した意味ではない。DB builder の2675旧source versions保持監査を別に参照する。

変更268件の原言語は en127、ja86、de12、es9、unknown8、fr7、it7、pt4、ru3、zh3、hi1、ko1。言語不明を英語へ埋めていない。

変更範囲ではsource refs383→634、evidence/allowed1068→1275。増分207件は許可されたOpen Library subject。synopsis91、description305、bibliographic426、note57は件数不変、subject189→396。全2020範囲のallowed6657→6864、全入力のfalse evidenceは0件。既存Wikisourceの原文40excerptは変更範囲に含まれない。

## 非許可本文の独立した監査

`audit_enrichment_sources(prepared_dir, output_path)` は、固定済みnamespaceを変更せず、新しい別ファイルへSourceRecord→input→promptの監査を保存する。実出力は `data/production-inputs/poc-openlibrary-enriched-v1-delta-v1-source-audit.json`。prepared manifest SHAとaccepted-records SHAにbindする。既存raw URLの再取得は行わない。

採用SourceRecordは456件（work264/agent192）。raw保存record中のevidence328件はallowed216、false112。false112は `openlibrary-prose` のdescriptionであり、許可済みinputへの再付替え0、実保存promptへの混入0を確認した。取得条件等の非許可説明文の除外は、false Evidence を用いる専用fixtureでも検証する。actual sourceで112件がすべて取得条件文だったとは主張しない。

source versions2675→3131、既存producerのraw SHA検証1502filesは、SHA検証済みproducer manifest/proofへの参照として記録する。この準備工程でraw1502件の取得・再検証を繰り返した実績ではない。

## 実行contextと引き渡し

`artifacts/<relative path>` に sources/taxonomy/schema/prompt/validation/runner/models/jobs/pyproject/uv.lock のcore10原bytesを保存する。manifestと `execution-context.json` はrun_config、controls、各SHAを保持する。旧processing contextは入力provenanceとして残し、現行validatorの元実行証明へ読み替えない。供給CLI versionはsnapshot上 `supplied_unverified` と記録し、実起動時のCLI観測と照合する。rootは別工程で0.162.1を観測済みだが、準備script自身がCLIを測定した表記にはしない。

deltaごとの `prompts/<new-input-hash>.txt` を保存し、false Evidence除外・allowed Evidence全項目の完全継承・output_identity.input_hashを照合する。権利・ライセンス・credit・継承条件を削らない。比較資料にはold/new prompt SHAも保持する。batch5で実行時はrunnerのbatch wrapperを使うため、保存単作品promptは根拠境界とserializationの証拠であり、実batch stdin bytesそのものではない。

`input-comparison.json` は全2020件を旧ordered順に保存する。overlay向け必須keysは old_work_id/new_work_id/expression_id/old_input_hash/new_input_hash/changed/identity_changed/source_refs_changed/evidence_changed。canonical crosswalkの原bytesは `canonical-crosswalk.source.json` に保持し、両path/SHAをmanifestへ保存する。`all-inputs.jsonl` はnamespace固定pathで、`all_inputs_sha256` で検証する。

入力準備の「feature enrichment比較」は根拠のkind/source/rights/言語coverageまでである。新分類ラベルや推薦特徴の改善は未観測で、別の実Sol完了結果から評価する。旧分類をchanged hashに移植して改善件数を作らない。268件のchanged-only runなので、full2020 importを無理に流用しない。

## SHAと検証

| 対象 | SHA256 |
| --- | --- |
| 新DB | `3a4c7bd8b364631294fb737aca26c7e865197c1d990bed6a4003e49855283ff6` |
| 旧ordered2020 | `bfd575c1ec9da9e589f75f4deaa298ea875257c3de8b5ed82b23a85365ec472c` |
| 新producer全2020 | `87cd6aac6f96295ab2d058af57a0775f7a8e44c49f8dc468e03789f9128cdd4c` |
| delta268 | `5099df8aa72166dfefea1359c1c58f306330f32d5e7be470ee754a5a7c1dd5aa` |
| oldorderでunchanged旧bytes保持した全2020 | `2dfda4436146747f4fb7c92c384bd8578af086c5bda8ddace91e6c5613c8149f` |
| delta manifest | `a173570202eeed51ac5e36c1a5d86caf323d9eb08cda439ff05bf4de8a3c1a29` |
| 別source audit | `6d5615032cec45d18bffb35f5ef9d112177271971dabc49473cf19cf989f7660` |

TDDは未実装13failures→13passed、独立source audit未実装1failureとmanifest未記載SQLite sidecarの見逃し1failureを確認して修正。`uv run pytest tests/test_prepare_enrichment_classification.py tests/test_freeze_classification_execution.py tests/test_combined_production_inputs.py -q` は **41 passed**、専用15cases。入力tree/core10を変更せず、旧freeze・review・結果・runner/prompt/schema等への追加編集は行っていない。人手精度の確認ではない。
