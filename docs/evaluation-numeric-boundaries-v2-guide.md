# scratch評価器の数値境界

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「現行ガイド・仕様」です。手順・契約の再利用前に、現在の状態は[current-status.md](current-status.md)、次の作業順序は[現行計画 v25](autonomous-next-phase-plan-v25.md)を参照し、入力・権利・版の前提は本文で確認してください。本文のCLI例・数値・測定値は記載した版・namespaceの再現用です。

> 本体への採用状況（2026-10-12）：この実装は本体へ適用し、本体の全テストで655 passedを確認した。以下のscratch限定・未適用・fixture限定の記述は隔離開発時点の記録である。実データのキャッシュ移行と実行条件の保存については、[採用記録](relevance-v2-development-verification.md#本体への採用と実データ移行)を参照。

対象は`data/development/relevance-v2/src/recommend_book/evaluation/metrics.py`。本体・ライブrun・実productiondataを変更・読取りせず、scratch内の合成fixtureだけで検証した。

`validate_gold`のrelevanceと、`evaluate_results`に渡す全prediction labelのrelevance・選択されたconfidenceを、nullまたは有限な0以上1以下の非bool実数（`numbers.Real`）に制限する。True/False、NaN、正負のinfinity、文字列、範囲外はValueError。非常に大きな整数も、float変換でOverflowErrorを起こす前に範囲外として拒否する。0・1・nullは受理する。複素数や文字列を数値へ変換しない。

predictionの数値検査はgoldとのjoin前に全labelへ適用する。空gold、goldにない作品・ラベル、decision unknownの場合にも不正値を見逃さない。未注釈ラベルを評価の正解率へ加える変更はしていない。

confidenceの選択優先順位は`raw_confidence`→`confidence_uncalibrated`→`confidence`で、既存のdict.getの意味を維持する。先のキーがない場合だけ後のキーを使う。先のキーに明示的nullがある場合はnullを選び、後へfallbackしない。検証するconfidenceは選択された1値だけで、後順位に隠れた値を指標へ使用しない。

known present/absentでrelevance nullでも、存在判定のTP/FNやcoverageの扱いは変えない。relevanceのMAE/rank等ではnullのpairを除外し、0へ置換しない。truth level、human/reference/LLM provenance、partitionとgroup漏洩検査、閾値、校正のfit/context/scopeは変更していない。人手goldがない場合はhuman結果が空、human_validation_pending、uncalibratedのままである。

校正帯のfitとholdoutへの適用は同じ境界判定を共有する。低帯は`[0, 0.5)`、高帯は`[0.5, 1]`とし、上端1の最終帯だけ1.0を含む。0.5を低帯へ適用していた境界不整合を修正した。scope最低40件・帯最低20件、context一致、calibration partition限定fitの条件は維持する。低帯20件の実精度1・高帯20件の実精度0を持つ合成fixtureと独立holdout groupで、0.5→高帯0、0.49999→低帯1、1.0→高帯0を確認する。39件scopeおよび19件低帯はholdoutを校正できず、context不一致も引き続き適用しない。

fitするscopeのmodel/prompt_version/taxonomy_version/cli_version/target_languageは、5値とも非空文字列である必要がある。欠落・null・空文字・空白のみ・数値・list/dict等が含まれるscopeは、40件以上の人手annotationがあってもuncalibratedのままにする。precision等の評価は可能であり、annotationを破棄しない。校正成功fixtureには`test-model/test-prompt/tax/test-cli/en`を明記し、過去の`(None, None, None, None, None)`による校正成功を期待しない。全5実行条件について、holdoutで値が異なる場合の不適用も確認する。

TDDではbool goldの誤受理、空gold/未注釈ラベルの不正prediction見逃し、大整数のOverflowErrorを先に確認した。その後、型・有限性・範囲を共通検査し、gold join前の全predictionへ適用した。合成fixtureは人手goldへ取り込まない。

```powershell
# リポジトリのルートで実行します。
uv run --offline pytest tests/test_evaluation.py tests/test_classification_summary.py tests/test_classification_stability.py -q
```

境界テストはTrue/False、非有限、文字列、上下範囲外、大整数、0/1/null、present/absent/unknown、空goldと未注釈ラベル経路、confidenceの3候補とnullによる優先順位、known-null保持を確認する。既存のtruth/provenance・partition・校正context検証も同じファイルの回帰テストで維持している。追加通信・Web・LLM・native CLIは使用していない。本番評価や人手品質の検証結果は含まない。
