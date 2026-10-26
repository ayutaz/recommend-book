# ユーザー工数0で進める残作業計画 v11

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「履歴・結果・計画・スナップショット」です。本文の件数・計画・実行状態と、本文内の「現在」「最新」「実行中」は記録日時のものとして扱い、現在の状態は[current-status.md](current-status.md)を参照してください。

更新: 2026-10-13 13:25 JST。[v10](autonomous-next-phase-plan-v10.md)以降の実終了・用途審査・DB取り込み準備を反映する。この時刻の状況を記録し、実行中の検証を完了扱いにしない。

ユーザーの追加工数は0。本人評価、試読、感想、採点、読書履歴入力、正解作成、画面確認を依頼しない。収集、名寄せ、用途審査、分類、AI内部評価、画面操作、再生成、報告まで実施担当が行う。AIの評価はAI内部評価として記録する。

13:41 JSTの実行追記: 世界A/Bの実行前検証は両方式とも実終了コード0で終了し、全98件のnative提出記録・保留・子プロセス終了を確認した。固定済み世界A/Bの実行処理を2本並行開始し、同じ実行を追跡中。これは起動確認であり、世界A/Bの実終了・分類精度・正式採用の確認ではない。DB取り込み処理の独立35テストも通過したが、全841件の実データ検証は継続中。

追加の準備検証: 新DB用の用途台帳処理は独立42テストを通過した。DB生成時に記録した全6出力のSHAを開始・終了時に照合し、別名proofの権限フラグ・unknown roleと、単一選定方式を厳密に検査する。全completed作品・全ラベルのAI審査記録を束縛する処理も、独立37テストを通過した。ラベル0件の作品も審査対象とし、未レビュー・出典の付け替えを拒否する。いずれも実行準備の検証であり、実データの用途台帳固定・意味審査・分類精度の合格を示すものではない。

native-only実評価も終了した。開発95作品の主指標は定義方式0.110526、文脈方式0.157895で、固定基準は文脈方式を選ぶ。計画100作品の年齢完全一致は22/100対30/100、職業文字列完全一致は両方0/95。品質検証済みの判定はfalseで、年齢検出率はadult定数の参考値55/100を下回る。抜粋による不明判定と全作品の専門家注釈の差、職業の表現・型・言語の対応を別診断し、事後の同義語正解化や再選定は行わない。世界6軸の実出力・評価は未完了であり、このnative-only数値を全6軸の精度へ広げない。

職業の別診断では、正解が英語カテゴリ、実出力がスペイン語の具体的職名であり、現prompt/schemaに英語出力の指定がないことを確認した。doctorとmédicoのような対応があるため、0/95だけで意味上の全誤りとは判断できない。言語だけで全相違を説明できるとも判断せず、固定スコアと方式選定は維持する。

正式入力の進捗: 全841件の実前処理が終了コード0で終了し、811件×8用途の6,488検査と最終ハッシュ照合を通過した。独立した終了後の照合も通過したため、ROOTがsource-only取り込みの許可記録を発行し、新しい保存先への811出典版の取り込み処理を開始した。正式DB生成の終了・作品対応監査・投影再生成・最終入力固定はまだ完了していない。保留30件と多言語42件は採用対象外のまま。

## 最新の現在地

| 対象 | 確認済みの状態 | 残っていること |
|---|---|---|
| 作品単位のDB | 2,005作品、分類入力候補1,985。追加252作品分の出典版・259根拠を統合し、作品対応・投影の独立監査を通過 | 審査済みの追加根拠を新しいDBへ取り込み、正式入力を再生成 |
| 現実装 | 全体1,378テスト成功、対象203ファイルの前後SHA一致 | 実分類の意味品質・新DBでの画面・再生成は別途確認 |
| 基礎入力の用途審査 | 分類1,985入力は1,862許可・123保留。書誌2,005入力は1,870許可・135保留。別名683入力は600許可・83保留。全群の生成が終了し、全件の構造・ハッシュ照合も通過 | 新DBから生成する最終入力全件を現行の用途チェックへ通し、実行前に台帳を固定。既存審査を新しい入力へ流用しない |
| Wikidataの追加根拠 | 841候補すべての用途審査と最終照合が終了。811件が取り込み候補、30件が保留。保留30件×8用途の再検査もすべて否認を維持 | DB取り込み処理の全841件に対する読み取り専用検証が実行中。終了後の独立レビューと正式取り込みは未了 |
| DB取り込み処理 | 元の候補生成の実終了記録との対応を強化。反例を含む35テスト成功。旧処理・原資料・基礎DB・203保護ファイルは不変 | 全件検証と独立レビュー後に、担当側で取り込み条件を確認して新DBを生成 |
| 専門家参照A/B | Sol CLIの両実行が終了コード0で終了。各98作品中93件成功・5件保留。両方の子プロセス終了も確認 | 原応答・全98件の提出記録・出典との対応を検証し、固定基準で実スコアを集計。終了コード0は分類精度の合格を意味しない |
| 世界6軸72ラベルA/B | 98作品の準備・独立事前監査は完了。native A/Bの記録を照合する実行前検証が進行中 | 検証終了後に2方式を並行実行。現時点の世界A/Bモデル実行は0件 |
| 多言語資料42件 | 取得・原資料照合済み。40件は外部LLM入力・表示等の審査を通過、2件はメタデータ条件保留。取得・保存は42件とも現行の混合出典モデルの技術的制約で保留 | 正式DBへの取り込みを保留して証拠を保存。必要条件を満たした基礎入力とWikidata根拠で進める |
| 本分類・正式採用 | 現在の新DBに対する本分類は未実行、新しい推薦特徴の正式採用は0件 | 正式入力と選定方式を固定し、本分類・品質審査・DB反映を実施 |
| ローカル納品 | 新DB向けの3方式比較、6軸分類検索、再生成比較の処理を準備済み | 現在の正式分類結果から生成し、担当側で実画面・再生成を確認 |

別名683は入力数で、対象は540作品、許可入力を持つのは495作品。252作品分・841作品分・42作品分は既存作品への資料補充であり、作品総数へ加算しない。811件も正式DB採用件数ではない。

Wikidataの主題・ジャンルは出典の主張として保存する。物語本文、独立した正解、人手評価へ読み替えない。基礎の許可入力1,862件のうち、あらすじ根拠を持つのは277作品で、識別・書誌情報中心の入力が1,521件ある。用途が許可されていても、全6軸を判定できるとは限らない。追加根拠によってジャンル・テーマの支持を増やし、構造・主人公・文体・雰囲気は資料が足りなければunknownを残す。

## ここからの実行順序

| 順序 | 実施担当が行うこと | 完了条件 |
|---|---|---|
| 1. 正式入力を確定 | 実行中の全841件のDB取り込み前検証を終え、独立レビューを行う。条件を満たした811候補だけを別出典版として取り込み、元の書誌・根拠・作品対応を照合する。新DBから入力を再生成し、全件の用途・型・出典対応を検証する | 作品対応、原資料、出典版、入力、用途台帳が一致し、本分類開始前に入力と設定が固定される。30保留と保存条件未達の42件を取り込まない |
| 2. 分類方式を選定 | 終了済みnative A/Bの提出記録を照合して集計。次に世界6軸72ラベルA/Bを実行し、固定済みの開発95作品の基準で方式を選ぶ | 成功・保留・失敗、原応答、実終了、利用量、実スコアを記録。評価3作品で方式を選び直さず、欠測を計画分母に残す |
| 3. PoC本分類 | 1と2が揃ったら、1,000〜3,000作品のPoC範囲で選定方式をSol CLIにより実行する。現在の2,005作品DBから、最終条件を満たした入力を対象にする | 対象・実処理数、軸別の判定可能率、unknown、失敗・保留、根拠、関連度、未校正confidenceを確定。実行対象数と有効な分類が得られた作品数を分けて報告 |
| 4. 分類品質を検証 | 全件の形式、引用、出典対応、実CLI記録を確認。別担当が意味支持、対象違い、版の範囲、矛盾を審査し、言語・年代・軸・資料状態別に結果をまとめる | 原応答とAI内部審査が対応する。対象違い・矛盾は採用を保留し、曖昧な結果は不確実として保存。独立正解との一致とAI審査を分ける |
| 5. DB・評価報告・ローカルPoCを仕上げる | 品質審査を通した分類だけを新DBへ反映。書誌閲覧、6軸分類検索、3方式の参考推薦を生成し、担当側で実画面を操作。別保存先への再生成と内容比較、最終監査を行う | DB、実評価報告、画面、再生成手順、保留・未検証一覧が揃う。ユーザー確認待ちを完了条件にしない |

1と2を並行する。品質・納品処理の準備と独立レビューも並行する。5は依頼範囲の工程1〜4を再現・確認できる成果物にする作業であり、本人の嗜好評価を追加する工程ではない。

分類を実行できる作品数が最終入力で1,000未満なら、PoCの件数目標は未達と記録し、許諾条件を満たす資料補充を担当側で進める。全軸にラベルを付けるための推測や、保留入力の取り込みで件数を満たさない。

## 並列実行と進め方

Sol CLIは共有最大8枠、本分類最大6枠、参照評価最大2枠。native A/Bの終了・子プロセス終了により解放した2枠を、実行前検証終了後に世界A/Bへ引き継ぐ。担当ごとの保存先・書き込み範囲を分け、実行中の入力・設定・実装を固定する。

用途審査は実データ20件の検証後、8プロセスで全841候補を処理し、6,728件の用途別判定を生成した。採用候補811件×8用途の6,488件を最終入力として再検査し、全件が通過した。DB取り込み前の現行検証は別の条件を確認しており、その終了を待つ。担当間で同じ重い検証を重複起動しない。

観測待ちの時間切れを実行失敗として扱わず、同じ実行を追跡する。native A/Bの各5件は、バッチ内の1件が主人公属性の値を欠いたためバッチ全体で保留になった。残り4件も部分採用せず、この固定比較では5件すべてを欠測として分母に残す。実行後の再試行結果で置き換えない。

## ユーザー作業なしで評価する範囲

専門家参照の年齢・職業と、世界6軸72ラベルは別課題として報告する。参照100候補中98作品が許可、2作品が保留。許可入力は開発95・評価3作品。比較できる世界ラベル4種類の正例64件はすべて開発群にあり、全6軸の独立評価精度を示すデータではない。

方式選定の主指標は、開発群の年齢完全一致率と職業完全一致率の平均。同点は定義提示方式を選ぶ。保留・欠測・unknownは不一致として分母へ残し、評価群を選定へ使わない。世界6軸の実出力は対応する出典正例との一致を別集計し、参照の回答を世界ラベルへ変換して評価結果にしない。

引用の再現、AI内部審査による意味支持、独立正解との一致、確率校正を分ける。正解・正負例が不足する軸はprecision/F1や確率校正を未評価として記録する。関連度とraw confidenceは保存し、confidenceを校正済み確率として表示しない。本人評価を求めず、AI同士の一致だけで分類精度を確定しない。

保存済み335評点は読み取り専用で参照する。既存の書名類似比較のnDCG@10は0.452627で、ID順0.497428・固定乱数0.510569を上回っていない。新しい本人評点を作らず、読書状態不明は「読書状態未確認」と表示する。本人への推薦改善・将来の満足度は未検証として残す。

取得・保存・外部LLM入力・表示・再配布・商用は用途別に扱う。[用途審査範囲](source-rights-review-scope-v3.md)に従い、私的利用の審査を商用や全世界の許可へ広げない。多言語42件の保存保留は現行モデルの技術的制約であり、出典ライセンスによる保存禁止を認定したものではない。新規契約・問い合わせが必要な素材は保留する。

## 完了条件と見積もり

正式用途で利用できる作品DB、実行済みの本分類、関連度・未校正confidence・根拠・保留、実評価報告、ローカル動作と再生成の確認を揃える。書誌2,005件、用途審査通過、テスト成功、実終了コード0だけで工程1〜4を完了としない。

完了時刻は未確定。現在の主な依存は、新DB取り込み前の全件検証と独立レビュー、正式入力の固定、世界A/B実評価、本分類と品質審査。実時間と進捗から更新し、未評価範囲をユーザー作業で補わない。ライトノベル、年代、国・言語を含む全世界の代表性は未達。1万〜3万・10万作品への拡張、ToC公開、push、merge、新規有料契約は次段階。

## 証拠の保存先

原資料、個人評点、認証情報はGit管理外。本文へ転載しない。

| 証拠 | 保存先 |
|---|---|
| 現在のDB投影 | `data/autonomous-next-v3/database/bibliographic-enriched-final-v1/manifest.json` |
| 全体1,378テスト | `data/autonomous-next-v3/verification/all-v5/terminal-observation.json` |
| 基礎入力の全群審査・件数 | `data/autonomous-next-v3/rights/world-projections-v5/manifest.json`、`data/autonomous-next-v3/rights/world-projections-v5-coverage-v1/coverage.json` |
| 基礎入力の独立構造監査 | `data/autonomous-next-v3/independent-audit/world-projections-v5-structural-review-v1/delivery-manifest.json` |
| Wikidata全841候補の審査完了 | `data/autonomous-next-v3/rights/wikidata-subject-admission-handoff-v1/manifest.json` |
| 候補・実終了・採用候補・保留の対応 | `data/autonomous-next-v3/rights/subject-candidate-bridge-v4/candidate-bridge.json` |
| DB取り込み処理の35テスト・固定状態 | `data/autonomous-next-v3/database/subject-source-preparation-v3/verification.json` |
| 全841件の取り込み前検証の開始観測 | `data/autonomous-next-v3/database/subject-full-preflight-v3-observation/running-observation.json` |
| 全841件の実検証終了・終了後の独立照合 | `data/autonomous-next-v3/database/subject-full-preflight-v3-observation/terminal-observation.json`、`data/autonomous-next-v3/independent-audit/subject-source-preparation-v3-review-v2/post-terminal-review.json` |
| source-only取り込みのROOT判断・実終了 | `data/autonomous-next-v3/preparation/subject-source-root-go-v1/source-go.json`、`data/autonomous-next-v3/preparation/subject-source-root-go-v1/actual-tool-terminal.json` |
| 811出典版の取り込み処理の開始観測 | `data/autonomous-next-v3/database/subject-source-base-v1-observation/writer-start.json` |
| native Aの実終了 | `data/autonomous-next-v3/classification/conssa-native-definitions-v3-run-v2/root-terminal-observation-v1.json` |
| native Bの実終了 | `data/autonomous-next-v3/classification/conssa-native-context-v3-run-v2/root-terminal-observation-v1.json` |
| nativeの子プロセス終了・2枠解放 | `data/autonomous-next-v3/preparation/native-run-v2-terminal-proof-root-v1/slot-release.json` |
| 世界A/Bの実行前検証の実終了 | `data/autonomous-next-v3/preparation/conssa-world-go-terminal-root-v1/terminal-observation.json` |
| 世界A/Bの実行処理開始・追跡対象 | `data/autonomous-next-v3/preparation/conssa-world-run-root-start-v1/start-observation.json` |
| DB取り込み処理の独立35テスト・全件検証待ち | `data/autonomous-next-v3/independent-audit/subject-source-preparation-v3-review-v1/report.json` |
| 新DB向け用途台帳処理の修正・独立42テスト | `data/autonomous-next-v3/independent-audit/world-final-ledger-preparation-v3-review-v1/report.json` |
| 全completed作品・全ラベルのAI審査記録処理の独立37テスト | `data/autonomous-next-v3/independent-audit/semantic-review-preparation-review-v1/manifest.json` |
| 世界A/Bの独立事前監査 | `data/autonomous-next-v3/independent-audit/conssa-world-final-context-review-v2/delivery-manifest.json` |
| 固定した参照評価仕様 | `data/autonomous-next-v3/evaluation/conssa-native-metrics-v2/metric-spec.json` |
| native-only実評価・品質未達の記録 | `data/autonomous-next-v3/evaluation/conssa-native-only-results-v2/report.json`、`data/autonomous-next-v3/evaluation/conssa-native-only-results-v2/README.md` |
| 職業0/95の表現・言語・型・粒度の別診断 | `data/autonomous-next-v3/evaluation/conssa-profession-diagnostic-v1/diagnosis.json` |
| 多言語42件の用途別結果・保留 | `data/autonomous-next-v3/rights/multilingual-wikipedia-admission-v1/manifest.json` |
| 正式採用前検証の独立確認 | `data/autonomous-next-v3/independent-audit/quality-adoption-tools-v5-root-review-v1/report.json` |

コミットは依頼済みだが、作業ブランチ作成が実行ポリシーに拒否され、承認要求も無効のため未実施。変更と証拠はworktreeに保持する。分類・評価・ローカル納品の作業は継続する。
