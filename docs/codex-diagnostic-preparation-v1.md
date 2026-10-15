# Codex 診断 STEP3: 保存済み保留からの局所 task packet 準備

2026-10-15。STEP3 の参照固定だけを実行した。採用する出力は `data/autonomous-next-v3/evaluation/phase5-codex-diagnostic-preparation-v3/`。classification **20 works / 74 labels**、identity **3 pairs**。全 packet の診断状態は `notrun`、`execution_allowed=false`。STEP4 の診断・再分類・同定・採用、DB 更新、外部取得、モデル呼出は実行していない。本人の追加 annotation は 0 件で足りる。

## 固定された選択規則

実装 version は `codex-diagnostic-preparation-v1`、選択 algorithm は `held-repair-priority-lexical-v1`。`config/quality-evaluation-v1.json` の main DB path を読み取り専用で使用する。作品・source 全体の再走査、DB 全体の hash、旧診断全件の再生成はしない。

分類は `world-main-quality-review-v1/adoption-review.json` の既存 `held` 25 件だけを repair 候補にする。`evidence_subject_scope_mismatch` を優先し、次に `unresolved_version_scope_conflict`、同順位は `work_id` と `job_key` の辞書順で固定する。重複 work は拒否し、先頭 20 works を採る。結果は subject/scope mismatch 9 works、version scope conflict 11 works。付随する既存 semantic uncertainty 3 件も元 finding のまま参照する。残る 5 held works は cap 外であり、解決済みという意味ではない。

`uncertain` のみの 321 findings は新たな材料のない意味判定反復になるため再レビュー対象にしない。既存 observations の正確な JSON pointer と値 hash を各 packet に結び付ける。cached synopsis expansion の既存 work review と一致する場合も pointer/hash のみを付す。旧 finding の理由や source text を新しい生成入力に詰め直す処理はない。

同定は `resolution/v2/audit.json` の `same_candidates` のうち `held` 3 pairs を case ID 順で固定する。上限 40 pairs を埋めない。unordered source-version pair または case ID の重複は拒否する。既存 binding 74 件は再レビューしない。NDL unresolved 26 records は対を持たないので架空の pair にしない。v3 supplementary type audit は既存文脈として hash を固定し、generic class を根拠にした追加保留や新判定を作らない。

| 同定 case ID | 既存保留の理由 |
|---|---|
| `pair_67b1ede8f868893c1ee8f4ee` | exact raw Wikidata ID が異なる |
| `pair_8b37ef1f3f4fbe17b41f7c45` | 異なる QID 間の明示的同一性資料がない |
| `pair_e48f9f7d4710a6f7f3b6f6b3` | 異なる QID 間の明示的同一性資料がない |

## 新しい task と旧レビューとの差

各 packet は既存 verdict を変更せず、次に確認すべき **repair prerequisite** を明記する。subject mismatch は「どの evidence ID が別 subject を指し、どの source-version/work mapping を訂正する必要があるか」、version conflict は「原作品・翻訳・adaptation・edition のどの authority assertion / locator が不足し、どんな回帰テストで誤った work-level 継承を止めるか」を問う。同定は「正確な source versions の work / expression / edition 間に必要な明示的 authority 関係と、不足時に title/author/AI same を拒否する fixture」を準備する問いである。同じ保存資料から同じ label または same/different を再判定する依頼ではない。

これは将来の STEP4 の問いと受入条件を局所資料に固定する工程であり、その問いの回答自体もまだ生成していない。再分類は変更された evidence / corrected inputs を必要とし、同定 verdict は新たな明示的 authority proof を必要とする。既存 unknown / held、missing axes は保持し、unknown を absent / 負例に変えない。

## 固定された参照と利用条件

`classification-task-packets.json` は旧 finding / observation の artifact path、JSON pointer、値 SHA、work ID、expression ID、job key、run ID、input hash を持つ。各対象 label の元 scope / decision、label 値 SHA、taxonomy の definition / include / exclude 等、evidence ID / locator と元 label 内参照を固定する。source version の payload/raw SHA、原 classification source scope / work nodes / edition IDs、現在の work node / canonical / resolved を保存し、古い mapping と現在の binding を混同しない。

`identity-task-packets.json` は旧 finding、両 side source versions、raw files、既存 bibliographic metadata 内の claim locator / 値 SHA、work と expression/edition の未確定性を固定する。旧 `AI same` は authority ではなく、`authority_merge_approved=false` を維持する。旧 identity 原物監査の actor は mechanical のままであり、人手同定へ昇格させない。

`source-ledger.json` の **37 files** は利用した保存レビュー・taxonomy / policy・局所 raw・実行 code/test だけを hash する。DB は `mode=ro&immutable=1` / `query_only=ON` で開き、選択 job / source / work の **112 keyed row bindings** を値 hash で固定する。DB 全体や世界母集団の鮮度・完全性を保証するものではない。原本文、quote、title、私有 335 件履歴は新 packet にコピーしない。

各 packet の `external_llm_blockers` は次を具体的に保存する。

- 新しい repair 問いに対する purpose-specific `external_llm` UsageGuard admission がない。今回の packet は `local_storage` の参照資料である。
- 分類は packet 固有の subject/version scope reason codes、同定は元の exact QID conflict / missing equivalence が未解決である。
- 将来の新入力ごとに page/revision/authority identity、work / expression / edition scope、inherited restrictions と操作別利用条件を検証する必要がある。既存 `llm_input_allowed` や rights policy ID だけで新操作を許可しない。
- 変更されていない意味判定の反復は許可対象にしない。

UsageGuard 自体の変更・迂回はない。provenance は `llm_or_silver` の診断 namespace、`human_gold=false`、precision / recall / F1 / probability calibration の品質測定値はない。

## 成果物と再検証

```powershell
uv run --offline pytest tests/test_diagnostic_preparation.py -q
uv run --offline python scripts/prepare_codex_diagnostics.py
uv run --offline python scripts/prepare_codex_diagnostics.py --verify --manifest-sha256 deea5b26f2c4fe4579abcc592bc8acda2892b35b75019ac3aa49478d1bd2b3dd
```

TDD の初回実行は module 不在で RED。その後、cap・選択順・uncertainty-only 除外・duplicate 拒否・notrun/provenance・元 file 変更・task 改変・DB source row 変更・原文非コピーを検証した。追加の adversarial tests は、task / contract / source を書き換えて manifest も再計算する攻撃、ledger digest 不整合、source/output/database の workspace 外 path、table/key SQL identifier 改変、output directory escape を対象に RED→GREEN を確認した。最終結果は **18 tests passed**。

verifier は原生成時の receipt に保存した **外部 pin** を `--manifest-sha256` で必須とする。`--verify` 単独は拒否する。候補 manifest 自体から今計算した digest を信頼する自己認証ではない。receipt / この文書の既知の値を別途信頼できる履歴から取得することが前提であり、攻撃者が差し替えた manifest とその新しい digest の両方を承認すれば元 bundle の認証にはならない。

原生成直後の tool output に記録した pin と確認結果のコピーを、bundle 外の `data/autonomous-next-v3/evaluation/receipts/phase5-codex-diagnostic-preparation-v3.receipt.json` に上書きせず保存した。このコピーも電子署名ではなく、作成時の tool history / 信頼できる文書履歴と一緒に保持する receipt である。

最初に manifest bytes と外部 pin を比較し、manifest の固定 4 output filenames、明示的 `source_ledger_sha256`、各 output digest を検証する。同じ読み取り bytes を parse する。source path はすべて `ArtifactLedger.add` を通して resolve した workspace 内に限定し、output files も output directory 内に限定する。DB の table/key は固定 4 組 whitelist と静的な quoted SQL だけを使用し、ledger の文字列を SQL identifier に補間しない。contract と packet counts も確認する。その後、元 file の hash、選択 DB row の hash を再読取する。

最終 v3 の実 preparation と、上記の原生成時 pin を指定した verifier は Windows native / offline で終了コード 0。`verified=true, status=notrun, files=37, row_bindings=112` を確認した。既存 namespace の上書きを拒否する。

最初の draft は `phase5-codex-diagnostic-preparation-v1/` に残した。新しい問いと具体的 blockers を追記した時点で code hash が変わり、旧 draft の verifier が `Source changed` で拒否したことを確認した。v2 は問いを具体化した履歴である。独立 review で旧 verifier の自己申告 manifest / path / SQL 信頼の問題が見つかり、外部 pin と構造検証を追加した。v1 / v2 を削除・上書き・move せず保持し、最終 code/test で新規 v3 namespace を一度生成して検証した。v1 / v2 は採用出力として使用しない。task packet 本体と report の SHA は v2 と同じであり、新しい品質判断や分類結果を作った変更ではない。

| 最終 v3 artifact | SHA256 |
|---|---|
| manifest.json | `deea5b26f2c4fe4579abcc592bc8acda2892b35b75019ac3aa49478d1bd2b3dd` |
| source-ledger.json | `84ef711cea721e270ea93e1a33354a6bbfc6885c5eeeabf0e6387c45a9ff4d0f` |
| classification-task-packets.json | `58db8c5be6c30c724ea70b5a851918e3f6add093237fd99c82d7be2c14e542e3` |
| identity-task-packets.json | `9c7e6cce2cfbf88688ccdda8b62f650e87436f4bf253a9c2d551a9963247ec69` |
| report.json | `4d101ec279c569e3762c57d1f81221b04fb0d5a2a7ffcbcaf0f5e2a8016b7577` |

残る制約は、元 finding の保留理由がこの工程では解消されていないこと、参照のみなのでモデル投入可能な text packet ではないこと、選択外の 5 held works と uncertainty-only findings を網羅する品質検証ではないことである。STEP4 の実行許可や品質承認をこの準備完了から推定しない。
