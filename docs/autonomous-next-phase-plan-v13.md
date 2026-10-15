# ユーザー工数0で進める残作業計画 v13

更新: 2026-10-14。本分類1,862作品の実行条件の独立監査通過と、途中1,500作品の結果保存までを反映。[v12](autonomous-next-phase-plan-v12.md)の実行履歴を保持し、現在地と残作業を更新する。

本人評価もユーザー工数に含め、依頼しない。試読、感想、採点、履歴入力、正解作成、画面確認を含め、収集・統合・分類・検証・ローカル納品まで担当側で実施する。本人の満足度と未読状態は、既存データで確認できる範囲を超えて断定しない。

## 現在地

| 対象 | 現在の実績 | 残作業 |
|---|---|---|
| 作品DB・名寄せ | 2,005作品。811出典版の追加、全件独立監査、用途審査後の保存DB生成が完了。ROOT実tool 89748はexit0、公開6ファイル・899原期待pinを照合。独立監査で元53表の51表全値不変・2表完全保存、FK0・integrity OKを確認 | 品質審査後の分類結果を別DBへ採用 |
| 入力再生成 | 書誌2,005、分類候補1,985、別名683の計4,673入力を照合。最終台帳と、保存DBの全値・順序・参照先に対応する本分類用1,862入力の固定も完了 | 実行条件の束縛・独立照合 |
| 全3群の用途審査 | 実終了コード0。分類1,985候補中1,862許可・123保留、書誌2,005中1,870許可・135保留、別名683中600許可・83保留。897原期待pinと4,673入力の系譜を照合し、終了後の独立照合も通過。保存DBと単一台帳も生成済み | 保留と用途範囲を後続処理へ維持 |
| 専門家参照A/B | 各98作品中93件通過・5保留。事前固定指標では文脈方式が優位 | 全6軸への一般化は未検証 |
| 世界6軸72ラベルA/B | 定義方式65件通過・33保留、文脈方式73件通過・25保留。各98件、未処理0、両プロセス実終了。文脈方式は時間上限到達。追加診断は終了コード1・判定不能と独立レビューで確認 | 文脈方式の過去3 timeout attemptの終了コード未取得により、全実行履歴と全73通過結果の系譜の診断が未成立。世界評価指標は未生成 |
| 本分類の方式 | 専門家参照の事前固定指標に基づく文脈方式をROOTが固定。世界比較による選び直しなし | 全6軸の品質合格やモデル起動許可とは別。正式入力・台帳・実行前監査が必要 |
| 別名・最終台帳 | 別名600入力の原資料対応と、文脈方式・特徴・表示用途を束縛する単一台帳が完了。ROOT実tool 14049はexit0。全2,605表示用途検査、開始915・台帳713原期待pinを照合し、独立監査も通過 | 台帳固定はモデル起動許可とは別。実行条件の独立照合が必要 |
| 実prompt量・実行前確認 | 正式1,862入力・373バッチの計測と既存の実行前確認が完了。実行条件の束縛もexit0。実出力の独立監査は31項目通過・blocking 0。元3,965 pins、生成13ファイル、全入力・プロンプト・許諾対応を確認 | 実行直前の既存Guardによる再検査 |
| 本分類・品質審査 | 独立監査後、ROOT実tool 50164で起動判断を記録しexit0。10月13日21:08 JSTにROOT実tool 53418で本分類処理を起動。実行ID `3705ac8e52074deca2637b10c94fed94` の全1,862入力・条件の開始記録を照合。21:39 JSTに実Codex CLI開始。10月14日00:45 JST時点で1,500件のcompleted jobと1,500結果ファイル、保留30件を確認 | 全1,862対象の終端・全件品質審査は未確認。1,500件は途中の保存状態で、品質合格・新分類の正式採用ではない。採用0件 |

用途審査は担当の実tool 13351（exit0、chunk 1d1fc0）とROOTの実Windowsプロセス観測62572（exit0、chunk 346386）で別々に終了を確認した。ROOTは担当toolを直接pollしたとは記録しない。形式検証通過数を意味精度や正式採用数へ読み替えない。

## ここからの実行順序

下表の1・2と、3の実行条件の独立照合・担当側の起動判断は完了済み。本分類処理の実tool 53418を監視する。確認と判断は担当側で行い、ユーザー確認待ちは設けない。

| 順序 | 実施内容 | 完了条件 |
|---|---|---|
| 1. 正式入力を確定 | 保存DB・別名原資料・単一台帳・正式1,862入力の固定と照合が完了。入力順序を維持した全373バッチの実prompt計測も完了 | DB、全入力、出典、許諾、台帳の対応を後続監査へ渡す。保留資料を混入させない |
| 2. 評価結果と方式を確定 | 方式選定と世界診断の判定不能を記録済み。専門家参照の固定指標と、世界比較の検証不足を別々に保持して後続監査へ渡す | 通常完了・品質合格と偽らず、基準の事後変更や固定比較のやり直しで結果を置き換えない |
| 3. PoC本分類 | 完了した実行前確認を、固定入力・単一台帳・CLI・文脈方式・資源補足へ束縛する。独立担当が実際の束縛出力を照合し、担当側で起動判断する。その後Sol CLIを最大6並列で実行し、ジャンル・テーマ・物語構造・主人公属性・文体・雰囲気の6軸72ラベルを付与する | 1,862作品すべての処理状態、ラベル、根拠、関連度、未校正confidence、unknown、失敗・保留を保存する |
| 4. 分類品質を検証 | 全件の形式・引用・出典・CLI記録を検証する。全completed作品と全ラベルをAIが独立審査し、意味支持、作品違い、版の範囲、矛盾を確認する。言語・年代・軸・資料状態別に集計する | 審査が原応答と対応する。対象違い・矛盾は採用保留、曖昧さは不確実。独立正解の評価とAI内部審査を区別する |
| 5. 成果物を仕上げる | 条件を満たした分類だけを新DBへ反映し、書誌閲覧・6軸検索・参考推薦のローカル画面を作る。担当側で実画面を操作し、固定結果から別保存先へ再生成して比較する | DB、実評価報告、画面、再生成手順、保留・未検証一覧が揃い、最終監査が終了する |

5は依頼された工程1〜4の成果物を確認・納品する作業である。推薦精度の追加検証では保存済み評価だけを参照し、本人への新しい評価依頼を追加しない。

## 対象・並列数・時間枠

現在の2,005作品DBを母集団とし、固定済みの1,862分類入力で1,000〜3,000作品のPoCを行う。最終台帳・実行前確認・実行条件の独立照合が完了し、本分類処理を起動済み。実行対象数と有効分類数を分け、失敗・保留も元の分母に残す。採用可能な分類が1,000作品未満なら目標未達を記録し、原因を切り分け、許諾条件を満たす資料補充など必要な対応を担当側で進める。

Sol CLIは共有最大8枠、本分類は最大6枠。用途検査の8プロセスはLLM枠とは別である。収集・用途審査・評価・DB・品質審査を担当ごとに分担し、依存のない作業を並行する。同じ重い処理の重複実行、実行中の入力変更は避ける。

新しい本分類1実行の時間上限は24時間、batch5、並列6、1呼出900秒、token上限40M。元の設定と終了済みA/Bの条件は変更しない。24時間は停止上限であり、完了予定時刻ではない。入力固定後の実測に基づき見通しを更新する。停止時は未処理・保留を残し、全件完了と報告しない。

途中の保留30作品は、5バッチ25作品でSol提供側が `Selected model is at capacity` を返したものと、1バッチ5作品で応答の作品ID不一致・重複を検知したもの。前者の実終了コード1、後者の実終了コード0と検証拒否を保存し、成功・品質合格へ数えない。原実行と結果は書き換えず、全1,862対象の実行を監視する。

本分類後の品質審査・採用・画面検証も必要なため、全工程の完了時刻は現時点で固定しない。本分類の実測が得られた時点で残り時間の見通しを更新する。失敗した過去の比較評価を繰り返すことは、本分類開始の前提にしない。

## 自動評価の限界と成果物

- 根拠不足の軸はunknownを残す。Wikidataの主題・ジャンルを本文や独立正解に読み替えない。許可分類1,862作品のうち、あらすじ根拠277作品・279Evidence、主題811作品・811Evidence、両方125作品、どちらか963作品。識別・書誌情報中心854作品、Evidenceなし14作品。用途許可だけでは全6軸を判定できない。
- confidenceは未校正値。独立した正負例が不足する軸のprecision/F1・確率校正は未評価とし、AI同士の一致を独立正解にしない。
- 取得、保存、外部LLM入力、表示、再配布、商用を用途別に扱う。[用途審査範囲](source-rights-review-scope-v3.md)に従い、私的PoCの許可をToC商用許諾へ広げない。
- 保存済み335評点は読み取り専用で参照する。本人への推薦改善・満足度は未検証。読書状態不明を未読と断定しない。

専門家参照の開発95作品の主指標は定義方式0.110526、文脈方式0.157895。品質検証済みとは判定していない。世界比較で独立参照と対応する正例は4種類64件、すべて開発群であり、全6軸の精度評価には不足する。文脈方式の時間上限到達は通常完了の比較条件を満たさず、追加診断も過去timeoutの終了コード未取得により判定不能となった。評価指標を生成せず、拒否報告と独立レビューを保存した。

完了条件は、用途条件を満たす作品DB、本分類の全対象の実行記録、根拠・関連度・confidence・保留、実評価報告、ローカル画面の動作確認、再生成確認が揃うこと。テスト成功や終了コード0だけで分類精度の合格としない。1万〜3万、10万作品以上への拡張とToC公開は次段階とする。

## 現在地の証拠

すべて`data/autonomous-next-v3/`配下。原資料・個人評点・認証情報はGit管理外で保持する。過去の経緯は[v12](autonomous-next-phase-plan-v12.md)を参照する。

| 対象 | 保存先 |
|---|---|
| 新DB取り込み・全件独立監査 | `database/subject-source-base-v2-observation/writer-binding.json`、`independent-audit/subject-source-identity-review-v2/identity-review.json` |
| 全4,673入力の再生成 | `database/subject-current-projection-v2/projection/manifest.json`、`database/subject-current-projection-v2-observation/projection-end.json` |
| 全3群用途審査の正式出力・終端 | `rights/world-subject-projections-v1/manifest.json`、`rights/world-subject-projections-launch-v1/owner-direct-tool-terminal.json`、`rights/world-subject-projections-launch-v1/owner-terminal-end-verification.json` |
| 用途審査後の独立照合 | `independent-audit/world-subject-projections-v1-post-terminal-review-v1/report.json`、`independent-audit/world-subject-projections-v1-post-terminal-review-v1/canonical-review-v1.json` |
| 保存DBの実生成・独立監査 | `preparation/world-subject-sealed-writer-terminal-v1/terminal-observation.json`、`independent-audit/world-subject-sealed-v1-post-terminal-review-v1/report.json` |
| 別名の原資料対応 | `rights/world-subject-alias-language-v1/manifest.json`、`rights/world-subject-alias-language-launch-v1/owner-terminal-end-verification.json` |
| 最終単一台帳の実終了・独立監査 | `preparation/world-subject-final-ledger-terminal-v1/terminal-observation.json`、`independent-audit/world-subject-final-ledger-v1-post-terminal-review-v1/report.json` |
| 正式本分類入力・実prompt計測 | `preparation/world-main-ordered-inputs-v1/manifest.json`、`preparation/world-main-prompt-load-v1/measurement.json` |
| 本分類の実行前確認 | `preparation/world-main-standard-prepare-launch-v1/launch-observation.json`、`classification/world-main-context-v3-run-v1/preparation-manifest.json`（prepared_not_started、selected 1,862、cli_invocations 0。モデル起動許可ではない） |
| 本分類の実行条件の束縛 | `preparation/world-main-runtime-binding-v1/binding.json`、`preparation/world-main-runtime-binding-terminal-v1/observation.json`（実exit0、approved false、execution_go_issued false、モデル0） |
| 実束縛出力の独立監査 | `independent-audit/world-main-runtime-binding-review-v1/review-final.json`、`actual-audit-report-final.json`、`actual-tool-terminal-observation-final.json`（実51723 exit0、31項目通過。初回の監査側の形式想定違いによるFAILも保存） |
| 担当側の起動判断・実処理起動 | `preparation/world-main-model-root-go-v1/go.json`、`slot-observation.json`、`actual-tool-observation.json`、`preparation/world-main-runtime-binding-v1/launch-request.json`（実tool 53418を監視。実モデル開始と品質採用の証明とは別） |
| 実モデル開始・途中保存状態 | `preparation/world-main-model-root-go-v1/admission-observation.json`、`native-start-observation.json`（1,862入力・実条件一致、実Codexプロセス、途中60件のcompletedを確認。終端と意味品質は未確認） |
| 本分類500件の途中観測 | `preparation/world-main-model-root-go-v1/progress-500-observation.json`（実tool 53418稼働、500結果保存・保留10件。全対象の終端・意味品質・採用の証明ではない） |
| 本分類1,000件到達の途中観測 | `preparation/world-main-model-root-go-v1/progress-1000-observation.json`（23:50 JSTに1,015結果保存・保留25・実行中10件。全1,862対象を維持し、品質審査・正式採用は未実施） |
| 本分類1,500件の途中観測 | `preparation/world-main-model-root-go-v1/progress-1500-observation.json`（10月14日00:45 JSTに1,500結果保存・保留30件。実tool 53418を継続監視、終端・品質・採用は未確認） |
| 全3群の資料分布 | `rights/world-subject-coverage-v1/coverage.json`（資料の充足度。分類精度・地域代表性ではない） |
| 世界A/Bの実終端 | `classification/conssa-world-definitions-v3-run-v2/root-terminal-observation-v1.json`、`classification/conssa-world-context-v3-run-v2/root-terminal-observation-v1.json` |
| 専門家参照の評価 | `evaluation/conssa-native-only-results-v2/report.json` |
| 世界診断の判定不能・独立確認 | `evaluation/conssa-world-budget-limited-diagnostic-v1/rejection-report.json`、`independent-audit/conssa-world-budget-limited-diagnostic-review-v1/manifest.json` |
| 本分類方式のROOT固定 | `preparation/main-family-root-decision-v1/selection.json`（モデル起動許可ではない） |
| 本分類の正式入力固定処理 | `preparation/main-ordered-inputs-preparation-v1/assemble.py`（読み取りレビューと実入力生成が完了） |
| 本分類の限定資源補足 | `preparation/main-runtime-root-decision-v1/runtime-supplement.json` |
| 本分類実行処理の準備監査 | `independent-audit/main-runtime-context-preparation-review-v1/`（実入力の実行許可とは別） |

コミットは依頼済みだが、先行するブランチ作成が自動承認レビューに拒否され、未実施。変更はローカルに保持し、分類・検証を継続する。これはユーザーへの再承認依頼ではない。
