# relevance 契約の独立レビュー

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「履歴・結果・計画・スナップショット」です。本文の件数・計画・実行状態と、本文内の「現在」「最新」「実行中」は記録日時のものとして扱い、現在の状態は[current-status.md](current-status.md)を参照してください。

2026-10-12。計画・prompt・taxonomy・schema・models・validation・job cache を読み、既存 fixture によるメモリ内検証を行った。追加 LLM / Web / 分類 CLI 呼出しはゼロ。この文書以外の live 設定・共有コード・DB・data は変更していない。以下の改善は **live v1 終了後の次版案**であり、稼働中や frozen v1 を差し替えない。

## 結論

資料が taxonomy の label 定義を支持するなら、`decision=present` と「中心性は資料から不明」の `relevance=null` は両立する。次版ではこれを許容することを推奨する。中心性だけの不足を label 自体の `unknown` に一律変換すると、支持された存在判断を失う。0 / 0.5 / 1 の補充も根拠不足を隠す。

現在の v1 validator はこの組合せを拒否するため、runtime 上は invalid result である。しかし prompt / schema に沿う出力なので、今回の null だけをモデル不遵守・誤分類の証明とはしない。契約の層間不整合として扱う。資料の引用が正しいことと、引用が taxonomy の意味を十分に支持することも別である。

## 規定の照合

| 根拠（今回読んだ位置） | 規定・観察 |
| --- | --- |
| [計画:136–143](world-novel-dataset-plan.md#L136) | decision、関連度、信頼度は別項目。141 行は関連度を中心性と定義し、根拠不足なら null、未記述を 0 / 否定に変えない。143 行も confidence と関連度を分離 |
| [prompt:12–25](../src/recommend_book/classification/prompt.py#L12) | 資料が label を支持する場合に全 unknown を強制しない。20–23 行は中心性と confidence を分離し、unknown relevance は null。24–25 行の known 必須条件は evidence / locator / 引用または要約 / directness で、数値 relevance の必須指示はない |
| [schema:153–168](../schemas/classification.schema.json#L153) / [241–254](../schemas/classification.schema.json#L241) | relevance / raw_confidence は number または null。キー自体は必須だが、known の relevance を非 null にする条件規則はない |
| [taxonomy:1345–1355](../config/taxonomy.json#L1345) | unknown は insufficient_or_conflicting_evidence、absent は明示的反証 / 十分な資料が必要。関連度の anchor は core / peripheral / clear element / major。label existence から数値を自動算出する規定はない |
| [models:1](../src/recommend_book/models.py#L1) / [75–85](../src/recommend_book/models.py#L75) | 未知の事実は null とする説明と WorkInput の入力契約。分類 label 出力の Pydantic model はここに存在せず、known の relevance 非 null 制約もない |
| [validation:64–70](../src/recommend_book/classification/validation.py#L64) | unknown は relevance=null と missing_reason を要求。一方、present / absent は evidence、relevance 非 null、raw_confidence 非 nullを一括要求。69–70 行が今回の不整合箇所 |
| [validation:95–98](../src/recommend_book/classification/validation.py#L95) | missing_axes は supported decision のない軸。label が支持され、関連度だけ不明なら、軸の存在判断まで missing にする意味はない |

既存 `tests/test_classification.py::result_for()` の引用・locator・ID・confidence を保持し、relevance だけ null にしたメモリ内結果は JSON Schema を通過した。同じ結果への `validate_result()` は `Known decisions need evidence, relevance and raw confidence` で拒否した。実モデルを再実行せず不整合を再現できる。

## existence と taxonomy 定義の境界

| 資料から分かること | 意味上の適切な扱い |
| --- | --- |
| taxonomy の定義は支持されるが、周辺 / 主要 / 中核の程度は不明 | present、根拠と decision confidence を保持、relevance=null（次版案） |
| genre 名は資料にあるが、その taxonomy label に必須の条件を満たすか不明 | definition に対して unknown / 保留。中心性スコアの欠損とは別 |
| genre 自体が未記述 | unknown / unassessed。absent / relevance=0 にしない |
| 十分な資料に中心性の程度も明示される | 根拠に即した数値。genre metadata の存在だけで 1 を付けない |

たとえば [genre.mystery:38–46](../config/taxonomy.json#L38) は investigation / puzzle が物語を動かすこと、[genre.romance:92–100](../config/taxonomy.json#L92) は恋愛関係の発展が central plot であることを定義に含む。genre 名の正しい引用だけで、これら全条件の意味的充足を人手確認したとはいえない。一方、定義を満たす資料があることと、0.75 / 1 の中心性を十分に区別できることは同じ条件ではない。

root の保存済み [観察 JSON](../data/evaluation/poc-frozen-v2/relevance-contract-observation-v1/observation.json) は `2026-10-12T01:58:31.087886+00:00` 時点の immutable invalid final **6 attempts / 12 genre-present-null 項目**。同じ作品・label の複数 attempt を含み、unique work-label pair は **8**。入力 hash・根拠 ID・locator は照合済みという観察であり、人手による意味的支持判定・修復ではない。これを 12 個の独立誤分類や 12 作品と数えない。

## validator だけ変える場合の cache / 再利用

[runner:137–149](../src/recommend_book/classification/runner.py#L137) の config は model、taxonomy / schema / prompt の hash と version、CLI、batch_size 等を持つが、**validation hash / version を持たない**。[jobs:22–23](../src/recommend_book/jobs.py#L22) の job_key は input hash とこの config の hash。prompt / schema / taxonomy を保って validation.py だけ変えると、同一 input の job_key は変わらない。

[runner:176–183](../src/recommend_book/classification/runner.py#L176) は completed cache を現在の validator で再検証するため、完全な検証省略ではない。しかし同じ job 状態 / manifest の意味が変更される。[jobs:85–94](../src/recommend_book/jobs.py#L85) は held を claim しないので、旧規則で上限に達した failed/held は validator 緩和だけで再検証されない。[runner:240–272](../src/recommend_book/classification/runner.py#L240) はバッチ全件を検証してから complete し、一件でも error ならそのバッチの各 lease が fail する。今回の一項目がバッチ再試行に広がる経路はコードと整合する。

最小の安全案は次の通り。実装・採用・再検証は今回行っていない。

1. live v1 が終了してから、新 validation policy version と validation.py SHA を config / manifest に追加する。job_key の既存 config hashing を利用して次版 key を分離し、新 output / jobs namespace を使う。schema / prompt を保つ場合は、その旧 hash と version を正しく保持し、検証版だけが新しいことを provenance に明示する。
2. 最小の意味変更は、known の evidence / raw_confidence 必須を維持し、relevance 非 null条件だけを分離する。identity、rights、scope、引用、taxonomy、unknown の missing_reason、uncalibrated 等の検証は弱めない。中心性不明の説明は既存 missing_reason に記録できるが、promptを変えず新しい必須説明を追加することは別の隠れた契約不整合になる。説明を必須にするなら prompt / schema の版も揃える。
3. 旧 completed と immutable attempt final を読み取り、新 validator・同一 frozen input / hash・同一 taxonomy / prompt / schema・model / CLI metadata・元イベントの tool-use 等の実行条件で再検証する。バッチ identity の完全性も確認する。新 job の結果と別の移行台帳に旧 job / final SHA / 新 validation版 / 移行理由を保存する。旧 bytes、jobs、results、failed履歴、token計測を変更せず、再検証を追加 LLM 実行と表示しない。
4. validator 再検証の成功だけで taxonomy の意味的精度・人手支持・中心性の正しさを保証しない。必要なら review queue に残す。null を数値補完したり present を自動 unknown 化したりする移行は行わない。旧 valid で今回も valid な結果も、新規則で検証済みという台帳を経由して再利用する。
5. decision coverage と relevance coverage を分ける。既存 [評価:131–143](../src/recommend_book/evaluation/metrics.py#L131) は non-null relevance pair だけで MAE 等を計算している。次版はスコア採用件数と対象分母を併記し、present-null を負例・relevance=0 と扱わない。

次版の確認 fixture は、支持された present-null、支持された数値 present、根拠なし / confidenceなしの拒否、unknown-null と理由、unknown に数値を付けた拒否、taxonomy 定義自体の不足、同 input で validation版が異なる job_key、旧 completed / held の別台帳再検証、バッチ一件の不整合を含める。採用前の live v1 と frozen input / results / jobs は現在の契約・状態を維持する。

後続作業では[隔離コピーでの採用前検証](relevance-v2-development-verification.md)を実施し、全322テストと保存済み6 attemptの読み取り専用再検証を確認した。本体への適用・実cache移行は未実施であり、この静的レビュー時点の旧契約と実行証拠を次版の実績へ読み替えない。
