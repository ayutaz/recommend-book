# 本人向け推薦の現在評価 v1

2026-10-16更新: 最新表示は2,025候補・特徴1,027件・暫定得点216件。履歴内容接続は2/335、新規0のため比較再実行0。5分割中3分割は内容得点可能0件で固定順位と同じ、残り2分割の各1件だけが差を作る。本人向け改善は未証明。[最新監査](phase8-quality-evaluation-v1.md)を参照する。以下の表示1,989件・得点213件とその理由検証は、2026-10-15の旧表示版の記録である。

確認日: **2026-10-15（JST）**。既存335評点と保存済み成果物の集約確認。新しい推薦比較・モデル実行・API取得は行っていない。

本人向け改善は**未証明**。内容と接続できる評点は **2 / 335件（0.597%）** のままであり、残り333件を分母から除外していない。原連載とのidentity関係は2件から10件へ増えたが、新しい内容特徴は0件、凍結runtime-v2の更新もない。identity接続と内容接続を区別し、同じ入力による比較の再実行・再調整は行わなかった。

## 保存済み比較の再利用

`data/autonomous-next-v3/database/phase3-personal-poc-v1/comparison.json` の結果をそのまま引用する。5-fold、各train268 / held67、k=10、seed=20261015。4方式は各foldで同一67候補、合計335候補を評価し、未接続・内容特徴欠損・支持なしも残す。候補の評点はrankerへ渡す前にnull、読書状態はunknownへ置換する。内容方式の固定値はmidpoint=3、prior=0.1、neighbors=20、探索0回。

| 方式 | score支持 / 全335候補 | nDCG@10 | Precision@10 | Recall@10 | AP@10 |
|---|---:|---:|---:|---:|---:|
| 内容 `personal_content` | 2 / 335（0.597%） | 0.520158 | 0.500000 | 0.176582 | 0.093776 |
| 書名bigram `owned_title_bigram` | 335 / 335（100%） | 0.415538 | 0.380000 | 0.133619 | 0.069742 |
| 固定順 `constant_no_popularity` | 0 / 335（0%） | 0.506099 | 0.500000 | 0.175841 | 0.092093 |
| 固定seedランダム `random` | 0 / 335（0%） | 0.377893 | 0.300000 | 0.106014 | 0.030781 |

値は定義可能なfold指標の平均。固定順は候補work_id辞書順であり、人気度指標ではない。randomは同じ辞書順候補をseed+foldで1回shuffleする。書名方式のscore支持は文字列計算可能性であり、内容根拠の存在とは異なる。固定順・randomにも順位はあるが個人内容scoreはない。

内容と固定順のnDCG差は約+0.014059だが、内容支持は2件のみで、335件の大半は支持なしの決定的同点に依存する。過去の既観測評点による探索的比較であり、fresh holdout=false、quality_verified=false。既知著者を持つ評点は1 / 335件、既知シリーズは0 / 335件。既知IDのtrain-held overlapは全foldで0だが、未知identityを含む独立性・漏洩なしは保証していない。将来の満足や本人向け改善、確率校正の証拠にはしない。

## 前回表示と読書状態（2026-10-15）

当時の `database/phase3-personal-poc-with-declared-genres-v2/verification.json` とmanifest/deliveryの集約は、候補1,989件、内容特徴あり1,004件（LLM由来820件、出典のgenre宣言由来184件）。本人内容特徴と共有するscore支持は213 / 1,989件（10.709%）、非支持1,776件。特徴ありと個人score支持は別集計であり、宣言genreをLLM分類やstrict72品質合格へ読み替えない。この候補数の拡張は本人335件の内容接続増加を意味しない。

**実履歴による既読除外は0件、全1,989候補の読書状態はunknown。確認済み未読は0件**。既知作品を実データで除外できたとの主張や、1,989件を未読推薦候補とする主張はしない。原連載・書籍・版のidentityや内容の同一性を推測して統合していない。

実装は参照work_idと外部ID一致、明示既読や履歴を共通engineの除外規則に渡す。保存済みGUIは支持数と説明表示を観察しているが、実履歴除外の成功例は記録していないため、今回既存の合成fixtureによる3テストだけを実行した。work_id一致1件・外部ID一致1件の除外と、残る候補のunknown保持を確認した。これは実335履歴の既読除外実績ではない。

## 前回表示の推薦理由の確認

保存済みdeliveryを集約検査し、支持213件すべてで説明が非空、feature_idごとのcontribution合計がscoreと一致（相対・絶対許容誤差1e-12）した。非支持1,776件はすべてscore=null、説明=[]。説明は共有された内容特徴の寄与であり、本人の書名・ID・個別評点を理由へ出力しない。scoreは満足確率ではなく、raw confidenceを重みとしていない。

最新manifestの `gui_verified=false` はbuild時点の値で、別の保存済みverificationは `actual_gui_verified=true`、console error 0を記録する。この評価では保存済み観察を引用し、新しいGUI操作は行っていない。既存3テストはprivate参照の非出力、参照評点を逆転したときの順位反転、欠損内容のnull/空説明、identity除外を確認し、**3 passed**だった。

## 接続拡張が止まる理由

公開8記事の内容入力実現性は、complete infobox recipe通過3 / 保留5。そのうち抽象作品レベルの新審査候補は1件、抽出成功でも原連載へのscope転用が未解決なものがある。runtime入力・新規採用特徴はいずれも0。recipe通過だけで原連載にgenreを転用せず、用途・taxonomy・scopeの不足を残す。

保存済み著者監査では行内著者profileリンク0、正当なprivate author ID0、private→public author bridge0。著者名や行外アカウント導線による補完はしていない。原連載APIの用途・権限も `unresolved_no_collection` のまま。この評価で権限を新規認定したりAPIを取得したりしていない。

[公開評点による一般書籍推薦評価の実現性](public-rating-evaluation-feasibility-v1.md)の既存3候補調査では、本プロジェクトの用途に適合すると確認済みのデータセットは0。今回は再調査・downloadを行っていない。この結果は当該3候補に限り、他に利用可能なデータが存在しないという意味ではない。公開評点で将来良い結果が出ても本人向け改善の代用証拠にはしない。

## 監査用成果物

集約receipt: `data/autonomous-next-v3/evaluation/phase5-personal-recommendation-status-v1/report.json`。必要な小さな既存文書・JSON・コード・テストの**ファイル値から計算したSHA256文字列**と、比較の既存spec/input/split digestを固定した。約916KBのdeliveryはローカルで選択集計のみを出力し、digestは既存manifest記録値として保持（再計算なし）。全DBのhash・DB読書き・履歴個別行の読み出し・元成果物再生成を行っていない。receiptにも個別書名・ID・評点は含めない。

今回の実行確認: `uv run --offline pytest -q` で `test_identity_read_exclusion_and_unknown_status`、`test_content_preference_changes_rank_without_exporting_private_observations`、`test_missing_content_is_unscored_and_expression_features_rejected` の3件。比較を再実行せず、既存成果物の件数・共通分母・説明合計・欠損保持の整合を確認した。
