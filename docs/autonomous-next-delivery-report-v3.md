# 世界小説PoCの実生成・検証結果 v3

2026-10-15更新。新しい本人作業・評価・履歴入力は0件。最新追加表示は1,989候補・特徴あり1,004件（LLM分類820＋資料記載184）・暫定得点213件。特徴1,000件の運用目標に到達した。連載URLと原HTMLの確定関係は2→10件、内容特徴がある履歴は2/335件のまま。独立精度・校正・推薦品質の不足は残り、PoC全体の完了とは扱わない。元の「工程4まで」の未達も保持する。実行順序は[計画 v15](autonomous-next-phase-plan-v15.md)を参照する。

実装は[PR #2](https://github.com/ayutaz/recommend-book/pull/2)でmainへ統合済み。その実装版の[Windows/Ubuntu CI](https://github.com/ayutaz/recommend-book/actions/runs/38701147898)は成功。README整理もPR #3で統合した。以下の過去の節で「未実行」「次」とある記述は、各節のスナップショットに対する記録として読む。

主要3ジャンル・名寄せの[品質評価仕様](quality-evaluation-protocol-v1.md)は、ユーザー指定により追加費用0・外部委託なし・Codex中心へ変更した。見積照会の許可は撤回され、問い合わせ送信・発注・支出は0。既存資料の適合正負gold0・独立精度未測定は変わらない。Codex診断はllm/silverとして区別する。旧仕様準備時の[ローカル検証記録](../data/autonomous-next-v3/evaluation/phase3-quality-evaluation-preparation-v1/verification.json)は過去の版として保持する。

[更新経路の統合検証](dataset-update-verification-v1.md)は、合成2作品で実DB・用途判定・推薦処理を接続し、訂正・再投入・再オープン・取込例外rollback・統合取消・利用許可の撤回を確認した。候補・推薦件数は2→2→1→2→1、旧snapshotは保持。関連98試験は成功。内容特徴の再分類・クラッシュ復旧・実作品での品質検証ではない。[公開評点の調査](public-rating-evaluation-feasibility-v1.md)は3候補で許諾未確定、評点取得・実評価0。独立注釈なしの[品質監査](../data/autonomous-next-v3/evaluation/phase4-quality-readiness-v1/readiness.json)はhuman_validation_pending/uncalibratedを確認した。

## 当初の「まずは3まで」に対する確認

最終ゴールの拡張計画とは分け、依頼時の3項目を以下で確認する。10万作品への拡張や72ラベルの品質合格を、この3項目の成立へ読み替えない。

| 当初の項目 | 実際の証拠 | 到達点・残作業 |
|---|---|---|
| 1. 現在成果の整理・Git反映・再現性 | 本報告、[別保存先への再生成と5出力のbytes一致](../data/autonomous-next-v3/database/phase3-personal-poc-reproduction-v1/reproduction-observation.json)、最新表示のmanifest・生成コード・出力の照合 | 整理と再現記録はある。Git切り替えの環境拒否は解消を確認。追加分の反映はブランチ履歴・リモート先頭と当該CIで確認し、既存38コミットの成功を流用しない |
| 2. 別コホートによる独立評価の基盤 | [CoNSSAの固定100作品・native年齢指標と欠測処理](../data/autonomous-next-v3/evaluation/phase3-reference-route-v1/report.md)、[Frenchの事前仕様](../data/autonomous-next-v3/evaluation/phase3-french-native-pilot-v1/preregistration.json)と[実評価](../data/autonomous-next-v3/evaluation/phase3-french-native-pilot-v1/report.json) | 注釈由来・対象作品・評価できるラベル・分母・保留・比較基準を固定した限定経路は成立。正負注釈を要するprecision・F1・校正、現72ラベルと名寄せの独立品質は未成立 |
| 3. 現在作品群と既存335評点による推薦PoC | [最新推薦画面と検証](../data/autonomous-next-v3/database/phase3-personal-poc-with-declared-genres-v2/verification.json)、[事前固定比較](../data/autonomous-next-v3/evaluation/phase3-personal-comparison-v1/preregistration.json)、[全335評点を残した比較結果](../data/autonomous-next-v3/database/phase3-personal-poc-v1/report.json) | 1,989候補・特徴1,004・暫定得点213。検索・理由・分類絞込み・0件と解除を実確認。確定既読除外は処理と試験で確認したが、実候補での除外0件・全件読書状態不明。内容接続2/335のため本人向け品質改善は未実証 |

2026-10-15の現在照合で、最新`delivery.json`・`index.html`、それらの生成に使った主要Python4本と表示設定、およびFrench評価コード・評価報告の計9件が保存manifest/実行観測のSHAと一致した。元DBの全再生成やLLM再実行は行っていない。画面操作の証拠は出力が変わっていない既存の実検証を使う。

最新作業ツリーで`uv run --offline pytest tests -q --maxfail=3`を1回実行し、**1,634 passed / 160.90秒 / 実exit 0**を確認した（終端chunk `17f696`）。`uv lock --offline --check`も実exit 0（chunk `277988`）。これは最新ローカルコードのfixture試験であり、未実施のリモートCI・意味的な分類精度・本人満足度を証明しない。文書更新のたびに再実行するものではない。

以前の確認ではローカルは`main`、作業ブランチのリモート先頭は`0b2db9e`、最新差分は未コミットで（chunk `e6d22a`）、複数ターンの環境拒否によりblockedへ移した。その後、環境を変更して再開した通常の`git switch`がexit 0となり、当該作業ブランチへ切り替わった（chunk `2cefaa`）。これは過去の阻害と区別した実成功である。本人評価や不要な追加分類をGit反映の代わりに実行しない。

## 最新追加：独立資料を使ったFrench別コホート5件

保存済みの用途確認済みフランス語原文5作品を、編集者が付けた元のジャンル定義に対する別課題として実分類・集計した。[事前仕様](../data/autonomous-next-v3/evaluation/phase3-french-native-pilot-v1/preregistration.json)と正解情報を分類入力から分離し、書名・著者を伏せた本文根拠に限定した。現72ラベルへの対応正解や、未知作品だけの最終holdoutを確立した実験ではない。

[実行観測](../data/autonomous-next-v3/evaluation/phase3-french-native-pilot-v1/actual-observation.json)は実exit 0、選択5・完了5・保留/失敗0、CLI1回・再試行0、約85.8秒。入力48,360・出力4,398・reasoning341tokenを観測し、reasoningは別掲とする。原入力・根拠引用・処理版・実送信入力の一致と、正解を入力していないことを確認した。原物・引用結果はローカル保存のみで、商用利用・表示・再配布への採用は0。

[正例限定の評価](../data/autonomous-next-v3/evaluation/phase3-french-native-pilot-v1/report.json)は5正例中4件を検出。crime-fiction 2/2、education 1/1、fantastic 1/2、adventureは正例0で未評価。常にpresentなら5/5になるため、4/5を正答率・precision・F1・校正・品質合格とは扱わない。20判定の内訳はpresent 9・unknown 11で、未注釈を負例へ変更していない。独立した編集者注釈との実比較経路が成立した点と、その評価可能範囲を記録する。現72ラベルの独立専門家正解0は不変。

履歴側の[著者経由の成立性](../data/autonomous-next-v3/evaluation/phase3-author-preference-feasibility-v1/report.json)は保存17HTMLの評点行に著者profileリンク0、確定接続0で終了した。[原連載メタデータの条件確認](../data/autonomous-next-v3/evaluation/phase3-original-serial-metadata-rights-v1/report.md)でも用途適合が未確定のためAPI取得0。新しい根拠なしに両経路を繰り返さない。本人履歴の内容接続2/335、最新推薦表示の特徴1,004・暫定得点213は変更していない。

## 特徴1,004件と履歴関係10件

[最新画面](../data/autonomous-next-v3/database/phase3-personal-poc-with-declared-genres-v2/index.html)と[ROOT実検証](../data/autonomous-next-v3/database/phase3-personal-poc-with-declared-genres-v2/verification.json)を保存した。直前1,961候補・974特徴から、新規28候補と既存の特徴なし2件へ合計30件32タグを追加。候補1,989・特徴1,004・暫定得点213・未採点1,776となった。資料記載184件205タグとLLM分類820件は別集計。主DBの特徴778、原分類、旧台帳・出力は変更していない。

| 追加の根拠 | 実採用と用途確認 | 保持した制約 |
|---|---|---|
| P136→P279の明示経路 | [16件18タグ・19経路](../data/autonomous-next-v3/database/phase3-declared-genre-subclass-supplement-v1/manifest.json)。作品派生用途receipt48件、ontology資料22 sourceの別receipt66件 | 候補23のうち7は版・シリーズ・著者・題名等で保留。ontologyは小説数へ加算せず、専用receiptをUsageGuard判定と呼ばない |
| 既存の特徴なし作品 | [CarmillaとPersian Lettersの2件2タグ](../data/autonomous-next-v3/database/phase3-current-featureless-genre-supplement-v1/manifest.json)。派生用途receipt6件 | native分類はcompleted・labels空・conflicts空の2件だけを対象。既存の刊行年・著者不一致3件の保留は解除していない |
| 原語題名の補完 | [12件12タグ](../data/autonomous-next-v3/database/phase3-native-title-genre-supplement-v1/manifest.json)。派生用途receipt36件 | 新しいnative responseからadapter提案を作り、原語ラベルと全claimsの旧親一致を確認。古い親に別rawを付け替えていない |

公開20 QID（原語題名12＋履歴側8）の取得は1リクエスト、HTTP200、266,527bytes、再試行0。全12の原語ラベルがあり、原claimsは旧191親と全件一致した。descriptionsは今回の取得対象外で、記載なし・削除と解釈していない。初回12件の材料確認ではlabel根拠索引の不足で9件を保留し、保存済みの小索引から2 QIDの原根拠を補完した新台帳で残9件だけを確認した。旧保留記録を変更せず、成功した3件のreceiptも残9件へ流用していない。

2件の小文字statement owner表記は、原GUIDを保持して所有者のQ/qのみ比較する新v2処理で対応した。公式Wikibase資料も元表記が小文字の場合を説明している。[StatementGuidの仕様](https://doc.wikimedia.org/Wikibase/REL1_38/php/classWikibase_1_1DataModel_1_1Statement_1_1StatementGuid.html)。旧v1・原raw・旧保留は保存した。別数値の所有者、raw改変、scopeや用途の不成立を許可する変更ではない。

3採用はROOTの固定SHA指定で実exit 0。SQLite3本は整合性ok・外部キー違反0。P279では、作品の実UsageGuard判定、全ontology raw・主名前空間CC0条件の別型再現、全経路、登録済み派生artifactの実用途permitを組み合わせた。資料の1.0は有無であり、中心性・関連度・校正済み確率ではない。

表示では既存のタイトル・著者・別名を保持し、直前1,961公開行のうち変更したのは特徴なし2行だけ。残る1,959行は辞書全体が完全一致した。新28候補の著者表示名は採用せず、私有履歴・個別評点も出力していない。実画面でCarmilla＋fantasy、復元原語題名＋SF、P279対象作品＋歴史小説が各1件、資料記載184件、得点あり213件となることを確認した。観測console error0。タブと8770番サーバーを終了し、LISTENINGなしを確認した。faviconのHTTP404は記録し、全HTTP成功とは主張しない。

![新しい資料記載特徴を反映した実画面](../data/autonomous-next-v3/database/phase3-personal-poc-with-declared-genres-v2/gui-current-source-feature.png)

履歴側は[原HTML335評点の再現と確定関係10件](../data/autonomous-next-v3/evaluation/phase3-history-binding-expansion-v2/report.json)を保存した。子見出し・入れ子templateの2形式を限定対応した新matcherは別v3実装に分離し、旧profile実装のSHAを復元・確認した。[固定版の互換確認](../data/autonomous-next-v3/evaluation/phase3-forward-version-compatibility-v1/report.json)では旧profileの3 recipeと直前画面の9 source pinが全一致した。新関係8件は原Webから書籍・漫画等への派生関係であり、内容特徴を転用していない。内容profile2/335・初回比較結果・設定は不変、推薦改善や本人満足度は未実証。

関連試験はmatcherと版分離49件、投影とontology資料条件45件、Q/q互換等51件の成功記録がある。重複する試験を含むため合算しない。新表示の追加制御はROOTで10件成功し、既存特徴の上書き、重複ID、非二値重み等の拒否を確認した。この補充時点では全件テスト・CIは未実行だった。その後の最新ローカル全件試験1,634成功は冒頭の確認に記録し、最新差分のリモートCIは未実行のまま。今回の補充で分類LLM呼出0・主DB再生成0。

追加154作品とLTO23作品の[限定照合](../data/autonomous-next-v3/evaluation/phase3-declared-genre-lto-overlap-v1/report.md)でも確定対応0。現72ラベルの独立正解・校正は未成立のまま。次は件数だけを目的とした補充を止め、履歴の内容接続・独立評価・差分更新へ重点を移す。以下の節は各段階の固定スナップショットに対する記録である。

## 追加試行と次の到達点

主DBの件数は変更せず、次を別の証跡として追加した。

| 対象 | 実確認した結果と限界 |
|---|---|
| 公開10作品の分類 | [実終了記録](../data/autonomous-next-v3/preparation/phase3-public-serialization-pilot-v1/terminal.json)はexit 0、完了10・失敗0・CLI呼出2。8作品に29ラベル。漫画scopeの6ラベルや厳密定義が不確実なラベルを含み、主DBの採用数へ未加算 |
| 意味とscope | [限定審査](../data/autonomous-next-v3/evaluation/phase3-public-pilot-review-v1/report.md)を保存。AI審査であり、独立正解・精度・校正ではない |
| 履歴接続 | 原HTML17ページと335評点を再現。連載URLの確定関係10件、[内容profile](../data/autonomous-next-v3/evaluation/phase3-personal-profile-v1/runtime-v2/profile-summary.json)の特徴あり2件。本人嗜好の改善は未検証 |
| 推薦比較 | [事前固定仕様](../data/autonomous-next-v3/evaluation/phase3-personal-comparison-v1/preregistration.json)で全335評点を比較。内容の得点可能は2件のみで品質改善の実証ではない |
| 分類補充 | 保存資料の58件runは53結果保存・5出力検証保留。審査後41作品195ラベルを追加し、LLM分類の特徴あり820 |
| 資料記載ジャンル | 原作品scope・原題・用途条件等を確認した184件205タグを別途採用。[最新表示](../data/autonomous-next-v3/database/phase3-personal-poc-with-declared-genres-v2/index.html)は特徴あり1,004、暫定得点213。厳密72分類の精度・校正とは別 |
| 別コホート評価 | [CoNSSA native経路](../data/autonomous-next-v3/evaluation/phase3-reference-route-v1/report.md)を確認。限定属性の実測であり、現72ラベルの評価・校正とは別 |
| CI | [`0b2db9e`のrun](https://github.com/ayutaz/recommend-book/actions/runs/38668029235)はWindows/Ubuntu Python 3.14とも成功。未コミットの新差分は含まない |

既存1,807作品と暫定profileの接続、推薦・固定比較・画面操作は初回完了した。次は履歴接続と分類coverageを改善し、独立評価・差分更新を整備してから規模を拡張する。追加の本人作業は完了条件にしない。主データの採用778作品は変更していない。

## 資料記載ジャンル154候補の採用

Wikidataの原P136と固定13 QIDの名称対応に限った投影を実装し、12テストを確認した。P921主題はgenreへ転用せず、best-rank・qualifierなしの対応を対象にした。原raw・作品ID・書名の再現、原小説scopeと版・シリーズ・短編集・翻案の確認を行い、原題fallbackには保存P1476を利用した。資料名から主人公・文体・物語の中心性を推論していない。

書名解決済み154候補のscope審査では132を保持し、用途材料hold1と同題同著者IDの疑い1を除いた130を採用した。別の書名fallback37候補のうち保存P1476で25の原題を復元し、三部作全体1を保留した24を追加した。同題同著者の疑いは統合せず保留とし、既存作品数の水増しを避けた。

新しい入力台帳と処理定義を採用前に固定し、実際のUsageGuardで表示・保存・商用の用途を判定した。[130件の補助DB](../data/autonomous-next-v3/database/phase3-declared-genre-supplement-v1/manifest.json)は149タグ・receipt390件、[原題復元24件の補助DB](../data/autonomous-next-v3/database/phase3-declared-genre-restored-title-supplement-v1/manifest.json)は24タグ・receipt72件。両採用処理はexit 0、SQLite整合性ok・外部キー違反0。原主DB・原分類・過去台帳は変更していない。これは当該データと用途の確認であり、作品本文等への包括的許可ではない。

[最新画面](../data/autonomous-next-v3/database/phase3-personal-poc-with-declared-genres-v1/index.html)は1,961候補・特徴あり974・得点あり208・未採点1,753。既存1,807件の公開行データは完全一致で、新候補だけ資料記載の区別とWikidata出典リンクを付けた。追加候補の著者表示名は用途未確認のため出力していない。特徴の1.0は有無を表し、関連度や校正済み確率ではない。profileは2/335の固定版、比較結果と設定も変更していない。

実ブラウザで原題`Bienvenue au Paradis`とSFの組合せ1件、資料記載の検索154件、得点あり208件を確認した。観測console error0。[実検証記録](../data/autonomous-next-v3/database/phase3-personal-poc-with-declared-genres-v1/verification.json)に件数・出力SHA・DB検証を保存し、タブ・サーバーを終了、8769番にLISTENINGがないことを確認した。表示の関連4テスト成功。今回の資料投影では分類LLM呼出0・主DB再生成0であり、独立精度検証や本人満足度の実証は追加していない。生成時manifestの`gui_verified=false`は保持し、後続の実確認はverificationに保存した。

![資料記載ジャンルの実画面](../data/autonomous-next-v3/database/phase3-personal-poc-with-declared-genres-v1/gui-source-declared.png)

## 個人向け推薦の初回統合

[ローカル推薦画面](../data/autonomous-next-v3/database/phase3-personal-poc-v1/index.html)、[推薦データ](../data/autonomous-next-v3/database/phase3-personal-poc-v1/delivery.json)、[比較結果](../data/autonomous-next-v3/database/phase3-personal-poc-v1/comparison.json)、[ROOT検証](../data/autonomous-next-v3/database/phase3-personal-poc-v1/verification.json)を保存した。実生成はexit 0で約2.45秒。新モデル呼出・分類・DB再投入はいずれも0。

既存の表示用途確認済み分類ラベルを有無の二値特徴へ投影し、検証済みprofile2件の評点から暫定得点を計算した。旧文字類似得点・説明・私有参照情報はコピーせず、raw confidenceも重みに使わない。候補1,807件のうち得点あり173、未採点1,634、読書状態は全件不明。確定履歴と一致して除外された現候補は0件であり、未知の既読を除外できたとは主張しない。

| 固定方式 | nDCG@10 | Precision@10 | 得点可能な評価候補 / 全候補 |
|---|---:|---:|---:|
| 内容推薦 | 0.5202 | 0.50 | 2 / 335 |
| 書名文字類似 | 0.4155 | 0.38 | 335 / 335 |
| 固定順位 | 0.5061 | 0.50 | 0 / 335 |
| ランダム | 0.3779 | 0.30 | 0 / 335 |

5分割のmacro集計。同じ335評点・各foldの同じ候補集合を用いたが、内容方式は得点可能率約0.60%で、残る順位は固定同順位を含む。著者既知1件・シリーズ既知0件、既観測評点と依存するfoldであり、数値差を有意な改善・未知作品への品質・本人満足度の証拠にしない。仕様や設定の事後変更は0。

実ブラウザで全1,807表示→得点あり173→「夢の守り人」1件→ミステリー分類との組合せ0件→解除1,807→未採点1,634→存在しない検索語0件を確認した。操作で観測したconsole errorは0件。関連16テスト成功、出力SHA一致、公開行・説明の許可フィールド、私有IDと個別評点フィールドの非出力を確認。検証タブ・HTTPサーバーは終了した。GUI検証前に生成した`report.json`の`gui_verified=false`は保持し、実操作後の事実は`verification.json`と`gui-observations.json`に記録する。

![暫定内容推薦の実画面](../data/autonomous-next-v3/database/phase3-personal-poc-v1/gui-supported.png)

別保存先への再生成を約2.8秒で実行し、`delivery.json`・`comparison.json`・`report.json`・`index.html`・`manifest.json`の5ファイルのbytes一致を確認した。[再現観測](../data/autonomous-next-v3/database/phase3-personal-poc-reproduction-v1/reproduction-observation.json)を保存。分類・元DB再生成・GUIの再実行は0。固定profile・出典・実装・比較仕様のSHAが変われば停止する。今後の再生成は未使用の出力先を指定する。

```powershell
uv run --offline python scripts/build_personal_poc.py --output data/autonomous-next-v3/database/phase3-personal-poc-reproduction-v1
```

以下の主データ・従来画面の結果は、それぞれの固定スナップショットに対する過去の記録である。

## 保存資料の補充と追加表示

保存済みあらすじの補充では、最初の7作品・24ラベルは実exit 0で生成したが、入力台帳に特徴抽出の事前登録が欠けており、[採用時に拒否](../data/autonomous-next-v3/preparation/phase3-cached-synopsis-pilot-v1/feature-adoption-denial.json)された。準備時の確認漏れとして記録し、原結果・元台帳は変更せず、再分類も行っていない。この7件を採用数へ加えない。

未実行の別2作品で、抽出処理の事前登録と書誌情報の直接出典への帰属を整え、外部LLM・表示・素材ごとのローカル保存条件を確認した。2件の分類は実exit 0、[補助DBへの保存](../data/autonomous-next-v3/database/phase3-cached-synopsis-feature-supplement-v1/manifest.json)も実exit 0。元ラベル14件を保持し、矛盾のある1作品は特徴を保留した。[限定意味審査](../data/autonomous-next-v3/evaluation/phase3-cached-synopsis-feature-pilot-review-v1/report.md)後、もう1作品の7ラベルだけを表示へ反映した。

[追加反映画面](../data/autonomous-next-v3/database/phase3-personal-poc-with-synopsis-v1/index.html)は候補1,807・特徴あり779・暫定得点あり173。原主DBの特徴778は不変で、追加分は小さいSQLiteと表示用projectionに分離した。`斜陽`と`階級`の組合せで1件となり、7ラベル・未採点・読書状態不明を実ブラウザで確認。[実検証記録](../data/autonomous-next-v3/database/phase3-personal-poc-with-synopsis-v1/verification.json)にSQLite整合性ok・外部キー違反0・receipt10件・画面操作・終了済みサーバーを保存。新adopterの専用19テスト成功はfixtureでの記録、実2件の保存成功とは区別する。1,000作品の特徴目標と品質改善の実証は未達。

残る77候補は60件が入力用途を通り、17件をライセンス条項または著者出典の証拠不足で保留。上記2件を除く58作品を、[事前検証](../data/autonomous-next-v3/preparation/phase3-cached-synopsis-expansion-with-features-v1/root-audit.json)と[実行ゲート](../data/autonomous-next-v3/preparation/phase3-cached-synopsis-expansion-with-features-v1/root-execution-gate.json)確認後に分類した。最大2並列・5件batch、実行時間設定1,800秒・観測token上限100万・rate limit時停止の条件を保持し、以下の実終了を確認した。

## 58作品の補充終了・審査・追加表示

[実終了記録](../data/autonomous-next-v3/preparation/phase3-cached-synopsis-expansion-with-features-v1/terminal.json)はexit 0、選択58・結果保存53・保留5・未処理0、CLI12呼出・再試行0。run時間は約872.5秒。入力389,852・出力76,291・reasoning4,650tokenを観測した。reasoningは別掲値で出力に単純加算せず、利用枠観測をAPI請求額へ換算しない。5件は同じbatchの`Incorrect label axis`で出力検証保留となり、原応答を保存したまま修正・再分類・採用をしていない。

53結果・254ラベルを分担して一度ずつ審査した。[終端へ結び直した審査](../data/autonomous-next-v3/evaluation/phase3-cached-synopsis-expansion-review-v1/report.json)では、個別入力行・job・result・source descriptorのSHAを全件確認し、審査中に作ったpartial報告は変更せず保持した。work判定は問題未観測23・不確実25・保留5、label判定は問題未観測219・不確実35。AI内部審査であり、独立正解・精度・校正ではない。

[補助DB保存](../data/autonomous-next-v3/database/phase3-cached-synopsis-expansion-supplement-v1/manifest.json)は実exit 0。53原結果、抽出時点の特徴あり46作品・矛盾保留4作品、用途receipt265件を保持した。SQLiteの整合性ok・外部キー違反0。原結果・関連度・未校正confidence・conflictを変更していない。用途を通った特徴に意味・scope審査のmaskを適用し、41作品195ラベルだけを新しい表示へ追加した。

[最新推薦画面](../data/autonomous-next-v3/database/phase3-personal-poc-with-expansion-v1/index.html)は1,807候補・特徴あり820作品・暫定得点175作品・未採点1,632作品。先の779作品から41作品増え、原主DB778は不変。profileは2件の固定版、比較設定と初回比較結果も再利用しており、品質改善の実証や本人満足度の検証は追加していない。1,000作品まで180作品不足する。

実ブラウザで`Women's Barracks`＋歴史小説を操作し、追加した1作品の4ラベル・未採点・読書状態不明を確認した。解除後の得点あり175件も確認し、観測console errorは0。[ROOT検証](../data/autonomous-next-v3/database/phase3-personal-poc-with-expansion-v1/verification.json)へ出力SHA・DB整合性・件数を保存。画面とサーバーを終了し、TCP一覧で8768番にLISTENINGがないことを確認した。専用19テストは先の記録を再利用し、今回の実保存・画面確認の証拠と区別する。

![追加採用作品の実画面](../data/autonomous-next-v3/database/phase3-personal-poc-with-expansion-v1/gui-reviewed-supplement.png)

## 不足を補う次の保存資料

[未使用Wikidataの棚卸し](../data/autonomous-next-v3/evaluation/phase3-unused-wikidata-inventory-v1/report.md)では、保存済み3,154 source rowsと主DBの対象ID列だけを一度照合した。未使用候補は小説779＋ライトノベル37＝816件で、既存87候補・今回58件とのID重複は0。P136ジャンル明示299、P921主題62、両方26を分け、書名とP136が揃う限定20候補を保存した。独立作品としての同定・用途許可・特徴採用は0であり、816を追加作品数や分類可能数とは扱わない。本文・あらすじ根拠も0。主DBの全hash・コピー・再生成、raw64ファイルの再走査は行っていない。

[追加8記事の公開関係確認](../data/autonomous-next-v3/evaluation/phase3-serialization-scope-expansion-v1/report.md)では、原連載URLと書籍・翻案の関係8件を公開raw・revision・template/prose locatorで確認した。取得8回・再試行0・原serialサイト要求0。現profile matcher適合6、子見出し／入れ子templateへの対応が必要2である。Web版からの大幅改訂等の注意を保持し、genre・plot転用0・私有履歴join0・profile更新0。既存2/335の接続数を増えた扱いにはしていない。

今後は明示的な資料記載genreとLLM推論を区別して、再取得・同じ分類・全DB再生成を避けて補充する。今回の終端・採用経路では同じCodex実行ファイルを全量hashし直す構造があり、次の大量native処理前に読取りの固定とrun内共有を検討する。用途判定や原応答の検証を省略する方針ではない。

[資料記載genreの限定成立性](../data/autonomous-next-v3/evaluation/phase3-source-declared-genre-feasibility-v1/report.md)は13 QIDの直接名対応191候補を示す。書名解決済み154、系列関係・scope注意を一旦保留すると75で、新採用0。シリーズの一員であるP179をシリーズ全体の証拠とは扱わず、原作品・巻・版の確認待ちとして分けた。bare romance等の未確認の意味やP921主題をgenreへ推論せず、原資料の再hash・モデル・ネット・主DB照会は追加0だった。

旧否認の出所は、[先頭10件の照合](../data/autonomous-next-v3/evaluation/phase3-denial-origin-trace-v1/report.md)で旧Wikipediaの混在sectionに対する機械的保留と特定した。これは新たな許諾・一般解除ではなく、元の否認は保持する。残り121件を調査済みとは扱わない。

## 主データの確定成果物

| 成果物 | 実確認した結果 |
|---|---|
| [作品・分類DB](../data/autonomous-next-v3/database/final-delivery-main-v1/classified-database/dataset.sqlite) | 候補2,005作品。本分類1,862作品のうち結果1,832・保留30。2,131ラベル。特徴採用778作品 |
| [表示画面](../data/autonomous-next-v3/database/final-delivery-view-v2/site/index.html) | 書誌1,870・書名文字類似1,870・分類検索1,807作品。内容嗜好の改善を実証した順位ではない |
| [分類・欠測・審査の評価報告](../data/autonomous-next-v3/evaluation/world-main-semantic-evaluation-report-v1/report.md) | 軸・原言語・年代・資料・根拠・関連度・未校正confidence・欠測を集計。作成時点の未確定採用数は本報告の確定値で補う |
| [最終確認記録](../data/autonomous-next-v3/independent-audit/world-main-final-delivery-root-review-v1/report.json) | 正式生成の実exit 0、出力SHA、DB整合性、実画面操作、HTML再生成、修正後テストを記録 |

SQLiteの`integrity_check`は`ok`、外部キー違反は0。正式生成は2026-10-15 03:40:30 JSTに実exit 0で終了し、launcherと実Pythonの終了を確認した。元の3HTMLは保存済み表示JSONから再生成してbytes一致を確認した。独立したDB全再生成は過剰処理として中止しており、その一致保証は含めない。

## 分類と信頼度

ラベルはジャンル951、テーマ438、構成161、主人公337、雰囲気244、文体0。判断はpresent 2,130、unknown 1。関連度の非nullは1,476、未校正confidenceの非nullは2,130で、欠測を補作していない。confidenceの範囲は0.7〜1.0、平均0.910216。校正済みconfidenceと人間検証済みラベルはいずれも0。

文体は全1,832作品で不明。原文・翻訳本文の不足を、書誌や短いあらすじから推測して埋めない。保存結果のラベルあり810作品に対し、採用条件反映後の特徴ありは778作品。特徴矛盾保留55、品質保留25、不確実321、棄権992は異なる判定の集計で、足して全作品数としない。

全件Sol審査は、問題を観測しなかったラベル1,668、不確実381、保留82を記録した。原入力・応答・出典・ラベル・理由の対応は照合済み。AI判断の内訳をprecision、recall、F1、正解確率へ変換しない。

## 実画面の確認と修正

3モードをChromeで確認した。6軸の選択欄、5軸を指定したAND検索、解除、0件表示、日本語書名、著者、日本語別名から原題への検索を実操作した。文体欄は「すべて」のみ。分類画面は全1,807作品で読書状態不明。ラベルあり778・なし1,029をDOMでも確認し、ブラウザエラーは0件だった。

参考順位の表示データに別名が引き継がれない不具合を見つけ、表示用途を通った別名を全モードへ渡す1行を修正した。保留別名の非表示と許可別名の保持を2モードで検証し、関連19テストが成功した。既存1,378テストは修正前の記録で、修正後に全件を再実行したとは記載しない。

DB・元の正式出力を変更せず、[修正版ビュー](../data/autonomous-next-v3/database/final-delivery-view-v2/manifest.json)を別の保存先へ生成した。同一作品ID・原題・著者・読書状態で、用途確認済み書誌JSONの別名だけを引き継いだ。順位・得点・ラベル・読書状態は変更していない。書誌と文字類似は600別名、分類検索は569別名を保持する。分類・DB投入・native検証の再実行は0。

修正版でも「油断」から`Yudan!`、「トロッコ」から`Minecart`を検索できた。出力切替後のキャッシュ表示で一度0件となった記録も保持し、再読み込み後の成功と区別した。検証タブと担当所有HTTPサーバーを終了し、待受が残っていないことを確認した。

![日本語別名検索の実画面](../data/autonomous-next-v3/independent-audit/world-main-final-delivery-root-review-v1/final-alias-search.jpg)

## 工程1〜4の到達状況

| 元の工程 | 確認済み | 未達・未評価 |
|---|---|---|
| 1. 収集 | Open Library・NDL・Wikidata等の保存資料、出典・用途台帳、2,005作品候補 | 世界全体の網羅性。小説1,273・ライトノベル80・短編528・種別未確定124 |
| 2. 名寄せ・作品DB | 作品・表現・版の分離、外部ID、出典、型付き関係、保留の保存 | 広範な原題・翻訳・文庫対応の精度。NDL26版は現在作品への対応未確定。現在の翻訳対応25作品・版対応8作品に限る |
| 3. 多ラベル分類 | 主DBは1,832結果・2,131ラベル・保留30・特徴778。追加表示はLLM分類820＋資料記載184で特徴1,004 | 文体の不足。資料記載をLLM判定数・厳密72分類の品質へ加算しない |
| 4. 関連度・信頼度・精度検証 | 数値・欠測の保存、全件AI審査、根拠照合、層別・欠測報告、保留の採用制御 | 本runに適用可能な独立専門家正解は0。precision・recall・F1・Brier・ECE・信頼度校正は未評価 |

[収集・名寄せレビュー](../data/autonomous-next-v3/independent-audit/collection-resolution-completion-review-v1/report.md)と[LTO参照調査](../data/autonomous-next-v3/evaluation/world-main-lto-reference-feasibility-v1/report.md)を再利用し、大規模検証を繰り返さない。適用可能な独立正解が確立する前に精度・校正値を作らず、正解不足を解消する見込みなく追加分類を起動しない。独立正解との比較を残作業として保持する。

追加の小規模確認として、[別名照合記録](../data/autonomous-next-v3/evaluation/world-main-reference-alias-overlap-v1/report.json)でLTO23作品と本分類1,862作品の原題・表示用途確認済み別名600件を正規化完全一致で比較した。一致候補0、作品統合0、採用gold0、新モデル呼出0。入力3ファイルのSHAは前後一致した。原題・記事URLだけの既存調査を別名で補った結果であり、未知の別名を含む実際の重なりの不存在までは主張しない。

商用利用・AI入力・表示・再配布は用途別台帳で管理する。取得できることを本文・翻訳・商用利用の一括許可へ広げない。保存済み335評点の文字類似評価は改善を示しておらず、本人の満足度や未読推薦精度の検証完了とは扱わない。1万作品以上への拡張・ToC公開は実行していない。

## 残作業と再開条件

継続指示に基づく[Underwood限定照合](../data/autonomous-next-v3/evaluation/world-main-underwood-feasibility-v1/report.md)を追加した。保存済み984巻と本分類1,862作品の原題・許可済み別名600件を比較し、タイトル候補4件を確認した。2件は著者・刊行年不一致で除外。整合候補2件のうち1件は対照群タグのみ、もう1件は外部の粗いSF区分と保存済みSF presentが一致した。1件の区分一致を72ラベルの独立正解・精度指標へ転用せず、正解追加0を保持した。原資料SHAの前後一致、読み取り専用DBのsize/mtime一致を確認し、分類・DB更新・新モデル呼出は0だった。

この結果を踏まえて一度は現在成果で区切るかの判断を依頼したが、その後の「最終ゴールに向けて計画」の指示により、初期PoCで開発を終了する案を回答待ちから外した。[計画 v15](autonomous-next-phase-plan-v15.md)は、評価用コホートの確保・既存履歴の接続・1万〜3万作品・10万作品以上・将来のToC提供までを段階化する。元の工程4の未達や精度・校正の未検証を完了へ変更した事実はない。

本分類へ対応する独立注釈が得られ、作品・表現の同定、72ラベルとの意味対応、注釈由来、利用条件を確認できる範囲から評価を再開する。正例注釈しかない範囲でprecision・F1・校正を計算せず、評価対象と分母を先に固定する。追加の本人レビュー・採点・正解作成は再開条件にしない。

既存成果物を保持し、独立正解不足を解消する見込みのない再分類や全件DB再生成を追加しない。資料記載込みの推薦特徴1,000件は最新表示で達成したが、文体と翻訳・版対応の不足、名寄せ精度の未評価は残る。公開試行・履歴接続・補充は上記の最新追加へ記録し、本分類の精度評価を完了へ変更しない。

## コミット・push

反映先は [`feat/world-novel-dataset-poc`](https://github.com/ayutaz/recommend-book/tree/feat/world-novel-dataset-poc)。既存38コミットのリモート先頭 `0b2db9eff696bc748fc922582f1cf44a43fc5b47` とローカルHEADの一致は当時の確認記録。追加分は同ブランチの履歴と[対応CI](https://github.com/ayutaz/recommend-book/actions?query=branch%3Afeat%2Fworld-novel-dataset-poc)で確認する。取得原物・DB・私有評価はGit対象外である。

追加分の反映に向けた `git switch -c feat/world-novel-dataset-poc --track origin/feat/world-novel-dataset-poc` は以前「承認が必要だがAskForApprovalがNever」と拒否されたが、設定変更後の通常実行ではexit 0で成功した。ユーザー指示の不足だったとは扱わず、環境変更後の成功として記録する。mergeは行わない。

過去の反映準備では当時の未コミット72ファイルを対象に、Python構文45件、JSON読込3件、当時の4文書のローカルリンク50件、差分の空白を確認した。データ本体の混入0、実施した秘密情報パターン検査の検出0。当時の既存1,378テスト時点のソース203ファイルと比較し、差分は別名修正のコード・テスト2件だけであることを確認した。この2件の関連19テスト成功は当時の記録であり、その後の追加コードを含む最新全1,634試験成功とは別のスナップショットである。72ファイルという数や2件だけの差分を現在の作業ツリーへ一般化しない。構文やパターン検査は実行時品質・網羅的な秘密情報検査の保証ではない。
