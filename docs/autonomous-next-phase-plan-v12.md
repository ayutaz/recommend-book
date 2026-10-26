# ユーザー工数0で進める残作業計画 v12

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「履歴・結果・計画・スナップショット」です。本文の件数・計画・実行状態と、本文内の「現在」「最新」「実行中」は記録日時のものとして扱い、現在の状態は[current-status.md](current-status.md)を参照してください。

更新: 2026-10-13 17:41 JST。[v11](autonomous-next-phase-plan-v11.md)からの実行状況を反映した。以下の現在地と実行順序を最新の計画とする。

本人評価もユーザー工数に含まれるため、依頼しない。試読、感想、採点、読書履歴入力、正解作成、画面確認も不要とする。担当側で収集・統合・分類・検証・ローカル納品まで実施する。本人の満足度など、自動評価では確かめられない点は未検証として記録する。

## 最新の現在地

| 対象 | 確認済み | 未完了 |
|---|---|---|
| 作品DB | 2,005作品、分類入力候補1,985。811出典版の新DB取り込みと全件の独立監査が完了。書誌2,005・分類候補1,985・別名683、全4,673入力を再生成しハッシュ・参照順序を照合 | 再生成した全3群の用途審査、最終台帳と本分類入力の固定 |
| 基礎入力の用途審査 | 分類1,862入力、書誌1,870入力、別名600入力が許可 | 新DBから生成する入力の全件再審査・実行前固定 |
| Wikidata追加根拠 | 全841候補の事前検査、811出典版の取り込み、全件の独立監査、入力再生成が完了。旧42表・5履歴保存表、作品対応・書誌6項目・expressionを保全。保留30件は維持 | 新入力への用途許可はまだ確定していない。全3群の用途審査を継続する |
| 専門家参照A/B | Sol CLIの両実行が終了。各98作品中93成功・5保留。固定評価の集計と職業表現の別診断も終了 | 全6軸の精度・本分類の品質への一般化はできない |
| 世界6軸72ラベルA/B | 定義方式は実終了コード0。98作品中65件が形式検証通過、33件が保留、pending=0。文脈方式は同じ実行を継続中 | 文脈方式の実終了、両方式の固定評価の集計、最終方式確定。形式検証通過数を意味精度へ読み替えない |
| 品質審査・用途台帳の処理 | 新DB用台帳処理の独立42テスト、全completed作品・全ラベルのAI審査記録処理の独立37テストが成功 | 実際の新DBに対する台帳固定、本分類結果の意味審査 |
| 本分類・正式採用 | 本分類の実行・採用条件を準備 | 現在の新DBを対象とする本分類は未実行。新しい推薦特徴の正式採用は0件 |
| ローカル納品 | DB反映、3方式比較、6軸検索、再生成比較の処理を準備 | 正式分類結果を使った実画面操作、別保存先への再生成、最終監査 |

14:40時点の世界A/Bには、それぞれ7件・5件の実行試行要約があり、結果検証エラーも記録されている。これは作品数ではなく途中の試行数で、最終成功率ではない。実終了記録は未取得。観測待ちを実行失敗として扱わず、同じ実行を追跡する。

14:49 JSTの追加診断: 終了済みの失敗バッチだけを原応答と照合した。Aは4バッチ20作品、Bは2バッチ10作品が保留。引用4箇所は出典文字列と一致せず、Unicode正規化や画面表示の違いでは説明できない。軸・作品／版scopeの違反も実structured値に存在した。バッチ内の他の結果が逐件の形式検証を通過しても、この固定比較では部分採用しない。これは途中結果の原因診断であり、最終成功率・意味精度ではない。

終了済み14呼出の実測から、同程度の時間を要する約2,000入力をbatch5・並列6で処理する場合、単純な67波の換算は約7.5〜12.9時間となる。長い参照本文を使った実測であり、本分類の所要時間を保証する値ではない。既存4時間枠で全件を処理できる根拠は不足しているため、独立検討を経て14:57 JSTに新しい本分類1実行だけの時間上限を24時間とする補足を固定した。元設定のファイル、現在のA/B、評価基準は変更しない。本分類の実入力・実行設定・準備・独立監査へ補足のSHAを束縛してから起動する。これは時間上限であって完了予定時刻やモデル起動許可ではなく、40M token上限・本分類6並列・共有8枠・rate停止は維持する。新しい本分類の実prompt量の事前確認も必要である。

15:11 JSTの準備更新: 実prompt生成APIで全入力を順序どおりbatch5へ構成し、本文を複製せず文字数・UTF-8 bytes・SHAを記録する計測処理を用意した。レビューで見つかった読み取り途中の入力変更を別版v2で修正し、実際の反例3件を再現してから関連12テストを通過した。正式入力の実計測はまだ0件。サイズ測定だけではtoken数、用途許可、モデル起動を認定しない。

終端契約の訂正も保存した。実処理全体はpending=0、stopped=false、selected=completed+failedと件数対応を要求し、held・invalid_cached_resultを非採用として元の分母へ残せる。全jobをcompletedへ変える条件ではない。完了した結果には別途、実native・用途・出典・品質審査が必要である。

15:25 JSTの取り込み結果: source-only writerは実終了コード1で停止した。811件の事前6,488検査と取り込み、作品統合状態が不変である検査まで通過したが、名寄せ判断テーブル`resolution_decisions`の変化を既存テーブルの検証が拒否した。仮保存先は清掃され、新DB公開0、元DB・core203・原入力・旧許可記録は不変。既存コードは出典版の変更に伴って判断ID・有効状態を再生成するため、担当2名が少数の別コピーで全テーブル差分を調査している。旧処理・証拠は保持し、単純な許可範囲の緩和や同じ失敗実行の再起動を行わない。必要な履歴保持と版参照の検証を別版で整え、独立レビューと新しい取り込み判断を経て進める。

811件は既存作品への追加出典版であり、作品総数に加算しない。別名683入力は540作品分で、許可入力を持つのは495作品。多言語資料42件は保存審査上の技術的保留により正式採用しない。

16:14 JSTの更新: 出典版更新に伴う名寄せ判断ID・有効状態の変更を、既存履歴の保存と区別して検証する修正版v4を固定した。64テストの担当側実行と独立実行、実3件の別コピーの全テーブル照合、最終コード・手順・証拠の独立レビューを通過した。全841件の新しい事前検査は未開始であり、旧検査・旧取り込み判断は流用しない。次に新しい8プロセス検査を行い、終了記録と独立照合に基づいて担当側で取り込みを判断する。これはユーザーへの承認依頼ではない。世界A/Bの既存2実行は同じ実行を追跡しており、この時点でも実終了は未取得。

## ここからの実行順序

17:40 JST頃の実行更新: 新DB取り込み`50627 / exit 0 / a62a61`、全811件の独立監査`54962 / exit 0 / db0f0a`が終了し、25検査が通過した。ROOTは別の再生成判断を`20276 / exit 0 / e04031`へ固定した。入力再生成`91741 / exit 0 / 6c4051`も完了し、全4,673入力のfullhash・JSONLとSQLiteの対応・参照順序を照合、孤児0、旧next6表の保存を確認した。用途審査の基準はINNER `subject-current-projection-v2/projection/manifest.json`である。全3群の用途審査は担当の実tool `13351`で進行中。tool handleは担当間で共有できないことが判明したため、元担当が同じhandleを追跡し、ROOTは実Windowsプロセスのhandleを別途保持する観測tool `62572`を開始した。再起動0、審査のpartial出力を正式許可へ昇格しない。本分類・新分類採用は0のままである。

16:52 JST時点の実行更新: 修正版事前検査は`44584 / exit 0 / f393c2`で終了し、全841候補・811対象・30保留、8プロセス6,488検査、保護入力不変を終了後に独立照合した。実監査報告と取り込み判断処理の形式不一致は、旧処理の実拒否を再現して別版v2で修正し、実packageの読み取り検証と関連21テストを通過した。ROOTは実TTY `93964 / exit 0 / 2df841`で別のSource GOを発行し、担当がNEW `database/subject-source-base-v2`へ実writer `50627`を開始した。新DBの成功確定はまだなく、Projection GO・本分類GO・モデル起動は未実施。世界の定義方式はROOTが`65510 / exit 0 / 126911`を直接観測し、65成功・33保留の終端証拠を保存した。文脈方式`42439`は継続中。

16:18 JST以降の実行更新: 新しいreadonly事前検査を実tool session `44584`で開始し、担当が同じhandleを追跡している。保存先は`database/subject-full-preflight-v4`。開始証拠と実処理の観測は`database/subject-full-preflight-v4-observation/`に保存する。開始は成功を意味せず、新しい取り込み判断・DB生成・本分類は未実施。世界A/Bも既存handle `65510` / `42439`の生存を直接確認して追跡中。

最初の2工程を並行し、その両方が揃ってから本分類へ進む。以下の確認・判断は担当側で行い、ユーザー確認待ちを設けない。

| 順序 | 担当側で実施する作業 | 完了条件 |
|---|---|---|
| 1. 正式入力を確定 | 新DB生成・独立監査・入力再生成は完了。実行中の全3群の用途審査を追跡し、実終了後に独立照合する。その後に保存DBを固定し、別名の原物対応を再検査する。最終方式の入力、設定、単一の用途台帳を固定する | 新DBと全入力の対応が確認でき、本分類に使う単一方式・台帳・入力が固定される。保留30件と42件を除外したままにする |
| 2. 世界A/Bを終了・評価 | 固定した2方式のSol CLI実行を追跡し、実終了・原応答・成功・失敗・保留を確認する。終了済みの専門家参照結果と合わせ、事前固定の評価基準で最終方式を確定する | 欠測を分母に残した実評価報告ができる。評価群で選び直さず、失敗した比較結果を再実行結果で置き換えない |
| 3. PoC本分類 | 1と2の完了後、現在の2,005作品DBから最終条件を満たす入力を対象に、選定方式で6軸72ラベルの分類を実行する。Sol CLIを最大6枠で並列実行する | 処理件数、有効分類件数、軸別判定可能率、unknown、失敗・保留、根拠、関連度、raw confidenceを記録する |
| 4. 分類品質を検証 | 全件の形式、引用、出典、実CLI記録を確認する。独立したAI内部審査で、全completed作品と全ラベルの意味支持、作品違い、版の範囲、矛盾を確認する。言語・年代・軸・資料状態別に集計する | 原応答と審査記録が対応する。対象違い・矛盾は採用保留、曖昧さは不確実として記録する。独立正解との一致、AI内部審査、確率校正を区別する |
| 5. 成果物を仕上げる | 採用条件を満たした結果だけを新DBへ反映し、書誌閲覧・6軸検索・参考推薦のローカル画面を生成する。担当側で画面を操作し、同じ固定分類結果から別保存先へ再生成して比較する | DB、実評価報告、画面、再生成手順、保留・未検証一覧が揃い、独立監査が終了する |

5は依頼された工程1〜4の成果物を再現・確認する作業であり、新しい本人評価を追加するものではない。

本分類の対象は1,000〜3,000作品のPoC範囲とする。入力の最終審査結果によって件数を確定する。対象が1,000未満なら件数目標未達と明記し、許諾条件を満たす資料補充を担当側で進める。有効分類が得られた作品数は実行対象数と分けて報告する。

## 分類・評価で守る条件

- 根拠が不足する軸はunknownを残す。Wikidataのジャンル・主題は出典の主張として扱い、本文や独立正解に読み替えない。
- 基礎の分類許可入力1,862件のうち、あらすじ根拠があるのは277作品。識別・書誌情報中心の入力が1,521件あり、用途許可だけでは6軸を判定できない。追加根拠は主にジャンル・テーマを補う。
- confidenceは未校正値として保存する。独立した正負例が不足する軸のprecision/F1、確率校正は未評価とする。AI同士の一致を独立正解にしない。
- 取得・保存・外部LLM入力・表示・再配布・商用を用途別に審査する。[用途審査範囲](source-rights-review-scope-v3.md)に従い、私的利用の審査を商用許諾へ広げない。

専門家参照の固定結果は、開発95作品の主指標が定義方式0.110526、文脈方式0.157895で、基準上は文脈方式が優位。計画100作品の年齢完全一致は22/100対30/100、職業文字列完全一致は両方式0/95で、品質検証済みとは判定していない。

職業では英語の正解カテゴリとスペイン語の出力職名の表現・粒度の不一致を確認した。0/95は元の文字列の一致数であり、意味上の全誤りを示すものではない。言語だけで全相違を説明できるとも判断しない。事後の翻訳・同義語対応で固定スコアを上書きしない。

世界ラベルの比較可能な正例は4種類・64件で、すべて開発群にある。この参照だけでは全6軸の独立評価精度を示せない。世界A/Bの実出力と対応する正例の一致を別集計する。

保存済み335評点は読み取り専用で参照する。新しい本人評点を作らない。本人への推薦改善や将来の満足度は未検証とする。読書状態不明は「読書状態未確認」と表示し、未読と断定しない。

## 並列実行・完了条件

Sol CLIは共有最大8枠。本分類最大6枠、参照評価最大2枠。世界A/Bの2本を追跡し、並行して修正版によるDB取り込み前の再検査・独立照合を進める。用途検査の8プロセスはLLM呼び出し枠とは別である。担当ごとに保存先と書き込み範囲を分け、実行中の入力・設定・実装を変更しない。同じ重い検証を別担当が重複起動しない。

工程1〜4の完了条件は、用途条件を満たす作品DB、本分類の実行記録、関連度・未校正confidence・根拠・保留、実評価報告、ローカル動作・再生成の確認が揃うこと。テスト成功や終了コード0だけで分類精度の合格としない。

完了時刻は現時点で未確定。依存は、修正版での全件事前検査・新DB生成と実行中の世界A/B、その後の入力固定、本分類、意味審査である。本分類の24時間は停止上限であり、納期の保証ではない。実時間と実処理件数に基づいて見通しを更新する。全世界の年代・国・ジャンルの代表性、ライトノベルの十分な網羅、1万〜3万・10万作品への拡張、ToC公開は次段階とする。

## 現在地を確認する証拠

原資料・個人評点・認証情報はGit管理外とし、本文へ転載しない。過去の詳細は[v11の証拠一覧](autonomous-next-phase-plan-v11.md#証拠の保存先)を参照する。

| 対象 | 保存先 |
|---|---|
| 基礎DB | `data/autonomous-next-v3/database/bibliographic-enriched-final-v1/manifest.json` |
| 追加根拠の全件事前検証・独立照合 | `data/autonomous-next-v3/database/subject-full-preflight-v3-observation/terminal-observation.json`、`data/autonomous-next-v3/independent-audit/subject-source-preparation-v3-review-v2/post-terminal-review.json` |
| source-only取り込み判断・開始（旧v3履歴） | `data/autonomous-next-v3/preparation/subject-source-root-go-v1/source-go.json`、`data/autonomous-next-v3/database/subject-source-base-v1-observation/writer-start.json` |
| source-only取り込み停止・元データ保持 | `data/autonomous-next-v3/database/subject-source-base-v1-observation/failed-terminal-observation.json` |
| 出典版更新の修正版v4・最終独立レビュー | `data/autonomous-next-v3/database/subject-source-preparation-v4/verification.json`、`data/autonomous-next-v3/independent-audit/subject-source-preparation-v4-review-v1/report.json` |
| 実3件での修正版全テーブル照合 | `data/autonomous-next-v3/database/subject-source-forensic-v4-verification-v1/report.json` |
| 修正版の新しい全件検査開始 | `data/autonomous-next-v3/database/subject-full-preflight-v4-observation/start-observation.json` |
| 修正版の全件検査実終了・独立照合 | `data/autonomous-next-v3/database/subject-full-preflight-v4-observation/terminal-observation.json`、`data/autonomous-next-v3/independent-audit/subject-source-preparation-v4-review-v2/post-terminal-review.json` |
| 新しいSource GO実発行・取り込み開始 | `data/autonomous-next-v3/preparation/subject-v4-source-root-go-v1/actual-tool-observation.json`、`data/autonomous-next-v3/database/subject-source-base-v2-observation/writer-start.json` |
| 新DB取り込み成功・全件独立監査 | `data/autonomous-next-v3/database/subject-source-base-v2-observation/writer-binding.json`、`data/autonomous-next-v3/independent-audit/subject-source-identity-review-v2/identity-review.json` |
| 全入力の再生成と保存履歴確認 | `data/autonomous-next-v3/database/subject-current-projection-v2/projection/manifest.json`、`data/autonomous-next-v3/database/subject-current-projection-v2-observation/projection-end.json` |
| 全3群用途審査の起動・実プロセス観測 | `data/autonomous-next-v3/rights/world-subject-projections-launch-v1/start-boundary.json`、`data/autonomous-next-v3/preparation/world-subject-producer-root-os-observation-v1/observer-start.json` |
| 世界定義方式の実終端 | `data/autonomous-next-v3/classification/conssa-world-definitions-v3-run-v2/root-terminal-observation-v1.json` |
| 世界A/B開始 | `data/autonomous-next-v3/preparation/conssa-world-run-root-start-v1/start-observation.json` |
| 世界A/B途中記録 | `data/autonomous-next-v3/classification/conssa-world-definitions-v3-run-v2/`、`data/autonomous-next-v3/classification/conssa-world-context-v3-run-v2/` |
| 専門家参照の実評価・別診断 | `data/autonomous-next-v3/evaluation/conssa-native-only-results-v2/report.json`、`data/autonomous-next-v3/evaluation/conssa-profession-diagnostic-v1/diagnosis.json` |
| 世界A/B途中の実応答診断・実測時間 | `data/autonomous-next-v3/evaluation/conssa-world-failure-diagnostic-v1/report.json`、`data/autonomous-next-v3/evaluation/conssa-world-failure-diagnostic-v1/terminal-observation.json` |
| 本分類の資源見積もり・限定した時間枠の補足 | `data/autonomous-next-v3/evaluation/main-resource-estimate-v1/report.json`、`data/autonomous-next-v3/preparation/main-runtime-root-decision-v1/runtime-supplement.json`、`data/autonomous-next-v3/preparation/main-runtime-root-decision-v1/actual-tool-observation.json` |
| 終端・保留・採用の区別を実コードに照合した訂正 | `data/autonomous-next-v3/evaluation/main-resource-estimate-contract-addendum-v1/addendum.json` |
| 本分類の入力サイズ計測処理v2・レビュー | `data/autonomous-next-v3/preparation/main-prompt-load-preparation-v2/implementation-report.json`、`data/autonomous-next-v3/independent-audit/main-prompt-load-preparation-v2-root-review-v1/report.json` |
| 新DB用台帳・意味審査処理の独立検証 | `data/autonomous-next-v3/independent-audit/world-final-ledger-preparation-v3-review-v1/report.json`、`data/autonomous-next-v3/independent-audit/semantic-review-preparation-review-v1/manifest.json` |

コミットは依頼済みだが、前回の作業ブランチ作成が実行ポリシーに拒否され、承認要求も無効のため未実施。変更はworktreeに保持されている。分類・評価・ローカル納品の進行は継続する。
