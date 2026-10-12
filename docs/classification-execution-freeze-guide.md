# 分類実行 namespace のオフライン準備

> 本体への採用状況（2026-10-12）：この実装は本体へ適用し、本体の全テストで655 passedを確認した。以下のscratch限定・未適用・fixture限定の記述は隔離開発時点の記録である。実データのキャッシュ移行と実行条件の保存については、[採用記録](relevance-v2-development-verification.md#本体への採用と実データ移行)を参照。

`scripts/freeze_classification_execution.py` は scratch 内の次版開発用 preparer。分類・CLI 探索・CLI version 照会・通信を行わず、新 namespace に入力と実行条件の証拠を保存する。今回の検証は temp の合成 fixture のみで、root の実 input / DB / production run を読取り・更新していない。

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

新出力は `manifest.json`、`inputs/{ordered.jsonl,manifest.json}`、`artifacts/` の raw copies。指定コード artifact は正確に **10** 件：sources、taxonomy、classification schema、prompt、validation、runner、models、jobs、pyproject、uv.lock。コピー相対パス・SHA と validation version / hash を含む run_config を記録する。元 files の値や SHA を準備の開始と公開直前で比較し、変更があれば公開を拒否する。

既存出力と入力 directory 配下の出力を拒否する。新 namespace の親に UUID stage を作り、stage の raw copies / manifest と全 source bytes / config 値を再検証後、rename で公開する。失敗時の削除対象はこの呼出しが作った検証済み UUID stage だけ。既存出力、他 stage、元入力を消さない。将来の runner は別の run subdirectory を使い、この prepared manifest / raw evidence を保持する。

TDD は実装前 **11 failed**（script 未存在）を確認し、実装後・追加ケースを含め専用 **18 passed**。SHA 不一致、重複 identity / property、NaN / overflow、途中 code / input / manifest / config 変更、公開直前の新 input＋正しい新 manifest への差替え、旧 context 保持、new config hash、既存出力拒否、stage cleanup、CLI 引数転送を合成 fixture で検証した。CLI の探索・実起動はモックで禁止した。

```powershell
uv run --offline pytest tests/test_freeze_classification_execution.py -q
```

この開発成果を root の live code / frozen input / production jobs にコピーしていない。actual model run は別の明示的実行段階に残る。
