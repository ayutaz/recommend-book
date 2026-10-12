# 自動PoCの進捗と完了証拠

2026-10-13時点。[最新計画](autonomous-completion-plan-v2.md)のA〜Fローカル自動PoCを、保留・未検証範囲を含めて完了した。[実行結果](autonomous-poc-final-report.md)に詳細を記録した。追加の本人評価・試読・採点・感想・履歴入力・画面確認は0件。既存335件の本人評価を再利用し、AI判断を本人評価や人手正解として登録しない。

## 現在の証拠

| 段階 | 確認済み | 完了までの残作業 |
|---|---|---|
| A 固定 | 評価契約とbaseline16/core10を固定し実SHA照合 | 完了 |
| B 外部注釈 | 実成立60作品、55 completed/5 held。native別schema20。raw・cache・event・durable・index・source SHA照合、要求100未達/未実行120理由を確定 | 完了・範囲限定 |
| C 補充・名寄せ | 79作品/81Evidence採用、947保留。2020歴史DB/15scope hold、NDL26保留/16役割曖昧/新投影0。名寄せ元350＋別76回復で426処理、371判断/55保留、authority0 | 完了・失敗履歴保持 |
| D 分類評価 | 新79全完了/16calls、固定400両blind完了。第三120全完了/24calls、AI採用候補332/保留68 | 完了・AI安定性の評価 |
| E 推薦 | 保存335の8設定nested CV。2005world候補/731supported/表示100、確認済み未読0。実画面と別生成8内容出力一致 | 完了・world品質未検証 |
| F 納品 | 最終1132テスト成功/source170実前後一致、cache79再利用/新LLM0、DB/評価/rights/画面の独立照合、最終証拠manifestと要件監査、文書コミット | 完了・ローカルPoC範囲 |

同定が成立した本文は現時点で60作品であり、目標100件の達成とは数えない。条件不明の資料を通して件数を増やさない。名寄せのAI合意をauthority mergeへ反映しない。

全2,020 canonicalのうち、active Wikidata原物を持つ2,008件のP31を再検査し、版classが作品扱いになっていた15件を発見した。残る非WD12件はこのclass検査の対象外として保持する。統合担当もSQLと原物から全15件を独立に再検出し、出典/version/canonical/claims/SHAを照合した。最終DB・推薦では15件をscope矛盾の保留へ隔離し、2,020件の歴史分類と2,005件の推薦対象を分ける。既存rawと分類結果を消して数を合わせない。

## 評価で分かったこと

外部pilot20の主人公4ラベルの正例は23件、検出7件、入力が成立した範囲の検出率30.43%。固定180作品の全200正例を分母にすると3.5%、入力coverageは20/180。常時present方式は同じ入力で23/23となるため、検出率だけでは精度の合格を示せない。これは抜粋からの限定検出であり、作品全体のaccuracy・precision・F1・校正ではない。別native taskの結果も、この現taxonomy出力から作った値として代用しない。

外部60では24/65正例検出（36.92%）、全200分母では24/200（12%）、入力coverage60/180。held5を分母から除いていない。専用native20の直接4正例対応は0/23、別掲raw native age/profession40項目は2/40（age0/20・profession2/20）。語義統合せず、部分抜粋・文字列一致に限定した検出結果であり、accuracy・precision・F1・校正や世界全6軸の精度ではない。

本人の既存評価では、内側CVで選ぶ手続きの外側nDCG@10は0.573995、人気順0.633978。人気順を上回る証拠は得られていない。全335件の内側CVが選んだhybrid_50は実験候補であり、外側の値で再選定していない。世界作品には比較可能な人気指標がないため、納品では根拠付きpersonalの参考順位へ戻す。未知の読書状態を未読に変換しない。

出典: `data/autonomous-poc-v1/reference/pilot20-positive-report.json`、`data/autonomous-poc-v1/independent-audit/reference-pilot20-v1.json`、[推薦評価](autonomous-recommendation-evaluation.md)。新しい本人評価は求めず、worldの実効推薦精度は未検証として報告する。

## 修正・実行記録

DB構築の初回は、元runの大きなmetadataを作品ごとに再parseしてメモリが増える問題を実測した。担当のbuilderだけを意図的に停止し、exit1・出力未公開・baseline16不変を保存。run単位の共有cacheへ修正し、30作品/2MB metadataの反例はpeak約120MBのREDから16MB未満のGREENへ変わった。関連123テスト成功後に新attemptを開始した。停止した初回を成功扱いしない。

推薦納品APIは、呼出し側がbooleanだけでworld人気指標の確認を主張する経路を拒否した。反例RED後に修正し、納品6件＋推薦評価17件の23テスト成功。その後、最終DBによる生成・再生成・全体テスト・実ブラウザ確認を完了した。

scope隔離済みloaderへ納品経路を接続し、歴史poolが推薦に混入する反例REDから修正。納品7件＋推薦評価17件の24テスト成功。今後のWikidata取得でも、work指定に非deprecatedの版class/P629があれば原物付きholdとする再発防止を追加し、関連24テスト成功。既存のkind=edition取得は保持する。

旧全体テストはexit0、1,123 passed / 90.39秒をv1に記録した。当時は並行作業中で実行前source snapshotを捕捉していなかった。最終チェックは別wrapper session77538の実exit0、1,132 passed / 88.74秒、source170の実前後SHA/inventory一致を `full-tests-observation-v2.json` と `full-tests-terminal-v2.json` に記録した。pytest子プロセス観測とwrapper実終了を分ける。外部pilot20のraw4batchはread-only監査CLIでも照合し、原物不変・新LLM0を確認した。

79差分のexecution contextは10 core raw copy・入力・兄弟manifest・並列2/batch5等のcontrolsを実行前に固定した。新run namespace不在/cache移行0を準備記録に保存した後、root session20797の実exit0/79completedを観測し、raw16batch・全結果・終端proofを独立照合した。別cache検証は79再利用/新LLM0/原結果bytes一致。

名寄せv3は350/426 finalizedの後、sameとcontradictionsが同居する1出力をvalidatorが拒否し、native session38383の実exit1を観測した。原run/73応答を保持し、350を426成功と表示しない。未finalized76だけ別namespaceで回復し、回復/集約監査は実exit0。全426の欠損・重複0、判断371/保留55、新authority採用0。旧prelaunch観測不足と外部stdin bytes未捕捉を事後に補ったとは扱わない。

第三分類の固定400 cohortにはscope矛盾15件中3件を含む。標本を事後に変更せず歴史的な安定性実験として記載し、最終推薦対象の15除外とは分ける。

外部60とnative20は独立raw監査でも終了証拠・実イベント・durableを全件照合した。60のraw20呼出しには元pilot4と新phase16を含むため、元20作品を新規実行として二重計数しない。nativeは4呼出し。原stdin bytes/実argvの外部捕捉がない範囲は、そのまま未証明とする。

元分類のcore10、旧入力・結果・人手packを保持している。CLIは最大8並列の予約を分担し、root差分2枠を確保して実行した。全終了レーンの予約を解放した。raw versionは実測 `codex-cli 0.162.1`。利用量はCLIイベントの観測で、請求額ではない。

新81の4用途条件は検証したが、旧WP792/WS40の現行条件履行は未再承認。新79入力にも旧WP7が含まれ、個別の事後保留を別台帳へ固定する。条件宣言をprelaunch確認済みとしない。実Chromeのfilter/空表示/理由/input0/console0、DOM100と保存JSONの非暗号checksum、9出力SHAは `browser-delivery-validation-v1.json`、`browser-payload-binding-v1.json`、`delivery-reproduction-coverage-v1.json` に結合した。

[商用化・拡張準備](autonomous-commercial-scale-preparation.md)は用途別条件と線形見積もりを示す。全素材の商用許諾、全世界の本文権利、実10万作品の収集・性能を確認済みとはしない。GitHub hosted CIは未実行。データ・個人評価はローカルprivate成果物として保存し、push・公開は行っていない。

最終証拠は `data/autonomous-poc-v1/evidence-manifest-v1.json`（116成果物・baseline16・tested source170）。別担当の `independent-audit/final-requirements-review-v1.json` が52参照の存在/SHAとv2完了条件を照合した。ローカルcommitとclean-statusを含む完了receiptは `completion-manifest-v1.json` に保存する。旧人手gate・全世界の品質/権利保証は達成したことにしない。
