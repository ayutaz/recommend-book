# relevance v2 の隔離開発・検証

> 本体への採用状況（2026-10-12）：この実装は本体へ適用し、本体の全テストで655 passedを確認した。以下のscratch限定・未適用・fixture限定の記述は隔離開発時点の記録である。実データのキャッシュ移行と実行条件の保存については、[採用記録](relevance-v2-development-verification.md#本体への採用と実データ移行)を参照。

2026-10-12。base `cbd49f455a9abbce630f82e163cb389918091378` の 147 tracked ファイルから作った `data/development/relevance-v2` 内だけの修正。基準 SHA は `development-snapshot.json` に保持する。root の live code / frozen inputs / production jobs / DB / results へコピー・適用していない。

## 変更した契約

`validation.VALIDATION_VERSION = "2.0"`。present / absent の支持判断に根拠と raw_confidence を要求する規則は維持し、relevance の非 null 要求だけを取り除いた。中心性が不明なら null を保持し、0 / 0.5 / 1 の補充や unknown への自動格下げをしない。unknown は従来通り relevance=null と missing_reason が必須。identity / input hash / taxonomy / rights flags / scope / 引用 / locator / uncalibrated 等の既存条件も維持する。validation 通過は資料の意味的支持・人手精度の証明ではない。

prompt_version は `1.0` のまま。prompt、JSON Schema、taxonomy、models は変更していない。分類出力に新しい必須フィールドや新しい説明要求を加えていない。

## 移行担当と共有する config API

```python
from recommend_book.classification.runner import build_run_config

config = build_run_config(
    model="gpt-6.1-sol",
    cli_version=archived_cli_version,
    taxonomy=archived_taxonomy,
    batch_size=archived_batch_size,
)
```

helper は指定した identity とローカル artifact の read のみを使う。CLI の探索・version 照会・起動、通信、書込み、副作用による引数変更をしない。設定済み ROOT の schema / prompt / validation の実 bytes と taxonomy の canonical JSON から SHA を計算するため、同じ固定 artifact と引数では同じ config を返す。validation.py が変われば hash も変わる。

旧 config の全キーを保持し、追加は `validation_version` と `validation_hash` の二つ。run_classification も helper を利用する。manifest の config と job metadata の config は同一で、既存 `job_key(input_hash, config)` が検証契約の変更を新 key に分離する。schema / prompt の hash を変更したと偽装しない。

旧 completed / held / immutable attempt の再検証・新 namespace の seed は移行担当の別処理。旧履歴や結果を上書きせず、新版での再検証台帳を経由する。この変更自体では旧 run の再検証・追加分類・移行を行っていない。

## 隔離環境と TDD 実測

scratch を working directory として `uv sync --frozen --offline` を実行した。Python は scratch `.venv/Scripts/python.exe`、recommend_book と classification.validation の import 元、validation.ROOT はいずれも scratch 配下であることを確認した。

- RED: `uv run --offline pytest tests/test_classification.py -q` → **5 failed / 20 passed**。known-null が拒否され、公開 helper が存在しないことを確認。
- GREEN: 同コマンド → **25 passed**。
- 関連 checks: 下記コマンド → **56 passed**。

```powershell
uv run --offline pytest tests/test_classification.py tests/test_classification_cli.py tests/test_classification_benchmark.py tests/test_classification_summary.py tests/test_classification_stability.py -q
```

fixture は支持された present / absent の null を保持、evidence / confidence 欠落、denied evidence、identity / locator / 引用 / translation scope 不整合の拒否、unknown 制約、artifact 変更時の新 job key、helper と runner manifest / job config の一致を検証する。CLI 呼出しはモックだけであり、native Codex の実起動、実 LLM、Web はゼロ。

この担当の差分は `src/recommend_book/classification/{validation.py,runner.py}`、`tests/test_classification.py` と本 guide。移行・監査担当の同じ scratch 内の差分は別担当。root 適用は live 終了後の統合判断に委ねる。
