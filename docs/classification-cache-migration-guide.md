# relevance v2 の旧分類キャッシュ再検証

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「現行ガイド・仕様」です。手順・契約の再利用前に、現在の状態は[current-status.md](current-status.md)、入力・権利・版の前提は本文で確認してください。

> 本体への採用状況（2026-10-12）：この実装は本体へ適用し、本体の全テストで655 passedを確認した。以下のscratch限定・未適用・fixture限定の記述は隔離開発時点の記録である。実データのキャッシュ移行と実行条件の保存については、[採用記録](relevance-v2-development-verification.md#本体への採用と実データ移行)を参照。

この scratch 実装は、完了済みの旧実行を現在の validator で再検証し、未作成の新 namespace にキャッシュを準備する。追加 LLM 呼び出し、Codex CLI 起動、通信、元結果の修正は行わない。2026-10-12 時点の確認は合成 fixture のみで、旧本番 run の読み取りや実データ移行は実施していない。本番へのコードコピー・移行・次実行の開始は root の担当である。

## 入力と実行境界

`scripts/revalidate_classification_cache.py` の CLI は次の五つのディレクトリ指定をすべて要求する。コマンドは実行形の説明であり、このガイド作成中には実行していない。

```powershell
uv run python scripts/revalidate_classification_cache.py `
  --source-run <旧terminal-run> `
  --input-snapshot <旧runと件数が一致するexact-input-snapshot> `
  --ordered <旧ordered-inputsディレクトリ> `
  --frozen <旧frozen-snapshotディレクトリ> `
  --output <未作成の新future-run-cacheディレクトリ>
```

live run を指定してはならない。旧 manifest は全選択 job の terminal 状態、`pending=0`、lease 不在を要求する。旧入力に余分な作品を追加して source manifest の選択件数と合わせることは認めない。1,000 件の旧 run には exact 1,000 行 snapshot を使い、新しい 2,020 件の将来入力は次実行で別途指定する。同じ input_hash/config の最初の 1,000 件だけが新 job key で再利用可能になる。

exact snapshot の `manifest.json` は `work_count` と `work_inputs_sha256`、入力は `work-inputs.jsonl` を持つ。ordered は `ordered-inputs.jsonl` の全体 SHA と exact snapshot の先頭 N 行 bytes/order の一致を確認する。frozen は `work-inputs.jsonl` の SHA、作品 ID・expression ID・input_hash の一致を確認する。input_hash は source-version refs・根拠・rights 条件を含む。ordered manifest の旧 `processing_context` と frozen の `processing-context.json` および保存 artifact SHA を照合する。

旧 processing context は旧入力 provenance の証拠であり、新 validator で元実行が行われた証明ではない。旧 config に validation version/hash が記録されていない場合は元実行 policy 不明として保持し、新 policy に読み替えない。新 config は純粋な `runner.build_run_config(model, saved_cli_version, taxonomy, batch_size)` から取得する。旧 prompt/schema/taxonomy/model/CLI/batch size 等が完全一致し、追加が `validation_version` と `validation_hash` の二項目だけであることを要求する。CLI version の照会も行わない。

新 output は存在するだけで拒否する。source/input/ordered/frozen の配下への出力も拒否する。全保護 tree と validator/prompt/schema/taxonomy のファイル SHA と inventory を開始時、出力準備前、公開直前に比較する。変更・追加・削除があれば新 namespace を公開しない。UUID 付き staging sibling を作り、成功時だけ rename する。衝突時は相手の namespace を保持し、自分が作成した staging のみ除去する。

Python API `migrate(...)` の ordered/frozen optional は合成 fixture 向けである。実移行では両方を必須とする CLI を使用する。

## 旧結果の採用条件

旧 manifest の job keys は exact input と旧 config から再計算し、未知 ID・重複 ID・入力差を拒否する。保存 `jobs/<key>.json` と terminal manifest の job 値全体が一致し、job identity/config/input_hash、attempts と連続 history、completed durable と `results.jsonl` index が一致しなければ移行自体を拒否する。JSON 重複 property・非有限数も拒否する。JSON 値の比較は正規化ハッシュを使い、Python の `true == 1` による型不一致の見逃しを防ぐ。

各 history の final/events artifact を共有する batch を全件で照合する。history の共有件数・metrics、returncode 0、成功した実イベント、唯一の `turn.completed`、event usage、最後の agent message と immutable raw final の JSON 値一致を要求する。event error と tool use があれば候補を held にする。通信等の失敗 category は採用しない。旧 `invalid_result`/`invalid_schema` は raw final が残っており、他の成功条件と新 validator を満たす場合だけ候補にできる。

batch 内の ID 集合は history 全構成員と厳密に一致し、全結果を新 validator で検証する。一作品でも引用・locator・scope・schema・モデル情報等が不正なら、その batch 全体を held にし、一部だけ救済しない。completed は raw final の当該値と durable の一致も要求する。複数候補は各 job の新 validator を通った最大 attempt、同値時は final/events path の辞書順で決定する。最新候補が不正であれば、その前の全件 valid batch を選べる。

completed の durable 結果は bytes をそのままコピーする。held の final 内結果は canonical JSON で保存できるが、元と新の JSON 値ハッシュを同一にする。work_id、input_hash、数値、null、decision、metadata を修正しない。未知 relevance を推定値で埋めず、v2 validator の範囲内で根拠がある known decision と `relevance=null` を保持する。

## 出力と計数

| 出力 | 内容 |
| --- | --- |
| `jobs/<new-key>.json` | 新 config の completed cache。`attempts=0`、`history=[]`、`cache_import` は台帳 record_id を参照 |
| `results/<new-key>.json` / `results.jsonl` | 採用した元の結果。completed は durable byte copy、held は値を保持した抽出 |
| `migration-provenance.jsonl` | 採用一件ごとの source manifest/job/attempt、旧・新 key/config hash、raw final/events/durable SHA、元・新 value hash、human false |
| `migration-candidates.json` | 全 batch 候補の validated/held と理由。未採用候補も残す |
| `protected-source-sha.json` | 比較した source/input/context/code artifact の SHA |
| `manifest.json` | cache import 専用観測、新 config、各 job、保護件数、出力 SHA、旧実行量の別項目 |
| `migration-manifest.json` | `manifest.json` と同一の移行 report を先に atomic 保存した原本。将来 runner が通常 manifest を更新しても残る |

`cache_import` は `{record_id, ledger: "migration-provenance.jsonl", source_manifest_sha256}`。台帳は source の絶対 path と source manifest SHA、source job 相対 path/SHA、source status、source attempt の final/events 相対 path/SHA/shared size を保持する。旧 attempts/history は `legacy_attempts`/`legacy_history`、元 policy 未記録は `source_validation_policy_recorded=false` として残す。新結果の path/SHA と `source_raw_result_value_hash == new_result_value_hash` を保持する。observer はこの台帳を元 run の manifest へコピーせず、必要なら外部 artifact として参照する。

import を新 CLI invocation・新 attempt・新 token 使用量として計数しない。新 manifest は `attempts=0`、`cli_invocations=0`、`usage={}`、`llm_started=false`、`web_requests=0`、`human_quality_verified=false`。旧使用量は `legacy_source_totals` にだけ引用する。未採用 job は manifest 上 `not_submitted` とし、completed job file は作らない。将来 runner はその作品だけ新規実行できる。

移行時 `output_sha256` は作成時点の証拠である。自己参照を避けるため `manifest.json` と `migration-manifest.json` は snapshot hash の対象外とし、`output_sha256_excluded` に明示する。将来 runner が同 namespace を利用すれば通常 manifest/job/index は更新されるが、script が別保存した `migration-manifest.json` と台帳で元の import 観測を監査できる。元 run の入力・結果は引き続き immutable のまま保持する。機械再検証を人手精度、校正済み confidence、作品全体の意味的正しさと呼ばない。

## fixture 検証結果

```powershell
uv run pytest tests/test_classification_cache_migration.py tests/test_classification.py tests/test_jobs.py -q
```

scratch cwd で **58 passed**。移行専用は 27 ケース。初回 RED は script 未実装による 14 failures。追加 RED は source inventory 変更の見逃し、boolean/数値型の等値見逃し二件、CLI の ordered/frozen 未必須二件を確認して修正した。最終レビュー追加 RED は移行 report 原本の未保存一件を確認して修正した。

専用試験は durable byte copy、held null の値保持、最新 valid attempt 選択、batch 全件検証、欠落 member、実イベントと raw final の不一致、error/tool use/非ゼロ returncode、入力・config・durable・index・保存 job の不一致、旧 source の途中変更/追加、新 output の衝突・境界、ordered prefix/frozen hash 保護を扱う。将来 runner 再利用 fixture は subprocess 呼び出しを禁止し、`reused=2`、CLI/attempts 0、usage 空を確認する。再利用後に通常 manifest の更新を確認し、移行原本の bytes/SHA、import 件数、新 CLI 0/usage 空、旧 usage の別引用が不変であることも確認する。これは実 Codex CLI や実本番 cache の再利用実績ではない。
