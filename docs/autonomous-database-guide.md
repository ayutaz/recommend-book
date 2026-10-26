# 自律PoCの派生DBと分類入力

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「現行ガイド・仕様」です。手順・契約の再利用前に、現在の状態は[current-status.md](current-status.md)、次の作業順序は[現行計画 v25](autonomous-next-phase-plan-v25.md)を参照し、入力・権利・版の前提は本文で確認してください。本文のCLI例・数値・測定値は記載した版・namespaceの再現用です。

`scripts/build_autonomous_database.py` は通信・LLM起動を行わず、新 namespace に書誌 DB、DB 再生成入力、元 run ごとの分類 DB、保留証拠を保存する。元 DB・immutable source version・固定入力・旧実結果・分類 core・設定を変更しない。

## 二段階の成果物

`build` の `dataset.sqlite` は更新後の**書誌専用 DB**。`baseline-classified.sqlite` は既に終了した旧分類と差分分類から再構成した全2020分類 DB。両者の入力が変更作品で異なるので、更新書誌 DB を分類完了と呼ばない。`finalize` は補充で変わった入力だけの新分類が終了してから、別 namespace に全2020分類済み `dataset.sqlite` を作る。

```python
from recommend_book.autonomous_database import (
    build_autonomous_database, finalize_autonomous_database,
)

built = build_autonomous_database(
    baseline_overlay={
        'base_database': old_classified_db,
        'enriched_database': frozen_enriched_db,
        'delta_inputs': fixed_delta_inputs,
        'delta_manifest': fixed_delta_manifest,
        'results': terminal_delta_results,
        'run_manifest': terminal_delta_manifest,
        'terminal_proof': observed_delta_proof,
    },
    accepted_source_records=accepted_records,
    accepted_inputs=accepted_proposals,
    accepted_ledger=accepted_ledger,
    author_role_audit=ndl_role_audit,
    output_dir=new_build_namespace,
)

final = finalize_autonomous_database(
    namespace=new_build_namespace,
    run_dir=terminal_changed_run,
    execution_context_manifest=fixed_execution_manifest,
    migration_manifest=cache_migration_manifest,
    terminal_proof=observed_changed_proof,
    cli_profile=fixed_execution_manifest,
    scope_quarantine=verified_scope_quarantine,
    output_dir=new_final_namespace,
)
```

新しい output のみを受理する。入力ディレクトリ内への出力は拒否し、確認済み output 親直下の UUID staging を使う。SQLite backup と書込みは単一ローカル writer、分類表の追加は transaction 内で行う。整合性、foreign key、原書誌テーブルの全値 signature と protected SHA を確認し、公開直前に再照合して rename する。失敗時は自身が作った UUID staging だけを削除する。

## 元2020分類の再構成

既存 [overlay 契約](recommendation-overlay-guide.md)で SQL crosswalk、全入力比較、current v2 validator/config、10 artifact と raw copy、ordered results/durable jobs、終了コード0の terminal proof を確認する。旧分類済み DB からは最新入力と hash・source refs が完全一致する1752行だけを取得し、残る268行は新 canonical ID に対する実差分結果を使う。97代表ID変更はすべて新差分側であり、旧結果の ID/hash 付替えを行わない。

結果 JSON・モデル数値・job key・元 run ID/context/proof を保存する。各 run の `classification_runs` 件数は**元の実 run 全体の歴史**であり、snapshot 採用部分の件数ではない。`baseline-classification-run-coverage.json` または最終 `classification-run-coverage.json` が元 selected/completed/failed と snapshot 採用数を対比する。全2020を一回の新実 run と偽装しない。

既存 importer の6 DDLを利用して `classification_inputs/source_refs/input_evidence/labels/label_evidence` を書誌 DB に結ぶ。source refs は immutable version、payload/raw SHA、work/edition node を持ち、全 input evidence は DB の `source_evidence_keys` に結ぶ。完成時に既存 `load_world_database` が全 input hash、ラベル、scope、引用所属、権利、refs を再検証する。feature だけの `world-works.json` を分類 DB と呼ばない。

## Evidence-only の immutable 新版

採用入力3ファイルは一括で指定する。

- `accepted-source-records.jsonl`: 既存 active source の同じ `(source,source_id)`。`evidence` の末尾追加以外の全値、元 evidence の値と順序、書誌、identity、extra、元 raw SHA/path、record rights を保持する。
- `accepted-inputs.jsonl`: 元 source refs を保持した提案。追加 evidence の正確な集合を確認し、最終 source refs と evidence の順序は DB 再生成値を別ファイルへ確定する。提案を上書きしない。
- `accepted-evidence-ledger.jsonl`: target old version、work/evidence ID、URL/locator、追加 raw path/SHA、用途別権利、identity/screening 証拠を持つ。

Wikidata 書誌の raw bytes は同じでも、追加 evidence により新版 `payload_sha256` は変わる。元 `raw_sha256` は元 WD 原物のまま、追加 Wikipedia raw の SHA/path は Evidence attribution と ledger に別途保持する。record の WD CC0 は書誌の範囲であり、外部テキストを CC0 にしない。追加 prose は `wikipedia-screened-prose`、CC BY-SA 4.0 とその URL、帰属・share-alike・変更表示・third-party screening 条件を evidence 自身に保持する。

ledger の exact target `(source,source_id,old_version_id)`、単一 WD work QID、WD raw entity、Wikipedia raw pageprops QID/page/revision、言語・revision permalink と SHA を照合する。用途6件 `acquire/local_storage/external_llm/commercial/display/redistribution` は registry の policy states と一致し、conditional 条件がすべて fulfilled、`usage_permitted` が bool trueでなければ採用しない。許諾未確定本文や旧 Open Library 拒否本文を再許諾しない。この検証は自動 screen の完全な法律審査を主張しない。

`DatasetDB.ingest` は元 immutable rows を保持し、新版 parent を元 head へ結ぶ。全 work node の canonical/resolved 対応を前後で完全比較する。新規 authority merge や AI 合意による決定を追加しない。`source-version-changes.json` に親・新版・追加 evidence と `undo_ingest(new_version_id)` による逆操作を記録する。fixture では undo 後の元入力 hash 復元も確認する。

## 確定入力と差分分類

`new-all-inputs.jsonl` は DB 再構成による全2020行、`changed-inputs.jsonl` は baseline との full WorkInput hash 差だけである。分類実行には同じ差分をコピーした `classification-inputs/ordered-inputs.jsonl` と sibling `manifest.json` を使う。この sibling は正確な raw input SHA と新書誌 DB SHA を持ち、既存 execution preparer/importer の契約へ接続できる。

`pending-classification.json` は changed ID/hash と未完了状態を明示する。changed 結果がない間は旧結果へ fallback しない。`finalize` では全変更行・固定順序・current config/10 raw copies・durable/index・model/CLI・引用/scope/refs を再照合し、`pending=0`、`stopped=false`、全 completed、実終了観測 `exit_code=0` を要求する。held や failed を成功へ変換しない。不変行のみ元結果を保持し、変更行は新実結果へ置換する。

## 未解決と NDL author-role 保留

`unresolved-holds.json` は元26未解決 canonical と review queue を sidecar へ保存する。名寄せ400ペアの AI 合意は authority や human merge として取り込まない。

`author_role_audit` は元 DB SHA、全 unresolved NDL immutable version、raw SHA、derived contributors、work nodes と exact role field 証拠を照合する。現対象26件のうち16件は `dc:creator` の訳者明記と、汎用 `dcterms:creator` authority の author 推定が混在する。全26件が unresolved で、2020入力や推薦 author IDs への投影は0。汎用 creator を確定 author authority として採用せず、今回 corrective version を追加しない。

`author-role-audit.source.json` は元監査の raw copy、`author-role-ambiguities.json` は採用禁止と機械的証拠を持つ。build/final manifest の SHA・ambiguity count・resolved ambiguous count0に結び、final namespace にも保存する。resolved ambiguous source が入る場合は処理を拒否する。将来の役割訂正には別の原物対応と入力差分審査が必要。

## CLI

```powershell
uv run --frozen --offline python scripts/build_autonomous_database.py build `
  --base-database <old-classified.sqlite> --enriched-database <enriched.sqlite> `
  --delta-inputs <ordered-inputs.jsonl> --delta-manifest <prepared-manifest.json> `
  --delta-results <results.jsonl> --delta-run-manifest <run-manifest.json> `
  --delta-terminal-proof <terminal-proof.json> `
  --accepted-source-records <accepted-source-records.jsonl> `
  --accepted-inputs <accepted-inputs.jsonl> --accepted-ledger <accepted-evidence-ledger.jsonl> `
  --author-role-audit <role-audit.json> --output <new-build-namespace>

uv run --frozen --offline python scripts/build_autonomous_database.py finalize `
  --namespace <new-build-namespace> --run-dir <terminal-run-dir> `
  --execution-context-manifest <fixed-context-manifest.json> `
  --migration-manifest <migration-manifest.json> --terminal-proof <terminal-proof.json> `
  --cli-profile <fixed-context-manifest.json> --scope-quarantine <work-scope-quarantine.json> `
  --output <new-final-namespace>
```

値は供給済みの固定 artifact を指定する。CLI version を探索・起動して測定しない。changed が0の場合のみ finalization の run 関連5オプションを省略する。

fixture の検証コマンド:

```powershell
uv run --frozen --offline pytest tests/test_autonomous_database.py tests/test_recommendation_overlay.py tests/test_world_recommendation_adapter.py tests/test_import_classification_database.py -q
```

実 build/finalize の件数・SHA・実行時間は各 namespace の manifest と納品報告で確定する。新分類結果が到着する前の fixture 成功を、実2020最終分類の完了や人手精度承認に置き換えない。

## 初回実 build の停止とメモリ修正

初回 native session `87725` は読み取り・復元段階で、元 run の migration JSON 1,753,745 bytes と provenance JSON 1,617,910 bytes を1752作品ごとに再 parse する問題が判明した。builder child PID `119692` の working set は観測時 1,523,757,056 bytes。新 namespace 未公開の状態で、その builder だけを明示終了した。同 handle の observed exit は **1** で、成功終了ではない。

元 baseline 16 artifact と採用 WP/元 WD raw 48 unique artifact の SHA は終了後も一致した。履歴は [build-attempts-v1.json](../data/autonomous-poc-v1/database/build-attempts-v1.json) に保存する。root の分類 run やダウンロードを停止・再起動していない。

30作品・2 MB 元 provenance の memory fixture は修正前 peak 120,413,597 bytes で RED。元 run metadata を run 単位で一度 decode して共有し、入力も一括読取りする修正後は peak 16 MB 未満の条件で GREEN。元 JSON 値、run/context/proof、モデル数値を保持する。再 build の observed working set と最終状態は後続 attempt に記録し、fixture の peak と実プロセス working set を同じ測定値として扱わない。

## 実 bibliographic build の到達点（2026-10-13）

再 build session `2078` は実 exit `0` を同 handle で観測し、`data/autonomous-poc-v1/database/build-v1` を公開した。開始06:02:01 JST、公開ファイル時刻06:07:32 JST、約330秒。child PID18600 の working set 観測値は362,135,552 bytesで安定した。これはサンプル値であり peak の主張ではない。

再現 command（モデル起動・通信なし）:

```powershell
uv run --frozen --offline python scripts/build_autonomous_database.py build `
  --base-database data/poc-combined-classified-v2-v1/dataset.sqlite `
  --enriched-database data/poc-openlibrary-enriched-v1/dataset.sqlite `
  --delta-inputs data/production-inputs/poc-openlibrary-enriched-v1-delta-v2/ordered-inputs.jsonl `
  --delta-manifest data/production-inputs/poc-openlibrary-enriched-v1-delta-v2/manifest.json `
  --delta-results data/runs/poc-openlibrary-enriched-v1-delta-v1/results.jsonl `
  --delta-run-manifest data/runs/poc-openlibrary-enriched-v1-delta-v1/manifest.json `
  --delta-terminal-proof data/evaluation/poc-openlibrary-enriched-v1-delta-v1/terminal-proof-v1.json `
  --accepted-source-records data/autonomous-poc-v1/enrichment/final-v1/full/accepted-source-records.jsonl `
  --accepted-inputs data/autonomous-poc-v1/enrichment/final-v1/full/accepted-inputs.jsonl `
  --accepted-ledger data/autonomous-poc-v1/enrichment/final-v1/full/accepted-evidence-ledger.jsonl `
  --author-role-audit data/autonomous-poc-v1/resolution/role-audit.json `
  --output <NEW-build-namespace>
```

公開済み `build-v1` は上書きしない。新 evidence は79作品/81根拠、新 source version は3131→3210。元 canonical2020、未解決26、NDL ambiguity16を保持し、26は2020入力へ投影されない。`baseline-classified.sqlite` は authentic2020 completed/3268 labels/3678 evidence refs、1752旧分類＋268実 delta分類の元 run provenance を保持する。新 `dataset.sqlite` は新書誌・根拠だけで classification table がなく、変更79の分類結果が来るまで最終分類DBではない。

固定値:

| Artifact | SHA256 |
|---|---|
| `build-v1/manifest.json` | `38fbe9af109c7b481509fec56a4feaa145b2e8fff0db6c47cdab5462c40cfa17` |
| `build-v1/dataset.sqlite` | `4872a51e6600ade4c4af577eded814da9849e420086644f9974255c4012bfc81` |
| `build-v1/new-all-inputs.jsonl` | `e757053d93778b5d565287b8d389c7bcd75e92ecd130b64625dab296d0a8c255` |
| `build-v1/classification-inputs/ordered-inputs.jsonl` | `fb43815e9880b4384275115786cc3015a84f8ea1cf46b354873468cdfea422c5` |

builder自身はその後、下記の承認済み eligibility finalizer を追加した。build時 implementation SHA は元 manifest と最終 `build_implementation_source_sha256` に残し、最終処理の現 module SHA を別途 `source_sha256` に固定する。classifier core10・その他原物・入力・DBのSHA保護は緩めない。元 build へ新機能の結果を遡及記入しない。

## 歴史分類と推薦対象のscope隔離

`finalize_autonomous_database(..., scope_quarantine=path)` / `--scope-quarantine` は、[work-scope-quarantine.json](../data/autonomous-poc-v1/enrichment/work-scope-quarantine.json) を元 enriched DB SHAとbuild provenanceへ束縛する。全 historical canonical の active Wikidata source を保存原物から再走査し、非deprecated P31 `Q3331189` の全該当集合と台帳が完全一致すること、他12作品の非WD区分、source/version/QID/canonical、raw SHA、claim index、各property claim/locator、旧 normalized 値、全 active source versions、class原物のlabelを検証する。欠落・重複・不正count・未知version・deprecatedだけの根拠・raw変更を拒否する。同raw batchは走査内で一度decodeして共有する。

新 final DB の追加tableは `autonomous_scope_audit`（完全監査JSON/hash/count）と `autonomous_work_eligibility`（全historical works、eligible、source_version FK、raw SHA、P31 locator、理由・証拠JSON）。既存 authority graph、元 immutable source、分類6tableのモデル数値とscope/refは変更しない。原文/翻訳scopeやconfidenceの既存ルールも維持する。15 canonical を `source_work_scope_conflict_hold` として候補から隔離し、canonical全体が必ず版だと断定したり、他sourceのwork主張を破棄したりしない。

実 readonly再検証は全2020、WD2008＋非WD12、15 scope conflict、候補2005、unique raw66、SQLite integrity `ok` / FK違反0。これは原物に対する機械照合であり人手品質評価ではない。WP79との重複0なので分類79入力を変更しない。現時点では79新runの最終結果未到着で、2005件を最終DBとして公開したという意味ではない。

推薦は `load_autonomous_world_database(path)` を使う。まず既存 `load_world_database` で全歴史分類2020の入力/hash/source refs/evidence/scopeを監査し、次に追加2table・全work coverage・埋込みJSON SHA・source version FK・原物のP31証拠を再検証して、eligible rowsだけを順序を保って返す。table欠落/不一致はfail closed。元 `load_world_database` は歴史2020を返すため、利用候補の入口として使わない。

共通auditは `historical_work_count`, `eligible_work_count`, `scope_quarantined_count`, `scope_quarantine_verified=True`。他のworld adapter件数はhistorical scopeを明記し、`eligible_empty_features` / `eligible_works_with_features` を別計算する。最終 manifest はhistorical/eligible件数を分け、`work_count` と `completed` は分類履歴件数、推薦deliveryの `work_count` はeligible pool件数を表す。旧 source/raw・台帳 raw copy・全分類履歴を検証可能なまま保持する。
