# 分類実行 namespace のオフライン準備

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「現行ガイド・仕様」です。手順・契約の再利用前に、現在の状態は[current-status.md](current-status.md)、次の作業順序は[現行計画 v25](autonomous-next-phase-plan-v25.md)を参照し、入力・権利・版の前提は本文で確認してください。本文のCLI例・数値・測定値は記載した版・namespaceの再現用です。

2026-10-16更新: 現行処理は実データの分類・採用に使用済み。`--execution-spec provenance-v4`で実際のprompt/schema/spec registryを固定する。[実行版と境界](native-snapshot-runtime-v1.md)と[実施結果](phase8-quality-evaluation-v1.md)を参照する。以下のscratch・未適用・fixture限定の記述と試験数は初回開発時点の履歴。

> 本体への採用状況（2026-10-12）：この実装は本体へ適用し、本体の全テストで655 passedを確認した。以下のscratch限定・未適用・fixture限定の記述は隔離開発時点の記録である。実データのキャッシュ移行と実行条件の保存については、[採用記録](relevance-v2-development-verification.md#本体への採用と実データ移行)を参照。

`scripts/freeze_classification_execution.py` はオフライン準備用の処理。分類・CLI探索・CLI version照会・通信を行わず、新namespaceに入力と実行条件の証拠を保存する。初回の実装検証はtempの合成fixtureで行い、その後の実データ適用は冒頭の実施記録へ分けて保存した。

```powershell
# scratch working directory で使う例。実データに対して今回実行したコマンドではない。
uv run --offline python scripts/freeze_classification_execution.py `
  --input fixture-input/ordered.jsonl --output prepared-execution-v2 `
  --model gpt-6.1-sol --cli-version EXPLICIT_SAVED_VERSION `
  --batch-size 5 --concurrency 2 --timeout 180 `
  --max-run-seconds 600 --max-observed-tokens 100000 --stop-on-rate-limit
```

入力の sibling `manifest.json` に `ordered_inputs_sha256` または `work_inputs_sha256` が必要。双方があれば両方を検証する。SHA は JSON を再直列化せず exact bytes で照合し、WorkInput の work/expression 重複、重複 JSON property、非有限値も拒否する。旧 manifest の raw bytes / SHA と processing_context は `input_provenance` に保持し、新 `execution_context` と分離する。

`runner.build_run_config()` と `validate_controls()` を利用して model、供給 CLI version、batch_size と concurrency / timeout / time・token budget / stop_on_rate_limit を固定する。CLI version は **supplied_unverified** であり、実測したとの主張はしない。将来の実行直前に root が別途照合する。状態は **prepared_not_started**、model_run_started=false。この準備自体を実モデル run や評価成功として数えない。

新出力は `manifest.json`、`inputs/{ordered.jsonl,manifest.json}`、`artifacts/` の raw copies。従来の既定/baseline artifactは10件を維持する。非baselineではローカル`get_spec()`が示す実prompt・実schema・`specs.py`を追加し、`provenance-v4`は13件となる。manifestの任意のファイル一覧を信頼せず、specから必要集合を導く。コピー相対パス・SHA と validation version / hash を含む run_config を記録する。元 files の値や SHA を準備の開始と公開直前で比較し、変更があれば公開を拒否する。

既存出力と入力 directory 配下の出力を拒否する。新 namespace の親に UUID stage を作り、stage の raw copies / manifest と全 source bytes / config 値を再検証後、rename で公開する。失敗時の削除対象はこの呼出しが作った検証済み UUID stage だけ。既存出力、他 stage、元入力を消さない。将来の runner は別の run subdirectory を使い、この prepared manifest / raw evidence を保持する。

TDD は実装前 **11 failed**（script 未存在）を確認し、実装後・追加ケースを含め専用 **18 passed**。SHA 不一致、重複 identity / property、NaN / overflow、途中 code / input / manifest / config 変更、公開直前の新 input＋正しい新 manifest への差替え、旧 context 保持、new config hash、既存出力拒否、stage cleanup、CLI 引数転送を合成 fixture で検証した。CLI の探索・実起動はモックで禁止した。

```powershell
uv run --offline pytest tests/test_freeze_classification_execution.py -q
```

上記は初回開発時点の記録。現在の実適用と本物のモデル実行は冒頭リンクの後続記録で確認できる。準備処理自体がモデルを実行することはない。
