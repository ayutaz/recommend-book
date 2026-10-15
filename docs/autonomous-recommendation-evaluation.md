# ユーザー追加作業0の推薦比較

**推薦のsource superiorityは未達。** innerで選定した方式のouter nDCG@10は0.573995で、人気baselineの0.633978を超えなかった。全335のinnerが選んだhybrid50は実験候補であり、品質検証済みdeploymentとは扱わない。

`config/autonomous-evaluation-v1.json` に従い、保存済み335評価だけでnested grouped CVを実行した。新規LLM・通信・ユーザー追加評価は0。既存engine、feature bridge、元評価DB、元annotationは変更していない。新規分類のhuman goldは0であり、本比較から分類正解率やconfidence校正を算出しない。

## 事前登録と再現

実データ比較前に独立したCLI呼出しで8設定を `data/autonomous-poc-v1/recommendation-evaluation/preregistration.json` に保存した。SHA256は `f8420425b08fb880dad7abebf170500ce30585023bc3976f97332993fcd3ea5b`。設定数上限12に対し、personal既存1、popularity1、rank hybrid3、同点近傍を保持するpersonal3を比較する。

| family | 固定設定 |
|---|---|
| personal | 既存engineの20近傍、prior 0.1、train全体平均とcosine² |
| popularity | 実際に記録された同domainのpopularityだけ |
| hybrid_rank | personal重み0.25/0.5/0.75、popularity重み1−personal重み |
| personal_tie_robust | nominal近傍10/20/40、prior 0.1、境界cosineと完全同値の近傍を全件保持 |

hybridは元score尺度を混合しない。各componentについてsupported同点群の平均順位を使い、eligible pool全体の件数をNとして `(N+1−平均順位)/N` をutilityとする。missing componentの寄与は0、重みは固定で候補ごとの再正規化はしない。両componentがunsupportedならscoreはnull、末尾の安定同点順に残す。欠損特徴をabsentと解釈せず、shared/native bridgeの対応範囲も変えない。

```powershell
uv run --offline pytest tests/test_autonomous_recommendation_evaluation.py tests/test_recommendation_evaluation.py tests/test_recommendation_engine.py -q
uv run --offline python scripts/evaluate_autonomous_recommendations.py --register-only
uv run --offline python scripts/evaluate_autonomous_recommendations.py
```

既存結果は上書きしない。再実行時は新規 `--output-dir` を指定し、そのdirectoryについて事前登録を別呼出しで完了する。CLIはcontract・DB・annotation・bridgeのSHAをbaseline manifestと照合し、前後の不変性を確認する。設定変更やsource変更を同じrunに混ぜることはできない。

## 分割、選定、privacy

outer5foldのseedは20261013、各outer train内のinner3foldは20261014。著者authority、series ID、保守的な著者名blocking key、exact external aliasをunionし、未評価bridgeを含む全rowのconnected groupを分割しない。IDがない作品は共通unknown tokenでまとめず、singletonから始める。5fold/3foldを満たすrated groupが足りなければ例外とし、group分割や黙ったfold縮小は行わない。

分割はrating有無と件数を使い、rating値を使わない。各inner/outer評価はtrainのknown ratingだけを参照し、held candidateのratingをnull、read statusをunknownにして評価値を伏せる。これは既存評価作品のwithholding simulationであり、実際の未読確認ではない。元のread statusは入力digestに保存する。全設定・popularity・独立seeded randomは同一common eligible poolを全件返す。null scoreの候補も除外せず、順位metricsと別にsupport coverageを表示する。

選定はinner nDCG@10のfold算術平均最大、完全同値なら事前登録順。coverageやprecisionを後付け選定条件には使わない。各outerではinner選定方式のscoreをheld poolで観測する。全8候補のouter値も記述用に残すが、それを使って採用設定を選び直さない。最終参照用設定はfull-sourceの別inner3fold CVのみで選定し、outer値を参照しない。このfull-source選定値自体は独立評価値ではない。

レポートとCLIログは個別作品名・private ID・評価一覧・推薦理由中の個人履歴を保存しない。内部計算でのみidentityを使用し、入力、分割、eligible poolにはdigestを残す。`report.json` は全foldの件数・同一pool・記録identity overlap・全候補metrics・support/null・根拠0・同点を集計する。`report.html` は集計表示、`selection.json` は最終wrapper向けAPI結果である。

## 保存済み335での実測

335 rated work、283 connected group、outerは各train268/held67。各foldのeligibleは67、除外0、記録identity overlap0。全335の元read statusはunknown。authority著者ID0、著者名key331、series ID0であり、未記録seriesについて漏洩0とは主張できない。

| outer観測方式 | nDCG@10 | Precision@10 | score support | 平均rank10境界同点数 |
|---|---:|---:|---:|---:|
| innerで選定した方式 | 0.573995 | 0.44 | 331/335 | 1.0 |
| popularity baseline | 0.633978 | 0.56 | 331/335 | 1.0 |
| independent seeded random | 0.452457 | 0.38 | 335/335（順序定義のみ） | 1.0 |
| personal既存・記述用 | 0.547200 | 0.52 | 328/335 | 40.2 |
| hybrid 0.25・記述用 | 0.624756 | 0.50 | 331/335 | 1.0 |
| hybrid 0.50・記述用 | 0.626804 | 0.52 | 331/335 | 1.0 |
| hybrid 0.75・記述用 | 0.596379 | 0.50 | 331/335 | 1.0 |
| tie robust10・記述用 | 0.566445 | 0.48 | 328/335 | 34.4 |
| tie robust20・記述用 | 0.558776 | 0.48 | 328/335 | 34.4 |
| tie robust40・記述用 | 0.558776 | 0.48 | 328/335 | 34.4 |

outerの選定はpopularity3fold、hybrid0.25が1fold、hybrid0.50が1fold。full-source inner選定は `hybrid_50`（inner nDCG0.679404、popularity0.667065）だった。採用設定の選択手続きをouterで評価した値は0.573995であり、deployment設定の記述用outer0.626804にすり替えて改善成功とはしない。popularity優越を超える証拠は得られていない。

ランダムbaselineは今回の事前登録seed導出による別の固定shuffleであり、以前のflat CV random値と同じsampleではない。randomは根拠0の順序、popularityは特徴根拠0の観測人気順位である。意味的な特徴の根拠とscore可用性は分けて解釈する。

## uncertaintyと未検証範囲

outer heldの記録connected groupを各fold内で200回seed20261015により復元抽出し、固定OOF順位のgraded metricsを再計算した。選定方式−popularityのnDCG差の条件付き95%区間は `[-0.102220, 0.010428]`。foldごとに固定予測の再標本化を行った記述区間であり、5foldを独立実験として扱っていない。再学習、inner選択、missing identity、過去のfeature時点、domain/user変化の不確実性を推定しない。区間を独立改善証明や一般化保証に使わない。

335評価は既に観測・分析済みなのでfresh holdoutではない。日時があっても後から収集したmetadata/AI特徴の過去状態を再現できず、temporal/prospective評価ではない。narou sourceのpopularityを世界作品へ移植しない。世界作品のpopularityが全nullの場合はroot wrapperが参考personal fallbackを明示する必要があり、その品質をsource CVから保証しない。

## pure API

```python
from recommend_book.recommendation.autonomous_evaluation import rank_with_setting
response = rank_with_setting(
    candidates, references,
    {'id': 'personal', 'family': 'personal'},
    limit=20, read_history=None, require_confirmed_unread=False,
)
```

返り値は既存engineと同じ `{ranking, audit}`。ranking rowは既存features/provenance/read statusにscore/status/explanations/evidence_countを保持し、auditに `autonomous_setting`、必要時 `component_coverage`、`world_transfer_verified:false` を加える。選定dictは `selection.json` の `recommended_setting`、参考fallbackは `personal_setting` にある。`recommended_setting` はinner選択結果というAPI名であり、同fileの `source_quality_verified:false` と `world_transfer_verified:false` を併読する。`selection-claims.json` に実験候補の限定とbaseline比較結論を明示した。APIは外部通信や入力mutationを行わない。

テストは未実装ImportErrorのREDから実装し、17新規testsと既存評価13・engine35の合計65件がpass。outer truthを変えてもそのfoldのinner選択が変わらないこと、transitive未評価bridgeとexternal alias、入力順の不変性、同点近傍、欠損hybrid、原データ不変、privacy、事前登録/改変拒否、immutable出力を確認した。
